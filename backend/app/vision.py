"""Local ONNX adapter. No automatic downloads or fabricated detections."""

import hashlib
import json
import threading
from pathlib import Path
from functools import lru_cache
import cv2
import numpy as np
from .config import MODEL_MANIFEST, REQUIRE_CUDA


class DetectorUnavailable(RuntimeError):
    pass


DEFAULT_INFERENCE_PARAMETERS = {
    "confidence": 0.25,
    "iou": 0.5,
    "max_detections": 300,
}
DEFAULT_TILING_PARAMETERS = {"enabled": False, "overlap": 0.2}
MAX_TILES_PER_FRAME = 100


def normalize_inference_parameters(parameters=None, defaults=None):
    values = {
        **DEFAULT_INFERENCE_PARAMETERS,
        **(defaults or {}),
        **(parameters or {}),
    }
    if set(values) != set(DEFAULT_INFERENCE_PARAMETERS):
        raise ValueError("Неизвестный параметр обработки модели")
    try:
        confidence = float(values["confidence"])
        iou = float(values["iou"])
    except (TypeError, ValueError) as error:
        raise ValueError("Пороги модели должны быть числами") from error
    maximum = values["max_detections"]
    if not 0 < confidence <= 1:
        raise ValueError("Confidence должен быть больше 0 и не больше 1")
    if not 0 < iou <= 1:
        raise ValueError("NMS IoU должен быть больше 0 и не больше 1")
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or not 1 <= maximum <= 1000
    ):
        raise ValueError("Максимум детекций должен быть от 1 до 1000")
    return {
        "confidence": confidence,
        "iou": iou,
        "max_detections": maximum,
    }


def manifest_inference_defaults(manifest):
    return normalize_inference_parameters(
        {
            "confidence": manifest.get("confidence", 0.25),
            "iou": manifest.get("iou", 0.5),
            "max_detections": manifest.get("max_detections", 300),
        }
    )


def normalize_tiling_parameters(parameters=None):
    values = {**DEFAULT_TILING_PARAMETERS, **(parameters or {})}
    if set(values) != set(DEFAULT_TILING_PARAMETERS):
        raise ValueError("Неизвестный параметр тайловой обработки")
    if not isinstance(values["enabled"], bool):
        raise ValueError("Признак тайловой обработки должен быть логическим")
    overlap = values["overlap"]
    if isinstance(overlap, bool):
        raise ValueError("Перекрытие тайлов должно быть числом")
    try:
        overlap = float(overlap)
    except (TypeError, ValueError) as error:
        raise ValueError("Перекрытие тайлов должно быть числом") from error
    if not 0 < overlap <= 0.5:
        raise ValueError("Перекрытие тайлов должно быть больше 0 и не больше 0.5")
    return {"enabled": values["enabled"], "overlap": overlap}


def _tile_starts(length, tile_size, overlap):
    if length <= tile_size:
        return [0]
    stride = max(1, tile_size - round(tile_size * overlap))
    starts = list(range(0, length - tile_size + 1, stride))
    last = length - tile_size
    if starts[-1] != last:
        starts.append(last)
    return starts


def tile_windows(height, width, tile_size, overlap):
    return [
        (x, y, min(x + tile_size, width), min(y + tile_size, height))
        for y in _tile_starts(height, tile_size, overlap)
        for x in _tile_starts(width, tile_size, overlap)
    ]


def _global_nms(detections, iou, maximum):
    result = []
    for class_id in sorted({item["class_id"] for item in detections}):
        candidates = [item for item in detections if item["class_id"] == class_id]
        boxes = [
            [
                item["bbox"][0],
                item["bbox"][1],
                item["bbox"][2] - item["bbox"][0],
                item["bbox"][3] - item["bbox"][1],
            ]
            for item in candidates
        ]
        keep = cv2.dnn.NMSBoxes(
            boxes,
            [item["confidence"] for item in candidates],
            0,
            iou,
        )
        result.extend(candidates[int(index)] for index in np.asarray(keep).reshape(-1))
    result.sort(key=lambda detection: detection["confidence"], reverse=True)
    return result[:maximum]


def _merge_tiled_seams(detections):
    """Prefer a complete neighbouring-tile box over its clipped duplicate."""
    ordered = sorted(
        detections,
        key=lambda item: (
            not item["_tile_edge"],
            (item["bbox"][2] - item["bbox"][0])
            * (item["bbox"][3] - item["bbox"][1]),
            item["confidence"],
        ),
        reverse=True,
    )
    kept = []
    for candidate in ordered:
        box = candidate["bbox"]
        area = max(0, box[2] - box[0]) * max(0, box[3] - box[1])
        duplicate = False
        for existing in kept:
            if (
                candidate["class_id"] != existing["class_id"]
                or candidate["_tile"] == existing["_tile"]
                or not (candidate["_tile_edge"] or existing["_tile_edge"])
            ):
                continue
            other = existing["bbox"]
            intersection = max(0, min(box[2], other[2]) - max(box[0], other[0])) * max(
                0, min(box[3], other[3]) - max(box[1], other[1])
            )
            other_area = max(0, other[2] - other[0]) * max(0, other[3] - other[1])
            if intersection / max(1e-9, min(area, other_area)) >= 0.55:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return [
        {key: value for key, value in item.items() if not key.startswith("_tile")}
        for item in kept
    ]


def detect_tiled(model, frame, parameters=None, tiling=None, on_tile=None):
    inference = normalize_inference_parameters(
        parameters, model.info.get("inference_defaults")
    )
    options = normalize_tiling_parameters(tiling)
    if not options["enabled"]:
        return model.detect(frame, inference)
    height, width = frame.shape[:2]
    tile_size = int(model.info["input_size"])
    windows = tile_windows(height, width, tile_size, options["overlap"])
    if len(windows) > MAX_TILES_PER_FRAME:
        raise ValueError(
            f"Кадр требует {len(windows)} тайлов; максимум {MAX_TILES_PER_FRAME}"
        )
    detections = []
    for number, (left, top, right, bottom) in enumerate(windows, start=1):
        tile = frame[top:bottom, left:right]
        tile_height, tile_width = tile.shape[:2]
        for detection in model.detect(tile, inference):
            x1, y1, x2, y2 = detection["bbox"]
            mapped = {
                **detection,
                "_tile": number,
                "_tile_edge": (
                    (left > 0 and x1 <= 0.025)
                    or (top > 0 and y1 <= 0.025)
                    or (right < width and x2 >= 0.975)
                    or (bottom < height and y2 >= 0.975)
                ),
                "bbox": [
                    (left + x1 * tile_width) / width,
                    (top + y1 * tile_height) / height,
                    (left + x2 * tile_width) / width,
                    (top + y2 * tile_height) / height,
                ],
            }
            detections.append(mapped)
        if on_tile:
            on_tile(number, len(windows))
    return _global_nms(
        _merge_tiled_seams(detections),
        inference["iou"],
        inference["max_detections"],
    )


@lru_cache(maxsize=4)
def validated_manifest(path, manifest_stamp, weight_stamp):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    weights = Path(path).parent / data["weights"]
    if data["format"] not in ("xyxy6", "yolo_raw"):
        raise ValueError("Неподдержанный формат ONNX")
    if not isinstance(data["input_size"], int) or not 32 <= data["input_size"] <= 2048:
        raise ValueError("Неверный размер входа модели")
    from .catalog import class_map

    mapping = data["class_map"]
    if not mapping or any(
        not str(k).isdigit() or str(v) not in class_map() for k, v in mapping.items()
    ):
        raise ValueError("Неизвестный класс в manifest")
    manifest_inference_defaults(data)
    if "class_names" in data and set(data["class_names"]) != set(mapping):
        raise ValueError("class_names и class_map должны описывать одни классы")
    if hashlib.sha256(weights.read_bytes()).hexdigest() != data["sha256"]:
        raise ValueError("SHA256 весов не совпадает с manifest")
    return data


def model_status():
    if not MODEL_MANIFEST:
        return {
            "ready": False,
            "reason": "Модель детектора не подключена",
            "supported_classes": [],
        }
    try:
        data = json.loads(Path(MODEL_MANIFEST).read_text(encoding="utf-8"))
        weights = Path(MODEL_MANIFEST).parent / data["weights"]
        if not weights.is_file():
            raise ValueError("Файл весов отсутствует")
        data = validated_manifest(
            MODEL_MANIFEST,
            Path(MODEL_MANIFEST).stat().st_mtime_ns,
            (weights.stat().st_mtime_ns, weights.stat().st_size),
        )
        return {
            "ready": True,
            "name": data["name"],
            "sha256": data["sha256"],
            "supported_classes": list(dict.fromkeys(data["class_map"].values())),
            "format": data["format"],
            "runtime_validation": "on_worker_start",
            "inference_defaults": manifest_inference_defaults(data),
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"ready": False, "reason": str(error), "supported_classes": []}


class OnnxDetector:
    def __init__(self, manifest_path=MODEL_MANIFEST, *, providers=None):
        import onnxruntime as ort

        if not manifest_path:
            raise DetectorUnavailable("Модель детектора не подключена")
        self.manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        weights = Path(manifest_path).parent / self.manifest["weights"]
        self.manifest = validated_manifest(
            str(manifest_path),
            Path(manifest_path).stat().st_mtime_ns,
            (weights.stat().st_mtime_ns, weights.stat().st_size),
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        require_cuda = providers is None and REQUIRE_CUDA
        if (
            providers is None
            and "CUDAExecutionProvider" in ort.get_available_providers()
        ):
            ort.preload_dlls(directory="")
        providers = providers or [
            p
            for p in ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if p in ort.get_available_providers()
        ]
        self.session = ort.InferenceSession(
            str(weights), sess_options=options, providers=providers
        )
        if require_cuda and "CUDAExecutionProvider" not in self.session.get_providers():
            raise DetectorUnavailable("GPU-профиль требует CUDAExecutionProvider")
        inputs = self.session.get_inputs()
        size = self.manifest["input_size"]
        if (
            len(inputs) != 1
            or inputs[0].type != "tensor(float)"
            or len(inputs[0].shape) != 4
            or any(
                isinstance(actual, int) and actual != expected
                for actual, expected in zip(inputs[0].shape, (1, 3, size, size))
            )
        ):
            raise DetectorUnavailable("Ожидался ONNX float32 [1,3,size,size]")
        self.lock = threading.Lock()
        self.info = {
            k: self.manifest[k]
            for k in ("name", "sha256", "class_map", "input_size", "format")
        }
        self.info["providers"] = self.session.get_providers()
        self.info["supported_classes"] = [
            str(v) for v in dict.fromkeys(self.manifest["class_map"].values())
        ]
        self.info["inference_defaults"] = manifest_inference_defaults(self.manifest)
        for key in (
            "class_names",
            "confidence",
            "iou",
            "max_detections",
            "source_sha256",
        ):
            if key in self.manifest:
                self.info[key] = self.manifest[key]

    def detect(self, frame, parameters=None):
        h, w = frame.shape[:2]
        size = self.manifest["input_size"]
        scale = min(size / w, size / h)
        nw, nh = round(w * scale), round(h * scale)
        x, y = (size - nw) // 2, (size - nh) // 2
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        canvas[y : y + nh, x : x + nw] = cv2.resize(frame, (nw, nh))
        tensor = (
            cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
            .transpose(2, 0, 1)[None]
            .astype(np.float32)
            / 255
        )
        with self.lock:
            output = self.session.run(
                None, {self.session.get_inputs()[0].name: tensor}
            )[0][0]
        inference = normalize_inference_parameters(
            parameters, self.info["inference_defaults"]
        )
        threshold = inference["confidence"]
        if self.manifest["format"] == "yolo_raw":
            # The contract is [1, 4 + classes, anchors], even for small outputs.
            if output.ndim != 2 or output.shape[0] < 5:
                raise ValueError("Ожидался ONNX output [1,4+classes,N]")
            if "class_names" in self.manifest and output.shape[0] != 4 + len(
                self.manifest["class_names"]
            ):
                raise ValueError("Число классов ONNX не совпадает с manifest")
            output = output.T
            scores = output[:, 4:]
            classes = scores.argmax(axis=1)
            confidence = scores.max(axis=1)
            boxes = output[:, :4].copy()
            boxes[:, :2] -= boxes[:, 2:] / 2
            boxes[:, 2:] += boxes[:, :2]
        else:
            if output.shape[1] != 6:
                raise ValueError("Ожидался ONNX output [1,N,6]")
            boxes, confidence, classes = (
                output[:, :4],
                output[:, 4],
                output[:, 5].astype(int),
            )
        canonical_classes = np.array(
            [self.manifest["class_map"].get(str(int(cls_id))) for cls_id in classes],
            dtype=object,
        )
        result = []
        for canonical in {value for value in canonical_classes if value is not None}:
            # Suppress overlapping boxes within each application class.
            indices = np.where(
                (canonical_classes == canonical) & (confidence >= threshold)
            )[0]
            b = boxes[indices]
            keep = cv2.dnn.NMSBoxes(
                [
                    [float(a), float(b_), float(c - a), float(d - b_)]
                    for a, b_, c, d in b
                ],
                confidence[indices].tolist(),
                threshold,
                inference["iou"],
            )
            for j in np.asarray(keep).reshape(-1):
                i = indices[j]
                a, b_, c, d = boxes[i]
                bbox = [
                    float(np.clip((a - x) / scale, 0, w) / w),
                    float(np.clip((b_ - y) / scale, 0, h) / h),
                    float(np.clip((c - x) / scale, 0, w) / w),
                    float(np.clip((d - y) / scale, 0, h) / h),
                ]
                if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    continue
                result.append(
                    {
                        "class_id": str(canonical),
                        "confidence": float(confidence[i]),
                        "bbox": bbox,
                    }
                )
        result.sort(key=lambda detection: detection["confidence"], reverse=True)
        return result[: inference["max_detections"]]


def frame_quality(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    light = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return {
        "brightness": round(light, 2),
        "sharpness": round(sharpness, 2),
        "usable": light > 12 and light < 248 and sharpness > 2,
        "limitations": ["Качество зоны требует подтверждения оператором"],
    }

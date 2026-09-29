"""Build a YOLO pseudo-label dataset from the 100 supplied construction photos.

The ONNX file is the project's verified export of the exact best_v2.pt checkpoint.
This script keeps the checkpoint's 20 raw IDs rather than the application's
19-class catalog mapping. Source images are only read, never modified.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "datasets/7.ДГП_датасеты/Строительная_техника"
OUTPUT = ROOT / "datasets/labeled_src"
PT = ROOT / "models/yolo26m/best_v2.pt"
ONNX = ROOT / "models/yolo26m/best.onnx"
MANIFEST = ROOT / "models/yolo26m/manifest.json"
CONFIDENCE = 0.35
IOU = 0.70
MAX_DETECTIONS = 300
VAL_NUMBERS = {15, 16, 47, 48, 60, 61, 62, 63, 95, 96, 97, 98, 99, 100}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_image(path: Path) -> np.ndarray:
    frame = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError(f"Cannot decode {path}")
    return frame


def save_image(path: Path, frame: np.ndarray) -> None:
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError(f"Cannot encode {path}")
    encoded.tofile(str(path))


def predict(
    session: ort.InferenceSession,
    frame: np.ndarray,
    confidence_threshold: float = CONFIDENCE,
) -> list[dict]:
    height, width = frame.shape[:2]
    size = 960
    scale = min(size / width, size / height)
    new_width, new_height = round(width * scale), round(height * scale)
    left, top = (size - new_width) // 2, (size - new_height) // 2
    canvas = np.full((size, size, 3), 114, np.uint8)
    canvas[top : top + new_height, left : left + new_width] = cv2.resize(
        frame, (new_width, new_height)
    )
    tensor = (
        cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        .transpose(2, 0, 1)[None]
        .astype(np.float32)
        / 255
    )
    raw = session.run(None, {session.get_inputs()[0].name: tensor})[0]
    if raw.shape != (1, 24, 18900):
        raise ValueError(f"Unexpected model output: {raw.shape}")
    rows = raw[0].T
    scores = rows[:, 4:]
    classes = scores.argmax(axis=1)
    confidence = scores.max(axis=1)
    candidates = np.where(confidence >= confidence_threshold)[0]
    boxes = rows[candidates, :4].copy()
    boxes[:, :2] -= boxes[:, 2:] / 2
    boxes[:, 2:] += boxes[:, :2]
    detections = []
    # Raw IDs 7 and 14 refer to the same loader family. NMS across both IDs
    # prevents one machine from acquiring two boxes; the winning raw ID remains.
    groups = sorted({7 if classes[index] == 14 else int(classes[index]) for index in candidates})
    for group in groups:
        positions = [
            pos
            for pos, index in enumerate(candidates)
            if (7 if classes[index] == 14 else int(classes[index])) == group
        ]
        grouped = boxes[positions]
        nms_boxes = [
            [float(x1), float(y1), float(x2 - x1), float(y2 - y1)]
            for x1, y1, x2, y2 in grouped
        ]
        keep = cv2.dnn.NMSBoxes(
            nms_boxes,
            confidence[candidates[positions]].tolist(),
            confidence_threshold,
            IOU,
        )
        for kept in np.asarray(keep).reshape(-1):
            index = int(candidates[positions[int(kept)]])
            x1, y1, x2, y2 = boxes[positions[int(kept)]]
            x1 = float(np.clip((x1 - left) / scale, 0, width))
            x2 = float(np.clip((x2 - left) / scale, 0, width))
            y1 = float(np.clip((y1 - top) / scale, 0, height))
            y2 = float(np.clip((y2 - top) / scale, 0, height))
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append(
                {
                    "class_id": int(classes[index]),
                    "confidence": round(float(confidence[index]), 6),
                    "xyxy": [round(value, 3) for value in (x1, y1, x2, y2)],
                }
            )
    detections.sort(key=lambda item: item["confidence"], reverse=True)
    # Class-aware NMS leaves nearly identical cross-class boxes. Such boxes
    # describe one object twice and are invalid training targets. Keep the
    # stronger hypothesis only when the overlap is effectively exact.
    unique = []
    for candidate in detections:
        a = candidate["xyxy"]
        duplicate = False
        for retained in unique:
            b = retained["xyxy"]
            intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
                0, min(a[3], b[3]) - max(a[1], b[1])
            )
            area_a = (a[2] - a[0]) * (a[3] - a[1])
            area_b = (b[2] - b[0]) * (b[3] - b[1])
            overlap = intersection / (area_a + area_b - intersection)
            if overlap >= 0.95:
                duplicate = True
                break
        if not duplicate:
            unique.append(candidate)
    return unique[:MAX_DETECTIONS]


def draw_overlay(frame: np.ndarray, detections: list[dict], names: dict) -> np.ndarray:
    output = frame.copy()
    for detection in detections:
        x1, y1, x2, y2 = map(round, detection["xyxy"])
        class_id = detection["class_id"]
        color = tuple(int(value) for value in cv2.applyColorMap(
            np.array([[(class_id * 37) % 255]], dtype=np.uint8), cv2.COLORMAP_HSV
        )[0, 0])
        cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)
        label = f"{class_id} {names[str(class_id)]} {detection['confidence']:.2f}"
        (text_width, text_height), _ = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.43, 1
        )
        label_top = max(0, y1 - text_height - 6)
        cv2.rectangle(output, (x1, label_top), (x1 + text_width + 4, label_top + text_height + 5), color, -1)
        cv2.putText(output, label, (x1 + 2, label_top + text_height + 1),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.43, (0, 0, 0), 1, cv2.LINE_AA)
    return output


def main() -> None:
    profile = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if sha256(PT) != profile["source_sha256"]:
        raise ValueError("PT SHA256 does not match the verified model manifest")
    if sha256(ONNX) != profile["sha256"]:
        raise ValueError("ONNX SHA256 does not match the verified model manifest")
    names = profile["class_names"]
    if set(names) != {str(number) for number in range(20)}:
        raise ValueError("Expected 20 raw YOLO classes")
    files = sorted(SOURCE.glob("Screenshot_*.png"), key=lambda path: int(path.stem.split("_")[1]))
    if len(files) != 100 or {int(path.stem.split("_")[1]) for path in files} != set(range(1, 101)):
        raise ValueError("Expected precisely Screenshot_1..Screenshot_100")

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(str(ONNX), sess_options=options, providers=["CPUExecutionProvider"])
    review = OUTPUT / "review/overlays"
    review.mkdir(parents=True, exist_ok=True)
    records = []
    counts = Counter()
    for path in files:
        number = int(path.stem.split("_")[1])
        split = "val" if number in VAL_NUMBERS else "train"
        frame = read_image(path)
        height, width = frame.shape[:2]
        detections = predict(session, frame)
        counts.update(item["class_id"] for item in detections)
        image_dir = OUTPUT / "images" / split
        label_dir = OUTPUT / "labels" / split
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, image_dir / path.name)
        label_lines = []
        for detection in detections:
            x1, y1, x2, y2 = detection["xyxy"]
            label_lines.append(
                f"{detection['class_id']} {(x1+x2)/(2*width):.6f} "
                f"{(y1+y2)/(2*height):.6f} {(x2-x1)/width:.6f} {(y2-y1)/height:.6f}"
            )
        (label_dir / f"{path.stem}.txt").write_text("\n".join(label_lines) + ("\n" if label_lines else ""), encoding="utf-8")
        save_image(review / f"{path.stem}.jpg", draw_overlay(frame, detections, names))
        records.append({
            "image": path.name,
            "split": split,
            "width": width,
            "height": height,
            "source_sha256": sha256(path),
            "detections": detections,
        })
        print(f"{number:3d}/100 {split:5s} {len(detections):2d} detections", flush=True)

    yaml_lines = [
        "# Pseudo-labels from best_v2.pt; human review required before accuracy claims.",
        "train: images/train",
        "val: images/val",
        "nc: 20",
        "names:",
    ]
    yaml_lines.extend(f"  {index}: '{names[str(index)]}'" for index in range(20))
    (OUTPUT / "data.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
    summary = {
        "source": str(SOURCE),
        "checkpoint": str(PT),
        "checkpoint_sha256": profile["source_sha256"],
        "inference_weights": str(ONNX),
        "inference_weights_sha256": profile["sha256"],
        "inference": {"image_size": 960, "confidence": CONFIDENCE, "iou": IOU, "max_detections": MAX_DETECTIONS},
        "split": {"train": 100 - len(VAL_NUMBERS), "val": len(VAL_NUMBERS), "method": "manual scene groups"},
        "class_counts": {str(index): counts[index] for index in range(20)},
        "records": records,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"images": len(records), "boxes": sum(counts.values()), "counts": dict(counts)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

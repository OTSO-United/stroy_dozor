"""Export local equipment weights; publish a manifest only after CPU parity passes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_predictions(reference, actual, width, height):
    """Match within each canonical class; tolerate at most one pixel and 1e-4 score."""
    if len(reference) != len(actual):
        raise ValueError(
            f"Detection count differs: PT={len(reference)}, ONNX={len(actual)}"
        )
    remaining = list(actual)
    max_pixel_error = max_score_error = 0.0
    for expected in reference:
        candidates = [d for d in remaining if d["class_id"] == expected["class_id"]]
        if not candidates:
            raise ValueError(f"Missing ONNX class {expected['class_id']}")

        def pixel_error(d):
            return max(
                abs(a - b) * scale
                for a, b, scale in zip(
                    expected["bbox"], d["bbox"], (width, height, width, height)
                )
            )

        match = min(candidates, key=pixel_error)
        box_error = pixel_error(match)
        score_error = abs(expected["confidence"] - match["confidence"])
        if box_error > 1.0 or score_error > 1e-4:
            raise ValueError(
                f"Export parity failed: bbox={box_error}px, confidence={score_error}"
            )
        max_pixel_error = max(max_pixel_error, box_error)
        max_score_error = max(max_score_error, score_error)
        remaining.remove(match)
    return {"max_pixel_error": max_pixel_error, "max_score_error": max_score_error}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, default=ROOT / "models/best.pt")
    parser.add_argument(
        "--profile", type=Path, default=ROOT / "models/yolo26m-profile.json"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "models/yolo26m")
    parser.add_argument(
        "--images",
        type=Path,
        nargs="+",
        required=True,
        help="Local verification images; read only",
    )
    args = parser.parse_args()
    weights = args.weights.resolve(strict=True)
    images = [path.resolve(strict=True) for path in args.images]
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    output = args.output.resolve()
    if any(
        (output / name).exists()
        for name in ("manifest.json", "best.onnx", "verification.json")
    ):
        parser.error(
            "Output already contains a model. Choose a new --output directory."
        )
    output.mkdir(parents=True, exist_ok=True)
    settings = ROOT / "tmp/model-export-config"
    settings.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(settings))
    os.environ["YOLO_AUTOINSTALL"] = "false"

    import cv2
    import numpy as np
    import onnx
    import torch
    import ultralytics
    from ultralytics import YOLO
    from app.vision import OnnxDetector

    torch.set_num_threads(2)
    with tempfile.TemporaryDirectory(prefix="export-", dir=output) as temporary:
        staging = Path(temporary)
        checkpoint = staging / "best.pt"
        shutil.copy2(weights, checkpoint)
        model = YOLO(str(checkpoint))
        names = {str(k): v for k, v in model.names.items()}
        if model.task != "detect" or names != profile["class_names"]:
            raise ValueError(
                "Checkpoint task/classes differ from the reviewed equipment profile"
            )
        if profile["format"] != "yolo_raw":
            raise ValueError("This exporter requires the yolo_raw profile")
        # Ultralytics 8.4.146: None selects the one-to-many head without embedded NMS.
        exported = Path(
            model.export(
                format="onnx",
                imgsz=profile["input_size"],
                batch=1,
                device="cpu",
                dynamic=False,
                simplify=False,
                opset=17,
                nms=None,
            )
        )
        graph = onnx.load(exported)
        onnx.checker.check_model(graph)
        shape = [d.dim_value for d in graph.graph.output[0].type.tensor_type.shape.dim]
        if (
            len(graph.graph.output) != 1
            or len(shape) != 3
            or shape[:2] != [1, 4 + len(names)]
        ):
            raise ValueError(f"Expected raw [1,{4 + len(names)},N], got {shape}")
        manifest = {
            **profile,
            "weights": "best.onnx",
            "sha256": sha256(exported),
            "source_sha256": sha256(weights),
            "export": {
                "ultralytics": ultralytics.__version__,
                "torch": torch.__version__,
                "onnx": onnx.__version__,
                "opset": 17,
                "nms": None,
                "output_shape": shape,
            },
        }
        staged_manifest = staging / "manifest.json"
        staged_manifest.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        adapter = OnnxDetector(staged_manifest, providers=["CPUExecutionProvider"])
        reference_model = YOLO(str(checkpoint))
        measurements = []
        for path in images:
            frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError(f"Cannot decode verification image: {path}")
            height, width = frame.shape[:2]
            reference = reference_model.predict(
                frame,
                imgsz=profile["input_size"],
                rect=False,
                device="cpu",
                nms=None,
                conf=profile["confidence"],
                iou=profile["iou"],
                max_det=profile["max_detections"],
                verbose=False,
                save=False,
            )[0]
            expected = [
                {
                    "class_id": profile["class_map"][str(int(cls))],
                    "confidence": float(conf),
                    "bbox": [
                        float(v / s)
                        for v, s in zip(box, (width, height, width, height))
                    ],
                }
                for box, conf, cls in zip(
                    reference.boxes.xyxy.cpu().numpy(),
                    reference.boxes.conf.cpu().numpy(),
                    reference.boxes.cls.cpu().numpy(),
                )
            ]
            started = time.perf_counter()
            actual = adapter.detect(frame)
            elapsed_ms = (time.perf_counter() - started) * 1000
            errors = verify_predictions(expected, actual, width, height)
            measurements.append(
                {
                    "image": path.name,
                    "sha256": sha256(path),
                    "width": width,
                    "height": height,
                    "detections": actual,
                    "onnx_ms": elapsed_ms,
                    **errors,
                }
            )
            print(
                f"Parity OK: {path.name}, {len(actual)} detections, {elapsed_ms:.1f} ms",
                flush=True,
            )
        if not any(item["detections"] for item in measurements):
            raise ValueError(
                "Parity needs at least one detected object; all verification images were empty"
            )
        report = {
            "status": "passed",
            "model_sha256": manifest["sha256"],
            "source_sha256": manifest["source_sha256"],
            "providers": adapter.info["providers"],
            "scope": "export parity, not accuracy evaluation",
            "images": measurements,
        }
        shutil.copy2(exported, output / "best.onnx")
        (output / "verification.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        # The application only discovers the model after every verification succeeds.
        shutil.copy2(staged_manifest, output / "manifest.json")
    print(f"Model ready: {output / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()

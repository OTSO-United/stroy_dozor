"""Apply visually reviewed corrections to the 100 still-image YOLO labels.

The model manifest and original labels remain available for comparison.
Run after auto_label_construction.py; rerunning is deterministic.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1] / "datasets/labeled_src"
REVIEW = ROOT / "review"
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
NAMES = [
    "Dump truck", "Excavator", "Motor grader", "Roller", "Crane manipulator",
    "Gazelle", "Forklift Standart", "Bucket loader Big", "Mixer", "Tanker",
    "Bulldozer", "Cleaning equipment", "Truck", "Trailer",
    "Bucket loader Standart", "Autocran", "Tower crane", "Asphalt paver",
    "Piling rig", "Concrete pump truck",
]


def format_yolo(boxes: list[dict], width: int, height: int) -> str:
    rows = []
    for box in boxes:
        x1, y1, x2, y2 = box["xyxy"]
        class_id = box["class_id"]
        if not (0 <= class_id < 20 and 0 <= x1 < x2 <= width
                and 0 <= y1 < y2 <= height):
            raise ValueError(f"Invalid box: {box}")
        rows.append(
            f"{class_id} {(x1 + x2) / (2 * width):.6f} "
            f"{(y1 + y2) / (2 * height):.6f} "
            f"{(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}"
        )
    return "\n".join(rows) + ("\n" if rows else "")


def manual(class_id: int, xyxy: tuple[int, int, int, int]) -> dict:
    return {"class_id": class_id, "xyxy": list(xyxy), "origin": "manual"}


def curate(record: dict) -> tuple[list[dict], list[str]]:
    index = int(Path(record["image"]).stem.split("_")[-1])
    boxes = [dict(item, origin="model") for item in record["detections"]]
    reasons = []

    if index == 15:
        for item in boxes:
            if item["class_id"] == 0:
                item["class_id"] = 12
                item["origin"] = "manual"
                item.pop("confidence", None)
            elif item["class_id"] == 16:
                item["xyxy"] = [914, 0, 1218, 355]
                item["origin"] = "manual"
                item.pop("confidence", None)
        reasons.append("Flatbed vehicle 0 -> Truck 12; include visible tower-crane mast")
    elif index == 32:
        boxes = [item for item in boxes if item["class_id"] != 15]
        boxes.append(manual(18, (232, 0, 417, 548)))
        reasons.append("Retag complete visible piling rig, including crawler base")
    elif index == 62:
        for item in boxes:
            if item["class_id"] == 15:
                item["class_id"] = 18
                item["origin"] = "manual"
                item.pop("confidence", None)
            elif item["class_id"] == 7:
                item["class_id"] = 14
                item["origin"] = "manual"
                item.pop("confidence", None)
        reasons.append("Piling rig 15 -> 18; compact JCB backhoe loader 7 -> 14")
    elif index == 67:
        boxes = [item for item in boxes if item["class_id"] != 16]
        boxes.extend([
            manual(16, (450, 0, 603, 650)),
            manual(16, (448, 0, 1000, 638)),
        ])
        boxes.append(manual(15, (0, 380, 1000, 708)))
        reasons.append("Split two tower cranes; add visible mobile crane on left")
    elif index == 79:
        boxes = [item for item in boxes if item["class_id"] != 0]
        for item in boxes:
            if item["class_id"] == 7:
                item["class_id"] = 14
                item["origin"] = "manual"
                item.pop("confidence", None)
        reasons.append("Remove unconfirmed distant dump truck; compact JCB loaders 7 -> 14")
    elif index == 98:
        boxes = [item for item in boxes if item["class_id"] != 1]
        boxes.extend([
            manual(1, (169, 195, 744, 624)),
            manual(1, (622, 196, 1004, 729)),
        ])
        reasons.append("Separate two overlapping excavators")

    return boxes, reasons


def write_overlay(path: Path, image: np.ndarray, boxes: list[dict]) -> None:
    canvas = image.copy()
    for item in boxes:
        x1, y1, x2, y2 = map(lambda value: round(value), item["xyxy"])
        color = (0, 255, 0) if item["origin"] == "manual" else (255, 180, 0)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 3)
        title = f"{item['class_id']} {NAMES[item['class_id']]}"
        cv2.putText(canvas, title, (max(0, x1), max(24, y1 + 22)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, title, (max(0, x1), max(24, y1 + 22)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError(f"Cannot encode {path}")
    encoded.tofile(str(path))


def main() -> None:
    changes = []
    model_counts = Counter()
    curated_counts = Counter()
    for record in MANIFEST["records"]:
        name = record["image"]
        split = record["split"]
        label = ROOT / "labels" / split / f"{Path(name).stem}.txt"
        backup = REVIEW / "pseudo_labels" / split / label.name
        original = format_yolo(record["detections"], record["width"], record["height"])
        if not backup.exists():
            if label.read_text(encoding="utf-8") != original:
                raise ValueError(f"Original label differs from model manifest: {label}")
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(original, encoding="utf-8")
        elif backup.read_text(encoding="utf-8") != original:
            raise ValueError(f"Backup differs from model manifest: {backup}")

        boxes, reasons = curate(record)
        label.write_text(format_yolo(boxes, record["width"], record["height"]),
                         encoding="utf-8")
        model_counts.update(item["class_id"] for item in record["detections"])
        curated_counts.update(item["class_id"] for item in boxes)
        if reasons:
            changes.append({"frame": name, "split": split, "reasons": reasons,
                            "before": record["detections"], "after": boxes})
        image_path = ROOT / "images" / split / name
        image = cv2.imdecode(np.fromfile(str(image_path), np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Cannot decode {image_path}")
        write_overlay(REVIEW / "curated_overlays" / f"{Path(name).stem}.jpg",
                      image, boxes)

    result = {
        "source": "manifest.json model predictions",
        "final_labels": "labels/train and labels/val",
        "original_labels": "review/pseudo_labels/train and review/pseudo_labels/val",
        "changed_frames": len(changes),
        "model_box_count": sum(model_counts.values()),
        "curated_box_count": sum(curated_counts.values()),
        "model_class_counts": {str(i): model_counts[i] for i in range(20)},
        "curated_class_counts": {str(i): curated_counts[i] for i in range(20)},
        "changes": changes,
    }
    (REVIEW / "curation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: result[key] for key in (
        "changed_frames", "model_box_count", "curated_box_count",
        "curated_class_counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

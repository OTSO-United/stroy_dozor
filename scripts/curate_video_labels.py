"""Apply reviewed YOLO corrections to the 210 video frames.

The inference manifest remains immutable. Original model labels are kept in
review/pseudo_labels, while labels/{train,val} becomes the corrected dataset.
Running this script again reconstructs the same labels from the manifest.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1] / "datasets/labeled_video"
REVIEW = ROOT / "review"
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
NAMES = [
    "Dump truck", "Excavator", "Motor grader", "Roller", "Crane manipulator",
    "Gazelle", "Forklift Standart", "Bucket loader Big", "Mixer", "Tanker",
    "Bulldozer", "Cleaning equipment", "Truck", "Trailer",
    "Bucket loader Standart", "Autocran", "Tower crane", "Asphalt paver",
    "Piling rig", "Concrete pump truck",
]

# Boxes were read from full 1920x1080 frames. One clearly visible excavator
# was added to every previously empty camera-2 label file.
MISSING_EXCAVATORS = {
    13: (240, 710, 520, 990),
    **{index: (745, 510, 945, 655) for index in
       (22, 23, 24, 25, 26, 27, 29, 30, 31, 32, 33, 34)},
    37: (1200, 495, 1390, 620),
    41: (1000, 475, 1245, 640),
    47: (925, 725, 1170, 995),
    49: (740, 500, 945, 660),
    69: (1100, 510, 1410, 650),
}

# Central tower crane in camera 3: mast reaches y=0..~805 throughout the
# recording. Its jib rotates, so horizontal limits use frame-specific groups.
CRANE_X = {
    1: (725, 1100), 5: (805, 1210), 14: (765, 1100),
    18: (850, 1010), 23: (745, 1100), 24: (745, 1100),
    25: (765, 1030), 27: (745, 1020), 28: (745, 1020),
    29: (745, 1020), 30: (745, 1020), 31: (745, 1040),
    32: (850, 1020), 33: (700, 1030), 34: (795, 990),
    35: (735, 1070), 36: (755, 1050), 37: (830, 990),
    41: (500, 1020), 42: (490, 1020), 45: (505, 1020),
    46: (505, 1020), 58: (790, 1060), 59: (735, 1070),
    62: (790, 1210), 68: (790, 1210), 69: (755, 1040),
    70: (765, 1050),
}


def format_yolo(boxes: list[dict], width: int, height: int) -> str:
    lines = []
    for item in boxes:
        x1, y1, x2, y2 = item["xyxy"]
        if not (0 <= item["class_id"] < 20 and 0 <= x1 < x2 <= width
                and 0 <= y1 < y2 <= height):
            raise ValueError(f"Invalid curated box: {item}")
        lines.append(
            f"{item['class_id']} {(x1 + x2) / (2 * width):.6f} "
            f"{(y1 + y2) / (2 * height):.6f} "
            f"{(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}"
        )
    return "\n".join(lines) + ("\n" if lines else "")


def manual(class_id: int, box: tuple[int, int, int, int]) -> dict:
    return {"class_id": class_id, "xyxy": list(box), "origin": "manual"}


def curate(record: dict) -> tuple[list[dict], list[str]]:
    folder = record["folder"]
    index = int(record["image"].split("_")[-1].split(".")[0])
    boxes = [dict(item, origin="model") for item in record["detections"]]
    reasons = []

    if folder == "1":
        if index in (51, 52, 53, 54):
            for item in boxes:
                if item["class_id"] == 9 and item["xyxy"][0] > 1200:
                    item["class_id"] = 0
                    item["origin"] = "manual"
                    item.pop("confidence", None)
            reasons.append("Visible dump truck: Tanker 9 -> Dump truck 0")
        if index == 52:
            boxes = [item for item in boxes if not (
                item["class_id"] == 15 and item["xyxy"][0] < 1000
            )]
            reasons.append("Remove floodlight glare predicted as Autocran")

    elif folder == "2":
        if index in (5, 6, 7, 17):
            boxes = [item for item in boxes if not (
                item["class_id"] == 7 and item["xyxy"][0] > 1500
            )]
            reasons.append("Remove static stair/platform false loader")
        if index == 20:
            boxes = [manual(1, (860, 495, 1130, 660))]
            reasons.append("Excavator predicted as Autocran")
        if index == 21:
            boxes = [manual(1, (725, 530, 860, 790))]
            reasons.append("Complete excavator body and arm; loader class was wrong")
        if index == 38:
            boxes = [manual(1, (1000, 450, 1285, 610))]
            reasons.append("Remove duplicate/merged predictions around excavator")
        if index in (55, 57, 59):
            boxes = [item for item in boxes if item["class_id"] != 7]
            reasons.append("Remove loader duplicate of excavator")
        if index in (56, 60):
            boxes = [max(boxes, key=lambda item: item["confidence"])]
            reasons.append("Remove duplicate excavator prediction")
        if index in MISSING_EXCAVATORS:
            if boxes:
                raise ValueError(f"Expected empty camera-2 model labels: {record['image']}")
            boxes.append(manual(1, MISSING_EXCAVATORS[index]))
            reasons.append("Add visible excavator to previously empty label")

    elif folder == "3":
        # The central tower crane is visible in every selected frame; model
        # boxes frequently covered only a mast or jib. Keep separate mixers.
        boxes = [item for item in boxes if item["class_id"] == 8]
        if index == 67 and len(boxes) == 2:
            boxes = [max(boxes, key=lambda item: item["confidence"])]
        left, right = CRANE_X.get(index, (725, 1210))
        boxes.append(manual(16, (left, 0, right, 820)))
        reasons.append("Replace partial/missing crane with one whole central Tower crane")
        if any(item["class_id"] == 15 for item in record["detections"]):
            reasons.append("Remove bridge pylon false Autocran")

    return boxes, reasons


def draw(image: np.ndarray, boxes: list[dict]) -> np.ndarray:
    canvas = image.copy()
    for item in boxes:
        x1, y1, x2, y2 = map(lambda value: int(round(value)), item["xyxy"])
        color = (0, 255, 0) if item["origin"] == "manual" else (255, 180, 0)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 3)
        title = f"{item['class_id']} {NAMES[item['class_id']]} "
        title += "[edited]" if item["origin"] == "manual" else "[model]"
        cv2.putText(canvas, title, (max(0, x1), max(25, y1 + 25)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, title, (max(0, x1), max(25, y1 + 25)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    return canvas


def write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError(f"Cannot encode {path}")
    encoded.tofile(str(path))


def main() -> None:
    changes = []
    old_count = Counter()
    new_count = Counter()
    for record in MANIFEST["records"]:
        name = record["image"]
        split = record["split"]
        label = ROOT / "labels" / split / name.replace(".jpg", ".txt")
        backup = REVIEW / "pseudo_labels" / split / label.name
        original = format_yolo(record["detections"], record["width"], record["height"])
        if not backup.exists():
            if label.read_text(encoding="utf-8") != original:
                raise ValueError(f"Original label differs from inference manifest: {label}")
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(original, encoding="utf-8")
        elif backup.read_text(encoding="utf-8") != original:
            raise ValueError(f"Pseudo-label backup differs from manifest: {backup}")

        boxes, reasons = curate(record)
        label.write_text(format_yolo(boxes, record["width"], record["height"]),
                         encoding="utf-8")
        old_count.update(item["class_id"] for item in record["detections"])
        new_count.update(item["class_id"] for item in boxes)
        if reasons:
            changes.append({"frame": name, "split": split, "reasons": reasons,
                            "before": record["detections"], "after": boxes})
        image_path = ROOT / "images" / split / name
        image = cv2.imdecode(np.fromfile(str(image_path), np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Cannot decode {image_path}")
        write_image(REVIEW / "curated_overlays" / name, draw(image, boxes))

    result = {
        "source": "manifest.json model predictions",
        "final_labels": "labels/train and labels/val",
        "original_labels": "review/pseudo_labels/train and review/pseudo_labels/val",
        "changed_frames": len(changes),
        "model_box_count": sum(old_count.values()),
        "curated_box_count": sum(new_count.values()),
        "model_class_counts": {str(index): old_count[index] for index in range(20)},
        "curated_class_counts": {str(index): new_count[index] for index in range(20)},
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

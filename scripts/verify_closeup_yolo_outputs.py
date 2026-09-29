from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


DATASET_NAMES = [
    "closeup_asphalt_paver_yolo_filtered",
    "closeup_construction_vehicle_detection_yolo_filtered",
    "closeup_construction_monitoring_yolo_filtered",
    "closeup_roller_numeric_yolo_filtered",
    "closeup_choo_yolo_filtered",
]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_label(path: Path) -> tuple[Counter[int], list[str]]:
    counts: Counter[int] = Counter()
    errors: list[str] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            errors.append(f"{path}:{line_no}: blank line")
            continue
        parts = line.split()
        if len(parts) != 5:
            errors.append(f"{path}:{line_no}: expected 5 fields")
            continue
        try:
            class_id = int(parts[0])
            x, y, width, height = (float(value) for value in parts[1:])
        except ValueError:
            errors.append(f"{path}:{line_no}: parse error")
            continue
        if not 0 <= class_id <= 25:
            errors.append(f"{path}:{line_no}: invalid class {class_id}")
            continue
        if not all(0 <= value <= 1 for value in (x, y, width, height)) or width <= 0 or height <= 0:
            errors.append(f"{path}:{line_no}: invalid bbox")
            continue
        counts[class_id] += 1
    if not counts:
        errors.append(f"{path}: no valid objects")
    return counts, errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for name in DATASET_NAMES:
        root = args.datasets_root / name
        images = {
            path.stem: path
            for path in (root / "images").iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        }
        labels = {path.stem: path for path in (root / "labels").glob("*.txt")}
        class_counts: Counter[int] = Counter()
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=32) as executor:
            for counts, parse_errors in executor.map(parse_label, labels.values()):
                class_counts.update(counts)
                errors.extend(parse_errors)
        report = json.loads((root / "conversion_report.json").read_text(encoding="utf-8"))
        expected = {int(row["class_id"]): int(row["object_count"]) for row in report["classes"]}
        split_dirs = [part for part in ("train", "valid", "test") if (root / part).exists()]
        result = {
            "dataset": name,
            "image_count": len(images),
            "label_count": len(labels),
            "object_count": sum(class_counts.values()),
            "class_object_counts": dict(sorted(class_counts.items())),
            "missing_labels": sorted(set(images) - set(labels))[:20],
            "missing_images": sorted(set(labels) - set(images))[:20],
            "split_directories_present": split_dirs,
            "metadata_present": all((root / filename).exists() for filename in ("data.yaml", "class_names.json", "classes.txt", "conversion_report.json")),
            "matches_conversion_report": len(images) == report["retained_images"] and len(labels) == report["retained_images"] and dict(class_counts) == expected,
            "error_count": len(errors),
            "error_examples": errors[:20],
        }
        results.append(result)
        print(f"{name}: images={len(images)} labels={len(labels)} objects={sum(class_counts.values())} errors={len(errors)}")
    payload = {
        "all_valid": all(
            row["image_count"] == row["label_count"]
            and not row["missing_labels"]
            and not row["missing_images"]
            and not row["split_directories_present"]
            and row["metadata_present"]
            and row["matches_conversion_report"]
            and row["error_count"] == 0
            for row in results
        ),
        "datasets": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"all_valid={payload['all_valid']}")


if __name__ == "__main__":
    main()

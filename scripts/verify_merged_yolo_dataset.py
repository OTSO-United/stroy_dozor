from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


SPLITS = ("train", "val", "test")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def read_label(path: Path, class_count: int) -> tuple[set[int], Counter[int], list[str]]:
    image_classes: set[int] = set()
    objects: Counter[int] = Counter()
    errors: list[str] = []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not any(line.strip() for line in lines):
        errors.append(f"empty label: {path}")
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            errors.append(f"{path}:{line_no}: expected 5 fields")
            continue
        try:
            class_id = int(fields[0])
            x, y, width, height = (float(value) for value in fields[1:])
        except ValueError:
            errors.append(f"{path}:{line_no}: non-numeric field")
            continue
        if not 0 <= class_id < class_count:
            errors.append(f"{path}:{line_no}: class {class_id} outside range")
        if width <= 0 or height <= 0 or not all(0 <= value <= 1 for value in (x, y, width, height)):
            errors.append(f"{path}:{line_no}: invalid normalized bbox")
        image_classes.add(class_id)
        objects[class_id] += 1
    return image_classes, objects, errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--project-report", required=True, type=Path)
    args = parser.parse_args()

    class_names = json.loads((args.dataset / "class_names.json").read_text(encoding="utf-8"))
    class_count = len(class_names)
    counts: dict[str, dict] = {}
    validation_errors: list[str] = []
    calculated_images = {split: Counter() for split in SPLITS}
    calculated_objects = {split: Counter() for split in SPLITS}

    for split in SPLITS:
        image_dir = args.dataset / split / "images"
        label_dir = args.dataset / split / "labels"
        image_stems = {
            path.stem for path in image_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        }
        label_stems = {path.stem for path in label_dir.glob("*.txt")}
        missing_labels = sorted(image_stems - label_stems)
        orphan_labels = sorted(label_stems - image_stems)
        validation_errors.extend(f"{split}: missing label for {stem}" for stem in missing_labels)
        validation_errors.extend(f"{split}: orphan label {stem}" for stem in orphan_labels)
        for index, path in enumerate(label_dir.glob("*.txt"), 1):
            ids, objects, errors = read_label(path, class_count)
            validation_errors.extend(errors)
            calculated_images[split].update(ids)
            calculated_objects[split].update(objects)
            if index % 10000 == 0:
                print(f"verified {split}: {index} labels", flush=True)
        counts[split] = {
            "images": len(image_stems),
            "labels": len(label_stems),
            "missing_labels": len(missing_labels),
            "orphan_labels": len(orphan_labels),
        }

    hashes: dict[str, set[str]] = defaultdict(set)
    components: dict[str, set[str]] = defaultdict(set)
    manifest_rows = 0
    manifest_path = args.dataset / "metadata" / "image_manifest.csv"
    with manifest_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            manifest_rows += 1
            hashes[row["sha256"]].add(row["split"])
            components[row["scene_component"]].add(row["split"])
    hash_leakage = {key: sorted(value) for key, value in hashes.items() if len(value) > 1}
    scene_leakage = {key: sorted(value) for key, value in components.items() if len(value) > 1}

    expected_rows = {}
    summary_path = args.dataset / "metadata" / "class_summary.csv"
    with summary_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            expected_rows[int(row["class_id"])] = row
    class_mismatches = []
    class_summary = []
    for class_id in range(class_count):
        row = {"class_id": class_id, "class_name": class_names[str(class_id)]}
        for split in SPLITS:
            row[f"{split}_images"] = calculated_images[split][class_id]
            row[f"{split}_objects"] = calculated_objects[split][class_id]
            expected = expected_rows[class_id]
            for metric in ("images", "objects"):
                key = f"{split}_{metric}"
                if int(expected[key]) != row[key]:
                    class_mismatches.append({
                        "class_id": class_id,
                        "field": key,
                        "expected": int(expected[key]),
                        "actual": row[key],
                    })
        row["total_images"] = sum(row[f"{split}_images"] for split in SPLITS)
        row["total_objects"] = sum(row[f"{split}_objects"] for split in SPLITS)
        class_summary.append(row)

    total_images = sum(item["images"] for item in counts.values())
    result = {
        "status": "ok" if not validation_errors and not hash_leakage and not scene_leakage and not class_mismatches and manifest_rows == total_images else "failed",
        "dataset": str(args.dataset),
        "split_counts": counts,
        "total_images": total_images,
        "manifest_rows": manifest_rows,
        "hash_leakage_count": len(hash_leakage),
        "scene_group_leakage_count": len(scene_leakage),
        "class_summary_mismatch_count": len(class_mismatches),
        "label_validation_error_count": len(validation_errors),
        "validation_error_examples": validation_errors[:100],
        "hash_leakage_examples": dict(list(hash_leakage.items())[:20]),
        "scene_leakage_examples": dict(list(scene_leakage.items())[:20]),
        "class_summary_mismatches": class_mismatches[:100],
        "class_summary": class_summary,
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(payload, encoding="utf-8")
    args.project_report.parent.mkdir(parents=True, exist_ok=True)
    args.project_report.write_text(payload, encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "class_summary"}, ensure_ascii=False, indent=2), flush=True)
    if result["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

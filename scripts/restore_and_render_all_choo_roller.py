from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sample_train_class_bboxes as renderer  # noqa: E402
from process_closeup_yolo_datasets import (  # noqa: E402
    CANONICAL_CLASSES,
    enumerate_records,
    retained_boxes,
    safe_stem,
    select_records,
    write_metadata,
)


OUTPUT_NAME = "closeup_choo_yolo_filtered"
RAW_CLASSES = ["compactor", "dump_truck", "excavator", "forklift", "mixer_truck", "mobile_crane"]
MAPPING = {0: 3, 3: 6}


def read_yolo(path: Path) -> list[tuple[int, float, float, float, float]]:
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        parts = line.split()
        rows.append((int(parts[0]), *(float(value) for value in parts[1:])))
    return rows


def reset_directory(path: Path, expected_parent_name: str) -> None:
    if path.parent.name != expected_parent_name:
        raise ValueError(f"Refusing to reset unexpected path: {path}")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def rebuild_dataset(source: Path, output: Path) -> dict:
    staging = output.parent / f"{output.name}.__restore_tmp"
    reset_directory(staging, "datasets")
    (staging / "images").mkdir()
    (staging / "labels").mkdir()

    records, errors = enumerate_records(source, len(RAW_CLASSES))
    if errors:
        raise RuntimeError(f"Source label errors: {len(errors)}; examples: {errors[:5]}")
    config = {"mapping": MAPPING, "group_mode": "header", "max_versions": 2}
    selected, augmentation_stats = select_records(records, config)

    raw_objects: Counter[int] = Counter()
    for record in records:
        raw_objects.update(box[0] for box in record["boxes"])
    class_images: Counter[int] = Counter()
    class_objects: Counter[int] = Counter()
    group_versions: Counter[str] = Counter()
    materialization: Counter[str] = Counter()
    omitted_no_target = 0
    retained_images = 0

    for record in sorted(selected, key=lambda row: (row["header"], row["image"].name)):
        boxes = retained_boxes(record, MAPPING)
        if not boxes:
            omitted_no_target += 1
            continue
        group_value = record["header"]
        group_versions[group_value] += 1
        version = group_versions[group_value]
        dest_stem = safe_stem(f"closeup_choo__{group_value}__v{version}")
        destination_image = staging / "images" / f"{dest_stem}{record['image'].suffix.lower()}"
        destination_label = staging / "labels" / f"{dest_stem}.txt"
        if destination_image.exists() or destination_label.exists():
            digest = hashlib.sha1(record["image"].name.encode("utf-8")).hexdigest()[:10]
            dest_stem = f"{dest_stem}__{digest}"
            destination_image = staging / "images" / f"{dest_stem}{record['image'].suffix.lower()}"
            destination_label = staging / "labels" / f"{dest_stem}.txt"
        try:
            os.link(record["image"], destination_image)
            materialization["hardlink"] += 1
        except OSError:
            shutil.copy2(record["image"], destination_image)
            materialization["copy"] += 1
        destination_label.write_text(
            "\n".join(
                f"{class_id} {x:.6f} {y:.6f} {width:.6f} {height:.6f}"
                for class_id, x, y, width, height in boxes
            ) + "\n",
            encoding="utf-8",
        )
        class_objects.update(class_id for class_id, *_ in boxes)
        class_images.update(set(class_id for class_id, *_ in boxes))
        retained_images += 1

    write_metadata(staging, source.name)
    report = {
        "dataset": OUTPUT_NAME,
        "source": str(source),
        "output": str(output),
        "source_preserved": True,
        "source_images": len(records),
        "source_objects": sum(raw_objects.values()),
        "augmentation": augmentation_stats,
        "selected_before_target_filter": len(selected),
        "omitted_no_target": omitted_no_target,
        "omitted_exact_cross_dataset_duplicate": 0,
        "retained_images": retained_images,
        "retained_objects": sum(class_objects.values()),
        "annotation_types": {"bbox": retained_images},
        "materialization": dict(materialization),
        "mapping_notes": {
            "0": "compactor -> Roller; restored for complete user review without model-side filtering",
            "1": "dump_truck rejected after 100-image QA",
            "2": "excavator rejected after 100-image QA",
            "3": "forklift -> Forklift Standart; retained after QA",
            "4": "mixer_truck rejected after 100-image QA",
            "5": "mobile_crane rejected after 100-image QA",
        },
        "excluded_semantic_risks": {},
        "raw_classes": [
            {
                "raw_id": raw_id,
                "raw_name": raw_name,
                "object_count": raw_objects[raw_id],
                "retained": raw_id in MAPPING,
                "mapped_id": MAPPING.get(raw_id),
            }
            for raw_id, raw_name in enumerate(RAW_CLASSES)
        ],
        "classes": [
            {
                "class_id": class_id,
                "class_name": CANONICAL_CLASSES[class_id],
                "image_count": class_images[class_id],
                "object_count": class_objects[class_id],
            }
            for class_id in sorted(class_objects)
        ],
        "quality_control": {
            "status": "pending_user_review",
            "roller_policy": "all selected source Roller annotations restored; no Roller frames removed automatically",
        },
    }
    (staging / "conversion_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    backup = output.parent / f"{output.name}.__before_restore"
    if backup.exists():
        shutil.rmtree(backup)
    if output.exists():
        output.rename(backup)
    staging.rename(output)
    if backup.exists():
        shutil.rmtree(backup)
    return report


def render_all_roller(dataset: Path, review_root: Path) -> dict:
    renderer.CLASS_NAMES = CANONICAL_CLASSES
    renderer.PALETTE = (renderer.PALETTE * 2)[: len(CANONICAL_CLASSES)]
    reset_directory(review_root, "closeup_choo_yolo_filtered")
    annotated_dir = review_root / "annotated"
    pages_dir = review_root / "contact_pages_20"
    annotated_dir.mkdir()
    pages_dir.mkdir()

    images_by_stem = {
        path.stem: path
        for path in (dataset / "images").iterdir()
        if path.is_file()
    }
    rows = []
    annotated_paths = []
    for label in sorted((dataset / "labels").glob("*.txt")):
        boxes = read_yolo(label)
        if not any(class_id == 3 for class_id, *_ in boxes):
            continue
        source_image = images_by_stem[label.stem]
        output_image = annotated_dir / source_image.name
        width, height, target_count, all_count = renderer.render_annotated(
            source_image, boxes, 3, output_image, 1600
        )
        annotated_paths.append(output_image)
        rows.append({
            "image_name": source_image.name,
            "dataset_image": str(source_image),
            "dataset_label": str(label),
            "annotated_preview": str(output_image),
            "source_width": width,
            "source_height": height,
            "roller_bbox_count": target_count,
            "all_bbox_count": all_count,
        })

    with (review_root / "review_manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for offset in range(0, len(annotated_paths), 20):
        renderer.make_contact_sheet(
            annotated_paths[offset : offset + 20],
            3,
            pages_dir / f"page_{offset // 20 + 1:03d}.jpg",
        )
    summary = {
        "class_id": 3,
        "class_name": "Roller",
        "annotated_images": len(rows),
        "contact_pages": (len(rows) + 19) // 20,
        "review_instruction": "Delete unwanted files from annotated/. Their exact names match dataset images; synchronize after review.",
    }
    (review_root / "review_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--review-root", required=True, type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    args = parser.parse_args()
    report = rebuild_dataset(args.source, args.dataset)
    review = render_all_roller(args.dataset, args.review_root)
    payload = {"dataset": report, "review": review}
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "retained_images": report["retained_images"],
        "retained_objects": report["retained_objects"],
        **review,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

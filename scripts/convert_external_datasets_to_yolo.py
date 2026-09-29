from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


CANONICAL_CLASSES = [
    "Dump truck",
    "Excavator",
    "Motor grader",
    "Roller",
    "Crane manipulator",
    "Gazelle",
    "Forklift Standart",
    "Bucket loader Big",
    "Mixer",
    "Tanker",
    "Bulldozer",
    "Cleaning equipment",
    "Truck",
    "Trailer",
    "Forklift Giraffe",
    "Bucket loader Standart",
    "Autocran",
    "Tower crane",
    "Asphalt paver",
    "Piling rig",
    "Concrete pump truck",
    "Aerial work platform",
    "Tunneling machine",
    "Road marking machine",
    "Tractor / mini-tractor",
    "Railway machine / track-laying crane",
]

MENDELEY_MAPPING = {
    "unknown": None,
    "excavator": 1,
    "dumptruck": 0,
    "loader": 7,
    "bull_dozer": 10,
    "roller": 3,
    "crawler_drill": 19,
    "pile_driver": 19,
    "pumpcar": 20,
    "mixer_truck": 8,
    "fork_lift_truck": 6,
    "crane": 16,
    "car": None,
}

MOCS_MAPPING = {
    "Worker": None,
    "Static crane": 17,
    "Hanging head": None,
    "Crane": 16,
    "Roller": 3,
    "Bulldozer": 10,
    "Excavator": 1,
    "Truck": 12,
    "Loader": 7,
    "Pump truck": 20,
    "Concrete mixer": 8,
    "Pile driving": 19,
    "Other vehicle": None,
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def guarded_reset_directory(path: Path) -> None:
    expected_suffixes = {
        "Bbox_dataset_classified_yolo_filtered",
        "instances_val_yolo_filtered",
        "Bbox_dataset_classified_yolo_filtered_v2",
        "instances_val_yolo_filtered_v2",
    }
    if path.name not in expected_suffixes:
        raise ValueError(f"Refusing to reset unexpected output directory: {path}")
    if path.exists():
        shutil.rmtree(path)
    (path / "images").mkdir(parents=True)
    (path / "labels").mkdir(parents=True)


def read_raw_counts(raw_audit: dict, dataset: str) -> dict[str, int]:
    return {
        row["raw_class"]: int(row["image_count"])
        for row in raw_audit[dataset]["classes"]
    }


def materialize_image(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def normalized_bbox(
    bbox: list[float] | tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[float, float, float, float] | None:
    x, y, box_width, box_height = (float(value) for value in bbox)
    x1 = max(0.0, min(float(width), x))
    y1 = max(0.0, min(float(height), y))
    x2 = max(0.0, min(float(width), x + box_width))
    y2 = max(0.0, min(float(height), y + box_height))
    if x2 <= x1 or y2 <= y1 or width <= 0 or height <= 0:
        return None
    return (
        ((x1 + x2) / 2) / width,
        ((y1 + y2) / 2) / height,
        (x2 - x1) / width,
        (y2 - y1) / height,
    )


def write_dataset_metadata(output: Path, dataset_name: str) -> None:
    names_json = {str(index): name for index, name in enumerate(CANONICAL_CLASSES)}
    (output / "class_names.json").write_text(
        json.dumps(names_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    yaml_lines = [
        f"path: {output.as_posix()}",
        "train: images",
        "val: images",
        f"nc: {len(CANONICAL_CLASSES)}",
        "names:",
    ]
    yaml_lines.extend(f"  {index}: {json.dumps(name)}" for index, name in enumerate(CANONICAL_CLASSES))
    yaml_lines.extend(
        [
            "",
            f"# Source: {dataset_name}",
            "# This is a filtered supplemental set. Create a grouped train/valid split before model evaluation.",
        ]
    )
    (output / "data.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")


def export_mendeley(
    source: Path,
    output: Path,
    raw_info: dict,
    raw_counts: dict[str, int],
    threshold: int,
) -> dict:
    guarded_reset_directory(output)
    annotation_files = sorted(source.glob("*/*/Annotations/*.json"))
    retained_raw_classes = {
        name
        for name, class_id in MENDELEY_MAPPING.items()
        if class_id is not None and raw_counts.get(name, 0) >= threshold
    }
    manifest = []
    class_images: dict[int, set[str]] = defaultdict(set)
    class_objects: Counter[int] = Counter()
    raw_objects_dropped: Counter[str] = Counter()
    invalid_boxes = 0
    missing_images = 0
    link_modes: Counter[str] = Counter()
    source_image_keys: set[str] = set()
    def convert_one(annotation_path: Path) -> dict:
        data = json.loads(annotation_path.read_text(encoding="utf-8-sig"))
        categories = {int(row["id"]): str(row["name"]) for row in data.get("categories", [])}
        image_row = data["images"][0]
        image_path = annotation_path.parent.parent / "JPEGImages" / image_row["file_name"]
        if not image_path.exists():
            return {"source_image": str(image_path), "missing": 1}
        width = int(image_row["width"])
        height = int(image_row["height"])
        yolo_rows = []
        classes_in_image: set[int] = set()
        local_dropped: Counter[str] = Counter()
        local_invalid = 0
        for annotation in data.get("annotations", []):
            raw_name = categories.get(int(annotation["category_id"]), f"unknown_id_{annotation['category_id']}")
            class_id = MENDELEY_MAPPING.get(raw_name)
            if raw_name not in retained_raw_classes or class_id is None:
                local_dropped[raw_name] += 1
                continue
            normalized = normalized_bbox(annotation["bbox"], width, height)
            if normalized is None:
                local_invalid += 1
                continue
            yolo_rows.append((class_id, *normalized))
            classes_in_image.add(class_id)
        if not yolo_rows:
            return {
                "source_image": str(image_path),
                "missing": 0,
                "invalid": local_invalid,
                "dropped": local_dropped,
            }

        relative = annotation_path.relative_to(source)
        date_part, camera_part = relative.parts[0], relative.parts[1]
        base_name = f"{date_part}__{camera_part}__{image_path.stem}"
        destination_image = output / "images" / f"{base_name}{image_path.suffix.lower()}"
        destination_label = output / "labels" / f"{base_name}.txt"
        link_mode = materialize_image(image_path, destination_image)
        destination_label.write_text(
            "".join(
                f"{class_id} {x:.6f} {y:.6f} {box_width:.6f} {box_height:.6f}\n"
                for class_id, x, y, box_width, box_height in yolo_rows
            ),
            encoding="utf-8",
        )
        return {
            "source_image": str(image_path),
            "missing": 0,
            "invalid": local_invalid,
            "dropped": local_dropped,
            "class_ids": classes_in_image,
            "object_class_ids": [row[0] for row in yolo_rows],
            "link_mode": link_mode,
            "manifest": {
                "dataset": "mendeley",
                "output_image": destination_image.name,
                "output_label": destination_label.name,
                "source_image": str(image_path),
                "source_annotation": str(annotation_path),
                "width": width,
                "height": height,
                "object_count": len(yolo_rows),
                "class_ids": ",".join(str(value) for value in sorted(classes_in_image)),
                "materialization": link_mode,
            },
        }

    with ThreadPoolExecutor(max_workers=8) as executor:
        for result in executor.map(convert_one, annotation_files, chunksize=32):
            source_image_keys.add(result["source_image"])
            missing_images += int(result.get("missing", 0))
            invalid_boxes += int(result.get("invalid", 0))
            raw_objects_dropped.update(result.get("dropped", {}))
            if "manifest" not in result:
                continue
            row = result["manifest"]
            manifest.append(row)
            link_modes[result["link_mode"]] += 1
            class_objects.update(result["object_class_ids"])
            for class_id in result["class_ids"]:
                class_images[class_id].add(row["output_image"])

    write_dataset_metadata(output, "Mendeley Bbox_dataset_classified")
    write_manifest(output / "manifest.csv", manifest)
    stats = build_export_stats(
        "mendeley",
        int(raw_info["image_count"]),
        manifest,
        class_images,
        class_objects,
        raw_counts,
        MENDELEY_MAPPING,
        threshold,
    )
    stats.update(
        {
            "source_annotation_files": len(annotation_files),
            "source_images_without_json": int(raw_info["images_without_annotation_file"]),
            "missing_images": missing_images,
            "invalid_boxes": invalid_boxes,
            "dropped_objects_by_raw_class": dict(sorted(raw_objects_dropped.items())),
            "materialization": dict(link_modes),
        }
    )
    (output / "conversion_report.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return stats


def export_mocs(
    source: Path,
    output: Path,
    raw_counts: dict[str, int],
    threshold: int,
) -> dict:
    guarded_reset_directory(output)
    data = json.loads((source / "instances_val.json").read_text(encoding="utf-8"))
    categories = {int(row["id"]): str(row["name"]) for row in data["categories"]}
    annotations_by_image: dict[int, list[dict]] = defaultdict(list)
    for annotation in data["annotations"]:
        annotations_by_image[int(annotation["image_id"])].append(annotation)
    retained_raw_classes = {
        name
        for name, class_id in MOCS_MAPPING.items()
        if class_id is not None and raw_counts.get(name, 0) >= threshold
    }
    manifest = []
    class_images: dict[int, set[str]] = defaultdict(set)
    class_objects: Counter[int] = Counter()
    raw_objects_dropped: Counter[str] = Counter()
    invalid_boxes = 0
    missing_images = 0
    link_modes: Counter[str] = Counter()
    image_dir = source / "instances_val"

    for image_row in data["images"]:
        image_id = int(image_row["id"])
        image_path = image_dir / image_row["file_name"]
        if not image_path.exists():
            missing_images += 1
            continue
        width = int(image_row["width"])
        height = int(image_row["height"])
        yolo_rows = []
        classes_in_image: set[int] = set()
        for annotation in annotations_by_image.get(image_id, []):
            raw_name = categories.get(int(annotation["category_id"]), f"unknown_id_{annotation['category_id']}")
            class_id = MOCS_MAPPING.get(raw_name)
            if raw_name not in retained_raw_classes or class_id is None:
                raw_objects_dropped[raw_name] += 1
                continue
            normalized = normalized_bbox(annotation["bbox"], width, height)
            if normalized is None:
                invalid_boxes += 1
                continue
            yolo_rows.append((class_id, *normalized))
            classes_in_image.add(class_id)
            class_objects[class_id] += 1
        if not yolo_rows:
            continue

        base_name = f"mocs__{image_path.stem}"
        destination_image = output / "images" / f"{base_name}{image_path.suffix.lower()}"
        destination_label = output / "labels" / f"{base_name}.txt"
        link_mode = materialize_image(image_path, destination_image)
        link_modes[link_mode] += 1
        destination_label.write_text(
            "".join(
                f"{class_id} {x:.6f} {y:.6f} {box_width:.6f} {box_height:.6f}\n"
                for class_id, x, y, box_width, box_height in yolo_rows
            ),
            encoding="utf-8",
        )
        for class_id in classes_in_image:
            class_images[class_id].add(destination_image.name)
        manifest.append(
            {
                "dataset": "mocs",
                "output_image": destination_image.name,
                "output_label": destination_label.name,
                "source_image": str(image_path),
                "source_annotation": str(source / "instances_val.json"),
                "width": width,
                "height": height,
                "object_count": len(yolo_rows),
                "class_ids": ",".join(str(value) for value in sorted(classes_in_image)),
                "materialization": link_mode,
            }
        )

    write_dataset_metadata(output, "MOCS instances_val")
    write_manifest(output / "manifest.csv", manifest)
    stats = build_export_stats(
        "mocs",
        len(data["images"]),
        manifest,
        class_images,
        class_objects,
        raw_counts,
        MOCS_MAPPING,
        threshold,
    )
    stats.update(
        {
            "source_annotations": len(data["annotations"]),
            "missing_images": missing_images,
            "invalid_boxes": invalid_boxes,
            "dropped_objects_by_raw_class": dict(sorted(raw_objects_dropped.items())),
            "materialization": dict(link_modes),
        }
    )
    (output / "conversion_report.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return stats


def write_manifest(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build_export_stats(
    dataset: str,
    raw_image_count: int,
    manifest: list[dict],
    class_images: dict[int, set[str]],
    class_objects: Counter[int],
    raw_counts: dict[str, int],
    mapping: dict[str, int | None],
    threshold: int,
) -> dict:
    raw_sources_by_class: dict[int, list[str]] = defaultdict(list)
    for raw_name, class_id in mapping.items():
        if class_id is not None and raw_counts.get(raw_name, 0) >= threshold:
            raw_sources_by_class[class_id].append(raw_name)
    return {
        "dataset": dataset,
        "raw_preserved": True,
        "rare_class_threshold_images": threshold,
        "raw_image_count": raw_image_count,
        "retained_image_count": len(manifest),
        "omitted_image_count": raw_image_count - len(manifest),
        "retained_object_count": sum(class_objects.values()),
        "retained_class_count": len(class_images),
        "classes": [
            {
                "class_id": class_id,
                "class_name": CANONICAL_CLASSES[class_id],
                "image_count": len(class_images[class_id]),
                "object_count": class_objects[class_id],
                "raw_classes": sorted(raw_sources_by_class[class_id]),
            }
            for class_id in sorted(class_images)
        ],
        "raw_class_decisions": [
            {
                "raw_class": raw_name,
                "raw_image_count": raw_counts.get(raw_name, 0),
                "mapped_class_id": class_id,
                "mapped_class_name": CANONICAL_CLASSES[class_id] if class_id is not None else None,
                "retained": class_id is not None and raw_counts.get(raw_name, 0) >= threshold,
                "reason": (
                    "retained"
                    if class_id is not None and raw_counts.get(raw_name, 0) >= threshold
                    else "below_50_images"
                    if class_id is not None
                    else "not_target_equipment"
                ),
            }
            for raw_name, class_id in mapping.items()
        ],
    }


def verify_export(output: Path) -> dict:
    image_files = {
        path.stem: path
        for path in (output / "images").iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    }
    label_files = {path.stem: path for path in (output / "labels").glob("*.txt")}
    errors = []
    object_count = 0
    class_ids: Counter[int] = Counter()
    for stem, label_path in label_files.items():
        rows = [line for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not rows:
            errors.append(f"empty label: {label_path}")
        for line_no, line in enumerate(rows, 1):
            parts = line.split()
            if len(parts) != 5:
                errors.append(f"{label_path}:{line_no}: expected 5 fields")
                continue
            try:
                class_id = int(parts[0])
                coordinates = [float(value) for value in parts[1:]]
            except ValueError:
                errors.append(f"{label_path}:{line_no}: parse error")
                continue
            if not 0 <= class_id < len(CANONICAL_CLASSES):
                errors.append(f"{label_path}:{line_no}: invalid class {class_id}")
            if not all(0.0 <= value <= 1.0 for value in coordinates):
                errors.append(f"{label_path}:{line_no}: coordinate outside [0,1]")
            if coordinates[2] <= 0 or coordinates[3] <= 0:
                errors.append(f"{label_path}:{line_no}: non-positive size")
            object_count += 1
            class_ids[class_id] += 1
    missing_labels = sorted(set(image_files) - set(label_files))
    missing_images = sorted(set(label_files) - set(image_files))
    return {
        "image_count": len(image_files),
        "label_count": len(label_files),
        "object_count": object_count,
        "class_object_counts": dict(sorted(class_ids.items())),
        "missing_label_count": len(missing_labels),
        "missing_image_count": len(missing_images),
        "error_count": len(errors),
        "error_examples": errors[:50],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mendeley-source", required=True, type=Path)
    parser.add_argument("--mocs-source", required=True, type=Path)
    parser.add_argument("--mendeley-output", required=True, type=Path)
    parser.add_argument("--mocs-output", required=True, type=Path)
    parser.add_argument("--raw-audit", required=True, type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--threshold", type=int, default=50)
    args = parser.parse_args()

    raw_audit = json.loads(args.raw_audit.read_text(encoding="utf-8"))
    mendeley = export_mendeley(
        args.mendeley_source,
        args.mendeley_output,
        raw_audit["mendeley"],
        read_raw_counts(raw_audit, "mendeley"),
        args.threshold,
    )
    mocs = export_mocs(
        args.mocs_source,
        args.mocs_output,
        read_raw_counts(raw_audit, "mocs"),
        args.threshold,
    )
    verification = {
        "mendeley": verify_export(args.mendeley_output),
        "mocs": verify_export(args.mocs_output),
    }
    result = {
        "mendeley": mendeley,
        "mocs": mocs,
        "verification": verification,
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

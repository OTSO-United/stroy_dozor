from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
from collections import Counter, defaultdict
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

RAW_CLASSES = [
    "Worker",
    "Static crane",
    "Hanging head",
    "Crane",
    "Roller",
    "Bulldozer",
    "Excavator",
    "Truck",
    "Loader",
    "Pump truck",
    "Concrete mixer",
    "Pile driving",
    "Other vehicle",
]

RAW_TO_CANONICAL = {
    0: None,
    1: 17,
    2: None,
    3: 16,
    4: 3,
    5: 10,
    6: 1,
    7: 12,
    8: 7,
    9: 20,
    10: 8,
    11: 19,
    12: None,
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
EXPECTED_OUTPUT_NAME = "mocs_instances_train_yolo_filtered"


def parse_label(path: Path) -> tuple[list[tuple[int, float, float, float, float]], list[str]]:
    rows: list[tuple[int, float, float, float, float]] = []
    errors: list[str] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
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
        if not 0 <= class_id < len(RAW_CLASSES):
            errors.append(f"{path}:{line_no}: invalid class {class_id}")
            continue
        if not all(0.0 <= value <= 1.0 for value in (x, y, width, height)):
            errors.append(f"{path}:{line_no}: coordinate outside [0,1]")
            continue
        if width <= 0 or height <= 0:
            errors.append(f"{path}:{line_no}: non-positive size")
            continue
        rows.append((class_id, x, y, width, height))
    return rows, errors


def materialize_image(source: Path, destination: Path) -> str:
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def reset_output(path: Path) -> None:
    if path.name != EXPECTED_OUTPUT_NAME:
        raise ValueError(f"Refusing to reset unexpected output directory: {path}")
    if path.exists():
        shutil.rmtree(path)
    (path / "images").mkdir(parents=True)
    (path / "labels").mkdir(parents=True)


def write_metadata(output: Path) -> None:
    names = {str(index): name for index, name in enumerate(CANONICAL_CLASSES)}
    (output / "class_names.json").write_text(
        json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "classes.txt").write_text("\n".join(CANONICAL_CLASSES) + "\n", encoding="utf-8")
    yaml_lines = [
        f"path: {output.as_posix()}",
        "train: images",
        "val:",
        f"nc: {len(CANONICAL_CLASSES)}",
        "names:",
    ]
    yaml_lines.extend(
        f"  {index}: {json.dumps(name)}" for index, name in enumerate(CANONICAL_CLASSES)
    )
    yaml_lines.extend(
        [
            "",
            "# Source: MOCS yolo_train",
            "# Filtered supplemental train set; keep validation independent.",
        ]
    )
    (output / "data.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")


def verify_export(output: Path) -> dict:
    images = {
        path.stem: path
        for path in (output / "images").iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    }
    labels = {path.stem: path for path in (output / "labels").glob("*.txt")}
    errors: list[str] = []
    class_objects: Counter[int] = Counter()
    class_images: Counter[int] = Counter()
    for stem, label_path in labels.items():
        image_classes: set[int] = set()
        lines = [line for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            errors.append(f"empty label: {label_path}")
        for line_no, line in enumerate(lines, 1):
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
            class_objects[class_id] += 1
            image_classes.add(class_id)
        class_images.update(image_classes)
    return {
        "image_count": len(images),
        "label_count": len(labels),
        "object_count": sum(class_objects.values()),
        "class_image_counts": dict(sorted(class_images.items())),
        "class_object_counts": dict(sorted(class_objects.items())),
        "missing_label_count": len(set(images) - set(labels)),
        "missing_image_count": len(set(labels) - set(images)),
        "error_count": len(errors),
        "error_examples": errors[:50],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--threshold", type=int, default=50)
    args = parser.parse_args()

    image_dir = args.source / "images"
    label_dir = args.source / "labels"
    image_files = {
        path.stem: path
        for path in image_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    }
    label_files = {path.stem: path for path in label_dir.glob("*.txt")}
    paired_stems = sorted(set(image_files) & set(label_files))

    parsed: dict[str, list[tuple[int, float, float, float, float]]] = {}
    parse_errors: list[str] = []
    raw_image_sets: dict[int, set[str]] = defaultdict(set)
    raw_objects: Counter[int] = Counter()
    for stem in paired_stems:
        rows, errors = parse_label(label_files[stem])
        parsed[stem] = rows
        parse_errors.extend(errors)
        raw_objects.update(row[0] for row in rows)
        for class_id in {row[0] for row in rows}:
            raw_image_sets[class_id].add(stem)

    retained_raw_ids = {
        raw_id
        for raw_id, canonical_id in RAW_TO_CANONICAL.items()
        if canonical_id is not None and len(raw_image_sets[raw_id]) >= args.threshold
    }

    reset_output(args.output)
    manifest: list[dict] = []
    class_images: dict[int, set[str]] = defaultdict(set)
    class_objects: Counter[int] = Counter()
    dropped_objects: Counter[int] = Counter()
    materialization: Counter[str] = Counter()

    for stem in paired_stems:
        output_rows: list[tuple[int, float, float, float, float]] = []
        for raw_id, x, y, width, height in parsed[stem]:
            canonical_id = RAW_TO_CANONICAL[raw_id]
            if raw_id not in retained_raw_ids or canonical_id is None:
                dropped_objects[raw_id] += 1
                continue
            output_rows.append((canonical_id, x, y, width, height))
        if not output_rows:
            continue

        source_image = image_files[stem]
        source_label = label_files[stem]
        output_stem = f"mocs_train__{stem}"
        output_image = args.output / "images" / f"{output_stem}{source_image.suffix.lower()}"
        output_label = args.output / "labels" / f"{output_stem}.txt"
        mode = materialize_image(source_image, output_image)
        materialization[mode] += 1
        output_label.write_text(
            "".join(
                f"{class_id} {x:.6f} {y:.6f} {width:.6f} {height:.6f}\n"
                for class_id, x, y, width, height in output_rows
            ),
            encoding="utf-8",
        )
        output_classes = {row[0] for row in output_rows}
        class_objects.update(row[0] for row in output_rows)
        for class_id in output_classes:
            class_images[class_id].add(output_image.name)
        manifest.append(
            {
                "dataset": "mocs_train",
                "output_image": output_image.name,
                "output_label": output_label.name,
                "source_image": str(source_image),
                "source_annotation": str(source_label),
                "object_count": len(output_rows),
                "class_ids": ",".join(str(value) for value in sorted(output_classes)),
                "materialization": mode,
            }
        )

    write_metadata(args.output)
    with (args.output / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)

    report = {
        "dataset": "mocs_train",
        "raw_preserved": True,
        "source": str(args.source),
        "rare_class_threshold_images": args.threshold,
        "raw_image_count": len(image_files),
        "raw_label_count": len(label_files),
        "paired_source_count": len(paired_stems),
        "source_missing_label_count": len(set(image_files) - set(label_files)),
        "source_missing_image_count": len(set(label_files) - set(image_files)),
        "source_parse_error_count": len(parse_errors),
        "source_parse_error_examples": parse_errors[:50],
        "retained_image_count": len(manifest),
        "omitted_image_count": len(image_files) - len(manifest),
        "retained_object_count": sum(class_objects.values()),
        "retained_class_count": len(class_images),
        "classes": [
            {
                "class_id": class_id,
                "class_name": CANONICAL_CLASSES[class_id],
                "image_count": len(class_images[class_id]),
                "object_count": class_objects[class_id],
                "raw_classes": [
                    RAW_CLASSES[raw_id]
                    for raw_id, canonical_id in RAW_TO_CANONICAL.items()
                    if canonical_id == class_id and raw_id in retained_raw_ids
                ],
            }
            for class_id in sorted(class_images)
        ],
        "raw_class_decisions": [
            {
                "raw_class_id": raw_id,
                "raw_class": RAW_CLASSES[raw_id],
                "raw_image_count": len(raw_image_sets[raw_id]),
                "raw_object_count": raw_objects[raw_id],
                "mapped_class_id": RAW_TO_CANONICAL[raw_id],
                "mapped_class_name": (
                    CANONICAL_CLASSES[RAW_TO_CANONICAL[raw_id]]
                    if RAW_TO_CANONICAL[raw_id] is not None
                    else None
                ),
                "retained": raw_id in retained_raw_ids,
                "reason": (
                    "retained"
                    if raw_id in retained_raw_ids
                    else "below_50_images"
                    if RAW_TO_CANONICAL[raw_id] is not None
                    else "not_target_equipment"
                ),
            }
            for raw_id in range(len(RAW_CLASSES))
        ],
        "dropped_objects_by_raw_class": {
            RAW_CLASSES[raw_id]: count for raw_id, count in sorted(dropped_objects.items())
        },
        "materialization": dict(materialization),
    }
    verification = verify_export(args.output)
    report["verification"] = verification
    (args.output / "conversion_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

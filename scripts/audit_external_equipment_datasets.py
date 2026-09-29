from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def list_images(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]


def audit_mendeley(root: Path) -> dict:
    all_images = list_images(root)
    annotation_files = sorted(root.rglob("Annotations/*.json"))
    xml_files = sorted(root.rglob("Annotations_xml/*.xml"))
    image_count_by_class: Counter[str] = Counter()
    object_count_by_class: Counter[str] = Counter()
    annotated_image_paths: set[str] = set()
    images_with_any_box: set[str] = set()
    missing_images: list[str] = []
    invalid_annotation_files: list[str] = []
    category_id_to_names: dict[int, set[str]] = defaultdict(set)

    for annotation_path in annotation_files:
        try:
            data = json.loads(annotation_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            invalid_annotation_files.append(f"{annotation_path}: {exc}")
            continue
        categories = {int(row["id"]): str(row["name"]) for row in data.get("categories", [])}
        for category_id, name in categories.items():
            category_id_to_names[category_id].add(name)
        image_rows = data.get("images", [])
        if not image_rows:
            invalid_annotation_files.append(f"{annotation_path}: no image record")
            continue
        image_row = image_rows[0]
        image_path = annotation_path.parent.parent / "JPEGImages" / image_row["file_name"]
        image_key = str(image_path)
        annotated_image_paths.add(image_key)
        if not image_path.exists():
            missing_images.append(str(image_path))
        annotations = data.get("annotations", [])
        if annotations:
            images_with_any_box.add(image_key)
        classes_in_image: set[str] = set()
        for annotation in annotations:
            name = categories.get(int(annotation["category_id"]), f"unknown_id_{annotation['category_id']}")
            object_count_by_class[name] += 1
            classes_in_image.add(name)
        image_count_by_class.update(classes_in_image)

    all_image_keys = {str(path) for path in all_images}
    return {
        "dataset": "mendeley",
        "root": str(root),
        "image_count": len(all_images),
        "json_annotation_count": len(annotation_files),
        "xml_annotation_count": len(xml_files),
        "images_with_annotation_file": len(annotated_image_paths),
        "images_with_any_box": len(images_with_any_box),
        "images_without_annotation_file": len(all_image_keys - annotated_image_paths),
        "annotation_without_image_count": len(missing_images),
        "invalid_annotation_file_count": len(invalid_annotation_files),
        "missing_image_examples": missing_images[:30],
        "invalid_annotation_examples": invalid_annotation_files[:30],
        "category_id_to_names": {str(key): sorted(value) for key, value in sorted(category_id_to_names.items())},
        "classes": [
            {
                "raw_class": name,
                "image_count": image_count_by_class[name],
                "object_count": object_count_by_class[name],
            }
            for name in sorted(object_count_by_class)
        ],
    }


def audit_mocs(root: Path) -> dict:
    annotation_path = root / "instances_val.json"
    data = json.loads(annotation_path.read_text(encoding="utf-8"))
    categories = {int(row["id"]): str(row["name"]) for row in data.get("categories", [])}
    image_rows = {int(row["id"]): row for row in data.get("images", [])}
    annotations_by_image: dict[int, list[dict]] = defaultdict(list)
    image_count_by_class: Counter[str] = Counter()
    object_count_by_class: Counter[str] = Counter()
    for annotation in data.get("annotations", []):
        annotations_by_image[int(annotation["image_id"])].append(annotation)
        name = categories.get(int(annotation["category_id"]), f"unknown_id_{annotation['category_id']}")
        object_count_by_class[name] += 1
    for image_id, annotations in annotations_by_image.items():
        classes_in_image = {
            categories.get(int(annotation["category_id"]), f"unknown_id_{annotation['category_id']}")
            for annotation in annotations
        }
        image_count_by_class.update(classes_in_image)

    image_dir = root / "instances_val"
    existing_images = list_images(image_dir)
    existing_names = {path.name for path in existing_images}
    declared_names = {str(row["file_name"]) for row in image_rows.values()}
    missing_images = sorted(declared_names - existing_names)
    extra_images = sorted(existing_names - declared_names)
    images_without_boxes = sorted(
        str(image_rows[image_id]["file_name"])
        for image_id in image_rows
        if image_id not in annotations_by_image
    )
    return {
        "dataset": "mocs",
        "root": str(root),
        "image_count_declared": len(image_rows),
        "image_count_on_disk": len(existing_images),
        "annotation_count": len(data.get("annotations", [])),
        "category_count": len(categories),
        "images_without_any_box": len(images_without_boxes),
        "missing_image_count": len(missing_images),
        "extra_image_count": len(extra_images),
        "missing_image_examples": missing_images[:30],
        "extra_image_examples": extra_images[:30],
        "categories": [{"id": key, "name": value} for key, value in sorted(categories.items())],
        "classes": [
            {
                "raw_class": name,
                "image_count": image_count_by_class[name],
                "object_count": object_count_by_class[name],
            }
            for name in sorted(object_count_by_class)
        ],
    }


def write_class_csv(path: Path, dataset: str, classes: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["dataset", "raw_class", "image_count", "object_count"],
        )
        writer.writeheader()
        for row in classes:
            writer.writerow({"dataset": dataset, **row})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mendeley", required=True, type=Path)
    parser.add_argument("--mocs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    mendeley = audit_mendeley(args.mendeley)
    mocs = audit_mocs(args.mocs)
    result = {"mendeley": mendeley, "mocs": mocs}
    (args.output / "raw_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_class_csv(args.output / "mendeley_raw_classes.csv", "mendeley", mendeley["classes"])
    write_class_csv(args.output / "mocs_raw_classes.csv", "mocs", mocs["classes"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

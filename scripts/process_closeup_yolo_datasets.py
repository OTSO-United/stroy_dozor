from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image


CANONICAL_CLASSES = [
    "Dump truck", "Excavator", "Motor grader", "Roller", "Crane manipulator",
    "Gazelle", "Forklift Standart", "Bucket loader Big", "Mixer", "Tanker",
    "Bulldozer", "Cleaning equipment", "Truck", "Trailer", "Forklift Giraffe",
    "Bucket loader Standart", "Autocran", "Tower crane", "Asphalt paver",
    "Piling rig", "Concrete pump truck", "Aerial work platform", "Tunneling machine",
    "Road marking machine", "Tractor / mini-tractor",
    "Railway machine / track-laying crane",
]

DATASETS = {
    "closeup_asphalt_paver_yolo_filtered": {
        "source": "Asphalt Paver.v1i.yolo26",
        "raw_classes": ["Asphalt Paver", "Tandem-Roller"],
        "mapping": {0: 18, 1: 3},
        "group_mode": "header",
        "max_versions": 1,
        "mapping_notes": {"0": "Asphalt Paver -> Asphalt paver", "1": "Tandem-Roller -> Roller"},
    },
    "closeup_construction_vehicle_detection_yolo_filtered": {
        "source": "Construction Vehicle Detection.v2i.yolo26",
        "raw_classes": ["Bulldozer", "Dump Truck", "Excavator", "Grader", "Loader", "Mixer Truck", "Mobile Crane", "Roller"],
        "mapping": {0: 10, 1: 0, 2: 1, 3: 2, 4: 7, 5: 8, 6: 16, 7: 3},
        "group_mode": "header",
        "max_versions": 1,
        "mapping_notes": {
            "0": "Bulldozer -> Bulldozer", "1": "Dump Truck -> Dump truck",
            "2": "Excavator -> Excavator", "3": "Grader -> Motor grader",
            "4": "Loader -> Bucket loader Big", "5": "Mixer Truck -> Mixer",
            "6": "Mobile Crane -> Autocran", "7": "Roller -> Roller",
        },
    },
    "closeup_construction_monitoring_yolo_filtered": {
        "source": "Construction Monitoring.v1i.yolo26",
        "raw_classes": [
            "Asphalt Paver", "Backhoe Loader", "Beam", "Bored Piling Rig", "Brick",
            "Bulldozer", "Cement bags", "Column", "Concrete Block", "Concrete Mixer",
            "Concrete Vibrator", "Crane", "Excavation", "Excavator", "Forklift",
            "Formwork", "Glass", "Graded Ground", "Hydraulic Cropper", "Jack Hammer",
            "Loader", "Mobile Crane", "Motor Grader", "Pile Driving", "Pump Truck",
            "Rebar", "Roller", "Roof", "Scaffold", "Skid Steer Loader", "Slab",
            "Soil Pile", "Steel Pipe", "Tank Truck", "Telehandler", "Truck", "Wall",
            "Worker",
        ],
        "mapping": {0: 18, 9: 8, 11: 17, 13: 1, 14: 6, 20: 7, 21: 16, 22: 2, 24: 20, 26: 3, 33: 9, 35: 12},
        "drop_if_raw_classes": {5},
        "group_mode": "augmentation_root",
        "max_versions": 1,
        "mapping_notes": {
            "0": "Asphalt Paver -> Asphalt paver",
            "5": "Bulldozer rejected after visual QA; drop every image containing raw class 5",
            "9": "Concrete Mixer -> Mixer",
            "11": "Crane -> Tower crane; визуально преобладают стационарные/башенные краны, но присутствует шум",
            "13": "Excavator -> Excavator",
            "14": "Forklift -> Forklift Standart", "20": "Loader -> Bucket loader Big",
            "21": "Mobile Crane -> Autocran; класс смешивает автокраны с отдельными манипуляторами и гусеничными кранами",
            "22": "Motor Grader -> Motor grader", "24": "Pump Truck -> Concrete pump truck",
            "26": "Roller -> Roller", "33": "Tank Truck -> Tanker", "35": "Truck -> Truck",
        },
        "excluded_semantic_risks": {
            "1 Backhoe Loader": "отдельный визуальный тип, не эквивалент Excavator или Loader",
            "3 Bored Piling Rig": "визуальная выборка смешивает буровые, стрелы и посторонние объекты",
            "23 Pile Driving": "визуально неоднородный и шумный класс",
            "29 Skid Steer Loader": "нет согласованного целевого класса",
            "34 Telehandler": "много рамок на посторонних объектах; не переносится в Forklift Giraffe",
        },
    },
    "closeup_roller_numeric_yolo_filtered": {
        "source": "train---- valid----.v1i.yolo26",
        "raw_classes": ["0", "1", "2"],
        "mapping": {0: 10, 1: 7, 2: 3},
        "required_raw_classes": {2},
        "group_mode": "header",
        "max_versions": 1,
        "mapping_notes": {
            "0": "визуально Bulldozer -> Bulldozer; сохранять существующие исходные рамки",
            "1": "визуально Bucket loader Big -> Bucket loader Big; сохранять существующие исходные рамки",
            "2": "визуально преимущественно Roller -> Roller",
        },
    },
    "closeup_choo_yolo_filtered": {
        "source": "Choo.v3i.yolo26",
        "raw_classes": ["compactor", "dump_truck", "excavator", "forklift", "mixer_truck", "mobile_crane"],
        "mapping": {0: 3, 3: 6},
        "excluded_output_stems": {
            "closeup_choo__000912__v2", "closeup_choo__002139__v2",
        },
        "group_mode": "header",
        "max_versions": 2,
        "mapping_notes": {
            "0": "compactor -> Roller; retained after user visual adjudication, with two confirmed false frames removed",
            "1": "dump_truck rejected: more than 10 clear errors in 100-image audit",
            "2": "excavator rejected: more than 10 clear errors in 100-image audit",
            "3": "forklift -> Forklift Standart; retained after 100-image QA",
            "4": "mixer_truck rejected: more than 10 clear errors in 100-image audit",
            "5": "mobile_crane rejected: more than 10 clear errors in 100-image audit",
        },
    },
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
RF_RE = re.compile(r"\.rf\.[0-9a-f]{16,}$", re.IGNORECASE)
AUG_RE = re.compile(r"(?:[_-]aug[_-]?\d+)+$", re.IGNORECASE)
SOURCE_EXT_RE = re.compile(r"_(?:jpg|jpeg|png|bmp|webp)$", re.IGNORECASE)


def roboflow_header(stem: str) -> str:
    return SOURCE_EXT_RE.sub("", RF_RE.sub("", stem))


def augmentation_root(header: str) -> str:
    return AUG_RE.sub("", header)


def parse_label(path: Path, class_count: int) -> tuple[list[tuple[int, float, float, float, float]], list[str], str]:
    boxes: list[tuple[int, float, float, float, float]] = []
    errors: list[str] = []
    annotation_type = "bbox"
    if not path.exists():
        return boxes, [f"missing label: {path}"], annotation_type
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5 and (len(parts) < 7 or len(parts) % 2 == 0):
            errors.append(f"{path}:{line_no}: expected YOLO bbox or polygon")
            continue
        try:
            class_id = int(parts[0])
            values = [float(value) for value in parts[1:]]
        except ValueError:
            errors.append(f"{path}:{line_no}: parse error")
            continue
        if not 0 <= class_id < class_count:
            errors.append(f"{path}:{line_no}: class {class_id} outside 0..{class_count - 1}")
            continue
        if len(parts) == 5:
            x, y, width, height = values
        else:
            annotation_type = "segmentation_converted_to_bbox"
            xs, ys = values[0::2], values[1::2]
            x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
            x, y, width, height = (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1
        if not all(0 <= value <= 1 for value in values) or width <= 0 or height <= 0:
            errors.append(f"{path}:{line_no}: invalid bbox")
            continue
        boxes.append((class_id, x, y, width, height))
    return boxes, errors, annotation_type


def enumerate_records(source: Path, class_count: int) -> tuple[list[dict], list[str]]:
    records: list[dict] = []
    for split in ("train", "valid", "test"):
        image_dir = source / split / "images"
        label_dir = source / split / "labels"
        if not image_dir.exists():
            continue
        for image_path in image_dir.iterdir():
            if image_path.is_file() and image_path.suffix.lower() in IMAGE_EXTENSIONS:
                header = roboflow_header(image_path.stem)
                records.append({
                    "split": split,
                    "image": image_path,
                    "label": label_dir / f"{image_path.stem}.txt",
                    "header": header,
                    "augmentation_root": augmentation_root(header),
                })
    errors: list[str] = []
    jobs = [(record["label"], class_count) for record in records]
    with ThreadPoolExecutor(max_workers=32) as executor:
        for record, parsed in zip(records, executor.map(lambda args: parse_label(*args), jobs)):
            boxes, parse_errors, annotation_type = parsed
            record["boxes"] = boxes
            record["annotation_type"] = annotation_type
            errors.extend(parse_errors)
    return records, errors


def reset_output(path: Path) -> None:
    if path.name not in DATASETS:
        raise ValueError(f"Refusing to reset unexpected output: {path}")
    resolved_root = path.parent.resolve()
    if resolved_root.name.lower() != "datasets":
        raise ValueError(f"Output must be directly under datasets: {path}")
    if path.exists():
        shutil.rmtree(path)
    (path / "images").mkdir(parents=True)
    (path / "labels").mkdir(parents=True)


def retained_boxes(record: dict, mapping: dict[int, int]) -> list[tuple[int, float, float, float, float]]:
    return [(mapping[raw_id], x, y, width, height) for raw_id, x, y, width, height in record["boxes"] if raw_id in mapping]


def candidate_score(record: dict, mapping: dict[int, int]) -> tuple:
    boxes = retained_boxes(record, mapping)
    area = sum(width * height for _, _, _, width, height in boxes)
    is_explicit_aug = "_aug_" in record["header"].lower() or "-aug-" in record["header"].lower()
    split_rank = {"valid": 0, "test": 1, "train": 2}.get(record["split"], 3)
    return (-int(bool(boxes)), is_explicit_aug, split_rank, -len(boxes), -area, record["image"].name)


def select_records(records: list[dict], config: dict) -> tuple[list[dict], dict]:
    group_field = config["group_mode"]
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        groups[record[group_field]].append(record)
    selected: list[dict] = []
    dropped_augmentation = 0
    for group in groups.values():
        ordered = sorted(group, key=lambda record: candidate_score(record, config["mapping"]))
        kept = ordered[: config["max_versions"]]
        selected.extend(kept)
        dropped_augmentation += len(group) - len(kept)
    return selected, {
        "group_mode": group_field,
        "group_count": len(groups),
        "max_versions_per_group": config["max_versions"],
        "dropped_as_augmentation": dropped_augmentation,
    }


def pixel_hash(path: Path) -> str:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        payload = rgb.size[0].to_bytes(4, "little") + rgb.size[1].to_bytes(4, "little") + rgb.tobytes()
    return hashlib.sha256(payload).hexdigest()


def safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", value).strip("._")
    return cleaned[:150] or "image"


def write_metadata(output: Path, source_name: str) -> None:
    names = {str(index): name for index, name in enumerate(CANONICAL_CLASSES)}
    (output / "class_names.json").write_text(json.dumps(names, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "classes.txt").write_text("\n".join(CANONICAL_CLASSES) + "\n", encoding="utf-8")
    lines = [
        f"path: {output.as_posix()}", "train: images", "val:", f"nc: {len(CANONICAL_CLASSES)}", "names:",
    ]
    lines.extend(f"  {index}: {json.dumps(name)}" for index, name in enumerate(CANONICAL_CLASSES))
    lines.extend(["", f"# Filtered from: {source_name}", "# Flat images/labels dataset; create a leakage-safe split before evaluation."])
    (output / "data.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=True)

    seen_by_header: dict[str, list[tuple[str, Path, str]]] = defaultdict(list)
    reports: list[dict] = []

    for output_name, config in DATASETS.items():
        source = args.source_root / config["source"]
        output = args.output_root / output_name
        reset_output(output)
        records, errors = enumerate_records(source, len(config["raw_classes"]))
        if errors:
            raise RuntimeError(f"{output_name}: {len(errors)} label errors; examples: {errors[:5]}")
        selected, augmentation_stats = select_records(records, config)

        raw_objects: Counter[int] = Counter()
        for record in records:
            raw_objects.update(box[0] for box in record["boxes"])
        class_images: Counter[int] = Counter()
        class_objects: Counter[int] = Counter()
        annotation_types: Counter[str] = Counter()
        omitted_no_target = 0
        omitted_quality = 0
        omitted_exact_duplicate = 0
        materialization: Counter[str] = Counter()
        retained_images = 0
        group_versions: Counter[str] = Counter()

        for record in sorted(selected, key=lambda row: (row[config["group_mode"]], row["image"].name)):
            if config.get("required_raw_classes") and not any(
                raw_id in config["required_raw_classes"] for raw_id, *_ in record["boxes"]
            ):
                omitted_no_target += 1
                continue
            if any(raw_id in config.get("drop_if_raw_classes", set()) for raw_id, *_ in record["boxes"]):
                omitted_quality += 1
                continue
            boxes = retained_boxes(record, config["mapping"])
            if not boxes:
                omitted_no_target += 1
                continue
            header = record["augmentation_root"]
            duplicate = False
            if seen_by_header[header]:
                current_hash = pixel_hash(record["image"])
                for _, previous_path, previous_hash in seen_by_header[header]:
                    if not previous_hash:
                        previous_hash = pixel_hash(previous_path)
                    if current_hash == previous_hash:
                        duplicate = True
                        break
            else:
                current_hash = ""
            if duplicate:
                omitted_exact_duplicate += 1
                continue

            group_value = record[config["group_mode"]]
            group_versions[group_value] += 1
            version = group_versions[group_value]
            suffix = f"__v{version}" if config["max_versions"] > 1 else ""
            dest_stem = safe_stem(f"{output_name.replace('_yolo_filtered', '')}__{group_value}{suffix}")
            if dest_stem in config.get("excluded_output_stems", set()):
                omitted_quality += 1
                continue
            destination_image = output / "images" / f"{dest_stem}{record['image'].suffix.lower()}"
            destination_label = output / "labels" / f"{dest_stem}.txt"
            if destination_image.exists() or destination_label.exists():
                digest = hashlib.sha1(record["image"].name.encode("utf-8")).hexdigest()[:10]
                dest_stem = f"{dest_stem}__{digest}"
                destination_image = output / "images" / f"{dest_stem}{record['image'].suffix.lower()}"
                destination_label = output / "labels" / f"{dest_stem}.txt"

            try:
                os.link(record["image"], destination_image)
                materialization["hardlink"] += 1
            except OSError:
                shutil.copy2(record["image"], destination_image)
                materialization["copy"] += 1
            destination_label.write_text(
                "\n".join(f"{class_id} {x:.6f} {y:.6f} {width:.6f} {height:.6f}" for class_id, x, y, width, height in boxes) + "\n",
                encoding="utf-8",
            )
            retained_images += 1
            annotation_types[record["annotation_type"]] += 1
            ids = set()
            for class_id, *_ in boxes:
                class_objects[class_id] += 1
                ids.add(class_id)
            class_images.update(ids)
            if not current_hash and seen_by_header[header]:
                current_hash = pixel_hash(record["image"])
            seen_by_header[header].append((output_name, record["image"], current_hash))

        write_metadata(output, config["source"])
        report = {
            "dataset": output_name,
            "source": str(source),
            "output": str(output),
            "source_preserved": True,
            "source_images": len(records),
            "source_objects": sum(raw_objects.values()),
            "augmentation": augmentation_stats,
            "selected_before_target_filter": len(selected),
            "omitted_no_target": omitted_no_target,
            "omitted_quality_control": omitted_quality,
            "omitted_exact_cross_dataset_duplicate": omitted_exact_duplicate,
            "retained_images": retained_images,
            "retained_objects": sum(class_objects.values()),
            "annotation_types": dict(annotation_types),
            "materialization": dict(materialization),
            "mapping_notes": config["mapping_notes"],
            "excluded_semantic_risks": config.get("excluded_semantic_risks", {}),
            "raw_classes": [
                {"raw_id": index, "raw_name": name, "object_count": raw_objects[index], "retained": index in config["mapping"], "mapped_id": config["mapping"].get(index)}
                for index, name in enumerate(config["raw_classes"])
            ],
            "classes": [
                {"class_id": class_id, "class_name": CANONICAL_CLASSES[class_id], "image_count": class_images[class_id], "object_count": class_objects[class_id]}
                for class_id in sorted(class_objects)
            ],
        }
        (output / "conversion_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        reports.append(report)
        print(f"{output_name}: {retained_images} images, {sum(class_objects.values())} objects")

    portfolio = {
        "datasets": reports,
        "total_images": sum(report["retained_images"] for report in reports),
        "total_objects": sum(report["retained_objects"] for report in reports),
        "aerial_work_platform_found": False,
        "notes": [
            "No explicit aerial work platform/scissor lift class was found in the five sources.",
            "Construction Monitoring segmentation polygons were converted to detector bounding boxes.",
            "Source directories under крупный план were preserved; filtered outputs are flat images/labels datasets.",
        ],
    }
    (args.report_dir / "closeup_conversion_summary.json").write_text(
        json.dumps(portfolio, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

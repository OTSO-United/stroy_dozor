from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


DATASETS = {
    "asphalt_paver": {
        "folder": "Asphalt Paver.v1i.yolo26",
        "classes": ["Asphalt Paver", "Tandem-Roller"],
        "inspect": [0, 1],
    },
    "construction_vehicle_detection": {
        "folder": "Construction Vehicle Detection.v2i.yolo26",
        "classes": [
            "Bulldozer", "Dump Truck", "Excavator", "Grader", "Loader",
            "Mixer Truck", "Mobile Crane", "Roller",
        ],
        "inspect": list(range(8)),
    },
    "construction_monitoring": {
        "folder": "Construction Monitoring.v1i.yolo26",
        "classes": [
            "Asphalt Paver", "Backhoe Loader", "Beam", "Bored Piling Rig", "Brick",
            "Bulldozer", "Cement bags", "Column", "Concrete Block", "Concrete Mixer",
            "Concrete Vibrator", "Crane", "Excavation", "Excavator", "Forklift",
            "Formwork", "Glass", "Graded Ground", "Hydraulic Cropper", "Jack Hammer",
            "Loader", "Mobile Crane", "Motor Grader", "Pile Driving", "Pump Truck",
            "Rebar", "Roller", "Roof", "Scaffold", "Skid Steer Loader", "Slab",
            "Soil Pile", "Steel Pipe", "Tank Truck", "Telehandler", "Truck", "Wall",
            "Worker",
        ],
        "inspect": [0, 1, 3, 5, 9, 11, 13, 14, 20, 21, 22, 23, 24, 26, 29, 33, 34, 35],
    },
    "numeric_train_valid": {
        "folder": "train---- valid----.v1i.yolo26",
        "classes": ["0", "1", "2"],
        "inspect": [0, 1, 2],
    },
    "choo": {
        "folder": "Choo.v3i.yolo26",
        "classes": ["compactor", "dump_truck", "excavator", "forklift", "mixer_truck", "mobile_crane"],
        "inspect": list(range(6)),
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


def parse_label(job: tuple[Path, int]) -> tuple[list[tuple[int, float, float, float, float]], list[str]]:
    path, class_count = job
    boxes: list[tuple[int, float, float, float, float]] = []
    errors: list[str] = []
    if not path.exists():
        return boxes, [f"missing label: {path}"]
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
            xs = values[0::2]
            ys = values[1::2]
            x1, x2 = min(xs), max(xs)
            y1, y2 = min(ys), max(ys)
            x, y = (x1 + x2) / 2, (y1 + y2) / 2
            width, height = x2 - x1, y2 - y1
        if not all(0 <= value <= 1 for value in values) or width <= 0 or height <= 0:
            errors.append(f"{path}:{line_no}: invalid bbox")
            continue
        boxes.append((class_id, x, y, width, height))
    return boxes, errors


def audit_dataset(root: Path, key: str, config: dict) -> tuple[dict, list[dict]]:
    dataset = root / config["folder"]
    records: list[dict] = []
    missing_dirs: list[str] = []
    for split in ("train", "valid", "test"):
        image_dir = dataset / split / "images"
        label_dir = dataset / split / "labels"
        if not image_dir.exists():
            continue
        if not label_dir.exists():
            missing_dirs.append(str(label_dir))
        for image_path in image_dir.iterdir():
            if image_path.is_file() and image_path.suffix.lower() in IMAGE_EXTENSIONS:
                records.append(
                    {
                        "split": split,
                        "image": image_path,
                        "label": label_dir / f"{image_path.stem}.txt",
                        "header": roboflow_header(image_path.stem),
                    }
                )

    jobs = [(record["label"], len(config["classes"])) for record in records]
    all_errors: list[str] = []
    with ThreadPoolExecutor(max_workers=32) as executor:
        for record, (boxes, errors) in zip(records, executor.map(parse_label, jobs)):
            record["boxes"] = boxes
            record["augmentation_root"] = augmentation_root(record["header"])
            all_errors.extend(errors)

    class_objects: Counter[int] = Counter()
    class_images: Counter[int] = Counter()
    header_groups: Counter[str] = Counter()
    augmentation_groups: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    empty_images = 0
    for record in records:
        ids = {box[0] for box in record["boxes"]}
        class_objects.update(box[0] for box in record["boxes"])
        class_images.update(ids)
        header_groups[record["header"]] += 1
        augmentation_groups[record["augmentation_root"]] += 1
        split_counts[record["split"]] += 1
        if not record["boxes"]:
            empty_images += 1

    group_histogram = Counter(header_groups.values())
    augmentation_histogram = Counter(augmentation_groups.values())
    summary = {
        "key": key,
        "folder": config["folder"],
        "path": str(dataset),
        "image_count": len(records),
        "split_counts": dict(split_counts),
        "empty_image_count": empty_images,
        "object_count": sum(class_objects.values()),
        "label_error_count": len(all_errors),
        "label_error_examples": all_errors[:30],
        "missing_directories": missing_dirs,
        "roboflow_header_unique_count": len(header_groups),
        "roboflow_header_group_size_histogram": dict(sorted(group_histogram.items())),
        "augmentation_root_unique_count": len(augmentation_groups),
        "augmentation_root_group_size_histogram": dict(sorted(augmentation_histogram.items())),
        "classes": [
            {
                "raw_id": class_id,
                "raw_name": class_name,
                "image_count": class_images[class_id],
                "object_count": class_objects[class_id],
            }
            for class_id, class_name in enumerate(config["classes"])
        ],
    }
    return summary, records


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/dejavu/DejaVuSans.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def render_sheet(output: Path, dataset_key: str, class_id: int, class_name: str, records: list[dict]) -> None:
    candidates = [record for record in records if any(box[0] == class_id for box in record["boxes"])]
    random.Random(20260918 + class_id).shuffle(candidates)
    candidates = candidates[:12]
    if not candidates:
        return
    tile = 320
    title_height = 60
    canvas = Image.new("RGB", (tile * 4, title_height + tile * 3), "#f4f6f8")
    draw = ImageDraw.Draw(canvas)
    title_font = font(24)
    label_font = font(14)
    draw.text((12, 12), f"{dataset_key} | raw {class_id}: {class_name} | n={len(candidates)}", fill="#111827", font=title_font)
    palette = ["#e11d48", "#2563eb", "#16a34a", "#d97706", "#7c3aed", "#0891b2"]
    for index, record in enumerate(candidates):
        row, column = divmod(index, 4)
        x0, y0 = column * tile, title_height + row * tile
        with Image.open(record["image"]) as source:
            image = source.convert("RGB")
        image.thumbnail((tile, tile - 32))
        ox = x0 + (tile - image.width) // 2
        oy = y0 + 28 + (tile - 32 - image.height) // 2
        canvas.paste(image, (ox, oy))
        local = ImageDraw.Draw(canvas)
        local.text((x0 + 4, y0 + 5), f"{record['split']} | {record['image'].name[:35]}", fill="#111827", font=label_font)
        for box in record["boxes"]:
            box_id, cx, cy, width, height = box
            left = ox + (cx - width / 2) * image.width
            top = oy + (cy - height / 2) * image.height
            right = ox + (cx + width / 2) * image.width
            bottom = oy + (cy + height / 2) * image.height
            color = palette[box_id % len(palette)]
            local.rectangle((left, top, right, bottom), outline=color, width=3)
            local.text((left + 2, max(oy, top - 16)), str(box_id), fill=color, font=label_font)
    output.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", class_name).strip("_")
    canvas.save(output / f"{class_id:02d}_{safe_name}.jpg", quality=90)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", choices=sorted(DATASETS), action="append")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    summaries = []
    selected = args.only or list(DATASETS)
    for key in selected:
        config = DATASETS[key]
        summary, records = audit_dataset(args.source_root, key, config)
        summaries.append(summary)
        for class_id in config["inspect"]:
            render_sheet(
                args.output / "contact_sheets" / key,
                key,
                class_id,
                config["classes"][class_id],
                records,
            )
        print(f"{key}: {summary['image_count']} images, {summary['object_count']} objects")
    (args.output / "source_audit.json").write_text(
        json.dumps({"datasets": summaries}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()

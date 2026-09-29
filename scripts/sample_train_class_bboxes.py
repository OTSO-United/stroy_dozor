from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


CLASS_NAMES = [
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
]

PALETTE = [
    "#E6194B", "#3CB44B", "#4363D8", "#F58231", "#911EB4",
    "#46F0F0", "#F032E6", "#BCF60C", "#FABEBE", "#008080",
    "#E6BEFF", "#9A6324", "#FFFAC8", "#800000", "#AAFFC3",
    "#808000", "#000075",
]


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")


def read_labels(path: Path) -> list[tuple[int, float, float, float, float]]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5:
            raise ValueError(f"{path}:{line_no}: expected 5 fields, got {len(parts)}")
        class_id = int(parts[0])
        x, y, width, height = (float(value) for value in parts[1:])
        if class_id < 0 or class_id >= len(CLASS_NAMES):
            raise ValueError(f"{path}:{line_no}: unsupported class {class_id}")
        rows.append((class_id, x, y, width, height))
    return rows


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def label_box(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, fill: str) -> None:
    left, top = xy
    bbox = draw.textbbox((left, top), text, font=font, stroke_width=0)
    width = bbox[2] - bbox[0] + 10
    height = bbox[3] - bbox[1] + 6
    draw.rectangle((left, top, left + width, top + height), fill=fill)
    draw.text((left + 5, top + 3), text, fill="white", font=font)


def render_annotated(
    image_path: Path,
    boxes: list[tuple[int, float, float, float, float]],
    target_class: int,
    output_path: Path,
    max_width: int,
) -> tuple[int, int, int, int]:
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    original_width, original_height = image.size
    scale = min(1.0, max_width / original_width)
    if scale < 1.0:
        image = image.resize(
            (round(original_width * scale), round(original_height * scale)),
            Image.Resampling.LANCZOS,
        )
    width, height = image.size
    draw = ImageDraw.Draw(image)
    font = load_font(max(14, round(width / 90)))
    target_count = 0

    # Context boxes first, then the target class on top.
    for pass_target in (False, True):
        for class_id, x, y, box_width, box_height in boxes:
            is_target = class_id == target_class
            if is_target != pass_target:
                continue
            x1 = max(0, min(width - 1, round((x - box_width / 2) * width)))
            y1 = max(0, min(height - 1, round((y - box_height / 2) * height)))
            x2 = max(0, min(width - 1, round((x + box_width / 2) * width)))
            y2 = max(0, min(height - 1, round((y + box_height / 2) * height)))
            if x2 <= x1 or y2 <= y1:
                continue
            color = "#FF1744" if is_target else PALETTE[class_id]
            line_width = max(4, round(width / 320)) if is_target else max(2, round(width / 640))
            draw.rectangle((x1, y1, x2, y2), outline=color, width=line_width)
            if is_target:
                target_count += 1
            label_y = max(0, y1 - (font.size + 10 if hasattr(font, "size") else 24))
            label_box(draw, (x1, label_y), f"{class_id} {CLASS_NAMES[class_id]}", font, color)

    header_font = load_font(max(16, round(width / 75)))
    header = f"Target: {target_class} {CLASS_NAMES[target_class]} | target boxes: {target_count} | all boxes: {len(boxes)}"
    header_bbox = draw.textbbox((0, 0), header, font=header_font)
    header_height = header_bbox[3] - header_bbox[1] + 14
    draw.rectangle((0, 0, width, header_height), fill=(0, 0, 0, 190))
    draw.text((8, 7), header, fill="white", font=header_font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, quality=90, optimize=True)
    return original_width, original_height, target_count, len(boxes)


def make_contact_sheet(
    image_paths: list[Path],
    class_id: int,
    output_path: Path,
) -> None:
    cols = 4
    rows = math.ceil(len(image_paths) / cols)
    cell_width, cell_height = 340, 225
    caption_height, title_height = 34, 54
    canvas = Image.new(
        "RGB",
        (cols * cell_width, title_height + rows * (cell_height + caption_height)),
        "white",
    )
    draw = ImageDraw.Draw(canvas)
    title_font = load_font(24)
    caption_font = load_font(14)
    draw.text(
        (12, 12),
        f"Class {class_id}: {CLASS_NAMES[class_id]} — 20 random train images",
        fill="#202124",
        font=title_font,
    )
    for index, image_path in enumerate(image_paths):
        col, row = index % cols, index // cols
        x0 = col * cell_width
        y0 = title_height + row * (cell_height + caption_height)
        with Image.open(image_path) as source:
            thumb = source.convert("RGB")
            thumb.thumbnail((cell_width - 12, cell_height - 12), Image.Resampling.LANCZOS)
        paste_x = x0 + (cell_width - thumb.width) // 2
        paste_y = y0 + (cell_height - thumb.height) // 2
        canvas.paste(thumb, (paste_x, paste_y))
        draw.text(
            (x0 + 7, y0 + cell_height + 7),
            f"{index + 1:02d}  {image_path.name[:38]}",
            fill="#333333",
            font=caption_font,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=91, optimize=True)


def read_audit(path: Path) -> dict[int, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {int(row["class_id"]): row for row in csv.DictReader(handle)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", required=True, type=Path)
    parser.add_argument("--audit-csv", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--threshold", type=int, default=100)
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260917)
    parser.add_argument("--max-width", type=int, default=1600)
    args = parser.parse_args()

    image_dir = args.train / "images"
    label_dir = args.train / "labels"
    args.output.mkdir(parents=True, exist_ok=True)
    audit = read_audit(args.audit_csv)

    image_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    images = {
        path.stem: path
        for path in image_dir.iterdir()
        if path.is_file() and path.suffix.lower() in image_extensions
    }
    labels_by_stem: dict[str, list[tuple[int, float, float, float, float]]] = {}
    images_by_class: dict[int, list[str]] = defaultdict(list)
    object_count_by_class: Counter[int] = Counter()
    missing_images: list[str] = []

    for label_path in sorted(label_dir.glob("*.txt")):
        if label_path.stem not in images:
            missing_images.append(label_path.name)
            continue
        boxes = read_labels(label_path)
        labels_by_stem[label_path.stem] = boxes
        present_classes = {box[0] for box in boxes}
        for class_id in present_classes:
            images_by_class[class_id].append(label_path.stem)
        object_count_by_class.update(box[0] for box in boxes)

    selected_classes = [
        class_id
        for class_id in range(len(CLASS_NAMES))
        if len(images_by_class[class_id]) > args.threshold
    ]
    summary_rows = []

    for class_id in selected_classes:
        candidates = sorted(images_by_class[class_id])
        if len(candidates) < args.sample_size:
            raise ValueError(f"Class {class_id} has only {len(candidates)} candidate images")
        rng = random.Random(args.seed + class_id * 1009)
        selected_stems = sorted(rng.sample(candidates, args.sample_size))
        class_dir = args.output / f"{class_id:02d}_{safe_name(CLASS_NAMES[class_id])}"
        annotated_dir = class_dir / "annotated"
        annotated_dir.mkdir(parents=True, exist_ok=True)
        selection_rows = []
        annotated_paths = []

        for sequence, stem in enumerate(selected_stems, 1):
            source_image = images[stem]
            output_image = annotated_dir / f"{sequence:02d}_{source_image.name}"
            width, height, target_boxes, all_boxes = render_annotated(
                source_image,
                labels_by_stem[stem],
                class_id,
                output_image,
                args.max_width,
            )
            annotated_paths.append(output_image)
            selection_rows.append(
                {
                    "sequence": sequence,
                    "class_id": class_id,
                    "class_name": CLASS_NAMES[class_id],
                    "image_name": source_image.name,
                    "source_image": str(source_image),
                    "source_label": str(label_dir / f"{stem}.txt"),
                    "annotated_image": str(output_image),
                    "source_width": width,
                    "source_height": height,
                    "target_bbox_count": target_boxes,
                    "all_bbox_count": all_boxes,
                }
            )

        selection_csv = class_dir / "selection.csv"
        with selection_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(selection_rows[0]))
            writer.writeheader()
            writer.writerows(selection_rows)
        make_contact_sheet(annotated_paths, class_id, class_dir / "contact_sheet.jpg")

        audit_row = audit[class_id]
        summary_rows.append(
            {
                "class_id": class_id,
                "class_name": CLASS_NAMES[class_id],
                "train_image_count_direct": len(candidates),
                "train_object_count_direct": object_count_by_class[class_id],
                "sampled_images": len(selected_stems),
                "work_primary_count": int(audit_row["work_primary_count"]),
                "work_primary_share": float(audit_row["work_primary_share"]),
                "work_episodic_count": int(audit_row["work_episodic_count"]),
                "work_episodic_share": float(audit_row["work_episodic_share"]),
                "output_folder": str(class_dir),
            }
        )

    summary_csv = args.output / "class_summary.csv"
    with summary_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)

    manifest = {
        "dataset_train": str(args.train),
        "audit_csv": str(args.audit_csv),
        "selection_rule": f"strictly more than {args.threshold} train images containing the class",
        "sample_size_per_class": args.sample_size,
        "random_seed": args.seed,
        "selected_class_count": len(selected_classes),
        "selected_classes": summary_rows,
        "missing_images_for_labels": missing_images,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    readme_lines = [
        "# Случайная выборка train с bounding box",
        "",
        f"Критерий: класс встречается строго более чем на {args.threshold} изображениях train.",
        f"Для каждого класса выбрано {args.sample_size} изображений, random seed = {args.seed}.",
        "Каждая рамка подписана ID и названием класса. Красная толстая рамка — целевой класс папки; тонкие цветные рамки — остальные классы изображения.",
        "Классы 7 Bucket loader Big и 15 Bucket loader Standart обработаны отдельно.",
        "Из 15 отобранных классов 12 указаны хотя бы в одной основной работе, 14 — хотя бы в одной эпизодической работе.",
        "Нулевые доли класса 15 означают, что аудит работ не использовал его как отдельную категорию; это не отсутствие погрузчиков в работах.",
        "",
        "| ID | Класс | Train-изображения | Train-объекты | Основные работы | Эпизодические работы |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        readme_lines.append(
            f"| {row['class_id']} | {row['class_name']} | {row['train_image_count_direct']} | "
            f"{row['train_object_count_direct']} | {row['work_primary_share']:.1%} | "
            f"{row['work_episodic_share']:.1%} |"
        )
    readme_lines.extend(
        [
            "",
            "В каждой папке класса находятся `annotated/` с 20 изображениями, `selection.csv` и `contact_sheet.jpg`.",
        ]
    )
    (args.output / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

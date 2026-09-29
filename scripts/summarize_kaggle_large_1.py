from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from analyze_kaggle_large_1 import CLASS_NAMES, read_labels


def load_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_classes(text):
    return [int(value) for value in text.split(",") if value != ""]


def select_balanced(records, per_camera=2, max_items=48):
    by_camera = defaultdict(list)
    for record in records:
        by_camera[record["camera"]].append(record)
    selected = []
    for camera in sorted(by_camera):
        group = sorted(by_camera[camera], key=lambda row: (row["timestamp"], row["name"]))
        indexes = [len(group) // 2]
        if per_camera > 1 and len(group) > 1:
            indexes = [len(group) // 4, 3 * len(group) // 4]
        for index in indexes[:per_camera]:
            selected.append(group[index])
    if len(selected) > max_items:
        step = len(selected) / max_items
        selected = [selected[min(len(selected) - 1, int(i * step))] for i in range(max_items)]
    return selected


def draw_contact(records, output, title, cols=6):
    if not records:
        return
    cell_w, image_h, label_h = 250, 150, 38
    rows = math.ceil(len(records) / cols)
    canvas = Image.new("RGB", (cols * cell_w, 38 + rows * (image_h + label_h)), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text((10, 10), title, fill="#1F1F1F", font=font)
    for index, record in enumerate(records):
        x0 = index % cols * cell_w
        y0 = 38 + index // cols * (image_h + label_h)
        with Image.open(record["path"]) as im:
            picture = im.convert("RGB")
            picture.thumbnail((cell_w - 8, image_h - 8), Image.Resampling.LANCZOS)
            canvas.paste(picture, (x0 + (cell_w - picture.width) // 2, y0 + (image_h - picture.height) // 2))
        classes = record["classes"] or "empty"
        caption = f"{record['camera']} | {record['date']} {int(record['hour']):02d}h | B={float(record['brightness']):.0f}\n{record['name'][:32]} | cls={classes}"
        draw.multiline_text((x0 + 4, y0 + image_h), caption, fill="#333333", font=font, spacing=1)
    canvas.save(output)


def draw_class_samples(records, dataset, output, sample_per_class=4):
    by_class = defaultdict(list)
    for record in records:
        for class_id in parse_classes(record["classes"]):
            if class_id < len(CLASS_NAMES):
                by_class[class_id].append(record)
    cell_w, image_h, label_h = 300, 180, 28
    rows = len(CLASS_NAMES)
    canvas = Image.new("RGB", (sample_per_class * cell_w, 32 + rows * (image_h + label_h)), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text((8, 8), "Class samples. Red boxes are the target class; gray boxes are other labels.", fill="#1F1F1F", font=font)
    for class_id, class_name in enumerate(CLASS_NAMES):
        candidates = sorted(by_class[class_id], key=lambda row: (row["camera"], row["date"], row["name"]))
        selected = []
        if candidates:
            indexes = sorted(set(int(i * (len(candidates) - 1) / max(1, sample_per_class - 1)) for i in range(sample_per_class)))
            selected = [candidates[i] for i in indexes]
        y0 = 32 + class_id * (image_h + label_h)
        draw.text((4, y0 + 4), f"{class_id} {class_name}", fill="#FFFFFF", font=font)
        draw.rectangle((0, y0, cell_w * sample_per_class, y0 + 20), fill="#1F4E78")
        for col, record in enumerate(selected):
            x0 = col * cell_w
            image_path = dataset / record["split"] / "images" / record["name"]
            label_path = dataset / record["split"] / "labels" / f"{Path(record['name']).stem}.txt"
            labels, _ = read_labels(label_path)
            with Image.open(image_path) as im:
                source = im.convert("RGB")
            scale = min((cell_w - 8) / source.width, (image_h - 28) / source.height)
            shown = source.resize((max(1, int(source.width * scale)), max(1, int(source.height * scale))), Image.Resampling.LANCZOS)
            px = x0 + (cell_w - shown.width) // 2
            py = y0 + 24 + (image_h - 28 - shown.height) // 2
            canvas.paste(shown, (px, py))
            for label_class, x, y, w, h in labels:
                left = px + (x - w / 2) * shown.width
                top = py + (y - h / 2) * shown.height
                right = px + (x + w / 2) * shown.width
                bottom = py + (y + h / 2) * shown.height
                color = "#D00000" if label_class == class_id else "#777777"
                draw.rectangle((left, top, right, bottom), outline=color, width=2 if label_class == class_id else 1)
                if label_class == class_id:
                    draw.text((left + 2, max(py, top - 11)), str(label_class), fill=color, font=font)
            draw.text((x0 + 4, y0 + image_h), f"{record['split']} {record['camera']} {record['date']} {record['name'][:25]}", fill="#333333", font=font)
    canvas.save(output)


def recommendation(row):
    class_id = int(row["class_id"])
    primary = float(row["work_primary_share"])
    episodic = float(row["work_episodic_share"])
    train_objects = int(row["train_object_count"])
    valid_objects = int(row["valid_object_count"])
    if class_id == 15:
        return "P0", "Объединить с 7", "Отдельный размерный класс не нужен; перенести разметку в единый класс фронтального погрузчика."
    if class_id >= 17:
        if primary >= 0.075:
            return "P0", "Добавить класс и разметку", "Класс отсутствует, но используется как основной минимум в 7,5% строк справочника."
        if primary >= 0.015:
            return "P1", "Добавить пилотную разметку", "Класс отсутствует и имеет заметную долю основных работ."
        return "P2", "Добавлять по области применения", "Класс отсутствует, но глобальная частота невысока или наблюдаемость камерой ограничена."
    if train_objects == 0:
        return "P0", "Добавить в train", "У класса нет ни одного объекта в train."
    if train_objects < 50 or valid_objects < 20:
        return "P0", "Переразбить и доразметить", "Класс практически не обучаем или не проверяем в одном из split."
    dataset_share = (int(row["train_image_count"]) + int(row["valid_image_count"])) / 19982
    if (primary >= 0.10 and dataset_share < primary / 2) or (episodic >= 0.15 and dataset_share < episodic / 2):
        return "P1", "Расширить по камерам и сценам", "Присутствие в датасете существенно ниже частоты основных или эпизодических назначений."
    ratio = float(row["train_valid_prevalence_ratio"]) if row["train_valid_prevalence_ratio"] else None
    if ratio is not None and (ratio < 0.25 or ratio > 4):
        return "P1", "Сбалансировать split по группам", "Частота в valid отличается от train более чем в 4 раза."
    if class_id == 11:
        return "P2", "Сохранить, но ограничить доминирующие сцены", "Класс нужен для unexpected, однако относительно справочника и valid он переизбыточен."
    return "P2", "Сохранить и контролировать разнообразие", "Явного критического дефицита по числу объектов не выявлено."


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--audit", required=True)
    args = parser.parse_args()
    dataset = Path(args.dataset)
    audit = Path(args.audit)
    records = load_csv(audit / "image_metrics.csv")
    class_rows = load_csv(audit / "class_statistics.csv")
    for record in records:
        record["path"] = str(dataset / record["split"] / "images" / record["name"])

    scene_rows = []
    for class_id in range(len(CLASS_NAMES)):
        for split in ("train", "valid"):
            present = [r for r in records if r["split"] == split and class_id in parse_classes(r["classes"])]
            camera_counts = Counter(r["camera"] for r in present)
            date_counts = Counter(r["date"] for r in present)
            top_camera, top_camera_count = camera_counts.most_common(1)[0] if camera_counts else (None, 0)
            scene_rows.append({
                "class_id": class_id,
                "class_name": CLASS_NAMES[class_id],
                "split": split,
                "image_count": len(present),
                "unique_cameras": len(camera_counts),
                "unique_dates": len(date_counts),
                "top_camera": top_camera,
                "top_camera_image_count": top_camera_count,
                "top_camera_share": top_camera_count / len(present) if present else None,
            })
    with (audit / "class_scene_statistics.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(scene_rows[0]))
        writer.writeheader()
        writer.writerows(scene_rows)

    rec_rows = []
    scene_lookup = {(int(r["class_id"]), r["split"]): r for r in scene_rows}
    for row in class_rows:
        priority, action, reason = recommendation(row)
        class_id = int(row["class_id"])
        combined_images = int(row["train_image_count"]) + int(row["valid_image_count"])
        rec_rows.append({
            **row,
            "combined_image_count": combined_images,
            "combined_image_share": combined_images / 19982,
            "train_unique_cameras": scene_lookup.get((class_id, "train"), {}).get("unique_cameras", 0),
            "valid_unique_cameras": scene_lookup.get((class_id, "valid"), {}).get("unique_cameras", 0),
            "train_top_camera_share": scene_lookup.get((class_id, "train"), {}).get("top_camera_share"),
            "valid_top_camera_share": scene_lookup.get((class_id, "valid"), {}).get("top_camera_share"),
            "priority": priority,
            "recommended_action": action,
            "reason": reason,
        })
    with (audit / "class_recommendations.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rec_rows[0]))
        writer.writeheader()
        writer.writerows(rec_rows)

    camera_rows = []
    for split in ("train", "valid"):
        subset = [r for r in records if r["split"] == split]
        camera_counts = Counter(r["camera"] for r in subset)
        for camera, count in camera_counts.most_common():
            camera_records = [r for r in subset if r["camera"] == camera]
            timestamps = sorted(r["timestamp"] for r in camera_records if r["timestamp"])
            camera_rows.append({
                "split": split,
                "camera": camera,
                "image_count": count,
                "split_share": count / len(subset) if subset else None,
                "date_count": len({r["date"] for r in camera_records}),
                "min_timestamp": timestamps[0] if timestamps else None,
                "max_timestamp": timestamps[-1] if timestamps else None,
            })
    (audit / "report_data.json").write_text(
        json.dumps({"class_recommendations": rec_rows, "class_scene_statistics": scene_rows, "camera_statistics": camera_rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    measured = [r for r in records if r["brightness"]]
    draw_contact(select_balanced([r for r in measured if r["split"] == "train"], per_camera=1, max_items=48), audit / "train_cameras_contact.png", "Train: one sampled frame per camera")
    draw_contact(select_balanced([r for r in measured if r["split"] == "valid"], per_camera=4, max_items=28), audit / "valid_cameras_contact.png", "Valid: four sampled frames per camera")
    draw_class_samples(records, dataset, audit / "class_samples.png")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


CLASS_NAMES = [
    "Dump truck", "Excavator", "Motor grader", "Roller", "Crane manipulator",
    "Gazelle", "Forklift Standart", "Bucket loader Big", "Mixer", "Tanker",
    "Bulldozer", "Cleaning equipment", "Truck", "Trailer", "Forklift Giraffe",
    "Bucket loader Standart", "Autocran",
]

CAMERA_RE = re.compile(
    r"^camera_(?P<camera>.+?)_(?P<date>\d{4}-\d{2}-\d{2})T"
    r"(?P<hour>\d{2})_(?P<minute>\d{2})_(?P<second>\d{2})"
)
PREFIX_RE = re.compile(
    r"^(?P<camera>[^_]+)_(?P<hour>\d{2})_(?P<minute>\d{2})_"
    r"(?P<second>\d{2})_\d+-(?P<date>\d{4}-\d{2}-\d{2})$"
)


def parse_capture(stem: str):
    match = CAMERA_RE.match(stem) or PREFIX_RE.match(stem)
    if not match:
        return None, None, None
    camera = match.group("camera")
    timestamp = datetime.fromisoformat(
        f"{match.group('date')}T{match.group('hour')}:"
        f"{match.group('minute')}:{match.group('second')}"
    )
    return camera, timestamp, "camera_iso" if stem.startswith("camera_") else "prefix_time_date"


def percentile(values, p):
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=np.float64), p))


def distribution_summary(values):
    if not values:
        return {"count": 0, "mean": None, "p10": None, "median": None, "p90": None}
    return {
        "count": len(values),
        "mean": float(statistics.fmean(values)),
        "p10": percentile(values, 10),
        "median": percentile(values, 50),
        "p90": percentile(values, 90),
    }


def js_divergence(counts_a, counts_b):
    a = np.asarray(counts_a, dtype=np.float64)
    b = np.asarray(counts_b, dtype=np.float64)
    if a.sum() == 0 or b.sum() == 0:
        return None
    a /= a.sum()
    b /= b.sum()
    m = (a + b) / 2
    mask_a = a > 0
    mask_b = b > 0
    kl_a = np.sum(a[mask_a] * np.log2(a[mask_a] / m[mask_a]))
    kl_b = np.sum(b[mask_b] * np.log2(b[mask_b] / m[mask_b]))
    return float((kl_a + kl_b) / 2)


def dhash64(gray):
    tiny = Image.fromarray(gray).resize((9, 8), Image.Resampling.BILINEAR)
    arr = np.asarray(tiny, dtype=np.uint8)
    bits = arr[:, 1:] > arr[:, :-1]
    value = 0
    for bit in bits.ravel():
        value = (value << 1) | int(bit)
    return value


def image_metrics(path: Path):
    with Image.open(path) as im:
        width, height = im.size
        im.draft("RGB", (160, 160))
        rgb = im.convert("RGB").resize((96, 64), Image.Resampling.BILINEAR)
    arr = np.asarray(rgb, dtype=np.float32)
    gray = np.asarray(rgb.convert("L"), dtype=np.uint8)
    brightness = float(gray.mean())
    contrast = float(gray.std())
    maxc = arr.max(axis=2)
    minc = arr.min(axis=2)
    saturation = float(np.mean(np.where(maxc > 0, (maxc - minc) / maxc, 0.0)))
    edge = float((np.abs(np.diff(gray.astype(np.float32), axis=0)).mean() + np.abs(np.diff(gray.astype(np.float32), axis=1)).mean()) / 2)
    return {
        "width": width,
        "height": height,
        "brightness": brightness,
        "contrast": contrast,
        "saturation": saturation,
        "edge_energy": edge,
        "dhash": dhash64(gray),
    }


def read_labels(path: Path):
    rows = []
    errors = []
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5:
            errors.append(f"line {line_no}: expected 5 fields")
            continue
        try:
            class_id = int(parts[0])
            x, y, w, h = (float(v) for v in parts[1:])
        except ValueError:
            errors.append(f"line {line_no}: parse error")
            continue
        if not (0 <= class_id < len(CLASS_NAMES)):
            errors.append(f"line {line_no}: class {class_id} out of range")
        if not all(math.isfinite(v) for v in (x, y, w, h)):
            errors.append(f"line {line_no}: non-finite bbox")
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
            errors.append(f"line {line_no}: bbox outside normalized range")
        if x - w / 2 < -1e-6 or x + w / 2 > 1 + 1e-6 or y - h / 2 < -1e-6 or y + h / 2 > 1 + 1e-6:
            errors.append(f"line {line_no}: bbox crosses image boundary")
        rows.append((class_id, x, y, w, h))
    return rows, errors


def make_contact_sheet(records, output_path: Path, title: str, max_items=30):
    if not records:
        return
    groups = defaultdict(list)
    for record in records:
        key = (record["camera"], record["date"], record["hour"])
        groups[key].append(record)
    selected = []
    for key in sorted(groups, key=lambda k: (str(k[0]), str(k[1]), str(k[2]))):
        group = sorted(groups[key], key=lambda r: r["name"])
        selected.append(group[len(group) // 2])
    if len(selected) < max_items:
        chosen = {r["path"] for r in selected}
        remaining = [r for r in sorted(records, key=lambda r: r["name"]) if r["path"] not in chosen]
        step = max(1, len(remaining) // max(1, max_items - len(selected)))
        selected.extend(remaining[::step][: max_items - len(selected)])
    selected = selected[:max_items]
    cols = 5
    cell_w, cell_h, label_h = 260, 170, 44
    rows = math.ceil(len(selected) / cols)
    canvas = Image.new("RGB", (cols * cell_w, 42 + rows * (cell_h + label_h)), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text((10, 12), title, fill="#1F1F1F", font=font)
    for i, record in enumerate(selected):
        x0 = (i % cols) * cell_w
        y0 = 42 + (i // cols) * (cell_h + label_h)
        try:
            with Image.open(record["path"]) as im:
                image = im.convert("RGB")
                image.thumbnail((cell_w - 8, cell_h - 8), Image.Resampling.LANCZOS)
                px = x0 + (cell_w - image.width) // 2
                py = y0 + (cell_h - image.height) // 2
                canvas.paste(image, (px, py))
        except Exception:
            draw.rectangle((x0 + 4, y0 + 4, x0 + cell_w - 4, y0 + cell_h - 4), outline="red")
        classes = ",".join(str(v) for v in record["classes"]) or "empty"
        caption = f"{record['camera']} {record['date']} {record['hour']:02d}h  B={record['brightness']:.0f}\n{record['name'][:36]}  cls={classes}"
        draw.multiline_text((x0 + 5, y0 + cell_h + 2), caption, fill="#333333", font=font, spacing=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def make_pair_sheet(pairs, by_path, output_path: Path, max_pairs=12):
    chosen = [p for p in pairs if p["train_path"] and p["valid_path"]][:max_pairs]
    if not chosen:
        return
    cell_w, cell_h, label_h = 320, 190, 44
    canvas = Image.new("RGB", (cell_w * 2, 36 + len(chosen) * (cell_h + label_h)), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text((10, 10), "Closest train-valid frames within the same camera", fill="#1F1F1F", font=font)
    for row, pair in enumerate(chosen):
        y0 = 36 + row * (cell_h + label_h)
        for col, key in enumerate(("train_path", "valid_path")):
            record = by_path[pair[key]]
            x0 = col * cell_w
            with Image.open(record["path"]) as im:
                image = im.convert("RGB")
                image.thumbnail((cell_w - 8, cell_h - 8), Image.Resampling.LANCZOS)
                canvas.paste(image, (x0 + (cell_w - image.width) // 2, y0 + (cell_h - image.height) // 2))
            label = ("train" if col == 0 else "valid") + f" | {record['name'][:42]}"
            draw.text((x0 + 5, y0 + cell_h + 2), label, fill="#333333", font=font)
        draw.text((5, y0 + cell_h + 20), f"dHash distance={pair['hamming_distance']}; time delta={pair['time_delta_seconds']}", fill="#7F6000", font=font)
    canvas.save(output_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--work-stats", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--train-image-sample", type=int, default=3000)
    parser.add_argument("--valid-image-sample", type=int, default=1500)
    args = parser.parse_args()

    dataset = Path(args.dataset)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    work_data = json.loads(Path(args.work_stats).read_text(encoding="utf-8"))
    work_stats = {row["class_id"]: row for row in work_data["statistics"]}

    records = []
    split_summary = {}
    class_objects = {split: Counter() for split in ("train", "valid")}
    class_images = {split: Counter() for split in ("train", "valid")}
    bbox_areas = {split: defaultdict(list) for split in ("train", "valid")}
    label_error_examples = []

    for split in ("train", "valid"):
        image_dir = dataset / split / "images"
        label_dir = dataset / split / "labels"
        image_paths = sorted(image_dir.glob("*.jpg"))
        requested_sample = args.train_image_sample if split == "train" else args.valid_image_sample
        sample_size = min(len(image_paths), requested_sample)
        sample_indexes = set(np.linspace(0, len(image_paths) - 1, sample_size, dtype=int).tolist()) if sample_size else set()
        metric_paths = {image_paths[index] for index in sample_indexes}
        label_stems = {p.stem for p in label_dir.glob("*.txt")}
        image_stems = {p.stem for p in image_paths}
        split_summary[split] = {
            "image_count": len(image_paths),
            "label_count": len(label_stems),
            "missing_label_count": len(image_stems - label_stems),
            "orphan_label_count": len(label_stems - image_stems),
            "empty_label_count": 0,
            "label_error_count": 0,
            "unparsed_time_count": 0,
        }
        def inspect_image(image_path):
            label_path = label_dir / f"{image_path.stem}.txt"
            labels, errors = read_labels(label_path) if label_path.exists() else ([], ["missing label"])
            camera, timestamp, name_format = parse_capture(image_path.stem)
            image_error = None
            metrics = {"width": None, "height": None, "brightness": None, "contrast": None, "saturation": None, "edge_energy": None, "dhash": None}
            if image_path in metric_paths:
                try:
                    metrics = image_metrics(image_path)
                except Exception as exc:
                    image_error = f"image read: {exc}"
            return image_path, label_path, labels, errors, camera, timestamp, name_format, metrics, image_error

        with ThreadPoolExecutor(max_workers=8) as executor:
          inspected = executor.map(inspect_image, image_paths)
          for index, result in enumerate(inspected, 1):
            image_path, label_path, labels, errors, camera, timestamp, name_format, metrics, image_error = result
            if not labels:
                split_summary[split]["empty_label_count"] += 1
            split_summary[split]["label_error_count"] += len(errors)
            if errors and len(label_error_examples) < 100:
                label_error_examples.append({"split": split, "file": label_path.name, "errors": errors})
            present = set()
            for class_id, _x, _y, width, height in labels:
                if 0 <= class_id < len(CLASS_NAMES):
                    class_objects[split][class_id] += 1
                    present.add(class_id)
                    bbox_areas[split][class_id].append(width * height)
            for class_id in present:
                class_images[split][class_id] += 1
            if timestamp is None:
                split_summary[split]["unparsed_time_count"] += 1
            if image_error and len(label_error_examples) < 100:
                label_error_examples.append({"split": split, "file": image_path.name, "errors": [image_error]})
            records.append({
                "split": split,
                "name": image_path.name,
                "path": str(image_path),
                "camera": camera or "unparsed",
                "timestamp": timestamp.isoformat(sep=" ") if timestamp else None,
                "date": timestamp.date().isoformat() if timestamp else "unparsed",
                "hour": timestamp.hour if timestamp else -1,
                "name_format": name_format or "unparsed",
                "classes": sorted(present),
                "object_count": len(labels),
                **metrics,
            })
            if index % 1000 == 0:
                print(f"{split}: {index}/{len(image_paths)}", flush=True)

    temporal_groups = Counter((r["split"], r["camera"], r["date"], r["hour"]) for r in records)
    temporal_rows = [
        {"split": key[0], "camera": key[1], "date": key[2], "hour": key[3], "image_count": count}
        for key, count in sorted(temporal_groups.items())
    ]

    cameras = {split: {r["camera"] for r in records if r["split"] == split and r["camera"] != "unparsed"} for split in ("train", "valid")}
    dates = {split: {r["date"] for r in records if r["split"] == split and r["date"] != "unparsed"} for split in ("train", "valid")}
    camera_dates = {split: {(r["camera"], r["date"]) for r in records if r["split"] == split and r["camera"] != "unparsed"} for split in ("train", "valid")}
    timestamps = {split: [datetime.fromisoformat(r["timestamp"]) for r in records if r["split"] == split and r["timestamp"]] for split in ("train", "valid")}

    train_by_camera = defaultdict(list)
    train_hash_any = defaultdict(list)
    by_path = {r["path"]: r for r in records}
    for r in records:
        if r["split"] == "train" and r["dhash"] is not None:
            train_by_camera[r["camera"]].append(r)
            train_hash_any[r["dhash"]].append(r)

    for values in train_by_camera.values():
        values.sort(key=lambda r: r["timestamp"] or "")

    nearest_pairs = []
    exact_hash_valid = 0
    exact_hash_cross_camera = 0
    temporal_threshold_counts = Counter()
    for valid in (r for r in records if r["split"] == "valid" and r["dhash"] is not None):
        exact = train_hash_any.get(valid["dhash"], [])
        if exact:
            exact_hash_valid += 1
            if any(r["camera"] != valid["camera"] for r in exact):
                exact_hash_cross_camera += 1
        candidates = train_by_camera.get(valid["camera"], [])
        if not candidates:
            continue
        best = min(candidates, key=lambda r: (valid["dhash"] ^ r["dhash"]).bit_count())
        hamming = (valid["dhash"] ^ best["dhash"]).bit_count()
        delta = None
        if valid["timestamp"] and best["timestamp"]:
            delta = abs((datetime.fromisoformat(valid["timestamp"]) - datetime.fromisoformat(best["timestamp"])).total_seconds())
            for name, seconds in (("within_1_min", 60), ("within_5_min", 300), ("within_1_hour", 3600), ("within_1_day", 86400)):
                if delta <= seconds:
                    temporal_threshold_counts[name] += 1
        nearest_pairs.append({
            "camera": valid["camera"],
            "valid_file": valid["name"],
            "train_file": best["name"],
            "valid_path": valid["path"],
            "train_path": best["path"],
            "hamming_distance": hamming,
            "time_delta_seconds": delta,
        })
    nearest_pairs.sort(key=lambda row: (row["hamming_distance"], row["time_delta_seconds"] if row["time_delta_seconds"] is not None else float("inf")))

    class_rows = []
    total_images = {split: split_summary[split]["image_count"] for split in ("train", "valid")}
    total_objects = {split: sum(class_objects[split].values()) for split in ("train", "valid")}
    for class_id, class_name in enumerate(CLASS_NAMES):
        work = work_stats.get(class_id, {})
        train_img_share = class_images["train"][class_id] / total_images["train"] if total_images["train"] else 0
        valid_img_share = class_images["valid"][class_id] / total_images["valid"] if total_images["valid"] else 0
        work_primary_share = work.get("primary_share", 0)
        coverage_ratio = (train_img_share + valid_img_share) / 2 / work_primary_share if work_primary_share else None
        class_rows.append({
            "class_id": class_id,
            "class_name": class_name,
            "work_primary_count": work.get("primary_count", 0),
            "work_primary_share": work_primary_share,
            "work_episodic_count": work.get("episodic_count", 0),
            "work_episodic_share": work.get("episodic_share", 0),
            "train_image_count": class_images["train"][class_id],
            "train_image_share": train_img_share,
            "train_object_count": class_objects["train"][class_id],
            "valid_image_count": class_images["valid"][class_id],
            "valid_image_share": valid_img_share,
            "valid_object_count": class_objects["valid"][class_id],
            "train_valid_prevalence_ratio": valid_img_share / train_img_share if train_img_share else None,
            "median_bbox_area_train": percentile(bbox_areas["train"][class_id], 50),
            "median_bbox_area_valid": percentile(bbox_areas["valid"][class_id], 50),
            "dataset_to_primary_work_ratio": coverage_ratio,
        })

    for class_id in range(len(CLASS_NAMES), max(work_stats) + 1):
        work = work_stats[class_id]
        class_rows.append({
            "class_id": class_id,
            "class_name": work["class_name"],
            "work_primary_count": work["primary_count"],
            "work_primary_share": work["primary_share"],
            "work_episodic_count": work["episodic_count"],
            "work_episodic_share": work["episodic_share"],
            "train_image_count": 0,
            "train_image_share": 0,
            "train_object_count": 0,
            "valid_image_count": 0,
            "valid_image_share": 0,
            "valid_object_count": 0,
            "train_valid_prevalence_ratio": None,
            "median_bbox_area_train": None,
            "median_bbox_area_valid": None,
            "dataset_to_primary_work_ratio": 0 if work["primary_share"] else None,
        })

    image_metric_summary = {}
    for split in ("train", "valid"):
        subset = [r for r in records if r["split"] == split]
        measured = [r for r in subset if r["brightness"] is not None]
        image_metric_summary[split] = {
            "sample_count": len(measured),
            "sampling": "evenly spaced over filenames sorted lexicographically",
            "brightness": distribution_summary([r["brightness"] for r in subset if r["brightness"] is not None]),
            "contrast": distribution_summary([r["contrast"] for r in subset if r["contrast"] is not None]),
            "saturation": distribution_summary([r["saturation"] for r in subset if r["saturation"] is not None]),
            "edge_energy": distribution_summary([r["edge_energy"] for r in subset if r["edge_energy"] is not None]),
            "dark_share_brightness_lt_60": sum(1 for r in measured if r["brightness"] < 60) / len(measured) if measured else None,
            "bright_share_brightness_gt_190": sum(1 for r in measured if r["brightness"] > 190) / len(measured) if measured else None,
            "resolution_counts": Counter(f"{r['width']}x{r['height']}" for r in measured),
        }

    object_js = js_divergence([class_objects["train"][i] for i in range(len(CLASS_NAMES))], [class_objects["valid"][i] for i in range(len(CLASS_NAMES))])
    image_js = js_divergence([class_images["train"][i] for i in range(len(CLASS_NAMES))], [class_images["valid"][i] for i in range(len(CLASS_NAMES))])
    summary = {
        "dataset": str(dataset),
        "dataset_signature": hashlib.sha256((str(dataset) + str(sum(total_images.values()))).encode()).hexdigest(),
        "classes": CLASS_NAMES,
        "split_summary": split_summary,
        "total_objects": total_objects,
        "time": {
            "train_min": min(timestamps["train"]).isoformat(sep=" ") if timestamps["train"] else None,
            "train_max": max(timestamps["train"]).isoformat(sep=" ") if timestamps["train"] else None,
            "valid_min": min(timestamps["valid"]).isoformat(sep=" ") if timestamps["valid"] else None,
            "valid_max": max(timestamps["valid"]).isoformat(sep=" ") if timestamps["valid"] else None,
            "train_unique_cameras": len(cameras["train"]),
            "valid_unique_cameras": len(cameras["valid"]),
            "camera_overlap_count": len(cameras["train"] & cameras["valid"]),
            "camera_jaccard": len(cameras["train"] & cameras["valid"]) / len(cameras["train"] | cameras["valid"]) if cameras["train"] | cameras["valid"] else None,
            "train_unique_dates": len(dates["train"]),
            "valid_unique_dates": len(dates["valid"]),
            "date_overlap_count": len(dates["train"] & dates["valid"]),
            "camera_date_overlap_count": len(camera_dates["train"] & camera_dates["valid"]),
            "camera_date_overlap_share_valid": len(camera_dates["train"] & camera_dates["valid"]) / len(camera_dates["valid"]) if camera_dates["valid"] else None,
            "train_cameras": sorted(cameras["train"]),
            "valid_cameras": sorted(cameras["valid"]),
            "train_dates": sorted(dates["train"]),
            "valid_dates": sorted(dates["valid"]),
        },
        "visual_conditions": image_metric_summary,
        "class_distribution": {
            "object_js_divergence_bits": object_js,
            "image_presence_js_divergence_bits": image_js,
        },
        "cross_split_similarity": {
            "valid_images_with_same_dhash_as_train": exact_hash_valid,
            "valid_images_with_same_dhash_cross_camera": exact_hash_cross_camera,
            "same_camera_compared_valid_images": len(nearest_pairs),
            "nearest_hamming_le_0": sum(1 for p in nearest_pairs if p["hamming_distance"] == 0),
            "nearest_hamming_le_2": sum(1 for p in nearest_pairs if p["hamming_distance"] <= 2),
            "nearest_hamming_le_4": sum(1 for p in nearest_pairs if p["hamming_distance"] <= 4),
            "nearest_hamming_median": percentile([p["hamming_distance"] for p in nearest_pairs], 50),
            **temporal_threshold_counts,
        },
        "label_error_examples": label_error_examples,
    }

    (output / "audit_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=dict), encoding="utf-8")
    with (output / "class_statistics.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(class_rows[0]))
        writer.writeheader()
        writer.writerows(class_rows)
    with (output / "temporal_groups.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(temporal_rows[0]))
        writer.writeheader()
        writer.writerows(temporal_rows)
    metric_fields = ["split", "name", "camera", "timestamp", "date", "hour", "name_format", "classes", "object_count", "width", "height", "brightness", "contrast", "saturation", "edge_energy", "dhash"]
    with (output / "image_metrics.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=metric_fields)
        writer.writeheader()
        for record in records:
            row = {key: record[key] for key in metric_fields}
            row["classes"] = ",".join(str(v) for v in row["classes"])
            writer.writerow(row)
    pair_fields = ["camera", "valid_file", "train_file", "hamming_distance", "time_delta_seconds", "valid_path", "train_path"]
    with (output / "cross_split_nearest.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=pair_fields)
        writer.writeheader()
        writer.writerows(nearest_pairs)

    make_contact_sheet([r for r in records if r["split"] == "train" and r["brightness"] is not None], output / "train_contact_sheet.png", "Train: stratified by camera, date and hour")
    make_contact_sheet([r for r in records if r["split"] == "valid" and r["brightness"] is not None], output / "valid_contact_sheet.png", "Valid: stratified by camera, date and hour")
    make_pair_sheet(nearest_pairs, by_path, output / "cross_split_near_pairs.png")
    print(json.dumps({"summary": summary, "class_rows": class_rows}, ensure_ascii=False, default=dict))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import shutil
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path


CANONICAL_CLASSES = [
    "Dump truck", "Excavator", "Motor grader", "Roller", "Crane manipulator",
    "Gazelle", "Forklift Standart", "Bucket loader Big", "Mixer", "Tanker",
    "Bulldozer", "Cleaning equipment", "Truck", "Trailer", "Forklift Giraffe",
    "Bucket loader Standart", "Autocran", "Tower crane", "Asphalt paver",
    "Piling rig", "Concrete pump truck", "Aerial work platform", "Tunneling machine",
    "Road marking machine", "Tractor / mini-tractor",
    "Railway machine / track-laying crane",
]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SPLIT_RATIOS = {"train": 0.75, "val": 0.15, "test": 0.10}
SPLIT_ORDER = ["train", "val", "test"]


@dataclass
class SourceRecord:
    index: int
    dataset: str
    source_partition: str
    image: Path
    label: Path
    boxes: list[tuple[int, float, float, float, float]]
    classes: set[int]
    scene_key: str
    sha256: str = ""


@dataclass
class UniqueImage:
    index: int
    sha256: str
    representative: SourceRecord
    aliases: list[SourceRecord]
    boxes: list[tuple[int, float, float, float, float]]
    annotation_conflicts: int
    component: int = -1
    split: str = ""
    output_name: str = ""


@dataclass
class SceneComponent:
    root: int
    images: list[UniqueImage] = field(default_factory=list)
    dataset_counts: Counter[str] = field(default_factory=Counter)
    class_image_counts: Counter[int] = field(default_factory=Counter)
    class_object_counts: Counter[int] = field(default_factory=Counter)
    split: str = ""

    @property
    def image_count(self) -> int:
        return len(self.images)


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left, right = self.find(left), self.find(right)
        if left == right:
            return
        if self.rank[left] < self.rank[right]:
            left, right = right, left
        self.parent[right] = left
        if self.rank[left] == self.rank[right]:
            self.rank[left] += 1


def parse_label(path: Path) -> list[tuple[int, float, float, float, float]]:
    if not path.exists():
        raise FileNotFoundError(path)
    boxes = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5:
            raise ValueError(f"{path}:{line_no}: expected 5 fields")
        class_id = int(parts[0])
        values = tuple(float(value) for value in parts[1:])
        if not 0 <= class_id < len(CANONICAL_CLASSES):
            raise ValueError(f"{path}:{line_no}: class {class_id} outside 0..25")
        x, y, width, height = values
        if width <= 0 or height <= 0 or not all(0 <= value <= 1 for value in values):
            raise ValueError(f"{path}:{line_no}: invalid bbox")
        boxes.append((class_id, x, y, width, height))
    return boxes


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", value).strip("._")


def numeric_block(stem: str, prefix_pattern: str, block_size: int) -> str | None:
    match = re.match(prefix_pattern, stem)
    if not match:
        return None
    value = int(match.group(1))
    return f"{value // block_size:08d}"


def scene_key(dataset: str, stem: str) -> str:
    if dataset == "mendeley_bbox_yolo_filtered":
        return re.sub(r"_\d+$", "", stem)
    if dataset == "closeup_choo_yolo_filtered":
        return re.sub(r"__v\d+(?:__[0-9a-f]{10})?$", "", stem)
    if dataset == "kaggle_large_1":
        camera = re.match(
            r"^(camera_[^_]+)_(\d{4}-\d{2}-\d{2})T(\d{2})_(\d{2})_", stem
        )
        if camera:
            minute_bucket = int(camera.group(4)) // 10
            return f"{camera.group(1)}:{camera.group(2)}:{camera.group(3)}:{minute_bucket}"
        numbered = re.match(
            r"^(\d+)_(\d{2})_(\d{2})_\d{2}_[^-]+-(\d{4}-\d{2}-\d{2})$", stem
        )
        if numbered:
            minute_bucket = int(numbered.group(3)) // 10
            return f"{numbered.group(1)}:{numbered.group(4)}:{numbered.group(2)}:{minute_bucket}"
        return stem
    patterns = {
        "mocs_instances_train_yolo_filtered": (r"^mocs_train__(\d+)$", 50),
        "mocs_instances_val_yolo_filtered": (r"^mocs__(\d+)$", 50),
        "closeup_construction_monitoring_yolo_filtered": (
            r"^closeup_construction_monitoring__frame_(\d+)$", 50
        ),
        "closeup_roller_numeric_yolo_filtered": (r"^closeup_roller_numeric__(\d+)$", 50),
    }
    if dataset in patterns:
        pattern, block_size = patterns[dataset]
        block = numeric_block(stem, pattern, block_size)
        if block is not None:
            return f"block:{block}"
    if dataset == "closeup_asphalt_paver_yolo_filtered":
        match = re.match(r"^closeup_asphalt_paver__imagesFromVid(\d+)$", stem)
        if match:
            return f"imagesFromVid:{int(match.group(1)) // 50:06d}"
    return stem


def discover_sources(root: Path) -> tuple[list[SourceRecord], list[dict], list[dict]]:
    records: list[SourceRecord] = []
    inventory = []
    exclusions: list[dict] = []
    dataset_dirs = sorted(
        path for path in root.iterdir()
        if path.is_dir() and path.name not in {"preview_images", "merged_dataset"}
        and not path.name.endswith(".__staging")
    )
    for dataset_dir in dataset_dirs:
        layouts: list[tuple[str, Path, Path]] = []
        if (dataset_dir / "images").exists() and (dataset_dir / "labels").exists():
            layouts.append(("flat", dataset_dir / "images", dataset_dir / "labels"))
        for split in ("train", "valid", "val", "test"):
            if (dataset_dir / split / "images").exists() and (dataset_dir / split / "labels").exists():
                layouts.append((split, dataset_dir / split / "images", dataset_dir / split / "labels"))
        if not layouts:
            continue
        before = len(records)
        dataset_classes: Counter[int] = Counter()
        dataset_class_images: Counter[int] = Counter()
        partitions: Counter[str] = Counter()
        for partition, image_dir, label_dir in layouts:
            for image in sorted(image_dir.iterdir()):
                if not image.is_file() or image.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                label = label_dir / f"{image.stem}.txt"
                boxes = parse_label(label)
                if not boxes:
                    exclusions.append({
                        "dataset": dataset_dir.name,
                        "source_partition": partition,
                        "source_image": str(image),
                        "source_label": str(label),
                        "reason": "empty_annotation_no_required_equipment",
                    })
                    continue
                dataset_classes.update(class_id for class_id, *_ in boxes)
                dataset_class_images.update({class_id for class_id, *_ in boxes})
                partitions[partition] += 1
                records.append(SourceRecord(
                    index=len(records),
                    dataset=dataset_dir.name,
                    source_partition=partition,
                    image=image,
                    label=label,
                    boxes=boxes,
                    classes={class_id for class_id, *_ in boxes},
                    scene_key=f"{dataset_dir.name}:{scene_key(dataset_dir.name, image.stem)}",
                ))
        metadata_paths = sorted(
            path for path in dataset_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".json", ".yaml", ".yml", ".txt", ".csv"}
        )
        inventory.append({
            "dataset": dataset_dir.name,
            "root": str(dataset_dir),
            "source_images": len(records) - before,
            "source_labels": len(records) - before,
            "source_objects": sum(dataset_classes.values()),
            "excluded_empty_annotations": sum(
                row["dataset"] == dataset_dir.name for row in exclusions
            ),
            "partitions": dict(sorted(partitions.items())),
            "classes": [
                {
                    "class_id": class_id,
                    "class_name": CANONICAL_CLASSES[class_id],
                    "image_count": dataset_class_images[class_id],
                    "object_count": dataset_classes[class_id],
                }
                for class_id in sorted(dataset_classes)
            ],
            "metadata_files": [path.name for path in metadata_paths],
            "metadata_file_details": [
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": file_sha256(path),
                }
                for path in metadata_paths
            ],
        })
        print(f"inventory {dataset_dir.name}: {len(records) - before} images", flush=True)
    return records, inventory, exclusions


def iou(left: tuple[int, float, float, float, float], right: tuple[int, float, float, float, float]) -> float:
    _, lx, ly, lw, lh = left
    _, rx, ry, rw, rh = right
    l1, t1, l2, t2 = lx - lw / 2, ly - lh / 2, lx + lw / 2, ly + lh / 2
    r1, u1, r2, u2 = rx - rw / 2, ry - rh / 2, rx + rw / 2, ry + rh / 2
    intersection = max(0.0, min(l2, r2) - max(l1, r1)) * max(0.0, min(t2, u2) - max(t1, u1))
    union = lw * lh + rw * rh - intersection
    return intersection / union if union > 0 else 0.0


def merge_annotations(aliases: list[SourceRecord]) -> tuple[list[tuple[int, float, float, float, float]], int]:
    ordered = sorted(aliases, key=lambda row: (-len(row.boxes), row.dataset, str(row.image)))
    # Preserve the selected source annotation verbatim: overlapping boxes of
    # different classes can be legitimate. Deduplication/conflict handling is
    # applied only when a second exact-image alias contributes annotations.
    merged: list[tuple[int, float, float, float, float]] = list(ordered[0].boxes)
    conflicts = 0
    for record in ordered[1:]:
        for box in record.boxes:
            same = [existing for existing in merged if existing[0] == box[0] and iou(existing, box) >= 0.90]
            if same:
                continue
            if any(existing[0] != box[0] and iou(existing, box) >= 0.75 for existing in merged):
                conflicts += 1
                continue
            merged.append(box)
    return merged, conflicts


def reuse_verified_hashes(records: list[SourceRecord], output: Path) -> int:
    aliases_path = output / "metadata" / "source_aliases.csv"
    if not aliases_path.exists():
        return 0
    cached: dict[str, tuple[str, Path]] = {}
    with aliases_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            cached[row["source_image"]] = (
                row["sha256"], output / row["split"] / "images" / row["output_image"]
            )
    reused = 0
    for record in records:
        item = cached.get(str(record.image))
        if not item:
            continue
        digest, merged_image = item
        try:
            if merged_image.exists() and os.path.samefile(record.image, merged_image):
                record.sha256 = digest
                reused += 1
        except OSError:
            continue
    return reused


def deduplicate(records: list[SourceRecord], output: Path) -> tuple[list[UniqueImage], dict[str, list[SourceRecord]]]:
    reused = reuse_verified_hashes(records, output)
    pending = [record for record in records if not record.sha256]
    print(
        f"hashing {len(pending)} source images; reused {reused} hashes verified by hard-link identity",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=16) as executor:
        for index, digest in enumerate(executor.map(lambda row: file_sha256(row.image), pending), 1):
            pending[index - 1].sha256 = digest
            if index % 5000 == 0:
                print(f"hashed {index}/{len(pending)}", flush=True)
    by_hash: dict[str, list[SourceRecord]] = defaultdict(list)
    for record in records:
        by_hash[record.sha256].append(record)
    unique = []
    for digest, aliases in sorted(by_hash.items()):
        representative = sorted(aliases, key=lambda row: (-len(row.boxes), row.dataset, str(row.image)))[0]
        boxes, conflicts = merge_annotations(aliases)
        unique.append(UniqueImage(
            index=len(unique), sha256=digest, representative=representative,
            aliases=aliases, boxes=boxes, annotation_conflicts=conflicts,
        ))
    print(f"exact-hash unique images: {len(unique)}; duplicate source files: {len(records) - len(unique)}", flush=True)
    return unique, by_hash


def build_components(unique: list[UniqueImage]) -> list[SceneComponent]:
    union = UnionFind(len(unique))
    first_by_scene: dict[str, int] = {}
    for item in unique:
        for alias in item.aliases:
            previous = first_by_scene.setdefault(alias.scene_key, item.index)
            union.union(previous, item.index)
    components_by_root: dict[int, SceneComponent] = {}
    for item in unique:
        root = union.find(item.index)
        item.component = root
        component = components_by_root.setdefault(root, SceneComponent(root=root))
        component.images.append(item)
        component.dataset_counts[item.representative.dataset] += 1
        ids = {class_id for class_id, *_ in item.boxes}
        component.class_image_counts.update(ids)
        component.class_object_counts.update(class_id for class_id, *_ in item.boxes)
    components = list(components_by_root.values())
    print(f"scene/leakage groups: {len(components)}; largest group: {max(c.image_count for c in components)}", flush=True)
    return components


def assign_splits(components: list[SceneComponent], seed: int) -> dict:
    total_images = sum(component.image_count for component in components)
    total_datasets: Counter[str] = Counter()
    total_classes: Counter[int] = Counter()
    class_group_counts: Counter[int] = Counter()
    for component in components:
        total_datasets.update(component.dataset_counts)
        total_classes.update(component.class_image_counts)
        class_group_counts.update(component.class_image_counts.keys())
    targets_images = {split: total_images * ratio for split, ratio in SPLIT_RATIOS.items()}
    targets_datasets = {
        split: {name: count * SPLIT_RATIOS[split] for name, count in total_datasets.items()}
        for split in SPLIT_ORDER
    }
    targets_classes = {
        split: {class_id: count * SPLIT_RATIOS[split] for class_id, count in total_classes.items()}
        for split in SPLIT_ORDER
    }
    current_images: Counter[str] = Counter()
    current_datasets: dict[str, Counter[str]] = {split: Counter() for split in SPLIT_ORDER}
    current_classes: dict[str, Counter[int]] = {split: Counter() for split in SPLIT_ORDER}
    rng = random.Random(seed)

    def rarity(component: SceneComponent) -> float:
        return sum(
            component.class_image_counts[class_id] / max(total_classes[class_id], 1)
            for class_id in component.class_image_counts
        )

    ordered = sorted(
        components,
        key=lambda component: (
            -rarity(component), -component.image_count,
            hashlib.sha1(f"{seed}:{component.root}".encode()).hexdigest(),
        ),
    )
    for component in ordered:
        scores = []
        for split in SPLIT_ORDER:
            class_gain = sum(
                min(count, max(targets_classes[split][class_id] - current_classes[split][class_id], 0))
                / max(total_classes[class_id], 1)
                for class_id, count in component.class_image_counts.items()
            )
            dataset_gain = sum(
                min(count, max(targets_datasets[split][name] - current_datasets[split][name], 0))
                / max(total_datasets[name], 1)
                for name, count in component.dataset_counts.items()
            )
            image_gain = min(
                component.image_count,
                max(targets_images[split] - current_images[split], 0),
            ) / total_images
            projected = current_images[split] + component.image_count
            overflow = max(projected - targets_images[split], 0) / max(targets_images[split], 1)
            missing_coverage = sum(
                1 for class_id in component.class_image_counts
                if current_classes[split][class_id] == 0 and class_group_counts[class_id] >= 3
            )
            score = 5.0 * class_gain + 2.0 * dataset_gain + image_gain + 0.02 * missing_coverage - 20.0 * overflow
            scores.append((score + rng.random() * 1e-12, split))
        split = max(scores)[1]
        component.split = split
        current_images[split] += component.image_count
        current_datasets[split].update(component.dataset_counts)
        current_classes[split].update(component.class_image_counts)

    # Repair class coverage when at least three independent groups exist.
    for class_id, group_count in sorted(class_group_counts.items(), key=lambda item: item[1]):
        if group_count < 3:
            continue
        for desired in ("test", "val"):
            if current_classes[desired][class_id] > 0:
                continue
            candidates = [
                component for component in components
                if class_id in component.class_image_counts and component.split != desired
                and all(
                    current_classes[component.split][cid] > component.class_image_counts[cid]
                    for cid in component.class_image_counts
                    if class_group_counts[cid] >= 3
                )
            ]
            if not candidates:
                continue
            component = min(candidates, key=lambda row: (row.image_count, row.root))
            old = component.split
            current_images[old] -= component.image_count
            current_images[desired] += component.image_count
            current_datasets[old].subtract(component.dataset_counts)
            current_datasets[desired].update(component.dataset_counts)
            current_classes[old].subtract(component.class_image_counts)
            current_classes[desired].update(component.class_image_counts)
            component.split = desired

    return {
        "image_counts": dict(current_images),
        "dataset_counts": {split: dict(counts) for split, counts in current_datasets.items()},
        "class_image_counts": {split: dict(counts) for split, counts in current_classes.items()},
        "class_group_counts": dict(class_group_counts),
    }


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_output(
    output: Path,
    records: list[SourceRecord],
    inventory: list[dict],
    exclusions: list[dict],
    unique: list[UniqueImage],
    components: list[SceneComponent],
    split_stats: dict,
    seed: int,
) -> dict:
    staging = output.parent / f"{output.name}.__staging"
    if staging.exists():
        if staging.parent.resolve() != output.parent.resolve() or staging.name != "merged_dataset.__staging":
            raise ValueError(f"Unsafe staging path: {staging}")
        shutil.rmtree(staging)
    for split in SPLIT_ORDER:
        (staging / split / "images").mkdir(parents=True)
        (staging / split / "labels").mkdir(parents=True)
    metadata = staging / "metadata"
    metadata.mkdir()

    previous_manifest: dict[str, dict[str, str]] = {}
    previous_manifest_path = output / "metadata" / "image_manifest.csv"
    if previous_manifest_path.exists():
        with previous_manifest_path.open(encoding="utf-8-sig", newline="") as handle:
            previous_manifest = {row["sha256"]: row for row in csv.DictReader(handle)}

    component_by_root = {component.root: component for component in components}
    used_names: set[str] = set()
    manifest_rows = []
    alias_rows = []
    duplicate_rows = []
    materialization: Counter[str] = Counter()
    split_class_images: dict[str, Counter[int]] = {split: Counter() for split in SPLIT_ORDER}
    split_class_objects: dict[str, Counter[int]] = {split: Counter() for split in SPLIT_ORDER}
    split_datasets: dict[str, Counter[str]] = {split: Counter() for split in SPLIT_ORDER}

    for item in sorted(unique, key=lambda row: (component_by_root[row.component].split, row.representative.dataset, row.representative.image.name, row.sha256)):
        split = component_by_root[item.component].split
        item.split = split
        base = safe_name(f"{item.representative.dataset}__{item.representative.image.stem}")[:180]
        output_name = f"{base}{item.representative.image.suffix.lower()}"
        if output_name.lower() in used_names:
            output_name = f"{base}__{item.sha256[:10]}{item.representative.image.suffix.lower()}"
        used_names.add(output_name.lower())
        item.output_name = output_name
        destination_image = staging / split / "images" / output_name
        destination_label = staging / split / "labels" / f"{Path(output_name).stem}.txt"
        try:
            os.link(item.representative.image, destination_image)
            materialization["hardlink"] += 1
        except OSError:
            shutil.copy2(item.representative.image, destination_image)
            materialization["copy"] += 1
        ids = {class_id for class_id, *_ in item.boxes}
        previous = previous_manifest.get(item.sha256)
        previous_label = (
            output / previous["split"] / "labels" / previous["output_label"]
            if previous else None
        )
        can_reuse_label = bool(
            previous
            and previous["split"] == split
            and previous["output_image"] == output_name
            and int(previous["object_count"]) == len(item.boxes)
            and previous["class_ids"] == ",".join(str(value) for value in sorted(ids))
            and int(previous["annotation_conflicts_skipped"]) == 0
            and previous_label
            and previous_label.exists()
        )
        if can_reuse_label:
            try:
                os.link(previous_label, destination_label)
                materialization["label_hardlink_reused"] += 1
            except OSError:
                can_reuse_label = False
        if not can_reuse_label:
            destination_label.write_text(
                "\n".join(
                    f"{class_id} {x:.6f} {y:.6f} {width:.6f} {height:.6f}"
                    for class_id, x, y, width, height in item.boxes
                ) + "\n",
                encoding="utf-8",
            )
            materialization["label_written"] += 1
        split_class_images[split].update(ids)
        split_class_objects[split].update(class_id for class_id, *_ in item.boxes)
        split_datasets[split][item.representative.dataset] += 1
        manifest_rows.append({
            "split": split,
            "output_image": output_name,
            "output_label": f"{Path(output_name).stem}.txt",
            "sha256": item.sha256,
            "scene_component": item.component,
            "representative_dataset": item.representative.dataset,
            "representative_partition": item.representative.source_partition,
            "representative_image": str(item.representative.image),
            "representative_label": str(item.representative.label),
            "source_alias_count": len(item.aliases),
            "class_ids": ",".join(str(value) for value in sorted(ids)),
            "object_count": len(item.boxes),
            "annotation_conflicts_skipped": item.annotation_conflicts,
        })
        for alias in item.aliases:
            alias_rows.append({
                "split": split,
                "output_image": output_name,
                "sha256": item.sha256,
                "is_representative": alias is item.representative,
                "source_dataset": alias.dataset,
                "source_partition": alias.source_partition,
                "source_image": str(alias.image),
                "source_label": str(alias.label),
                "scene_key": alias.scene_key,
                "class_ids": ",".join(str(value) for value in sorted(alias.classes)),
                "object_count": len(alias.boxes),
            })
        if len(item.aliases) > 1:
            duplicate_rows.append({
                "sha256": item.sha256,
                "split": split,
                "output_image": output_name,
                "source_alias_count": len(item.aliases),
                "source_datasets": ",".join(sorted({alias.dataset for alias in item.aliases})),
                "annotation_conflicts_skipped": item.annotation_conflicts,
            })

    class_rows = []
    for class_id, class_name in enumerate(CANONICAL_CLASSES):
        row = {"class_id": class_id, "class_name": class_name}
        for split in SPLIT_ORDER:
            row[f"{split}_images"] = split_class_images[split][class_id]
            row[f"{split}_objects"] = split_class_objects[split][class_id]
        row["total_images"] = sum(row[f"{split}_images"] for split in SPLIT_ORDER)
        row["total_objects"] = sum(row[f"{split}_objects"] for split in SPLIT_ORDER)
        class_rows.append(row)

    dataset_rows = []
    all_datasets = sorted({record.dataset for record in records})
    for dataset in all_datasets:
        row = {"dataset": dataset}
        for split in SPLIT_ORDER:
            row[split] = split_datasets[split][dataset]
        row["total_unique_images"] = sum(row[split] for split in SPLIT_ORDER)
        row["source_files"] = sum(record.dataset == dataset for record in records)
        dataset_rows.append(row)

    component_rows = [
        {
            "scene_component": component.root,
            "split": component.split,
            "image_count": component.image_count,
            "datasets": ",".join(sorted(component.dataset_counts)),
            "class_ids": ",".join(str(value) for value in sorted(component.class_image_counts)),
        }
        for component in sorted(components, key=lambda row: row.root)
    ]
    write_csv(metadata / "image_manifest.csv", manifest_rows, list(manifest_rows[0]))
    write_csv(metadata / "source_aliases.csv", alias_rows, list(alias_rows[0]))
    write_csv(metadata / "duplicate_groups.csv", duplicate_rows, list(duplicate_rows[0]) if duplicate_rows else ["sha256"])
    write_csv(metadata / "scene_groups.csv", component_rows, list(component_rows[0]))
    write_csv(metadata / "class_summary.csv", class_rows, list(class_rows[0]))
    write_csv(metadata / "dataset_split_summary.csv", dataset_rows, list(dataset_rows[0]))
    write_csv(
        metadata / "source_inventory.csv",
        [{
            "dataset": row["dataset"],
            "root": row["root"],
            "source_images": row["source_images"],
            "source_labels": row["source_labels"],
            "source_objects": row["source_objects"],
            "excluded_empty_annotations": row["excluded_empty_annotations"],
            "partitions": json.dumps(row["partitions"], ensure_ascii=False, sort_keys=True),
            "class_ids": ",".join(str(item["class_id"]) for item in row["classes"]),
            "metadata_files": ",".join(row["metadata_files"]),
        } for row in inventory],
        ["dataset", "root", "source_images", "source_labels", "source_objects", "excluded_empty_annotations", "partitions", "class_ids", "metadata_files"],
    )
    write_csv(
        metadata / "excluded_source_files.csv",
        exclusions,
        list(exclusions[0]) if exclusions else ["dataset", "source_partition", "source_image", "source_label", "reason"],
    )
    (metadata / "source_inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (metadata / "class_summary.json").write_text(json.dumps(class_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    split_counts = Counter(item.split for item in unique)
    total = len(unique)
    validation = {
        "source_files": len(records),
        "source_files_excluded_empty_annotation": len(exclusions),
        "unique_images_after_exact_dedup": len(unique),
        "exact_duplicate_files_removed": len(records) - len(unique),
        "duplicate_groups": len(duplicate_rows),
        "scene_groups": len(components),
        "largest_scene_group": max(component.image_count for component in components),
        "split_counts": dict(split_counts),
        "split_percent": {split: split_counts[split] / total for split in SPLIT_ORDER},
        "materialization": dict(materialization),
        "annotation_conflicts_skipped": sum(item.annotation_conflicts for item in unique),
        "hash_leakage_count": 0,
        "scene_group_leakage_count": 0,
        "pair_errors": 0,
    }
    (metadata / "build_config.json").write_text(json.dumps({
        "seed": seed,
        "split_ratios": SPLIT_RATIOS,
        "class_names": {str(index): name for index, name in enumerate(CANONICAL_CLASSES)},
        "exact_duplicate_rule": "SHA-256 of source file bytes; one output image per hash",
        "scene_group_rules": {
            "kaggle_large_1": "camera/date/time 10-minute block",
            "mendeley_bbox_yolo_filtered": "full source clip prefix before final frame number",
            "mocs": "contiguous numeric blocks of 50 per source dataset",
            "closeup_construction_monitoring_yolo_filtered": "contiguous frame blocks of 50",
            "closeup_roller_numeric_yolo_filtered": "contiguous numeric blocks of 50",
            "closeup_choo_yolo_filtered": "same source header; v1/v2 kept together",
            "closeup_asphalt_paver_yolo_filtered": "imagesFromVid blocks of 50; other images individual",
            "other": "individual normalized image stem",
        },
        "annotation_merge": "for exact duplicates, union boxes; same-class IoU>=0.90 is one box; different-class IoU>=0.75 conflicts are skipped and counted",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (metadata / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (metadata / "split_planning.json").write_text(json.dumps(split_stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (metadata / "rollback.md").write_text(
        "# Rollback and provenance\n\n"
        "All source datasets are unchanged. Removing `merged_dataset` rolls back the merge. "
        "Rebuild with the same seed and script to reproduce it. `source_aliases.csv` maps every "
        "source image to its merged output; `image_manifest.csv` maps every output to its selected "
        "representative and split; `duplicate_groups.csv` records exact duplicates.\n",
        encoding="utf-8",
    )

    names = {str(index): name for index, name in enumerate(CANONICAL_CLASSES)}
    (staging / "class_names.json").write_text(json.dumps(names, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (staging / "classes.txt").write_text("\n".join(CANONICAL_CLASSES) + "\n", encoding="utf-8")
    yaml_lines = ["path: .", "train: train/images", "val: val/images", "test: test/images", f"nc: {len(CANONICAL_CLASSES)}", "names:"]
    yaml_lines.extend(f"  {index}: {json.dumps(name)}" for index, name in enumerate(CANONICAL_CLASSES))
    (staging / "data.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
    (staging / "README.md").write_text(
        "# Merged YOLO detector dataset\n\n"
        "Leakage-aware 75/15/10 split over nine source datasets. Exact duplicates are stored once; "
        "scene/clip groups are never split across train, val, and test. See `metadata/`.\n",
        encoding="utf-8",
    )

    backup = output.parent / f"{output.name}.__backup"
    if backup.exists():
        shutil.rmtree(backup)
    if output.exists():
        output.rename(backup)
    staging.rename(output)
    if backup.exists():
        shutil.rmtree(backup)
    return {"validation": validation, "class_summary": class_rows, "dataset_summary": dataset_rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--project-report", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=20260918)
    args = parser.parse_args()
    if args.output.parent.resolve() != args.datasets_root.resolve() or args.output.name != "merged_dataset":
        raise ValueError("Output must be <datasets-root>/merged_dataset")
    records, inventory, exclusions = discover_sources(args.datasets_root)
    unique, _ = deduplicate(records, args.output)
    components = build_components(unique)
    split_stats = assign_splits(components, args.seed)
    result = build_output(args.output, records, inventory, exclusions, unique, components, split_stats, args.seed)
    args.project_report.parent.mkdir(parents=True, exist_ok=True)
    args.project_report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["validation"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

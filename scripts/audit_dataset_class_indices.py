from __future__ import annotations

import argparse
import json
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


DATASETS = {
    "kaggle_large_1": {
        "relative_label_dirs": ["train/labels", "valid/labels"],
        "expected_ids": list(range(17)),
    },
    "mendeley_bbox_yolo_filtered": {
        "relative_label_dirs": ["labels"],
        "expected_ids": [0, 1, 6, 10, 16, 19],
    },
    "mocs_instances_val_yolo_filtered": {
        "relative_label_dirs": ["labels"],
        "expected_ids": [1, 3, 7, 8, 10, 12, 16, 17, 19, 20],
    },
    "mocs_instances_train_yolo_filtered": {
        "relative_label_dirs": ["labels"],
        "expected_ids": [1, 3, 7, 8, 10, 12, 16, 17, 19, 20],
    },
}


def load_class_names(dataset: Path) -> tuple[dict[int, str] | None, list[str]]:
    issues: list[str] = []
    metadata = dataset / "class_names.json"
    if not metadata.exists():
        return None, ["class_names.json missing"]
    try:
        raw = json.loads(metadata.read_text(encoding="utf-8-sig"))
        names = {int(key): str(value) for key, value in raw.items()}
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        return None, [f"class_names.json invalid: {error}"]
    expected = {index: name for index, name in enumerate(CANONICAL_CLASSES)}
    if names != expected:
        missing = sorted(set(expected) - set(names))
        extra = sorted(set(names) - set(expected))
        wrong = sorted(index for index in set(names) & set(expected) if names[index] != expected[index])
        if missing:
            issues.append(f"class_names.json missing IDs: {missing}")
        if extra:
            issues.append(f"class_names.json extra IDs: {extra}")
        if wrong:
            issues.append(f"class_names.json wrong names at IDs: {wrong}")
    return names, issues


def scan_dataset(root: Path, name: str, config: dict) -> dict:
    dataset = root / name
    object_counts: Counter[int] = Counter()
    image_ids: dict[int, set[str]] = defaultdict(set)
    malformed: list[str] = []
    label_count = 0
    empty_label_count = 0

    jobs: list[tuple[str, Path]] = []
    for relative in config["relative_label_dirs"]:
        label_dir = dataset / relative
        if not label_dir.exists():
            malformed.append(f"missing label directory: {relative}")
            continue
        split = relative.split("/")[0] if "/" in relative else "all"
        jobs.extend((split, label_path) for label_path in label_dir.glob("*.txt"))

    def read_label(job: tuple[str, Path]) -> tuple[str, Path, list[int], list[str], bool]:
        split, label_path = job
        ids: list[int] = []
        errors: list[str] = []
        populated = False
        for line_no, line in enumerate(label_path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            populated = True
            parts = line.split()
            if len(parts) != 5:
                errors.append(f"{label_path}:{line_no}: expected 5 fields")
                continue
            try:
                class_id = int(parts[0])
                coords = [float(value) for value in parts[1:]]
            except ValueError:
                errors.append(f"{label_path}:{line_no}: parse error")
                continue
            if class_id < 0 or class_id >= len(CANONICAL_CLASSES):
                errors.append(f"{label_path}:{line_no}: class ID {class_id} outside 0..25")
                continue
            if any(value < 0.0 or value > 1.0 for value in coords):
                errors.append(f"{label_path}:{line_no}: coordinate outside [0,1]")
                continue
            if coords[2] <= 0.0 or coords[3] <= 0.0:
                errors.append(f"{label_path}:{line_no}: non-positive box size")
                continue
            ids.append(class_id)
        return split, label_path, ids, errors, populated

    label_count = len(jobs)
    with ThreadPoolExecutor(max_workers=32) as executor:
        for split, label_path, ids, errors, populated in executor.map(read_label, jobs):
            malformed.extend(errors)
            if not populated:
                empty_label_count += 1
            for class_id in ids:
                object_counts[class_id] += 1
                image_ids[class_id].add(f"{split}/{label_path.stem}")

    actual_ids = sorted(object_counts)
    expected_ids = config["expected_ids"]
    names, metadata_issues = load_class_names(dataset)
    id_rows = [
        {
            "class_id": class_id,
            "class_name": CANONICAL_CLASSES[class_id],
            "image_count": len(image_ids[class_id]),
            "object_count": object_counts[class_id],
        }
        for class_id in actual_ids
    ]
    return {
        "dataset": name,
        "path": str(dataset),
        "label_count": label_count,
        "empty_label_count": empty_label_count,
        "object_count": sum(object_counts.values()),
        "actual_ids": actual_ids,
        "expected_ids": expected_ids,
        "actual_ids_match_expected": actual_ids == expected_ids,
        "class_names_present": names is not None,
        "class_names_match_canonical_0_25": names == {
            index: class_name for index, class_name in enumerate(CANONICAL_CLASSES)
        },
        "metadata_issues": metadata_issues,
        "malformed_count": len(malformed),
        "malformed_examples": malformed[:20],
        "classes": id_rows,
    }


def build_markdown(results: list[dict]) -> str:
    lines = [
        "# Аудит индексов классов четырёх YOLO-датасетов",
        "",
        "Единая целевая таксономия содержит 26 ID (0–25). `kaggle_large_1` использует исходное подмножество 0–16; остальные наборы были конвертированы в те же глобальные ID без уплотнения.",
        "",
        "| Датасет | ID в разметке | Ожидаемые ID | Совпали | class_names | Ошибки YOLO |",
        "|---|---|---|---:|---:|---:|",
    ]
    for result in results:
        actual = ", ".join(map(str, result["actual_ids"]))
        expected = ", ".join(map(str, result["expected_ids"]))
        lines.append(
            f"| {result['dataset']} | {actual} | {expected} | "
            f"{'да' if result['actual_ids_match_expected'] else 'НЕТ'} | "
            f"{'есть' if result['class_names_present'] else 'нет'} | {result['malformed_count']} |"
        )
    lines.extend(["", "## Канонический словарь", "", "| ID | Класс |", "|---:|---|"])
    lines.extend(f"| {index} | {name} |" for index, name in enumerate(CANONICAL_CLASSES))
    lines.extend(["", "## Фактически представленные классы", ""])
    for result in results:
        lines.extend(
            [
                f"### {result['dataset']}",
                "",
                "| ID | Класс | Изображения | Объекты |",
                "|---:|---|---:|---:|",
            ]
        )
        lines.extend(
            f"| {row['class_id']} | {row['class_name']} | {row['image_count']} | {row['object_count']} |"
            for row in result["classes"]
        )
        if result["metadata_issues"]:
            lines.extend(["", "Метаданные: " + "; ".join(result["metadata_issues"]) + "."])
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    results = [scan_dataset(args.datasets_root, name, config) for name, config in DATASETS.items()]
    payload = {
        "canonical_classes": {str(index): name for index, name in enumerate(CANONICAL_CLASSES)},
        "datasets": results,
        "all_existing_metadata_match": all(
            result["class_names_match_canonical_0_25"]
            for result in results
            if result["class_names_present"]
        ),
        "all_label_ids_match_expected": all(result["actual_ids_match_expected"] for result in results),
        "all_yolo_rows_valid": all(result["malformed_count"] == 0 for result in results),
    }
    (args.output / "class_index_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "class_index_audit.md").write_text(
        build_markdown(results) + "\n", encoding="utf-8"
    )
    (args.output / "kaggle_large_1_class_names.json").write_text(
        json.dumps(payload["canonical_classes"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

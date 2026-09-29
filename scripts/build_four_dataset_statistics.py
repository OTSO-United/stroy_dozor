from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
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

REQUIRED_CLASS_IDS = {0, 1, 3, 4, 8, 10, 12, 16}
PRIORITY_CLASS_IDS = {3, 10, 17, 18, 19, 20, 21}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def report_dataset(name: str, label: str, report: dict, path: str) -> dict:
    classes = {
        int(row["class_id"]): {
            "images": int(row["image_count"]),
            "objects": int(row["object_count"]),
        }
        for row in report["classes"]
    }
    return {
        "dataset": name,
        "label": label,
        "path": path,
        "image_count": int(report["retained_image_count"]),
        "label_count": int(report["retained_image_count"]),
        "object_count": int(report["retained_object_count"]),
        "classes": classes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--class-statistics", required=True, type=Path)
    parser.add_argument("--work-mapping", required=True, type=Path)
    parser.add_argument("--conversion-summary", required=True, type=Path)
    parser.add_argument("--mocs-train-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    class_stats_rows = read_csv(args.class_statistics)
    class_stats = {int(row["class_id"]): row for row in class_stats_rows}
    work_mapping = json.loads(args.work_mapping.read_text(encoding="utf-8"))
    conversion_summary = json.loads(args.conversion_summary.read_text(encoding="utf-8"))
    mocs_train_report = json.loads(args.mocs_train_report.read_text(encoding="utf-8"))

    kaggle_classes = {}
    for class_id, row in class_stats.items():
        image_count = int(row["train_image_count"]) + int(row["valid_image_count"])
        object_count = int(row["train_object_count"]) + int(row["valid_object_count"])
        if image_count:
            kaggle_classes[class_id] = {"images": image_count, "objects": object_count}

    datasets = [
        {
            "dataset": "kaggle_large_1",
            "label": "Kaggle large 1",
            "path": "E:/Projects/LCT_26/datasets/kaggle_large_1",
            "image_count": 19982,
            "label_count": 19982,
            "object_count": sum(row["objects"] for row in kaggle_classes.values()),
            "classes": kaggle_classes,
        },
        report_dataset(
            "mendeley",
            "Mendeley filtered",
            conversion_summary["mendeley"],
            "E:/Projects/LCT_26/datasets/mendeley_bbox_yolo_filtered",
        ),
        report_dataset(
            "mocs_val",
            "MOCS validation filtered",
            conversion_summary["mocs"],
            "E:/Projects/LCT_26/datasets/mocs_instances_val_yolo_filtered",
        ),
        report_dataset(
            "mocs_train",
            "MOCS train filtered",
            mocs_train_report,
            "E:/Projects/LCT_26/datasets/mocs_instances_train_yolo_filtered",
        ),
    ]

    mappings = work_mapping["mappings"]
    total_work_rows = len(mappings)
    primary_rows = [row for row in mappings if row["primary_class_ids"]]
    episodic_rows = [row for row in mappings if row["episodic_class_ids"]]
    total_primary_assignments = sum(len(row["primary_class_ids"]) for row in mappings)
    total_episodic_assignments = sum(len(row["episodic_class_ids"]) for row in mappings)

    def coverage(class_ids: set[int]) -> dict:
        primary_covered = sum(
            bool(class_ids.intersection(row["primary_class_ids"])) for row in mappings
        )
        episodic_covered = sum(
            bool(class_ids.intersection(row["episodic_class_ids"])) for row in mappings
        )
        primary_assignments = sum(
            len(class_ids.intersection(row["primary_class_ids"])) for row in mappings
        )
        episodic_assignments = sum(
            len(class_ids.intersection(row["episodic_class_ids"])) for row in mappings
        )
        return {
            "primary_work_rows_covered": primary_covered,
            "primary_work_share_all": primary_covered / total_work_rows,
            "primary_work_share_relevant": primary_covered / len(primary_rows),
            "episodic_work_rows_covered": episodic_covered,
            "episodic_work_share_all": episodic_covered / total_work_rows,
            "episodic_work_share_relevant": episodic_covered / len(episodic_rows),
            "primary_assignment_share": primary_assignments / total_primary_assignments,
            "episodic_assignment_share": episodic_assignments / total_episodic_assignments,
            "required_classes_covered": len(class_ids & REQUIRED_CLASS_IDS),
            "priority_classes_covered": len(class_ids & PRIORITY_CLASS_IDS),
        }

    dataset_rows = []
    for dataset in datasets:
        class_ids = set(dataset["classes"])
        dataset.update(coverage(class_ids))
        dataset["class_count"] = len(class_ids)
        dataset_rows.append(
            {
                "dataset": dataset["dataset"],
                "label": dataset["label"],
                "path": dataset["path"],
                "image_count": dataset["image_count"],
                "label_count": dataset["label_count"],
                "object_count": dataset["object_count"],
                "class_count": dataset["class_count"],
                "primary_work_rows_covered": dataset["primary_work_rows_covered"],
                "primary_work_share_all": dataset["primary_work_share_all"],
                "episodic_work_rows_covered": dataset["episodic_work_rows_covered"],
                "episodic_work_share_all": dataset["episodic_work_share_all"],
                "primary_assignment_share": dataset["primary_assignment_share"],
                "episodic_assignment_share": dataset["episodic_assignment_share"],
                "required_classes_covered": dataset["required_classes_covered"],
                "required_classes_total": len(REQUIRED_CLASS_IDS),
                "priority_classes_covered": dataset["priority_classes_covered"],
                "priority_classes_total": len(PRIORITY_CLASS_IDS),
            }
        )

    union_classes = set().union(*(set(dataset["classes"]) for dataset in datasets))
    portfolio = {
        "dataset": "portfolio_union",
        "label": "All four datasets",
        "path": "E:/Projects/LCT_26/datasets",
        "image_count": sum(dataset["image_count"] for dataset in datasets),
        "label_count": sum(dataset["label_count"] for dataset in datasets),
        "object_count": sum(dataset["object_count"] for dataset in datasets),
        "class_count": len(union_classes),
        **coverage(union_classes),
    }
    dataset_rows.append(
        {
            "dataset": portfolio["dataset"],
            "label": portfolio["label"],
            "path": portfolio["path"],
            "image_count": portfolio["image_count"],
            "label_count": portfolio["label_count"],
            "object_count": portfolio["object_count"],
            "class_count": portfolio["class_count"],
            "primary_work_rows_covered": portfolio["primary_work_rows_covered"],
            "primary_work_share_all": portfolio["primary_work_share_all"],
            "episodic_work_rows_covered": portfolio["episodic_work_rows_covered"],
            "episodic_work_share_all": portfolio["episodic_work_share_all"],
            "primary_assignment_share": portfolio["primary_assignment_share"],
            "episodic_assignment_share": portfolio["episodic_assignment_share"],
            "required_classes_covered": portfolio["required_classes_covered"],
            "required_classes_total": len(REQUIRED_CLASS_IDS),
            "priority_classes_covered": portfolio["priority_classes_covered"],
            "priority_classes_total": len(PRIORITY_CLASS_IDS),
        }
    )

    class_rows = []
    for class_id, class_name in enumerate(CANONICAL_CLASSES):
        row = {
            "class_id": class_id,
            "class_name": class_name,
            "work_primary_count": int(class_stats[class_id]["work_primary_count"]),
            "work_primary_share": float(class_stats[class_id]["work_primary_share"]),
            "work_episodic_count": int(class_stats[class_id]["work_episodic_count"]),
            "work_episodic_share": float(class_stats[class_id]["work_episodic_share"]),
            "required_class": class_id in REQUIRED_CLASS_IDS,
            "priority_class": class_id in PRIORITY_CLASS_IDS,
        }
        combined_images = 0
        combined_objects = 0
        dataset_count = 0
        for dataset in datasets:
            counts = dataset["classes"].get(class_id, {"images": 0, "objects": 0})
            row[f"{dataset['dataset']}_images"] = counts["images"]
            row[f"{dataset['dataset']}_objects"] = counts["objects"]
            combined_images += counts["images"]
            combined_objects += counts["objects"]
            dataset_count += counts["images"] > 0
        row["combined_image_occurrences"] = combined_images
        row["combined_objects"] = combined_objects
        row["datasets_with_class"] = dataset_count
        row["coverage_status"] = (
            "absent"
            if combined_images == 0
            else "low_under_100_images"
            if combined_images < 100
            else "present"
        )
        class_rows.append(row)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "dataset_summary_4.csv", dataset_rows)
    write_csv(args.output_dir / "class_coverage_4_datasets.csv", class_rows)
    write_csv(
        args.output_dir / "mocs_train_raw_classes.csv",
        mocs_train_report["raw_class_decisions"],
    )

    result = {
        "definitions": {
            "work_item_count": total_work_rows,
            "primary_work_share_all": "Share of all 377 work-catalog rows having at least one primary class represented in the dataset.",
            "episodic_work_share_all": "Share of all 377 work-catalog rows having at least one episodic class represented in the dataset.",
            "assignment_share": "Share of individual class-to-work assignments covered; a work row can contain several classes.",
            "combined_image_occurrences": "Sum of per-dataset image counts by class; not cross-dataset deduplicated.",
        },
        "required_class_ids": sorted(REQUIRED_CLASS_IDS),
        "priority_class_ids": sorted(PRIORITY_CLASS_IDS),
        "datasets": datasets,
        "portfolio": portfolio,
        "classes": class_rows,
    }
    (args.output_dir / "dataset_portfolio_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    conversion_summary["mocs_train"] = {
        key: value for key, value in mocs_train_report.items() if key != "verification"
    }
    conversion_summary.setdefault("verification", {})["mocs_train"] = mocs_train_report[
        "verification"
    ]
    args.conversion_summary.write_text(
        json.dumps(conversion_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"datasets": dataset_rows, "portfolio": portfolio}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

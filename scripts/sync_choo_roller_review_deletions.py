from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sample_train_class_bboxes as renderer  # noqa: E402
from process_closeup_yolo_datasets import CANONICAL_CLASSES, IMAGE_EXTENSIONS  # noqa: E402


def read_yolo(path: Path) -> list[tuple[int, float, float, float, float]]:
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        parts = line.split()
        rows.append((int(parts[0]), *(float(value) for value in parts[1:])))
    return rows


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--review-root", required=True, type=Path)
    parser.add_argument("--audit-output", required=True, type=Path)
    args = parser.parse_args()

    if args.dataset.name != "closeup_choo_yolo_filtered":
        raise ValueError(f"Unexpected dataset: {args.dataset}")
    if args.review_root.name != "03_Roller":
        raise ValueError(f"Unexpected review root: {args.review_root}")

    manifest_path = args.review_root / "review_manifest.csv"
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        manifest = list(csv.DictReader(handle))
    if not manifest:
        raise RuntimeError("Review manifest is empty")
    fieldnames = list(manifest[0])
    annotated_names = {
        path.name for path in (args.review_root / "annotated").iterdir() if path.is_file()
    }
    removed_rows = [row for row in manifest if row["image_name"] not in annotated_names]
    retained_rows = [row for row in manifest if row["image_name"] in annotated_names]
    if not removed_rows:
        raise RuntimeError("No deleted annotated previews found; refusing a no-op synchronization")

    removed_objects: Counter[int] = Counter()
    validated_pairs: list[tuple[Path, Path]] = []
    for row in removed_rows:
        image = Path(row["dataset_image"])
        label = Path(row["dataset_label"])
        if image.parent != args.dataset / "images" or label.parent != args.dataset / "labels":
            raise ValueError(f"Manifest path escapes dataset: {row['image_name']}")
        if not image.exists() or not label.exists():
            raise FileNotFoundError(f"Missing dataset pair before synchronization: {image}, {label}")
        boxes = read_yolo(label)
        if not any(class_id == 3 for class_id, *_ in boxes):
            raise ValueError(f"Deleted preview is not a Roller image: {label}")
        removed_objects.update(class_id for class_id, *_ in boxes)
        validated_pairs.append((image, label))

    for image, label in validated_pairs:
        image.unlink()
        label.unlink()

    original_manifest = args.review_root / "review_manifest_before_user_cleanup.csv"
    if not original_manifest.exists():
        shutil.copy2(manifest_path, original_manifest)
    write_csv(args.review_root / "deleted_by_user.csv", removed_rows, fieldnames)
    write_csv(manifest_path, retained_rows, fieldnames)

    images_by_stem = {
        path.stem: path
        for path in (args.dataset / "images").iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    }
    labels = list((args.dataset / "labels").glob("*.txt"))
    class_images: Counter[int] = Counter()
    class_objects: Counter[int] = Counter()
    for label in labels:
        ids: set[int] = set()
        for class_id, *_ in read_yolo(label):
            class_objects[class_id] += 1
            ids.add(class_id)
        class_images.update(ids)
    if set(images_by_stem) != {path.stem for path in labels}:
        raise RuntimeError("Dataset image/label pairs diverged after synchronization")

    report_path = args.dataset / "conversion_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["retained_images"] = len(labels)
    report["retained_objects"] = sum(class_objects.values())
    report["annotation_types"] = {"bbox": len(labels)}
    report["materialization"] = {"hardlink_or_existing": len(labels)}
    report["classes"] = [
        {
            "class_id": class_id,
            "class_name": CANONICAL_CLASSES[class_id],
            "image_count": class_images[class_id],
            "object_count": class_objects[class_id],
        }
        for class_id in sorted(class_objects)
    ]
    report["quality_control"] = {
        "status": "completed_user_review",
        "reviewed_roller_images": len(manifest),
        "retained_roller_images": len(retained_rows),
        "deleted_images": len(removed_rows),
        "deleted_objects_by_class": dict(sorted(removed_objects.items())),
        "decision_source": "manual deletion from annotated preview folder by user",
        "deleted_manifest": str(args.review_root / "deleted_by_user.csv"),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    renderer.CLASS_NAMES = CANONICAL_CLASSES
    renderer.PALETTE = (renderer.PALETTE * 2)[: len(CANONICAL_CLASSES)]
    pages_dir = args.review_root / "contact_pages_20"
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages_dir.mkdir()
    annotated_paths = [args.review_root / "annotated" / row["image_name"] for row in retained_rows]
    for offset in range(0, len(annotated_paths), 20):
        renderer.make_contact_sheet(
            annotated_paths[offset : offset + 20],
            3,
            pages_dir / f"page_{offset // 20 + 1:03d}.jpg",
        )
    review_summary = {
        "class_id": 3,
        "class_name": "Roller",
        "reviewed_images": len(manifest),
        "retained_images": len(retained_rows),
        "deleted_images": len(removed_rows),
        "contact_pages": (len(retained_rows) + 19) // 20,
        "status": "completed_user_review",
    }
    (args.review_root / "review_summary.json").write_text(
        json.dumps(review_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    audit = {
        "dataset": str(args.dataset),
        "review_root": str(args.review_root),
        "review": review_summary,
        "removed_objects_by_class": dict(sorted(removed_objects.items())),
        "dataset_after_sync": {
            "images": len(images_by_stem),
            "labels": len(labels),
            "objects": sum(class_objects.values()),
            "classes": report["classes"],
        },
    }
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

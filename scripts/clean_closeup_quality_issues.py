from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from PIL import Image

from process_closeup_yolo_datasets import CANONICAL_CLASSES, IMAGE_EXTENSIONS, pixel_hash, roboflow_header


CHO0_FAILED_CLASSES = {0, 1, 8, 16}
CHO0_REJECTED_STEMS = {
    "closeup_choo__000912__v2",
    "closeup_choo__002139__v2",
}


def read_rows(path: Path) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append((int(line.split()[0]), line.strip()))
    return rows


def paired_image(root: Path, stem: str) -> Path:
    matches = [
        path for path in (root / "images").glob(f"{stem}.*")
        if path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one image for {root.name}/{stem}, found {len(matches)}")
    return matches[0]


def delete_pair(root: Path, label: Path) -> None:
    paired_image(root, label.stem).unlink()
    label.unlink()


def recalculate_report(root: Path, quality_control: dict, retained_raw_ids: set[int] | None = None) -> dict:
    report_path = root / "conversion_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    class_images: Counter[int] = Counter()
    class_objects: Counter[int] = Counter()
    labels = list((root / "labels").glob("*.txt"))
    for label in labels:
        ids: set[int] = set()
        for class_id, _ in read_rows(label):
            class_objects[class_id] += 1
            ids.add(class_id)
        class_images.update(ids)
    report["retained_images"] = len(labels)
    report["retained_objects"] = sum(class_objects.values())
    annotation_types = report.get("annotation_types", {})
    if len(annotation_types) == 1:
        report["annotation_types"] = {next(iter(annotation_types)): len(labels)}
    report["classes"] = [
        {
            "class_id": class_id,
            "class_name": CANONICAL_CLASSES[class_id],
            "image_count": class_images[class_id],
            "object_count": class_objects[class_id],
        }
        for class_id in sorted(class_objects)
    ]
    report["materialization"] = {"hardlink_or_existing": len(labels)}
    report["quality_control"] = quality_control
    if retained_raw_ids is not None:
        for row in report.get("raw_classes", []):
            raw_id = int(row["raw_id"])
            row["retained"] = raw_id in retained_raw_ids
            if raw_id not in retained_raw_ids:
                row["mapped_id"] = None
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def clean_choo(root: Path) -> tuple[dict, dict]:
    existing_report = json.loads((root / "conversion_report.json").read_text(encoding="utf-8"))
    existing_qc = existing_report.get("quality_control")
    roller_result = next(
        (row for row in (existing_qc or {}).get("audit_results", []) if row.get("class_id") == 3),
        {},
    )
    if existing_qc and existing_qc.get("seed") == 20260918 and str(roller_result.get("decision", "")).startswith("retained"):
        return existing_qc, recalculate_report(root, existing_qc, retained_raw_ids={0, 3})
    removed_images = 0
    removed_objects: Counter[int] = Counter()
    stripped_objects: Counter[int] = Counter()
    for label in sorted((root / "labels").glob("*.txt")):
        rows = read_rows(label)
        if label.stem in CHO0_REJECTED_STEMS:
            removed_objects.update(class_id for class_id, _ in rows)
            delete_pair(root, label)
            removed_images += 1
            continue
        kept = [(class_id, line) for class_id, line in rows if class_id not in CHO0_FAILED_CLASSES]
        stripped_objects.update(class_id for class_id, _ in rows if class_id in CHO0_FAILED_CLASSES)
        if not kept:
            delete_pair(root, label)
            removed_images += 1
            continue
        label.write_text("\n".join(line for _, line in kept) + "\n", encoding="utf-8")
    if existing_qc:
        removed_objects.update(
            {int(class_id): count for class_id, count in existing_qc.get("removed_objects_with_deleted_images", {}).items()}
        )
        stripped_objects.update(
            {int(class_id): count for class_id, count in existing_qc.get("removed_objects_from_retained_images", {}).items()}
        )
    qc = {
        "method": "deterministic visual audit of 100 random images per class; reject class when more than 10 images contain a wrong target class or a materially false target box",
        "seed": 20260918,
        "sample_size_per_class": 100,
        "audit_results": [
            {"class_id": 0, "class_name": "Dump truck", "confirmed_error_lower_bound": 11, "decision": "removed"},
            {"class_id": 1, "class_name": "Excavator", "confirmed_error_lower_bound": 11, "decision": "removed"},
            {"class_id": 3, "class_name": "Roller", "decision": "retained after user visual adjudication; two user-confirmed bad frames removed"},
            {"class_id": 6, "class_name": "Forklift Standart", "confirmed_errors": 0, "decision": "kept"},
            {"class_id": 8, "class_name": "Mixer", "confirmed_error_lower_bound": 11, "decision": "removed"},
            {"class_id": 16, "class_name": "Autocran", "confirmed_error_lower_bound": 11, "decision": "removed"},
        ],
        "explicitly_removed_bad_stems": sorted(CHO0_REJECTED_STEMS),
        "removed_images": removed_images + int((existing_qc or {}).get("removed_images", 0)),
        "removed_objects_with_deleted_images": dict(sorted(removed_objects.items())),
        "removed_objects_from_retained_images": dict(sorted(stripped_objects.items())),
    }
    report = recalculate_report(root, qc, retained_raw_ids={0, 3})
    report["mapping_notes"] = {
        "3": "forklift -> Forklift Standart; retained after 100-image QA",
        "0": "compactor -> Roller; retained after user visual adjudication, with two user-confirmed false frames removed",
        "1": "dump_truck rejected: more than 10 clear errors in 100-image audit",
        "2": "excavator rejected: more than 10 clear errors in 100-image audit",
        "4": "mixer_truck rejected: more than 10 clear errors in 100-image audit",
        "5": "mobile_crane rejected: more than 10 clear errors in 100-image audit",
    }
    (root / "conversion_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return qc, report


def clean_construction_monitoring(root: Path) -> tuple[dict, dict]:
    existing_report = json.loads((root / "conversion_report.json").read_text(encoding="utf-8"))
    existing_qc = existing_report.get("quality_control")
    if existing_qc and str(existing_qc.get("method", "")).startswith("user-directed removal"):
        return existing_qc, recalculate_report(
            root, existing_qc, retained_raw_ids={0, 9, 11, 13, 14, 20, 21, 22, 24, 26, 33, 35}
        )
    removed_images = 0
    removed_objects: Counter[int] = Counter()
    collateral_objects: Counter[int] = Counter()
    for label in sorted((root / "labels").glob("*.txt")):
        rows = read_rows(label)
        if any(class_id == 10 for class_id, _ in rows):
            removed_objects[10] += sum(class_id == 10 for class_id, _ in rows)
            collateral_objects.update(class_id for class_id, _ in rows if class_id != 10)
            delete_pair(root, label)
            removed_images += 1
    qc = {
        "method": "user-directed removal of every image containing class 10 Bulldozer because of excessive visual annotation errors",
        "removed_images": removed_images,
        "removed_bulldozer_objects": removed_objects[10],
        "collateral_objects_removed_with_images": dict(sorted(collateral_objects.items())),
    }
    report = recalculate_report(root, qc, retained_raw_ids={0, 9, 11, 13, 14, 20, 21, 22, 24, 26, 33, 35})
    notes = dict(report.get("mapping_notes", {}))
    notes["5"] = "Bulldozer rejected after visual QA; all images containing this class were removed"
    report["mapping_notes"] = notes
    (root / "conversion_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return qc, report


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def restore_numeric_labels(root: Path, source: Path) -> tuple[dict, dict]:
    source_by_header: dict[str, list[tuple[Path, Path]]] = {}
    for split in ("train", "valid", "test"):
        image_dir = source / split / "images"
        label_dir = source / split / "labels"
        if not image_dir.exists():
            continue
        for image in image_dir.iterdir():
            if image.is_file() and image.suffix.lower() in IMAGE_EXTENSIONS:
                source_by_header.setdefault(roboflow_header(image.stem), []).append(
                    (image, label_dir / f"{image.stem}.txt")
                )

    mapping = {0: 10, 1: 7, 2: 3}
    added_objects: Counter[int] = Counter()
    matched_by_hardlink = 0
    matched_by_unique_header = 0
    matched_by_bytes = 0
    matched_by_pixels = 0
    for output_image in sorted((root / "images").iterdir()):
        if not output_image.is_file() or output_image.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        prefix = "closeup_roller_numeric__"
        if not output_image.stem.startswith(prefix):
            raise RuntimeError(f"Unexpected numeric output name: {output_image.name}")
        header = output_image.stem[len(prefix):]
        candidates = source_by_header.get(header, [])
        if len(candidates) == 1:
            matches = candidates
            matched_by_unique_header += 1
        else:
            matches = [(image, label) for image, label in candidates if os.path.samefile(output_image, image)]
            if matches:
                matched_by_hardlink += 1
            else:
                output_hash = file_hash(output_image)
                matches = [
                    (image, label)
                    for image, label in candidates
                    if image.stat().st_size == output_image.stat().st_size and file_hash(image) == output_hash
                ]
                if matches:
                    matched_by_bytes += 1
        if not matches:
            output_pixels = pixel_hash(output_image)
            matches = [(image, label) for image, label in candidates if pixel_hash(image) == output_pixels]
            if matches:
                matched_by_pixels += 1
        if len(matches) != 1:
            raise RuntimeError(f"Could not uniquely match {output_image.name}: {len(matches)} matches")
        _, source_label = matches[0]
        converted: list[str] = []
        for raw_id, line in read_rows(source_label):
            parts = line.split()
            canonical_id = mapping[raw_id]
            converted.append(" ".join([str(canonical_id), *parts[1:]]))
            if canonical_id in {7, 10}:
                added_objects[canonical_id] += 1
        if not converted:
            raise RuntimeError(f"Matched source label is empty: {source_label}")
        (root / "labels" / f"{output_image.stem}.txt").write_text("\n".join(converted) + "\n", encoding="utf-8")
    qc = {
        "method": "restore existing source annotations on retained Roller images; no new boxes were invented",
        "source_raw_mapping": {"0": 10, "1": 7, "2": 3},
        "matched_by_hardlink_identity": matched_by_hardlink,
        "matched_by_unique_header": matched_by_unique_header,
        "matched_by_exact_file_hash": matched_by_bytes,
        "matched_by_decoded_pixel_hash": matched_by_pixels,
        "restored_objects_for_additional_classes": dict(sorted(added_objects.items())),
        "limitation": "Objects not annotated in the source remain unlabelled and require a separate manual annotation pass.",
    }
    report = recalculate_report(root, qc, retained_raw_ids={0, 1, 2})
    report["mapping_notes"] = {
        "0": "visually Bulldozer -> Bulldozer; restored only where the source already contains a box",
        "1": "visually Bucket loader Big -> Bucket loader Big; restored only where the source already contains a box",
        "2": "visually predominantly Roller -> Roller",
    }
    raw_to_mapped = {0: 10, 1: 7, 2: 3}
    for row in report.get("raw_classes", []):
        row["mapped_id"] = raw_to_mapped[int(row["raw_id"])]
    (root / "conversion_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return qc, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets-root", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    args = parser.parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=True)

    choo_qc, choo_report = clean_choo(args.datasets_root / "closeup_choo_yolo_filtered")
    monitoring_qc, monitoring_report = clean_construction_monitoring(
        args.datasets_root / "closeup_construction_monitoring_yolo_filtered"
    )
    numeric_qc, numeric_report = restore_numeric_labels(
        args.datasets_root / "closeup_roller_numeric_yolo_filtered",
        args.source_root / "train---- valid----.v1i.yolo26",
    )

    names = [
        "closeup_asphalt_paver_yolo_filtered",
        "closeup_construction_vehicle_detection_yolo_filtered",
        "closeup_construction_monitoring_yolo_filtered",
        "closeup_roller_numeric_yolo_filtered",
        "closeup_choo_yolo_filtered",
    ]
    reports = [
        json.loads((args.datasets_root / name / "conversion_report.json").read_text(encoding="utf-8"))
        for name in names
    ]
    summary = {
        "datasets": reports,
        "total_images": sum(report["retained_images"] for report in reports),
        "total_objects": sum(report["retained_objects"] for report in reports),
        "quality_revision": {
            "choo": choo_qc,
            "construction_monitoring": monitoring_qc,
            "roller_numeric": numeric_qc,
        },
    }
    (args.report_dir / "closeup_conversion_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.report_dir / "quality_cleanup_report.json").write_text(
        json.dumps(summary["quality_revision"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "choo": {"images": choo_report["retained_images"], "objects": choo_report["retained_objects"]},
        "construction_monitoring": {"images": monitoring_report["retained_images"], "objects": monitoring_report["retained_objects"]},
        "roller_numeric": {"images": numeric_report["retained_images"], "objects": numeric_report["retained_objects"]},
        "portfolio": {"images": summary["total_images"], "objects": summary["total_objects"]},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sample_train_class_bboxes as renderer  # noqa: E402


def read_yolo(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split()
        rows.append((int(parts[0]), *(float(value) for value in parts[1:])))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--threshold", type=int, default=100)
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260917)
    args = parser.parse_args()

    names_map = json.loads((args.dataset / "class_names.json").read_text(encoding="utf-8"))
    renderer.CLASS_NAMES = [names_map[str(index)] for index in range(len(names_map))]
    renderer.PALETTE = (renderer.PALETTE * 2)[: len(renderer.CLASS_NAMES)]
    report = json.loads((args.dataset / "conversion_report.json").read_text(encoding="utf-8"))
    eligible = {
        int(row["class_id"]): row
        for row in report["classes"]
        if int(row["image_count"]) > args.threshold
    }
    candidates: dict[int, list[str]] = defaultdict(list)
    manifest_path = args.dataset / "manifest.csv"
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                for value in row["class_ids"].split(","):
                    class_id = int(value)
                    if class_id in eligible:
                        candidates[class_id].append(row["output_image"])
    else:
        images_by_stem = {
            path.stem: path.name
            for path in (args.dataset / "images").iterdir()
            if path.is_file()
        }
        for label_path in (args.dataset / "labels").glob("*.txt"):
            image_name = images_by_stem.get(label_path.stem)
            if image_name is None:
                continue
            for class_id in {row[0] for row in read_yolo(label_path)}:
                if class_id in eligible:
                    candidates[class_id].append(image_name)

    args.output.mkdir(parents=True, exist_ok=True)
    summary = []
    for class_id in sorted(eligible):
        rng = random.Random(args.seed + class_id * 1009)
        selected = sorted(rng.sample(candidates[class_id], args.sample_size))
        class_dir = args.output / f"{class_id:02d}_{renderer.safe_name(renderer.CLASS_NAMES[class_id])}"
        annotated_dir = class_dir / "annotated"
        annotated_paths = []
        rows = []
        for index, image_name in enumerate(selected, 1):
            source_image = args.dataset / "images" / image_name
            label_path = args.dataset / "labels" / f"{Path(image_name).stem}.txt"
            output_image = annotated_dir / f"{index:02d}_{image_name}"
            width, height, target_count, all_count = renderer.render_annotated(
                source_image, read_yolo(label_path), class_id, output_image, 1600
            )
            annotated_paths.append(output_image)
            rows.append(
                {
                    "sequence": index,
                    "class_id": class_id,
                    "class_name": renderer.CLASS_NAMES[class_id],
                    "image_name": image_name,
                    "source_image": str(source_image),
                    "source_label": str(label_path),
                    "annotated_image": str(output_image),
                    "source_width": width,
                    "source_height": height,
                    "target_bbox_count": target_count,
                    "all_bbox_count": all_count,
                }
            )
        with (class_dir / "selection.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        renderer.make_contact_sheet(annotated_paths, class_id, class_dir / "contact_sheet.jpg")
        summary.append({**eligible[class_id], "sampled_images": len(rows), "folder": str(class_dir)})

    (args.output / "preview_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

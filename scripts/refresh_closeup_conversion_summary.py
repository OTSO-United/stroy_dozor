from __future__ import annotations

import argparse
import json
from pathlib import Path


DATASETS = [
    "closeup_asphalt_paver_yolo_filtered",
    "closeup_construction_vehicle_detection_yolo_filtered",
    "closeup_construction_monitoring_yolo_filtered",
    "closeup_roller_numeric_yolo_filtered",
    "closeup_choo_yolo_filtered",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    reports = [
        json.loads((args.datasets_root / name / "conversion_report.json").read_text(encoding="utf-8"))
        for name in DATASETS
    ]
    payload = {
        "datasets": reports,
        "total_images": sum(report["retained_images"] for report in reports),
        "total_objects": sum(report["retained_objects"] for report in reports),
        "aerial_work_platform_found": False,
        "notes": [
            "Construction Monitoring images containing Bulldozer were removed after user visual QA.",
            "Numeric Roller retained source-provided Bulldozer and Bucket loader Big boxes on Roller images.",
            "Choo Roller completed user review: 429 of 1,275 reviewed images were deleted via preview-manifest synchronization.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total_images": payload["total_images"], "total_objects": payload["total_objects"]}))


if __name__ == "__main__":
    main()

"""Sample 70 frames from each of three video folders and create YOLO pseudo-labels.

Video and image sources are read only. The model is the verified ONNX export of
the exact best_v2.pt checkpoint; labels retain its 20 raw class IDs.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from auto_label_construction import (
    MANIFEST as MODEL_MANIFEST,
    ONNX,
    PT,
    draw_overlay,
    predict,
    read_image,
    save_image,
    sha256,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(r"E:\Projects\LCT_26\video_download\downloads")
OUTPUT = ROOT / "datasets/labeled_video"
CONFIDENCE = 0.2
IOU = 0.7
CANDIDATE_POSITIONS = (0.2, 0.5, 0.8)

# Hold out the entire third camera, so one fixed camera scene cannot appear
# on both sides of the split. Val labels are still pseudo-labels until reviewed.
VIDEO_PLAN = [
    ("1", "2026-09-16_02-38_MRQlXXj8Spg_last_2h_1080p.mp4", 8, "train"),
    ("1", "2026-09-16_23-50-32_MRQlXXj8Spg_last_5h_1080p.mp4", 21, "train"),
    ("1", "2026-09-17_19-26-42_MRQlXXj8Spg_last_5h_1080p.mp4", 21, "train"),
    ("1", "2026-09-18_19-05-59_MRQlXXj8Spg_last_5h_1080p.mp4", 20, "train"),
    ("2", "2026-09-17_21-18-02_W_UF7ZGCeXY_last_4h_1080p.mp4", 35, "train"),
    ("2", "2026-09-18_01-12-24_W_UF7ZGCeXY_last_4h_1080p.mp4", 35, "train"),
    ("3", "2026-09-17_11-58-55_dmEq_ddk0kw_last_4h_1080p.mp4", 13, "val"),
    ("3", "2026-09-17_16-14-40_dmEq_ddk0kw_last_4h_1080p.mp4", 13, "val"),
    ("3", "2026-09-18_10-38-01_dmEq_ddk0kw_last_4h_1080p.mp4", 13, "val"),
    ("3", "2026-09-22_20-33-56_dmEq_ddk0kw_last_6h_1080p.mp4", 18, "val"),
    ("3", "2026-09-23_16-15-43_dmEq_ddk0kw_last_4h_1080p.mp4", 13, "val"),
]


def feature(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    crop = frame[height // 8 : height * 15 // 16, width // 12 : width * 11 // 12]
    return cv2.resize(crop, (160, 90)).astype(np.float32) / 255


def choose_frame(cap: cv2.VideoCapture, times: list[float], previous: list[np.ndarray]):
    candidates = []
    for seconds in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        feat = feature(frame)
        if previous:
            diversity = min(float(np.abs(feat - old).mean()) for old in previous[-5:])
        else:
            diversity = 0.0
        candidates.append((diversity, -abs(seconds - times[1]), seconds, frame, feat))
    if not candidates:
        raise ValueError(f"Could not decode near {times[1]:.1f}s")
    diversity, _, seconds, frame, feat = max(candidates, key=lambda item: item[:2])
    previous.append(feat)
    return seconds, frame, diversity


def save_contact_sheets(records: list[dict], names: dict) -> None:
    del names  # overlays already include class names
    for folder in ("1", "2", "3"):
        selected = [record for record in records if record["folder"] == folder]
        for half in (0, 1):
            sheet = np.full((5 * 196, 7 * 280, 3), 242, np.uint8)
            for position, record in enumerate(selected[half * 35 : (half + 1) * 35]):
                image = read_image(OUTPUT / "review/overlays" / record["image"])
                thumb = cv2.resize(image, (272, 160))
                row, column = divmod(position, 7)
                top, left = row * 196, column * 280
                sheet[top : top + 160, left : left + 272] = thumb
                caption = f"{record['image'][:13]} {record['source_seconds']/60:.0f}m"
                cv2.putText(sheet, caption, (left + 3, top + 180),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0, 0, 0), 1)
            save_image(OUTPUT / "review" / f"contact_{folder}_{half + 1}.jpg", sheet)


def main() -> None:
    model = json.loads(MODEL_MANIFEST.read_text(encoding="utf-8"))
    if sha256(PT) != model["source_sha256"] or sha256(ONNX) != model["sha256"]:
        raise ValueError("The PT or ONNX file differs from the verified manifest")
    names = model["class_names"]
    assert set(names) == {str(index) for index in range(20)}
    assert {folder: sum(quota for current, _, quota, _ in VIDEO_PLAN if current == folder)
            for folder in ("1", "2", "3")} == {"1": 70, "2": 70, "3": 70}

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(str(ONNX), sess_options=options,
                                   providers=["CPUExecutionProvider"])
    (OUTPUT / "review/overlays").mkdir(parents=True, exist_ok=True)
    counts = Counter()
    records = []
    video_info = []
    folder_number = Counter()
    for folder, filename, quota, split in VIDEO_PLAN:
        path = SOURCE / folder / filename
        if not path.is_file():
            raise FileNotFoundError(path)
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise ValueError(f"Cannot open {path}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps
        if not 1 <= fps <= 120 or duration < quota:
            raise ValueError(f"Unexpected FPS/duration: {path} {fps} {duration}")
        video_info.append({"folder": folder, "file": filename, "size_bytes": path.stat().st_size,
                           "duration_seconds": duration, "fps": fps, "quota": quota, "split": split})
        previous = []
        for index in range(quota):
            times = [duration * (index + fraction) / quota for fraction in CANDIDATE_POSITIONS]
            seconds, frame, diversity = choose_frame(cap, times, previous)
            folder_number[folder] += 1
            image_name = f"camera_{folder}_{folder_number[folder]:03d}.jpg"
            image_path = OUTPUT / "images" / split / image_name
            label_path = OUTPUT / "labels" / split / image_name.replace(".jpg", ".txt")
            image_path.parent.mkdir(parents=True, exist_ok=True)
            label_path.parent.mkdir(parents=True, exist_ok=True)
            save_image(image_path, frame)
            detections = predict(session, frame, confidence_threshold=CONFIDENCE)
            counts.update(item["class_id"] for item in detections)
            height, width = frame.shape[:2]
            lines = []
            for item in detections:
                x1, y1, x2, y2 = item["xyxy"]
                lines.append(f"{item['class_id']} {(x1+x2)/(2*width):.6f} "
                             f"{(y1+y2)/(2*height):.6f} {(x2-x1)/width:.6f} {(y2-y1)/height:.6f}")
            label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            save_image(OUTPUT / "review/overlays" / image_name,
                       draw_overlay(frame, detections, names))
            records.append({"image": image_name, "folder": folder, "split": split,
                            "source_video": f"{folder}/{filename}",
                            "source_seconds": round(seconds, 3),
                            "sample_bin": index + 1, "sample_bin_count": quota,
                            "diversity_score": round(diversity, 6),
                            "width": width, "height": height,
                            "image_sha256": sha256(image_path), "detections": detections})
            print(f"{folder} {folder_number[folder]:02d}/70 {split:5s} "
                  f"t={seconds/60:6.1f}m score={diversity:.4f} "
                  f"boxes={len(detections)}", flush=True)
        cap.release()

    yaml_lines = [
        "# Model-generated pseudo-labels; manual review required before accuracy claims.",
        "train: images/train", "val: images/val", "nc: 20", "names:",
    ]
    yaml_lines.extend(f"  {index}: '{names[str(index)]}'" for index in range(20))
    (OUTPUT / "data.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
    manifest = {
        "source_root": str(SOURCE),
        "checkpoint": str(PT), "checkpoint_sha256": model["source_sha256"],
        "inference_weights": str(ONNX), "inference_weights_sha256": model["sha256"],
        "inference": {"image_size": 960, "confidence": CONFIDENCE, "iou": IOU,
                      "max_detections": 300},
        "selection": {"per_folder": 70, "candidates_per_bin": 3,
                      "candidate_positions": list(CANDIDATE_POSITIONS),
                      "method": "uniform temporal bins; max pixel difference from previous five selected frames within each recording",
                      "excluded": "short Trim and raw clips overlapping full recordings",
                      "split_method": "camera folders 1+2 train, folder 3 val"},
        "videos": video_info,
        "class_counts": {str(index): counts[index] for index in range(20)},
        "records": records,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save_contact_sheets(records, names)
    print(json.dumps({"images": len(records), "boxes": sum(counts.values()),
                      "class_counts": dict(counts)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

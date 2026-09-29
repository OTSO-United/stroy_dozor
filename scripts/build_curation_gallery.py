"""Render before/after examples from the saved model and curated overlays."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


PROJECT = Path(__file__).resolve().parents[1]
EXAMPLES = {
    "labeled_src": [
        ("Screenshot_15.jpg", "Truck: 0 -> 12"),
        ("Screenshot_32.jpg", "Piling rig: 15 -> 18"),
        ("Screenshot_62.jpg", "Piling rig and small loader"),
        ("Screenshot_67.jpg", "Missing mobile crane"),
        ("Screenshot_79.jpg", "Small loaders; false truck"),
        ("Screenshot_98.jpg", "Separate excavators"),
    ],
    "labeled_video": [
        ("camera_1_051.jpg", "Dump truck: 9 -> 0"),
        ("camera_2_005.jpg", "Static platform removed"),
        ("camera_2_013.jpg", "Missing excavator added"),
        ("camera_2_021.jpg", "Excavator: class and box"),
        ("camera_3_005.jpg", "Missing tower crane added"),
        ("camera_3_041.jpg", "Whole tower crane"),
    ],
}


def read_image(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot read {path}")
    return image


def panel(image: np.ndarray, width: int, height: int, title: str) -> np.ndarray:
    canvas = np.full((height + 42, width, 3), 245, np.uint8)
    scale = min(width / image.shape[1], height / image.shape[0])
    resized = cv2.resize(image, (round(image.shape[1] * scale),
                                 round(image.shape[0] * scale)),
                         interpolation=cv2.INTER_AREA)
    x = (width - resized.shape[1]) // 2
    y = 42 + (height - resized.shape[0]) // 2
    canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    cv2.putText(canvas, title, (12, 29), cv2.FONT_HERSHEY_SIMPLEX,
                0.67, (20, 20, 20), 2, cv2.LINE_AA)
    return canvas


def main() -> None:
    for dataset, examples in EXAMPLES.items():
        root = PROJECT / "datasets" / dataset / "review"
        width = 640
        height = 430 if dataset == "labeled_src" else 360
        rows = []
        for name, note in examples:
            before = read_image(root / "overlays" / name)
            after = read_image(root / "curated_overlays" / name)
            left = panel(before, width, height, f"BEFORE | {name}")
            right = panel(after, width, height, f"AFTER | {note}")
            rows.append(np.concatenate([left, right], axis=1))
        gallery = np.concatenate(rows, axis=0)
        output = root / "corrected_gallery.jpg"
        ok, encoded = cv2.imencode(".jpg", gallery,
                                   [cv2.IMWRITE_JPEG_QUALITY, 87])
        if not ok:
            raise ValueError(f"Cannot encode {output}")
        encoded.tofile(str(output))
        print(f"{output}: {gallery.shape[1]}x{gallery.shape[0]}")


if __name__ == "__main__":
    main()

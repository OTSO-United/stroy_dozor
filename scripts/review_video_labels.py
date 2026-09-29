"""Render visually verified pseudo-label errors on top of saved model overlays.

The red rectangles are review notes. They do not modify YOLO labels.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1] / "datasets/labeled_video/review"
ISSUES = [
    {
        "frame": "camera_1_051.jpg",
        "kind": "wrong_class",
        "note": "Visible dump truck was predicted as class 9 Tanker (0.62), not class 0 Dump truck.",
        "regions": [[1260, 440, 1565, 580, "Dump truck -> Tanker"]],
    },
    {
        "frame": "camera_2_005.jpg",
        "kind": "false_positive",
        "note": "Fixed stair/platform structure at right was predicted as class 7 Bucket loader Big (0.21).",
        "regions": [[1550, 485, 1650, 630, "Platform -> loader"]],
    },
    {
        "frame": "camera_2_013.jpg",
        "kind": "missed_object",
        "note": "The label file is empty although an excavator is visible at lower left.",
        "regions": [[230, 715, 525, 990, "Missing excavator"]],
    },
    {
        "frame": "camera_2_022.jpg",
        "kind": "missed_object",
        "note": "The label file is empty although an excavator is visible behind the skip.",
        "regions": [[745, 515, 940, 650, "Missing excavator"]],
    },
    {
        "frame": "camera_3_005.jpg",
        "kind": "missed_object",
        "note": "The label file is empty although a large tower crane occupies the centre of the frame.",
        "regions": [[785, 0, 1220, 810, "Missing tower crane"]],
    },
    {
        "frame": "camera_3_001.jpg",
        "kind": "false_positive_and_partial_box",
        "note": "Cable-stayed bridge pylon was predicted as Autocran (0.32); the central tower crane is split into partial boxes.",
        "regions": [
            [605, 285, 720, 460, "Bridge -> Autocran"],
            [855, 0, 1120, 810, "Partial crane boxes"],
        ],
    },
]


def read_image(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot decode {path}")
    return image


def write_image(path: Path, image: np.ndarray) -> None:
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 93])
    if not ok:
        raise ValueError(f"Cannot encode {path}")
    data.tofile(str(path))


def main() -> None:
    output = ROOT / "issues"
    output.mkdir(parents=True, exist_ok=True)
    cards = []
    for issue in ISSUES:
        image = read_image(ROOT / "overlays" / issue["frame"])
        for x1, y1, x2, y2, label in issue["regions"]:
            cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 255), 5)
            label_y = max(35, min(image.shape[0] - 10, y1 + 35))
            cv2.putText(image, label, (x1 + 6, label_y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.72, (255, 255, 255), 5, cv2.LINE_AA)
            cv2.putText(image, label, (x1 + 6, label_y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.72, (0, 0, 255), 2, cv2.LINE_AA)
        name = f"issue_{issue['frame']}"
        issue["review_image"] = f"issues/{name}"
        write_image(output / name, image)
        card = np.full((475, 800, 3), 245, dtype=np.uint8)
        card[:450, :800] = cv2.resize(image, (800, 450))
        caption = f"{issue['frame']} | {issue['kind']}"
        cv2.putText(card, caption, (8, 467), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (20, 20, 20), 1, cv2.LINE_AA)
        cards.append(card)

    gallery = np.full((3 * 475, 2 * 800, 3), 245, dtype=np.uint8)
    for index, card in enumerate(cards):
        row, column = divmod(index, 2)
        gallery[row * 475 : (row + 1) * 475, column * 800 : (column + 1) * 800] = card
    write_image(ROOT / "issue_gallery.jpg", gallery)
    (ROOT / "issues.json").write_text(
        json.dumps(ISSUES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

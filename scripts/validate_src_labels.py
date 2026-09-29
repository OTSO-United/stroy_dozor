"""Validate the curated still-image YOLO dataset and source provenance."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from curate_src_labels import curate, format_yolo


root = Path(__file__).resolve().parents[1] / "datasets/labeled_src"
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
curation = json.loads((root / "review/curation.json").read_text(encoding="utf-8"))
assert len(manifest["records"]) == 100
assert curation["model_box_count"] == sum(
    len(record["detections"]) for record in manifest["records"]
)

counts = Counter()
hashes = {}
for record in manifest["records"]:
    name, split = record["image"], record["split"]
    image = root / "images" / split / name
    label = root / "labels" / split / f"{Path(name).stem}.txt"
    backup = root / "review/pseudo_labels" / split / label.name
    assert image.is_file() and label.is_file()
    assert hashlib.sha256(image.read_bytes()).hexdigest() == record["source_sha256"]
    assert backup.read_text(encoding="utf-8") == format_yolo(
        record["detections"], record["width"], record["height"]
    )
    assert label.read_text(encoding="utf-8") == format_yolo(
        curate(record)[0], record["width"], record["height"]
    )
    assert (root / "review/curated_overlays" / f"{Path(name).stem}.jpg").is_file()
    for line in label.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        assert len(fields) == 5
        class_id = int(fields[0])
        cx, cy, width, height = map(float, fields[1:])
        assert 0 <= class_id < 20
        assert 0 < width <= 1 and 0 < height <= 1
        assert -0.000001 <= cx - width / 2 and cx + width / 2 <= 1.000001
        assert -0.000001 <= cy - height / 2 and cy + height / 2 <= 1.000001
        counts[class_id] += 1
    gray = cv2.imdecode(np.fromfile(str(image), np.uint8), cv2.IMREAD_GRAYSCALE)
    assert gray.shape[:2] == (record["height"], record["width"])
    small = cv2.resize(gray, (32, 32)).astype(np.float32)
    dct = cv2.dct(small)[:8, :8]
    bits = (dct > np.median(dct[1:])).reshape(-1)
    hashes[name] = (split, bits)

assert len(list((root / "images/train").glob("*.png"))) == 86
assert len(list((root / "images/val").glob("*.png"))) == 14
assert len(list((root / "labels/train").glob("*.txt"))) == 86
assert len(list((root / "labels/val").glob("*.txt"))) == 14
assert all(counts[index] == curation["curated_class_counts"][str(index)]
           for index in range(20))
assert sum(counts.values()) == curation["curated_box_count"]
assert len(curation["changes"]) == curation["changed_frames"]

potential_cross_split_matches = []
for name_a, (split_a, hash_a) in hashes.items():
    if split_a != "train":
        continue
    for name_b, (split_b, hash_b) in hashes.items():
        if split_b != "val":
            continue
        distance = int(np.count_nonzero(hash_a != hash_b))
        if distance <= 8:
            potential_cross_split_matches.append((name_a, name_b, distance))

result = {
    "images_verified": 100,
    "labels_verified": 100,
    "boxes_verified": sum(counts.values()),
    "changed_frames": curation["changed_frames"],
    "class_counts": {str(index): counts[index] for index in range(20)},
    "phash_cross_split_pairs_hamming_le_8": sorted(
        potential_cross_split_matches, key=lambda row: row[2]
    ),
}
(root / "review/validation.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(result, ensure_ascii=False))

"""Check the video-derived YOLO dataset and summarize visual similarity."""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

from curate_video_labels import curate, format_yolo


root = Path(__file__).resolve().parents[1] / 'datasets/labeled_video'
manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
curation = json.loads((root / 'review/curation.json').read_text(encoding='utf-8'))
assert len(manifest['records']) == 210
assert len(manifest['videos']) == 11
assert curation['model_box_count'] == sum(len(record['detections']) for record in manifest['records'])
counts = Counter()
folder_counts = Counter()
split_counts = Counter()
times = defaultdict(list)
hashes = []
low_confidence = 0
empty_images = 0
empty_by_folder = Counter()
empty_frame_names = []
for record in manifest['records']:
    folder = record['folder']
    split = record['split']
    name = record['image']
    image = root / 'images' / split / name
    label = root / 'labels' / split / name.replace('.jpg', '.txt')
    assert image.is_file() and label.is_file()
    assert hashlib.sha256(image.read_bytes()).hexdigest() == record['image_sha256']
    lines = label.read_text(encoding='utf-8').splitlines()
    backup = root / 'review/pseudo_labels' / split / label.name
    assert backup.read_text(encoding='utf-8') == format_yolo(
        record['detections'], record['width'], record['height']
    )
    assert label.read_text(encoding='utf-8') == format_yolo(
        curate(record)[0], record['width'], record['height']
    )
    assert (root / 'review/curated_overlays' / name).is_file()
    if not lines:
        empty_images += 1
        empty_by_folder[folder] += 1
        empty_frame_names.append(name)
    folder_counts[folder] += 1
    split_counts[split] += 1
    times[record['source_video']].append(record['source_seconds'])
    for line in lines:
        fields = line.split()
        assert len(fields) == 5
        class_id = int(fields[0])
        cx, cy, width, height = map(float, fields[1:])
        assert 0 <= class_id < 20
        assert -0.000001 <= cx - width / 2 and cx + width / 2 <= 1.000001
        assert -0.000001 <= cy - height / 2 and cy + height / 2 <= 1.000001
        assert 0 < width <= 1 and 0 < height <= 1
        counts[class_id] += 1
    low_confidence += sum(
        0.2 <= detection['confidence'] < 0.35 for detection in record['detections']
    )
    if folder == '3':
        assert sum(line.startswith('16 ') for line in lines) == 1, name
    if folder == '2' and not record['detections']:
        assert any(line.startswith('1 ') for line in lines), name
    gray = cv2.imdecode(np.fromfile(str(image), np.uint8), cv2.IMREAD_GRAYSCALE)
    assert gray.shape[:2] == (record['height'], record['width'])
    thumb = cv2.resize(gray, (32, 32)).astype(np.float32)
    dct = cv2.dct(thumb)[:8, :8]
    bits = (dct > np.median(dct[1:])).reshape(-1)
    hashes.append((name, folder, split, bits))

assert folder_counts == Counter({'1': 70, '2': 70, '3': 70})
assert split_counts == Counter({'train': 140, 'val': 70})
assert len(list((root / 'images/train').glob('*.jpg'))) == 140
assert len(list((root / 'images/val').glob('*.jpg'))) == 70
assert len(list((root / 'labels/train').glob('*.txt'))) == 140
assert len(list((root / 'labels/val').glob('*.txt'))) == 70
assert all(counts[index] == curation['curated_class_counts'][str(index)] for index in range(20))
assert sum(counts.values()) == curation['curated_box_count']
assert len(curation['changes']) == curation['changed_frames']
assert len({item['frame'] for item in curation['changes']}) == curation['changed_frames']
assert counts[9] == 0
assert all(values == sorted(values) and len(set(values)) == len(values)
           for values in times.values())
near_pairs = []
cross_split_pairs = []
for index, (a_name, a_folder, a_split, a_bits) in enumerate(hashes):
    for b_name, b_folder, b_split, b_bits in hashes[index + 1:]:
        distance = int(np.count_nonzero(a_bits != b_bits))
        if distance <= 6:
            pair = [a_name, b_name, distance]
            near_pairs.append(pair)
            if a_split != b_split:
                cross_split_pairs.append(pair)

result = {
    'images_verified': 210,
    'labels_verified': 210,
    'boxes_verified': sum(counts.values()),
    'folder_counts': dict(folder_counts),
    'split_counts': dict(split_counts),
    'empty_label_images': empty_images,
    'empty_label_images_by_folder': dict(empty_by_folder),
    'empty_label_frame_names': empty_frame_names,
    'model_confidence_0_20_to_0_35': low_confidence,
    'class_counts': {str(index): counts[index] for index in range(20)},
    'diversity_score_min': min(record['diversity_score'] for record in manifest['records']
                               if record['sample_bin'] > 1),
    'diversity_score_median': float(np.median([
        record['diversity_score'] for record in manifest['records'] if record['sample_bin'] > 1
    ])),
    'phash_pairs_hamming_le_6_count': len(near_pairs),
    'phash_cross_split_pairs_hamming_le_6_count': len(cross_split_pairs),
    'phash_cross_split_pairs_first_20': cross_split_pairs[:20],
}
(root / 'review/validation.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
)
print(json.dumps(result, ensure_ascii=False))

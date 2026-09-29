"""Collect ordinary equipment photographs from Wikimedia Commons."""

import argparse
import csv
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "datasets" / "_equipment_example_candidates"
OUT = ROOT / "datasets" / "equipment_examples"
API = "https://commons.wikimedia.org/w/api.php"
HEAD = {"User-Agent": "StroyKonturEquipmentExamples/1.0 (research; local dataset)"}
CLASSES = [
    (0, "Dump truck", "dump truck construction site"),
    (1, "Excavator", "hydraulic excavator construction"),
    (2, "Motor grader", "motor grader"),
    (3, "Roller", "road roller compactor construction"),
    (4, "Crane manipulator", "knuckle boom crane truck"),
    (5, "Gazelle", "GAZ Gazelle van"),
    (6, "Forklift Standart", "forklift truck warehouse"),
    (7, "Bucket loader Big", "Komatsu WA500 wheel loader"),
    (8, "Mixer", "concrete mixer truck"),
    (9, "Tanker", "water tanker truck"),
    (10, "Bulldozer", "bulldozer construction site"),
    (11, "Cleaning equipment", "street sweeper truck"),
    (12, "Truck", "flatbed cargo truck"),
    (13, "Trailer", "low loader trailer heavy equipment"),
    (14, "Bucket loader Standart", "wheel loader construction"),
    (15, "Autocran", "mobile crane truck construction"),
    (16, "Tower crane", "tower crane construction site"),
    (17, "Asphalt paver", "asphalt paver road"),
    (18, "Piling rig", "piling rig construction"),
    (19, "Concrete pump truck", "concrete pump truck Schwing"),
]


def request(url):
    for attempt in range(6):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=HEAD), timeout=90
            ) as r:
                return r.read()
        except urllib.error.HTTPError as error:
            if error.code not in (429, 503) or attempt == 5:
                raise
            time.sleep(10 * (attempt + 1))


def clean(s):
    return html.unescape(re.sub(r"<[^>]*>", " ", s or "")).strip()


def discover():
    STAGE.mkdir(parents=True, exist_ok=True)
    saved = STAGE / "candidates.json"
    collected = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else {}
    only = {int(x) for x in os.environ.get("ONLY_CLASSES", "").split(",") if x}
    for cid, name, query in CLASSES:
        if only and cid not in only:
            continue
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": 40,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": 640,
            "format": "json",
            "formatversion": 2,
        }
        data = json.loads(request(API + "?" + urllib.parse.urlencode(params)))
        rows = []
        for page in data.get("query", {}).get("pages", []):
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata") or {}
            lic = clean((meta.get("LicenseShortName") or {}).get("value", ""))
            if (
                info.get("mime") != "image/jpeg"
                or info.get("width", 0) < 1000
                or info.get("height", 0) < 650
                or not info.get("thumburl")
            ):
                continue
            if not (lic.startswith("CC") or "public domain" in lic.lower()):
                continue
            rows.append(
                {
                    "id": f"{cid:02d}_{len(rows) + 1:02d}",
                    "class_id": cid,
                    "class_name": name,
                    "title": page["title"],
                    "page_url": info["descriptionurl"],
                    "original_url": info["url"],
                    "thumb_url": info["thumburl"],
                    "width": info["width"],
                    "height": info["height"],
                    "license": lic,
                    "license_url": clean(
                        (meta.get("LicenseUrl") or {}).get("value", "")
                    ),
                    "artist": clean((meta.get("Artist") or {}).get("value", "")),
                }
            )
            if len(rows) >= 12:
                break
        collected[str(cid)] = rows
        (STAGE / f"{cid:02d}").mkdir(exist_ok=True)
        sheet = np.full((810, 1400, 3), 245, np.uint8)
        for i, row in enumerate(rows):
            thumb = STAGE / f"{cid:02d}" / (row["id"] + ".jpg")
            if not thumb.exists():
                try:
                    thumb.write_bytes(request(row["thumb_url"]))
                except Exception as e:
                    print(f"thumb failed {row['id']}: {e}", flush=True)
                    continue
            image = cv2.imdecode(np.fromfile(str(thumb), np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                continue
            scale = min(350 / image.shape[1], 220 / image.shape[0])
            image = cv2.resize(
                image, (round(image.shape[1] * scale), round(image.shape[0] * scale))
            )
            y, x = divmod(i, 4)
            px = x * 350 + (350 - image.shape[1]) // 2
            py = y * 270 + (220 - image.shape[0]) // 2
            sheet[py : py + image.shape[0], px : px + image.shape[1]] = image
            cv2.putText(
                sheet,
                row["id"] + " " + row["title"][5:43],
                (x * 350 + 5, y * 270 + 246),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.43,
                (20, 20, 20),
                1,
                cv2.LINE_AA,
            )
        cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tofile(
            str(STAGE / f"contact_{cid:02d}.jpg")
        )
        (STAGE / "candidates.json").write_text(
            json.dumps(collected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        saved.write_text(
            json.dumps(collected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{cid:02d} {name}: {len(rows)} candidates", flush=True)
        time.sleep(0.25)
    (STAGE / "candidates.json").write_text(
        json.dumps(collected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def matches_candidate(image_path, candidate_path):
    image = cv2.imdecode(np.fromfile(str(image_path), np.uint8), cv2.IMREAD_COLOR)
    candidate = cv2.imdecode(
        np.fromfile(str(candidate_path), np.uint8), cv2.IMREAD_COLOR
    )
    if image is None or candidate is None:
        return False
    image = cv2.resize(image, (64, 64)).astype(np.float32)
    candidate = cv2.resize(candidate, (64, 64)).astype(np.float32)
    return float(np.corrcoef(image.flat, candidate.flat)[0, 1]) >= 0.9


def finalize():
    candidates = json.loads((STAGE / "candidates.json").read_text(encoding="utf-8"))
    choices = json.loads((STAGE / "choices.json").read_text(encoding="utf-8"))
    if set(choices) != {str(i) for i in range(20)}:
        raise ValueError("Need IDs 0..19")
    OUT.mkdir(parents=True, exist_ok=True)
    state_file = STAGE / "download_state.json"
    state = (
        json.loads(state_file.read_text(encoding="utf-8"))
        if state_file.exists()
        else {}
    )
    rows = []
    for cid, name, _ in CLASSES:
        selected = choices[str(cid)]
        if len(selected) != 2 or selected[0] == selected[1]:
            raise ValueError(f"Need two for {cid}")
        lookup = {r["id"]: r for r in candidates[str(cid)]}
        for ordinal, choice in enumerate(selected, 1):
            row = lookup[choice]
            safe = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
            filename = f"{cid:02d}_{safe}_{ordinal:02d}.jpg"
            target = OUT / filename
            if filename not in state and target.exists():
                state[filename] = {
                    "download_url": row["original_url"],
                    "rendition": "original",
                }
            candidate_path = STAGE / f"{cid:02d}" / f"{choice}.jpg"
            if not target.exists() or not matches_candidate(target, candidate_path):
                width = (
                    1920
                    if row["width"] >= 1920
                    else 1280
                    if row["width"] >= 1280
                    else 960
                )
                url = re.sub(r"/\d+px-", f"/{width}px-", row["thumb_url"])
                staged = target.with_suffix(".download")
                staged.write_bytes(request(url))
                if not matches_candidate(staged, candidate_path):
                    raise ValueError(
                        f"Downloaded photo does not match {row['page_url']}"
                    )
                staged.replace(target)
                state[filename] = {
                    "download_url": url,
                    "rendition": f"Commons {width}px thumbnail",
                }
                print(f"replaced mismatched source: {filename}", flush=True)
            state_file.write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            if not matches_candidate(target, candidate_path):
                raise ValueError(f"Source mismatch: {target}")
            image = cv2.imdecode(np.fromfile(str(target), np.uint8), cv2.IMREAD_COLOR)
            if image is None or image.shape[1] < 900 or image.shape[0] < 600:
                raise ValueError(f"Bad original: {target}")
            rows.append(
                {
                    "filename": filename,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "saved_width": image.shape[1],
                    "saved_height": image.shape[0],
                    **state[filename],
                    **row,
                }
            )
            print(f"saved {filename}: {image.shape[1]}x{image.shape[0]}", flush=True)
    with (OUT / "sources.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Finished {len(rows)} photographs in {OUT}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["discover", "finalize"])
    (discover if p.parse_args().command == "discover" else finalize)()

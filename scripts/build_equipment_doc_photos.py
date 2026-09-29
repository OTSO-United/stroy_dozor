"""Build documentation images and attribution from the current local examples."""

import csv
import hashlib
import html
import json
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "datasets/equipment_examples"
ASSETS = ROOT / "web/public/docs/equipment"
PROVENANCE = ROOT / "docs/data/documentation-photo-sources.json"
MANIFEST = ROOT / "models/yolo26m/manifest.json"


def build():
    model = json.loads(MANIFEST.read_text(encoding="utf-8"))
    with (SOURCE / "sources.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != len(model["class_map"]) * 2:
        raise ValueError("Need two examples for each model class")

    ASSETS.mkdir(parents=True, exist_ok=True)
    records = []
    labels = {}
    for row in rows:
        raw_id = int(row["class_id"])
        class_id = int(model["class_map"][str(raw_id)])
        ordinal = int(row["filename"].rsplit("_", 1)[1].split(".")[0])
        if ordinal not in (1, 2):
            raise ValueError(f"Unexpected example number: {row['filename']}")
        source = SOURCE / row["filename"]
        actual_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        provenance_verified = actual_sha == row["sha256"]

        asset = f"docs/equipment/{class_id}-{ordinal}.webp"
        output = ROOT / "web/public" / asset
        with Image.open(source) as photo:
            photo = ImageOps.exif_transpose(photo).convert("RGB")
            photo.thumbnail((960, 960), Image.Resampling.LANCZOS)
            photo.save(output, "WEBP", quality=78, method=6)

        records.append(
            {
                "class_id": class_id,
                "raw_class_id": raw_id,
                "example": ordinal,
                "source": f"datasets/equipment_examples/{row['filename']}",
                "source_sha256": actual_sha,
                "provenance_status": (
                    "verified" if provenance_verified else "local_unverified"
                ),
                "source_page": row["page_url"] if provenance_verified else "",
                "title": row["title"] if provenance_verified else row["filename"],
                "artist": row["artist"] if provenance_verified else "Не подтверждён",
                "license": row["license"] if provenance_verified else "Не установлена",
                "license_url": row["license_url"] if provenance_verified else "",
                "asset": asset,
                "asset_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        )
        labels[raw_id] = row["class_name"]

    if len({r["asset"] for r in records}) != len(records):
        raise ValueError("Duplicate documentation asset")
    PROVENANCE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    table_rows = []
    for row in records:
        source_href = html.escape(row["source_page"], quote=True)
        license_href = html.escape(row["license_url"] or row["source_page"], quote=True)
        title = html.escape(row["title"].removeprefix("File:"))
        artist = html.escape(row["artist"] or "Не указан на странице источника")
        license_name = html.escape(row["license"])
        label = html.escape(labels[row["raw_class_id"]])
        photo_cell = (
            f'<a href="{source_href}">{title}</a>'
            if source_href
            else f"{title} (локальный файл)"
        )
        license_cell = (
            f'<a href="{license_href}">{license_name}</a>'
            if row["license_url"]
            else license_name
        )
        table_rows.append(
            f"<tr><td>{label} · {row['example']}</td>"
            f"<td>{photo_cell}</td><td>{artist}</td><td>{license_cell}</td></tr>"
        )

    page = (
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Авторы и лицензии фотопримеров · СтройДозор</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:1200px;"
        "margin:32px auto;padding:0 20px;color:#20342b}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border-bottom:1px solid #d7e1db;padding:10px;text-align:left;"
        "vertical-align:top}a{color:#176447}th{background:#eef4ef}</style>"
        "<h1>Авторы и лицензии фотопримеров</h1>"
        "<p>Фотографии взяты из локальной папки equipment_examples и уменьшены"
        " для просмотра. Для файлов, не совпадающих с прежним реестром Commons,"
        " автор и условия использования не подтверждены.</p><table><thead><tr>"
        "<th>Класс</th><th>Фотография</th><th>Автор</th><th>Лицензия</th>"
        "</tr></thead><tbody>\n" + "\n".join(table_rows) + "\n</tbody></table></html>\n"
    )
    (ASSETS / "sources.html").write_text(page, encoding="utf-8")
    total = sum((ROOT / "web/public" / row["asset"]).stat().st_size for row in records)
    print(f"{len(records)} WebP photographs, {total / 1048576:.2f} MiB")


if __name__ == "__main__":
    build()

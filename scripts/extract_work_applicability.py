"""Read the supplied workbook; write a versioned runtime seed, never change XLSX."""

import hashlib
import json
from pathlib import Path
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
IDS = [
    "housing",
    "education",
    "healthcare",
    "sport",
    "culture",
    "administration",
    "preschool",
    "office",
    "roads",
]


def main():
    source = (
        ROOT / "datasets/7.ДГП_датасеты/Сводный перечень строительных работ_ЛТЦ.xlsx"
    )
    catalog = json.loads(
        (ROOT / "backend/seed/catalog.json").read_text(encoding="utf-8")
    )
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    assert checksum == catalog["source"]["sha256"], (
        "Source workbook changed; review mapping first"
    )
    book = load_workbook(source, read_only=True, data_only=True)
    rows = list(book[catalog["source"]["sheet"]].values)
    book.close()
    types = [{"id": key, "name": rows[2][index + 2]} for index, key in enumerate(IDS)]
    works = []
    for work in catalog["works"]:
        row = work["source_row"]
        assert rows[row - 1][1].strip() == work["title"].strip(), (
            f"Title mismatch at row {row}"
        )
        cells = rows[row - 1][2:11]
        assert all(value in (None, "˅") for value in cells), (
            f"Unexpected applicability at row {row}"
        )
        works.append(
            {
                "code": work["code"],
                "title": work["title"],
                "source_row": row,
                "project_type_ids": [
                    key for key, value in zip(IDS, cells) if value == "˅"
                ],
            }
        )
    target = ROOT / "backend/seed/work_applicability_v1.json"
    target.write_text(
        json.dumps(
            {"source": catalog["source"], "project_types": types, "works": works},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Exported {len(types)} types and {len(works)} work mappings")


if __name__ == "__main__":
    main()

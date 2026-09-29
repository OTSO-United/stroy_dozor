"""The database owns object types and applicability; the seed supplies provenance."""

import json
from pathlib import Path
from sqlalchemy import select
from . import models as m
from .catalog import catalog, code_key
from .planning_presets import PLANNING_CLASS_IDS, resource_preset

SEED = Path(__file__).resolve().parents[1] / "seed/work_applicability_v1.json"


def seed_catalog(db):
    data = json.loads(SEED.read_text(encoding="utf-8"))
    db.add_all(m.ProjectType(**item) for item in data["project_types"])
    db.add_all(
        m.CatalogWork(**{k: w[k] for k in ("code", "title", "source_row")})
        for w in data["works"]
    )
    db.flush()
    db.add_all(
        m.ProjectTypeWork(project_type_id=t, work_code=w["code"])
        for w in data["works"]
        for t in w["project_type_ids"]
    )
    db.flush()


def public_catalog(db, project_type_id=None):
    links = list(db.scalars(select(m.ProjectTypeWork)))
    mapping = {}
    for link in links:
        mapping.setdefault(link.work_code, []).append(link.project_type_id)
    metadata = {w["code"]: w for w in catalog()["works"]}
    works = [
        {
            **metadata.get(w.code, {}),
            "code": w.code,
            "title": w.title,
            "source_row": w.source_row,
            "project_type_ids": mapping.get(w.code, []),
        }
        for w in db.scalars(select(m.CatalogWork))
        if project_type_id is None or project_type_id in mapping.get(w.code, [])
    ]
    types = [{"id": t.id, "name": t.name} for t in db.scalars(select(m.ProjectType))]
    order = [
        t["id"] for t in json.loads(SEED.read_text(encoding="utf-8"))["project_types"]
    ]
    return {
        **catalog(),
        "planning_class_ids": PLANNING_CLASS_IDS,
        "project_types": sorted(types, key=lambda t: order.index(t["id"])),
        "works": [
            {**w, "resource_preset": resource_preset(w)}
            for w in sorted(works, key=lambda w: code_key(w["code"]))
        ],
    }

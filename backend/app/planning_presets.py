"""Editable starting quantities; not normative quantities or detector capabilities."""

import json
from functools import lru_cache
from pathlib import Path

# Existing expert rules use class 7; class 28 starts at zero until a plan is edited.
PLANNING_CLASS_IDS = [*range(8), 28, *range(8, 15), *range(16, 21)]


@lru_cache
def profiles():
    return json.loads(
        (Path(__file__).resolve().parents[1] / "seed/plan_presets.json").read_text(
            encoding="utf-8"
        )
    )


def resource_preset(work):
    profile = profiles()["works"].get(work["code"], {})
    primary = {7 if c == 15 else c for c in work.get("primary_class_ids", [])}
    section = profile.get("is_section", False)
    counts = {str(c): int(c in primary and not section) for c in PLANNING_CLASS_IDS}
    if not section:
        for class_id, count in profile.get("starting_count_overrides", {}).items():
            if int(class_id) in PLANNING_CLASS_IDS:
                counts[str(class_id)] = count
    return {
        "version": profiles()["version"],
        "basis": "editable_starting_quantities",
        "is_section": section,
        "counts": counts,
        "episodic_class_ids": [
            c
            for c in work.get("episodic_class_ids", [])
            if counts.get(str(c), 0) == 0
        ],
        "outside_class_ids": sorted(primary - set(PLANNING_CLASS_IDS)),
        "explanation": profile.get(
            "explanation", "Уточните состав техники для этой работы."
        ),
    }

from copy import deepcopy
from datetime import datetime, timedelta
from .catalog import code_key, catalog


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def predecessor_ids(works, work):
    """User policy: earlier siblings in the catalog hierarchy precede this work."""
    key = code_key(work["code"])
    return [
        w["id"]
        for w in works
        if code_key(w["code"])[:-1] == key[:-1] and code_key(w["code"]) < key
    ]


def validate_plan(works, class_ids, states=None):
    errors, warnings = [], []
    ids = [w["id"] for w in works]
    codes = [w["code"] for w in works]
    if len(ids) != len(set(ids)):
        errors.append("Повторяются идентификаторы работ")
    if len(codes) != len(set(codes)):
        errors.append("Повторяются коды работ; разделите работы по подпунктам")
    known = {w["code"] for w in catalog()["works"]}
    for w in works:
        if set(w["resources"]) != {str(c) for c in class_ids}:
            errors.append(
                f"{w['code']}: нужны количества для всех классов, включая нули"
            )
        if w["code"] not in known:
            warnings.append(
                {
                    "work_id": w["id"],
                    "kind": "custom_code",
                    "message": f"{w['code']}: код отсутствует в каталоге",
                }
            )
    return {"errors": errors, "warnings": warnings}


def shift_plan(works, work_id, hours, cascade):
    result = deepcopy(works)
    target = next((w for w in result if w["id"] == work_id), None)
    if not target:
        raise ValueError("Работа не найдена")
    end = parse_time(target["ends_at"]) + timedelta(hours=hours)
    if end <= parse_time(target["starts_at"]):
        raise ValueError("Новый конец должен быть позже начала")
    target["ends_at"] = end.isoformat()
    if cascade:
        lookup = {w["id"]: w for w in result}
        for w in sorted(result, key=lambda x: code_key(x["code"])):
            preds = predecessor_ids(result, w)
            # Only the selected work's successors belong to this operation.
            # Pre-existing conflicts in another branch require their own review.
            if work_id not in preds:
                continue
            earliest = max(parse_time(lookup[p]["ends_at"]) for p in preds)
            start, stop = parse_time(w["starts_at"]), parse_time(w["ends_at"])
            if earliest > start:
                w["starts_at"] = earliest.isoformat()
                w["ends_at"] = (stop + (earliest - start)).isoformat()
    changes = [
        {
            "work_id": a["id"],
            "code": a["code"],
            "title": a["title"],
            "before": [a["starts_at"], a["ends_at"]],
            "after": [b["starts_at"], b["ends_at"]],
        }
        for a, b in zip(works, result)
        if a != b
    ]
    return result, changes

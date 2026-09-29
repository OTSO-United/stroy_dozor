from .planning import parse_time


def inside(point, polygon):
    x, y = point
    hit = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        cross = (x - xi) * (yj - yi) - (y - yi) * (xj - xi)
        if (
            abs(cross) <= 1e-9
            and min(xi, xj) - 1e-9 <= x <= max(xi, xj) + 1e-9
            and min(yi, yj) - 1e-9 <= y <= max(yi, yj) + 1e-9
        ):
            return True
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def evaluate(works, regions, detections, captured_at, supported, usable, states=None):
    """Evaluate one observed frame. No camera aggregation and no completion inference."""
    if not captured_at:
        return [
            {"readiness": "unknown_time", "message": "Время съёмки не подтверждено"}
        ]
    if not works:
        return [{"readiness": "no_plan", "message": "План не утверждён"}]
    if not regions:
        return [{"readiness": "no_binding", "message": "Зоны не привязаны к работам"}]
    active = {
        w["id"]: w
        for w in works
        if parse_time(w["starts_at"]) <= captured_at < parse_time(w["ends_at"])
        and (states or {}).get(w["id"]) != "completed"
    }
    output = []
    for region in regions:
        local = [
            d
            for d in detections
            if inside(
                ((d["bbox"][0] + d["bbox"][2]) / 2, d["bbox"][3]), region["polygon"]
            )
        ]
        assigned = [active[k] for k in region["work_ids"] if k in active]
        # Include any spatially overlapping active binding to explain presence.
        for work in assigned:
            counts = {
                c: sum(d["class_id"] == c for d in local)
                if c in supported and usable and region["visibility_confirmed"]
                else None
                for c in work["resources"]
            }
            findings = []
            expected_total = sum(work["resources"].values())
            missing = sum(
                max(0, needed - (counts[c] or 0))
                for c, needed in work["resources"].items()
                if counts[c] is not None
            )
            complete = all(
                counts[c] is not None for c, n in work["resources"].items() if n > 0
            )
            if complete and missing:
                findings.append(
                    {
                        "kind": "missing",
                        "missing_count": missing,
                        "missing_ratio": missing / expected_total
                        if expected_total
                        else 0,
                        "message": f"Не обнаружено {missing} из {expected_total} требуемых машин",
                    }
                )
            excess = []
            for c, count in counts.items():
                if count is None or count <= work["resources"][c]:
                    continue
                explained = any(
                    w["id"] != work["id"] and w["resources"].get(c, 0) > 0
                    for r in regions
                    for w in assigned_works(active, r)
                    if any(
                        d["class_id"] == c
                        and inside(
                            ((d["bbox"][0] + d["bbox"][2]) / 2, d["bbox"][3]),
                            r["polygon"],
                        )
                        for d in local
                    )
                )
                if not explained:
                    excess.append(
                        {"class_id": c, "count": count - work["resources"][c]}
                    )
            if excess:
                findings.append(
                    {
                        "kind": "excess",
                        "classes": excess,
                        "message": f"Сверх плана {sum(item['count'] for item in excess)} ед. техники",
                    }
                )
            idle_counts = {}
            if usable and region["visibility_confirmed"]:
                for detection in local:
                    if (
                        detection.get("observed") is False
                        or detection.get("activity") != "idle"
                    ):
                        continue
                    class_id = str(detection["class_id"])
                    idle_counts[class_id] = idle_counts.get(class_id, 0) + 1
            if idle_counts:
                findings.append(
                    {
                        "kind": "idle",
                        "classes": [
                            {"class_id": class_id, "count": count}
                            for class_id, count in sorted(idle_counts.items())
                        ],
                        "message": f"Простаивает техника: {sum(idle_counts.values())}",
                    }
                )
            shared = len(assigned) > 1
            output.append(
                {
                    "work_id": work["id"],
                    "code": work["code"],
                    "title": work["title"],
                    "region_id": region["id"],
                    "region_name": region["name"],
                    "primary": region["primary"],
                    "counts": counts,
                    "expected": work["resources"],
                    "readiness": "ready"
                    if complete and usable and region["visibility_confirmed"]
                    else "insufficient_evidence",
                    "findings": findings,
                    "limitations": [
                        "Данные только этой камеры; ID не являются паспортом машины",
                        "Присутствие не подтверждает выполнение работ",
                    ]
                    + (
                        [
                            "Ресурс общий для нескольких работ; обеспеченность каждой не доказана"
                        ]
                        if shared
                        else []
                    ),
                }
            )
    return output or [
        {
            "readiness": "no_active_work",
            "message": "В этот момент нет активных привязанных работ",
        }
    ]


def assigned_works(active, region):
    return [active[k] for k in region["work_ids"] if k in active]


def severity(finding, duration, settings):
    if (
        finding["kind"] == "missing"
        and duration >= settings["critical_after_seconds"]
        and finding["missing_ratio"] > settings["critical_missing_ratio"]
    ):
        return "critical"
    return "warning"

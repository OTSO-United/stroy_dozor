import hashlib
import time
from sqlalchemy import select
from .db import Session, now, utc
from .models import Observation, Assessment, Job, Plan, Binding, Alert, Project
from .rules import evaluate, severity


def assess_one(db, observation):
    job = db.get(Job, observation.run_id)
    payload = job.payload
    plan = db.get(Plan, payload["plan_id"]) if payload.get("plan_id") else None
    binding = (
        db.get(Binding, payload["binding_id"]) if payload.get("binding_id") else None
    )
    captured = utc(observation.captured_at) if observation.captured_at else None
    settings = payload["settings"]
    results = evaluate(
        plan.works if plan else [],
        binding.regions if binding else [],
        observation.detections,
        captured,
        observation.model["supported_classes"],
        observation.quality["usable"],
        payload.get("work_states", {}),
    )
    assessment = Assessment(
        project_id=observation.project_id,
        observation_id=observation.id,
        plan_id=plan.id if plan else None,
        binding_id=binding.id if binding else None,
        results=results,
        settings=settings,
    )
    db.add(assessment)
    db.flush()
    for result in results:
        if not result.get("work_id") or not captured:
            continue
        for kind in ("missing", "excess", "idle"):
            fingerprint = hashlib.sha256(
                f"{job.id}/{result['work_id']}/{result['region_id']}/{kind}".encode()
            ).hexdigest()
            previous = db.scalar(
                select(Alert)
                .where(Alert.fingerprint == fingerprint)
                .order_by(Alert.last_seen.desc())
                .limit(1)
            )
            finding = next((f for f in result["findings"] if f["kind"] == kind), None)
            if result["readiness"] != "ready":
                if previous and previous.status in ("open", "unknown"):
                    previous.status = "interrupted"
                continue
            if finding:
                gap = (
                    (captured - utc(previous.last_seen)).total_seconds()
                    if previous
                    else 0
                )
                allowed_gap = max(
                    settings["max_gap_seconds"],
                    float(payload.get("sample_seconds") or 0) * 1.5,
                )
                continuous = (
                    previous
                    and previous.status in ("open", "unknown")
                    and 0 <= gap <= allowed_gap
                )
                if not previous or not continuous:
                    episode = (
                        int(previous.details.get("episode", 1)) + 1 if previous else 1
                    )
                    if previous and previous.status in ("open", "unknown"):
                        previous.status = "interrupted"
                    previous = Alert(
                        project_id=observation.project_id,
                        source_id=observation.source_id,
                        run_id=job.id,
                        work_id=result["work_id"],
                        fingerprint=fingerprint,
                        kind=kind,
                        severity="warning",
                        status="unknown",
                        first_seen=captured,
                        last_seen=captured,
                        assessment_id=assessment.id,
                        details={},
                    )
                    db.add(previous)
                    duration = 0
                else:
                    episode = int(previous.details.get("episode", 1))
                    duration = previous.duration_seconds + gap
                confirmation_seconds = (
                    settings.get("absence_confirm_seconds", 30)
                    if kind == "missing"
                    else settings["warning_after_seconds"]
                )
                visible_after = max(
                    confirmation_seconds, settings["warning_after_seconds"]
                )
                confirmed = duration >= visible_after
                above = (
                    finding.get("missing_ratio", 0) > settings["critical_missing_ratio"]
                )
                prior_above = previous.details.get("above_critical_ratio", False)
                critical_duration = (
                    (previous.details.get("critical_duration_seconds", 0) + gap)
                    if continuous and above and prior_above
                    else 0
                )
                previous.duration_seconds = duration
                previous.last_seen = captured
                previous.assessment_id = assessment.id
                previous.status = "open" if confirmed else "unknown"
                previous.severity = (
                    "critical"
                    if kind == "idle" and duration >= settings["critical_after_seconds"]
                    else severity(finding, critical_duration, settings)
                )
                finding["confirmed"] = confirmed
                finding["confirmation_seconds"] = confirmation_seconds
                if kind == "missing" and not confirmed:
                    finding["message"] = (
                        "Временный пропуск детекции: отсутствие пока не подтверждено"
                    )
                previous.details = {
                    **finding,
                    **(
                        {"review": previous.details["review"]}
                        if previous.details.get("review")
                        else {}
                    ),
                    "episode": episode,
                    "above_critical_ratio": above,
                    "critical_duration_seconds": critical_duration,
                    "confirmation_seconds": confirmation_seconds,
                    "confirmed_absence_seconds": max(0, duration - confirmation_seconds)
                    if kind == "missing"
                    else 0,
                    "code": result["code"],
                    "work_starts_at": next(
                        (
                            w["starts_at"]
                            for w in plan.works
                            if w["id"] == result["work_id"]
                        ),
                        None,
                    )
                    if plan
                    else None,
                    "work_ends_at": next(
                        (
                            w["ends_at"]
                            for w in plan.works
                            if w["id"] == result["work_id"]
                        ),
                        None,
                    )
                    if plan
                    else None,
                    "notified_at": (previous.details or {}).get("notified_at")
                    or (now().isoformat() if confirmed else None),
                    "title": result["title"],
                    "region_id": result["region_id"],
                    "region_name": result["region_name"],
                    "expected": result["expected"],
                    "counts": result["counts"],
                    "evidence_id": observation.id,
                    "plan_id": assessment.plan_id,
                    "binding_id": assessment.binding_id,
                    "model_hash": observation.model.get("sha256"),
                    "limitations": result["limitations"]
                    + [
                        "Длительность оценена по пригодным выборкам; шаг и разрывы ограничивают вывод"
                    ],
                    "suppressed": not confirmed,
                }
            elif previous and previous.status in ("open", "unknown"):
                previous.status = "resolved"
                previous.last_seen = captured
    assessment.results = results
    observation.assessed = True
    return assessment


def tick(limit=100, project_id=None):
    processed = 0
    # One chronological transaction per sample; independent monitoring process.
    for _ in range(limit):
        with Session() as db:
            query = select(Observation).where(Observation.assessed.is_(False))
            if project_id:
                query = query.where(Observation.project_id == project_id)
            observation = db.scalar(
                query.order_by(Observation.created_at, Observation.sample_index)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not observation:
                break
            assess_one(db, observation)
            db.commit()
            processed += 1
    return processed


def main():
    due = {}
    while True:
        with Session() as db:
            projects = list(db.scalars(select(Project)))
        for project in projects:
            if time.monotonic() >= due.get(project.id, 0):
                tick(limit=1000, project_id=project.id)
                due[project.id] = (
                    time.monotonic() + project.settings["monitor_interval_seconds"]
                )
        time.sleep(1)


if __name__ == "__main__":
    main()

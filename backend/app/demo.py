"""Reproducible synthetic project for manual UI checks.

This module never invokes the detector.  It creates clearly marked observations from
a seeded pseudo-random stub, then sends them through the normal assessment code so
alerts and reports exercise the same read paths as real observations.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid5

import cv2
import numpy as np
from sqlalchemy import func, select

from . import models as m
from .monitor import assess_one
from .schemas import Settings


DEMO_NAMESPACE = UUID("719f573f-9e71-4919-8ea2-af87c84d2152")
DEMO_MODEL_NAME = "DEMO_RANDOM_STUB"
DEMO_CLASS_IDS = [0, 1, 3, 7, 8, 10]


def _id(seed: int, name: str) -> str:
    return str(uuid5(DEMO_NAMESPACE, f"{seed}:{name}"))


def _detections(rng: random.Random, counts: dict[str, int]) -> list[dict]:
    detections = []
    index = 0
    for class_id, count in counts.items():
        for _ in range(count):
            width = rng.uniform(0.09, 0.18)
            height = rng.uniform(0.12, 0.25)
            left = rng.uniform(0.03, 0.96 - width)
            top = rng.uniform(0.12, 0.94 - height)
            detections.append(
                {
                    "class_id": class_id,
                    "confidence": round(rng.uniform(0.64, 0.97), 3),
                    "bbox": [
                        round(left, 4),
                        round(top, 4),
                        round(left + width, 4),
                        round(top + height, 4),
                    ],
                    "track_id": index + 1,
                    "motion": rng.choice(["moving", "stationary_observed", "unknown"]),
                    "activity": "unknown",
                }
            )
            index += 1
    rng.shuffle(detections)
    return detections


def _frame(source_index: int, sample_index: int, detections: list[dict]) -> np.ndarray:
    height, width = 720, 1280
    image = np.full((height, width, 3), (224, 219, 205), dtype=np.uint8)
    cv2.rectangle(image, (0, 0), (width, 96), (36, 70, 62), -1)
    cv2.rectangle(image, (0, 500), (width, height), (103, 111, 113), -1)
    cv2.rectangle(image, (80, 260), (430, 505), (165, 155, 135), -1)
    cv2.rectangle(image, (760, 205), (1150, 505), (184, 173, 147), -1)
    cv2.putText(
        image,
        "SYNTHETIC UI FIXTURE - NOT A DETECTOR RESULT",
        (32, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.05,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        f"CAM {source_index + 1} / SAMPLE {sample_index + 1:02}",
        (36, 135),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (35, 50, 46),
        2,
        cv2.LINE_AA,
    )
    colors = [(39, 124, 88), (38, 105, 190), (41, 158, 202), (117, 73, 180)]
    for detection in detections:
        x1, y1, x2, y2 = detection["bbox"]
        p1, p2 = (
            (round(x1 * width), round(y1 * height)),
            (
                round(x2 * width),
                round(y2 * height),
            ),
        )
        color = colors[int(detection["class_id"]) % len(colors)]
        cv2.rectangle(image, p1, p2, color, 3)
        cv2.putText(
            image,
            f"class {detection['class_id']}  {detection['confidence']:.0%}",
            (p1[0], max(110, p1[1] - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
            cv2.LINE_AA,
        )
    return image


def _write_jpeg(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.jpg")
    if not cv2.imwrite(str(temporary), image, [cv2.IMWRITE_JPEG_QUALITY, 88]):
        raise RuntimeError(f"Не удалось записать демо-кадр: {path}")
    temporary.replace(path)


def _summary(db, project_id: str, created: bool) -> dict:
    def count(model):
        return db.scalar(
            select(func.count())
            .select_from(model)
            .where(model.project_id == project_id)
        )

    return {
        "created": created,
        "project_id": project_id,
        "observations": count(m.Observation),
        "assessments": count(m.Assessment),
        "alerts": count(m.Alert),
        "audit_events": count(m.Audit),
        "url": f"http://127.0.0.1:8000/?project={project_id}&tab=overview",
    }


def create_demo_project(
    db,
    data_dir: Path,
    *,
    seed: int = 20260918,
    reference_time: datetime | None = None,
) -> dict:
    """Create one idempotent demo project and return its identifiers/counts."""
    project_id = _id(seed, "project")
    if db.get(m.Project, project_id):
        return _summary(db, project_id, False)

    reference_time = reference_time or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        raise ValueError("reference_time must include a timezone")
    reference_time = reference_time.astimezone(timezone.utc).replace(microsecond=0)
    first_sample = reference_time - timedelta(minutes=11)
    rng = random.Random(seed)
    settings = {
        **Settings().model_dump(),
        "critical_after_seconds": 180,
        "critical_missing_ratio": 0.5,
        "max_gap_seconds": 90,
    }
    resource_keys = [str(value) for value in DEMO_CLASS_IDS]

    works = [
        {
            "id": _id(seed, "work:earth"),
            "code": "12.1",
            "title": "ДЕМО: разработка грунта и вывоз",
            "starts_at": (reference_time - timedelta(days=2)).isoformat(),
            "ends_at": (reference_time + timedelta(days=2)).isoformat(),
            "resources": {
                **dict.fromkeys(resource_keys, 0),
                "0": 3,
                "1": 2,
                "7": 1,
                "10": 1,
            },
        },
        {
            "id": _id(seed, "work:concrete"),
            "code": "12.2",
            "title": "ДЕМО: устройство бетонной подготовки",
            "starts_at": (reference_time - timedelta(days=1)).isoformat(),
            "ends_at": (reference_time + timedelta(days=3)).isoformat(),
            "resources": {
                **dict.fromkeys(resource_keys, 0),
                "0": 1,
                "8": 2,
            },
        },
    ]
    plan = m.Plan(
        id=_id(seed, "plan"),
        project_id=project_id,
        version=1,
        revision=2,
        status="approved",
        works=works,
        approved_at=reference_time - timedelta(hours=2),
    )
    project = m.Project(
        id=project_id,
        name="ДЕМО · Проверка интерфейса",
        address="Синтетические данные: случайная заглушка, не результаты детектора",
        timezone="Europe/Moscow",
        class_ids=DEMO_CLASS_IDS,
        settings=settings,
        current_plan_id=None,
    )
    # Project must exist before its plan on SQLite with foreign keys enabled.
    db.add(project)
    db.flush()
    db.add(plan)
    db.flush()
    project.current_plan_id = plan.id
    db.add_all(
        [
            m.WorkState(
                id=_id(seed, f"state:{work['id']}"),
                project_id=project_id,
                work_id=work["id"],
                status="in_progress",
                updated_at=reference_time - timedelta(hours=1),
            )
            for work in works
        ]
    )
    db.flush()

    model = {
        "name": DEMO_MODEL_NAME,
        "sha256": f"demo-random-stub-seed-{seed}",
        "supported_classes": resource_keys,
        "providers": ["seeded_python_stub"],
        "demo": True,
        "seed": seed,
    }
    sources: list[m.Source] = []
    jobs: list[m.Job] = []
    bindings: list[m.Binding] = []
    for source_index, (source_name, work) in enumerate(
        [
            ("ДЕМО · Камера котлована", works[0]),
            ("ДЕМО · Камера бетонных работ", works[1]),
        ]
    ):
        source = m.Source(
            id=_id(seed, f"source:{source_index}"),
            project_id=project_id,
            name=source_name,
            kind="video",
            capture_start=first_sample,
            sample_seconds=60,
            status="completed",
            enabled=False,
            metadata_json={
                "width": 1280,
                "height": 720,
                "duration_seconds": 660,
                "fps": 1 / 60,
                "demo": True,
                "generator": DEMO_MODEL_NAME,
                "seed": seed,
            },
            created_at=first_sample - timedelta(minutes=1),
        )
        binding = m.Binding(
            id=_id(seed, f"binding:{source_index}"),
            project_id=project_id,
            source_id=source.id,
            revision=1,
            regions=[
                {
                    "id": _id(seed, f"region:{source_index}"),
                    "name": "ДЕМО: весь кадр",
                    "work_ids": [work["id"]],
                    "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                    "primary": True,
                    "visibility_confirmed": True,
                }
            ],
            created_at=first_sample - timedelta(seconds=30),
        )
        job = m.Job(
            id=_id(seed, f"job:{source_index}"),
            project_id=project_id,
            source_id=source.id,
            kind="analyze",
            status="succeeded",
            progress=1,
            payload={
                "plan_id": plan.id,
                "binding_id": binding.id,
                "settings": settings,
                "work_states": {work["id"]: "in_progress" for work in works},
                "sample_seconds": 60,
                "model_hash": model["sha256"],
                "demo": True,
                "seed": seed,
            },
            attempts=1,
            created_at=first_sample - timedelta(seconds=15),
            updated_at=reference_time,
        )
        db.add(source)
        db.flush()
        db.add_all([binding, job])
        db.flush()
        sources.append(source)
        bindings.append(binding)
        jobs.append(job)

    written: list[Path] = []
    try:
        for source_index, (source, job) in enumerate(zip(sources, jobs)):
            last_frame = None
            for sample_index in range(12):
                if source_index == 0:
                    # Persistent shortage plus a random forbidden equipment class/count.
                    unexpected_class = rng.choice(("3", "8"))
                    counts = {
                        "0": rng.randint(0, 1),
                        "1": rng.randint(0, 1),
                        unexpected_class: rng.randint(1, 2),
                    }
                elif sample_index < 6:
                    # A temporary shortage that is later resolved and marked reviewed.
                    counts = {"0": 0, "8": rng.randint(0, 1)}
                else:
                    counts = {"0": 1, "8": 2}
                detections = _detections(rng, counts)
                captured_at = first_sample + timedelta(minutes=sample_index)
                evidence_key = (
                    f"evidence/demo/{project_id}/{source.id}/{sample_index:04}.jpg"
                )
                evidence_path = data_dir / evidence_key
                last_frame = _frame(source_index, sample_index, detections)
                _write_jpeg(evidence_path, last_frame)
                written.append(evidence_path)
                observation = m.Observation(
                    id=_id(seed, f"observation:{source_index}:{sample_index}"),
                    project_id=project_id,
                    source_id=source.id,
                    run_id=job.id,
                    sample_index=sample_index,
                    captured_at=captured_at,
                    offset_seconds=sample_index * 60,
                    detections=detections,
                    quality={
                        "usable": True,
                        "time_basis": "synthetic_demo_clock",
                        "limitations": [
                            "Синтетический кадр и псевдослучайные числа",
                            "Не является результатом детектора или наблюдением стройки",
                        ],
                    },
                    model=model,
                    evidence_key=evidence_key,
                    created_at=captured_at,
                )
                db.add(observation)
                db.flush()
                assess_one(db, observation)
            preview_path = data_dir / f"previews/{source.id}.jpg"
            _write_jpeg(preview_path, last_frame)
            written.append(preview_path)

        resolved = db.scalar(
            select(m.Alert)
            .where(m.Alert.project_id == project_id, m.Alert.status == "resolved")
            .order_by(m.Alert.last_seen.desc())
            .limit(1)
        )
        if resolved:
            resolved.reviewed = True

        audit_rows = [
            ("project.created", project.id, {"name": project.name}),
            ("plan.created", plan.id, {"version": 1, "demo": True}),
            ("plan.approved", plan.id, {"version": 1, "demo": True}),
            (
                "work.status_changed",
                works[0]["id"],
                {"before": "planned", "after": "in_progress", "note": "Демо-сценарий"},
            ),
        ]
        for source, binding, job in zip(sources, bindings, jobs):
            audit_rows.extend(
                [
                    (
                        "media.uploaded",
                        source.id,
                        {"name": source.name, "generator": DEMO_MODEL_NAME},
                    ),
                    (
                        "zones.updated",
                        binding.id,
                        {"source_id": source.id, "revision": 1},
                    ),
                    (
                        "analysis.started",
                        job.id,
                        {"source_id": source.id, "plan_id": plan.id, "demo": True},
                    ),
                ]
            )
        if resolved:
            audit_rows.append(
                (
                    "alert.reviewed",
                    resolved.id,
                    {
                        "decision": "acknowledged",
                        "note": "ДЕМО: проверка фильтра и журнала",
                        "assessment_id": resolved.assessment_id,
                    },
                )
            )
        audit_rows.append(
            (
                "demo.generated",
                project.id,
                {
                    "seed": seed,
                    "generator": DEMO_MODEL_NAME,
                    "observations": 24,
                    "warning": "Не является результатом детектора",
                },
            )
        )
        for index, (action, entity_id, data) in enumerate(audit_rows):
            db.add(
                m.Audit(
                    id=_id(seed, f"audit:{index}"),
                    project_id=project_id,
                    action=action,
                    entity_id=entity_id,
                    data=data,
                    actor="demo-generator",
                    created_at=reference_time
                    - timedelta(minutes=len(audit_rows) - index),
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return _summary(db, project_id, True)

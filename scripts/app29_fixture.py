"""Isolated APP-29 fixture/server; never connects to the user's runtime database."""

import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tmp" / "app29-runtime"
os.environ["STROY_DATA_DIR"] = str(DATA)
os.environ["DATABASE_URL"] = "sqlite:///" + (DATA / "audit.db").as_posix()
os.environ["MODEL_MANIFEST"] = str(DATA / "no-model.json")
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402
from app import models as m  # noqa: E402
from app.db import Base, engine, Session, now  # noqa: E402
from app.demo import _frame, _write_jpeg  # noqa: E402
from app.monitor import assess_one  # noqa: E402
from app.project_catalog import seed_catalog  # noqa: E402
from app.schemas import Settings  # noqa: E402


def seed():
    Base.metadata.create_all(engine)
    with Session() as db:
        project = db.scalar(
            select(m.Project).where(m.Project.name == "APP-29 · Изолированная проверка")
        )
        if project:
            return project.id
        if not db.scalar(select(m.CatalogWork.code).limit(1)):
            seed_catalog(db)
        start = now().replace(second=0, microsecond=0) - timedelta(minutes=10)
        settings = Settings().model_dump()
        project = m.Project(
            name="APP-29 · Изолированная проверка",
            address="Синтетические данные",
            class_ids=[0, 1, 16],
            settings=settings,
        )
        db.add(project)
        db.flush()
        works = [
            {
                "id": str(uuid4()),
                "code": f"12.{index + 1}",
                "title": title,
                "starts_at": (start - timedelta(hours=1)).isoformat(),
                "ends_at": (start + timedelta(hours=8)).isoformat(),
                "resources": {"0": 4, "1": 1, "16": 0},
            }
            for index, title in enumerate(
                [
                    "Подготовка основания и устройство протяжённых инженерных коммуникаций на строительной площадке",
                    "Монтаж конструкций",
                    "Этап без привязанной камеры",
                ]
            )
        ]
        plan = m.Plan(project_id=project.id, version=1, works=works)
        db.add(plan)
        db.flush()
        project.current_plan_id = plan.id
        for camera in range(2):
            source = m.Source(
                project_id=project.id,
                kind="video",
                name=f"Камера {camera + 1} · Очень длинное название участка наблюдения за инженерными коммуникациями",
                status="completed",
                sample_seconds=1,
                capture_start=start,
                metadata_json={"width": 1280, "height": 720, "duration_seconds": 300},
            )
            db.add(source)
            db.flush()
            binding = m.Binding(
                project_id=project.id,
                source_id=source.id,
                revision=1,
                regions=[
                    {
                        "id": str(uuid4()),
                        "name": "Весь кадр",
                        "work_ids": [work["id"] for work in works[:2]],
                        "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                        "primary": True,
                        "visibility_confirmed": True,
                        "color": "#27836b",
                        "visible": True,
                    }
                ],
            )
            db.add(binding)
            db.flush()
            job = m.Job(
                project_id=project.id,
                source_id=source.id,
                kind="analyze",
                status="succeeded",
                progress=1,
                payload={
                    "plan_id": plan.id,
                    "binding_id": binding.id,
                    "settings": settings,
                    "sample_seconds": 1,
                    "work_states": {},
                    "works": [
                        {key: w[key] for key in ("id", "code", "title")}
                        for w in works[:2]
                    ],
                    "started_at": start.isoformat(),
                },
            )
            db.add(job)
            db.flush()
            for index in range(240):
                count = 3 if index < 80 else 2 if index < 180 else 4
                if 20 <= index < 25 or 45 <= index < 50:
                    count = 4
                if 130 <= index < 134:
                    count = 0
                activity = (
                    "unknown" if index < 20 else "working" if index < 100 else "idle"
                )
                detections = [
                    {
                        "class_id": "0",
                        "confidence": 0.9,
                        "bbox": [0.08 + n * 0.18, 0.3, 0.2 + n * 0.18, 0.65],
                        "activity": activity,
                        "activity_basis": "insufficient_video"
                        if activity == "unknown"
                        else "vehicle_motion"
                        if activity == "working"
                        else "visible_inactivity",
                    }
                    for n in range(count)
                ]
                detections += [
                    {
                        "class_id": "1",
                        "confidence": 0.85,
                        "bbox": [0.65, 0.65, 0.85, 0.9],
                        "activity": "unknown",
                    }
                ]
                key = f"evidence/{source.id}/{index}.jpg"
                frame = _frame(camera, index, detections)
                _write_jpeg(DATA / key, frame)
                observation = m.Observation(
                    project_id=project.id,
                    source_id=source.id,
                    run_id=job.id,
                    sample_index=index,
                    offset_seconds=index,
                    captured_at=start
                    + timedelta(seconds=index + (20 if index >= 150 else 0)),
                    created_at=start + timedelta(seconds=index),
                    detections=detections,
                    quality={"usable": index != 145},
                    model={
                        "name": "APP29_SYNTHETIC_FIXTURE",
                        "supported_classes": ["0", "1"],
                    },
                    evidence_key=key,
                )
                db.add(observation)
                db.flush()
                assess_one(db, observation)
            _write_jpeg(DATA / f"previews/{source.id}.jpg", frame)
        db.commit()
        return project.id


if __name__ == "__main__":
    project_id = seed()
    if "--reset-browser" in sys.argv:
        with Session() as db:
            for alert in db.scalars(
                select(m.Alert).where(m.Alert.project_id == project_id)
            ):
                alert.reviewed = False
                alert.details = {
                    key: value
                    for key, value in alert.details.items()
                    if key != "review"
                }
            db.commit()
    if "--new-episode" in sys.argv:
        with Session() as db:
            for source in db.scalars(
                select(m.Source).where(m.Source.project_id == project_id)
            ):
                job = db.scalar(select(m.Job).where(m.Job.source_id == source.id))
                last = db.scalar(
                    select(m.Observation)
                    .where(m.Observation.run_id == job.id)
                    .order_by(m.Observation.sample_index.desc())
                )
                for index in range(40):
                    observation = m.Observation(
                        project_id=project_id,
                        source_id=source.id,
                        run_id=job.id,
                        sample_index=last.sample_index + index + 1,
                        captured_at=last.captured_at + timedelta(seconds=index + 1),
                        offset_seconds=last.offset_seconds + index + 1,
                        created_at=last.created_at + timedelta(seconds=index + 1),
                        detections=[d for d in last.detections if d["class_id"] == "1"],
                        quality={"usable": True},
                        model=last.model,
                        evidence_key=last.evidence_key,
                    )
                    db.add(observation)
                    db.flush()
                    assess_one(db, observation)
            db.commit()
    metadata = {
        "project_id": project_id,
        "url": f"http://127.0.0.1:8091/?project={project_id}&tab=overview",
    }
    (DATA / "fixture.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=True), flush=True)
    if "--serve" in sys.argv:
        import uvicorn

        uvicorn.run("app.api:app", host="127.0.0.1", port=8091)

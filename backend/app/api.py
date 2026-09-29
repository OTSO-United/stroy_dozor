import csv
import hashlib
import io
import math
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    Header,
    UploadFile,
    File,
    Form,
    Query,
)
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select, text, update
from .db import session, now, uid
from .config import DATA, MAX_UPLOAD_BYTES, ROOT
from . import models as m, schemas as s, repositories as repo
from .catalog import catalog, class_map
from .planning import validate_plan, shift_plan
from .planning_presets import PLANNING_CLASS_IDS
from .security import validate_rtsp, decode_uri, digest
from .vision import model_status
from .reports import build_report
from .project_catalog import public_catalog
from .telemetry import series_points
from .api_common import require, obj, mutation
from .detector_api import router as detector_router
from .model_settings import product_parameters
from .geocoding import router as map_router

app = FastAPI(
    title="СтройДозор API",
    version="0.1.0",
    description="Локальный профиль без авторизации. Публикация в интернете не поддерживается.",
)
app.include_router(detector_router)
app.include_router(map_router)


@app.exception_handler(ValueError)
async def value_error(_, error):
    return JSONResponse(status_code=422, content={"detail": str(error)})


def aware(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def summarize(plan, sources, states, open_alerts):
    instant = now()
    current = []
    if plan:
        for item in sorted(plan.works, key=lambda work: work["starts_at"]):
            if states.get(item["id"]) == "completed":
                continue
            if aware(item["starts_at"]) <= instant < aware(item["ends_at"]):
                current.append(
                    {"id": item["id"], "code": item["code"], "title": item["title"]}
                )
    return {
        "current_works": current,
        "sources": len(sources),
        "live_sources": sum(source.status == "running" for source in sources),
        "open_alerts": open_alerts,
    }


def effective_project_settings(p):
    return {**s.Settings().model_dump(), **(p.settings or {})}


def assessment_context_changed(snapshot, states, settings):
    """Compare persisted rules semantically across additive settings releases."""
    if not isinstance(snapshot, dict):
        return False
    saved_states = snapshot.get("work_states")
    if saved_states is not None and saved_states != states:
        return True
    saved_settings = snapshot.get("settings")
    if not isinstance(saved_settings, dict):
        return False
    defaults = s.Settings().model_dump()
    return any(
        saved_settings.get(key, default) != settings.get(key, default)
        for key, default in defaults.items()
    )


def public_project_record(p):
    data = obj(p)
    data["settings"] = effective_project_settings(p)
    return data


def public_project(p, plan, sources, states, open_alerts):
    data = public_project_record(p)
    data["summary"] = summarize(plan, sources, states, open_alerts)
    return data


def unique_alerts(alerts, *, include_suppressed=False):
    unique = {}
    for alert in sorted(alerts, key=lambda item: aware(item.last_seen), reverse=True):
        if not include_suppressed and (alert.details or {}).get("suppressed"):
            continue
        unique.setdefault(
            (alert.fingerprint, (alert.details or {}).get("episode", 1)), alert
        )
    return list(unique.values())


def open_alert_count(alerts):
    return sum(
        1
        for alert in unique_alerts(alerts)
        if alert.status == "open" and not alert.reviewed
    )


def public_source(row, db=None):
    data = obj(row)
    data.pop("uri_encrypted", None)
    data.pop("file_key", None)
    data["preview_url"] = (
        f"/api/v1/sources/{row.id}/preview"
        if (DATA / f"previews/{row.id}.jpg").exists()
        else None
    )
    if db is not None:
        job = db.scalar(
            select(m.Job)
            .where(m.Job.source_id == row.id)
            .order_by(m.Job.created_at.desc())
            .limit(1)
        )
        phase = row.status
        data["retry_at"] = None
        data["connection_attempt"] = (
            job.payload.get("stream_attempt", 1) if job and row.kind == "rtsp" else None
        )
        if job and job.status in ("queued", "running"):
            if row.kind == "rtsp" and job.status == "queued" and job.lease_until:
                data["retry_at"] = aware(job.lease_until)
            phase = (
                "reconnecting"
                if job.status == "queued"
                and job.lease_until
                and aware(job.lease_until) > now()
                and row.kind == "rtsp"
                else "connecting"
                if row.kind == "rtsp"
                and job.kind == "probe"
                and job.status == "running"
                else "connection_queued"
                if row.kind == "rtsp" and job.kind == "probe"
                else "preparing"
                if job.kind == "probe"
                else job.status
            )
        elif job and job.kind == "analyze" and job.status == "succeeded":
            total, assessed = db.execute(
                select(func.count(m.Observation.id), func.count(m.Assessment.id))
                .outerjoin(
                    m.Assessment, m.Assessment.observation_id == m.Observation.id
                )
                .where(m.Observation.run_id == job.id)
            ).one()
            phase = (
                "completed"
                if total and total == assessed
                else "assessing"
                if total
                else "empty"
            )
        data["processing_state"] = phase
    return data


def project(db, id, lock=False):
    return require(
        repo.Projects(db).locked(id) if lock else repo.Projects(db).get(id),
        "Объект не найден",
    )


def current_plan(db, p):
    return db.get(m.Plan, p.current_plan_id) if p.current_plan_id else None


def demo_loop_config(source):
    value = (source.metadata_json or {}).get("demo_loop") or {}
    return value if value.get("enabled") else None


def validate_demo_loop(source, plan, regions, *, require_media_duration=False):
    config = demo_loop_config(source)
    if not config:
        return None
    if source.kind != "video":
        raise HTTPException(422, "Зацикливание доступно только для видеофайла")
    if not source.capture_start:
        raise HTTPException(
            422, "Для тестового зацикливания укажите начало демонстрации"
        )
    seconds = config.get("duration_seconds")
    if (
        not isinstance(seconds, (int, float))
        or not math.isfinite(seconds)
        or seconds < 1
    ):
        raise HTTPException(422, "Укажите длительность демонстрации")
    if require_media_duration:
        media_seconds = (source.metadata_json or {}).get("duration_seconds")
        if (
            not isinstance(media_seconds, (int, float))
            or not math.isfinite(media_seconds)
            or media_seconds <= 0
        ):
            raise HTTPException(409, "Сначала дождитесь определения длительности видео")
    if not plan:
        raise HTTPException(422, "Для тестового зацикливания нужен текущий план")
    work_ids = {work_id for region in regions for work_id in region["work_ids"]}
    if not work_ids:
        raise HTTPException(
            422, "Привяжите тестовое видео хотя бы к одному этапу плана"
        )
    works = {work["id"]: work for work in plan.works}
    selected = [works[work_id] for work_id in work_ids if work_id in works]
    if len(selected) != len(work_ids):
        raise HTTPException(422, "Привязанный этап отсутствует в текущем плане")
    starts_at = aware(source.capture_start)
    available_seconds = max(
        (aware(work["ends_at"]) - starts_at).total_seconds() for work in selected
    )
    requested_seconds = float(seconds)
    effective_seconds = (
        min(requested_seconds, available_seconds)
        if available_seconds >= 1
        else requested_seconds
    )
    return {
        "enabled": True,
        "duration_seconds": effective_seconds,
        "requested_duration_seconds": requested_seconds,
        "trimmed_to_stage": effective_seconds < requested_seconds,
    }


def analysis_payload(db, p, source, loop_config=None):
    binding = repo.Bindings(db).latest(source.id)
    plan = current_plan(db, p)
    work_ids = {
        work_id
        for region in (binding.regions if binding else [])
        for work_id in region.get("work_ids", [])
    }
    status = model_status()
    return {
        "plan_id": p.current_plan_id,
        "binding_id": binding.id if binding else None,
        "works": [
            {key: work[key] for key in ("id", "code", "title")}
            for work in (plan.works if plan else [])
            if work["id"] in work_ids
        ],
        "settings": effective_project_settings(p),
        "work_states": repo.Plans(db).states(p.id),
        "sample_seconds": source.sample_seconds,
        "model_hash": status.get("sha256"),
        "inference_parameters": product_parameters(DATA, status),
        "demo_loop": loop_config,
    }


def queue_bound_video(db, source):
    """Queue a plan snapshot once a source and its binding are ready."""
    if source.kind not in ("video", "rtsp", "image") or repo.Jobs(db).active(source.id):
        return None
    p = project(db, source.project_id, True)
    plan = current_plan(db, p)
    binding = repo.Bindings(db).latest(source.id)
    if not plan or not binding or not any(r.get("work_ids") for r in binding.regions):
        return None
    if not (source.metadata_json or {}).get("width") or not model_status()["ready"]:
        return None
    loop_config = validate_demo_loop(
        source, plan, binding.regions, require_media_duration=True
    )
    source.status = "queued"
    source.enabled = source.kind == "rtsp"
    source.error = None
    job = repo.Jobs(db).add(
        project_id=p.id,
        source_id=source.id,
        kind="analyze",
        payload=analysis_payload(db, p, source, loop_config),
    )
    repo.Audits(db).record(
        p.id,
        "analysis.auto_started",
        job.id,
        source_id=source.id,
        plan_id=plan.id,
        binding_id=binding.id,
    )
    return job


@app.get("/api/v1/health")
def health(db=Depends(session)):
    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "model": model_status(),
        "auth_mode": "local",
        "max_sources": 20,
    }


@app.get("/api/v1/catalog")
def get_catalog(project_type_id: str | None = None, db=Depends(session)):
    if project_type_id and not db.get(m.ProjectType, project_type_id):
        raise HTTPException(422, "Неизвестный вид объекта")
    return public_catalog(db, project_type_id)


@app.get("/api/v1/projects")
def projects(project_type_id: str | None = None, db=Depends(session)):
    rows = repo.Projects(db).all()
    if project_type_id is not None:
        rows = [p for p in rows if p.project_type_id == (project_type_id or None)]
    if not rows:
        return []
    ids = [p.id for p in rows]
    plan_ids = [p.current_plan_id for p in rows if p.current_plan_id]
    plans = (
        {
            plan.id: plan
            for plan in db.scalars(select(m.Plan).where(m.Plan.id.in_(plan_ids)))
        }
        if plan_ids
        else {}
    )
    sources_by_project = {}
    for source in db.scalars(select(m.Source).where(m.Source.project_id.in_(ids))):
        sources_by_project.setdefault(source.project_id, []).append(source)
    states_by_project = {}
    for state in db.scalars(select(m.WorkState).where(m.WorkState.project_id.in_(ids))):
        states_by_project.setdefault(state.project_id, {})[state.work_id] = state.status
    alerts_by_project = {}
    for alert in db.scalars(
        select(m.Alert).where(m.Alert.project_id.in_(ids), m.Alert.status == "open")
    ):
        alerts_by_project.setdefault(alert.project_id, []).append(alert)
    return [
        public_project(
            p,
            plans.get(p.current_plan_id) if p.current_plan_id else None,
            sources_by_project.get(p.id, []),
            states_by_project.get(p.id, {}),
            open_alert_count(alerts_by_project.get(p.id, [])),
        )
        for p in rows
    ]


@app.post("/api/v1/projects", status_code=201)
def create_project(
    data: s.ProjectInput,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    if any(str(c) not in class_map() for c in data.class_ids):
        raise HTTPException(422, "Неизвестный класс техники")

    def operation():
        if data.project_type_id and not db.get(m.ProjectType, data.project_type_id):
            raise HTTPException(422, "Неизвестный вид объекта")
        p = repo.Projects(db).add(
            **data.model_dump(), settings=s.Settings().model_dump()
        )
        repo.Audits(db).record(
            p.id,
            "project.created",
            p.id,
            name=p.name,
            address=p.address,
            project_type_id=p.project_type_id,
            latitude=p.latitude,
            longitude=p.longitude,
        )
        return obj(p)

    return mutation(db, "projects:create", key, data.model_dump(mode="json"), operation)


@app.get("/api/v1/projects/{id}")
def get_project(id: str, db=Depends(session)):
    p = project(db, id)
    return public_project(
        p,
        current_plan(db, p),
        repo.Sources(db).all(project_id=id),
        repo.Plans(db).states(id),
        open_alert_count(repo.Alerts(db).recent(id)),
    )


@app.put("/api/v1/projects/{id}")
def update_project(
    id: str,
    data: s.ProjectUpdate,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        p = project(db, id, True)
        if p.revision != data.expected_revision:
            raise HTTPException(409, "Объект изменился, обновите страницу")
        if data.project_type_id and not db.get(m.ProjectType, data.project_type_id):
            raise HTTPException(422, "Неизвестный вид объекта")
        fields = ("name", "address", "project_type_id", "latitude", "longitude")
        before = {field: getattr(p, field) for field in fields}
        p.name, p.address, p.revision = data.name, data.address, p.revision + 1
        for field in ("project_type_id", "latitude", "longitude"):
            if field in data.model_fields_set:
                setattr(p, field, getattr(data, field))
        if (p.latitude is None) != (p.longitude is None):
            raise HTTPException(422, "Укажите точку целиком")
        repo.Audits(db).record(
            id,
            "project.updated",
            id,
            before=before,
            after={field: getattr(p, field) for field in fields},
        )
        return obj(p)

    return mutation(
        db,
        f"{id}:update",
        key,
        data.model_dump(mode="json", exclude_unset=True),
        operation,
    )


@app.delete("/api/v1/projects/{id}")
def delete_project(
    id: str,
    data: s.DeleteProjectInput,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        p = project(db, id, True)
        if p.revision != data.expected_revision:
            raise HTTPException(409, "Объект изменился, обновите страницу")
        name = p.name
        repo.Projects(db).delete_tree(id)
        return {"deleted_project_id": id, "name": name}

    return mutation(db, f"{id}:delete", key, data.model_dump(), operation)


@app.put("/api/v1/projects/{id}/settings")
def settings(
    id: str,
    data: s.SettingsInput,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        p = project(db, id, True)
        if p.revision != data.expected_revision:
            raise HTTPException(409, "Настройки изменились")
        if any(
            c not in {str(value) for value in p.class_ids}
            for c in data.equipment_activity
        ):
            raise HTTPException(422, "Тип активности задан для класса вне объекта")
        previous = p.settings
        p.settings = data.model_dump(exclude={"expected_revision"})
        p.revision += 1
        repo.Audits(db).record(
            id, "settings.updated", id, before=previous, after=p.settings
        )
        return obj(p)

    return mutation(db, f"{id}:settings", key, data.model_dump(), operation)


@app.get("/api/v1/projects/{id}/plans")
def plans(id: str, db=Depends(session)):
    p = project(db, id)
    return {
        "current_plan_id": p.current_plan_id,
        "plans": [
            obj(plan)
            for plan in sorted(
                repo.Plans(db).all(project_id=id), key=lambda x: x.version, reverse=True
            )
        ],
        "work_states": repo.Plans(db).states(id),
    }


def publish_plan(db, p, works, base, additional_class_ids=()):
    if base != p.current_plan_id:
        raise HTTPException(409, "Текущая версия плана изменилась")
    if any(c not in PLANNING_CLASS_IDS for c in additional_class_ids):
        raise HTTPException(422, "Неизвестный класс для добавления в план")
    class_ids = sorted(set(p.class_ids) | set(additional_class_ids))
    validation = validate_plan(works, class_ids)
    if validation["errors"]:
        raise HTTPException(422, validation)
    if set(class_ids) != set(p.class_ids):
        repo.Audits(db).record(
            p.id,
            "project.classes_extended",
            p.id,
            before=p.class_ids,
            after=class_ids,
        )
        p.class_ids = class_ids
    plan = repo.Plans(db).add(
        project_id=p.id,
        version=repo.Plans(db).next_version(p.id),
        works=works,
        parent_id=base,
        status="approved",
        approved_at=now(),
    )
    p.current_plan_id = plan.id
    p.revision += 1
    repo.Audits(db).record(
        p.id, "plan.approved", plan.id, version=plan.version, work_count=len(works)
    )
    return {**obj(plan), "validation": validation}


@app.post("/api/v1/projects/{id}/plans", status_code=201)
def create_plan(
    id: str,
    data: s.PlanInput,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    return mutation(
        db,
        f"{id}:plans",
        key,
        data.model_dump(mode="json"),
        lambda: publish_plan(
            db,
            project(db, id, True),
            [w.model_dump(mode="json") for w in data.works],
            data.base_plan_id,
            data.additional_class_ids,
        ),
    )


@app.delete("/api/v1/projects/{id}/plan")
def delete_plan(
    id: str,
    data: s.DeletePlanInput,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        p = project(db, id, True)
        if p.current_plan_id != data.expected_plan_id:
            raise HTTPException(409, "План изменился; обновите страницу")
        plan = require(current_plan(db, p), "Нет действующего плана")
        plan.status = "deleted"
        p.current_plan_id = None
        p.revision += 1
        # Keep immutable evidence and old bindings, but stop jobs using this plan.
        active = list(
            db.scalars(
                select(m.Job).where(
                    m.Job.project_id == id,
                    m.Job.kind == "analyze",
                    m.Job.status.in_(["queued", "running"]),
                )
            )
        )
        for job in active:
            job.status, job.token = "cancelled", None
            source = db.get(m.Source, job.source_id)
            source.enabled, source.status = False, "stopped"
        repo.Audits(db).record(id, "plan.deleted", plan.id, version=plan.version)
        return {"current_plan_id": None, "deleted_plan_id": plan.id}

    return mutation(db, f"{id}:delete-plan", key, data.model_dump(), operation)


@app.get("/api/v1/plans/{id}/validation")
def validation(id: str, db=Depends(session)):
    plan = require(db.get(m.Plan, id))
    # Historical versions keep their own resource columns after class expansion.
    class_ids = sorted({int(c) for w in plan.works for c in w["resources"]})
    return validate_plan(plan.works, class_ids)


@app.post("/api/v1/plans/{id}/shift-preview")
def shift_preview(id: str, data: s.ShiftInput, db=Depends(session)):
    plan = require(db.get(m.Plan, id))
    works, changes = shift_plan(plan.works, data.work_id, data.hours, data.cascade)
    states = repo.Plans(db).states(plan.project_id)
    if any(states.get(c["work_id"]) == "completed" for c in changes):
        raise HTTPException(
            422, "Завершённые этапы нельзя сдвигать; сначала отмените завершение"
        )
    return {
        "base_plan_id": id,
        "works": works,
        "changes": changes,
        "validation": validate_plan(works, project(db, plan.project_id).class_ids),
    }


@app.post("/api/v1/projects/{id}/shift")
def apply_shift(
    id: str,
    data: s.ShiftApply,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        p = project(db, id, True)
        if p.current_plan_id != data.expected_plan_id:
            raise HTTPException(409, "Предпросмотр устарел")
        plan = require(current_plan(db, p))
        works, changes = shift_plan(plan.works, data.work_id, data.hours, data.cascade)
        states = repo.Plans(db).states(id)
        if any(states.get(c["work_id"]) == "completed" for c in changes):
            raise HTTPException(422, "Нельзя менять сроки завершённых этапов")
        changed = repo.Plans(db).add(
            project_id=id,
            version=repo.Plans(db).next_version(id),
            status="approved",
            works=works,
            parent_id=plan.id,
            approved_at=now(),
        )
        p.current_plan_id = changed.id
        p.revision += 1
        repo.Audits(db).record(
            id,
            "plan.shifted",
            changed.id,
            changes=changes,
            cascade=data.cascade,
            parent_id=plan.id,
        )
        return obj(changed)

    return mutation(db, f"{id}:shift", key, data.model_dump(), operation)


@app.post("/api/v1/projects/{id}/works/{work_id}/status")
def work_status(
    id: str,
    work_id: str,
    data: s.StatusInput,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        p = project(db, id, True)
        plan = require(current_plan(db, p), "Нет утверждённого плана")
        work = require(
            next((w for w in plan.works if w["id"] == work_id), None),
            "Работа не найдена",
        )
        states = repo.Plans(db).states(id)
        warnings = []
        previous = states.get(work_id, "planned")
        if previous == data.status:
            return {"status": previous, "warnings": warnings}
        if previous == "completed" and data.status == "in_progress":
            raise HTTPException(409, "Сначала отмените завершение этапа")
        if data.status == "in_progress" and previous != "in_progress":
            start = aware(work["starts_at"])
            current = now()
            local_today = current.astimezone(ZoneInfo(p.timezone)).date()
            local_start_day = start.astimezone(ZoneInfo(p.timezone)).date()
            if local_start_day > local_today:
                if aware(work["ends_at"]) <= current:
                    raise HTTPException(
                        422, "Конец этапа должен быть позже досрочного начала"
                    )
                changed_works = [
                    {**item, "starts_at": current.isoformat()}
                    if item["id"] == work_id
                    else item
                    for item in plan.works
                ]
                changed = repo.Plans(db).add(
                    project_id=id,
                    version=repo.Plans(db).next_version(id),
                    status="approved",
                    works=changed_works,
                    parent_id=plan.id,
                    approved_at=current,
                )
                p.current_plan_id = changed.id
                p.revision += 1
                repo.Audits(db).record(
                    id,
                    "plan.early_start",
                    changed.id,
                    work_id=work_id,
                    previous_start=work["starts_at"],
                    actual_start=current.isoformat(),
                    parent_id=plan.id,
                )
                plan = changed
        repo.Plans(db).set_state(id, work_id, data.status)
        repo.Audits(db).record(
            id,
            "work.status_changed",
            work_id,
            before=previous,
            title=work["title"],
            code=work["code"],
            after=data.status,
            note=data.note,
            warnings=warnings,
            plan_id=plan.id,
        )
        return {"status": data.status, "warnings": warnings}

    return mutation(db, f"{id}:{work_id}:status", key, data.model_dump(), operation)


def parse_csv_date(value, tz):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.strptime(value, "%d.%m.%Y %H:%M")
    return dt.replace(tzinfo=ZoneInfo(tz)) if dt.tzinfo is None else dt


@app.get("/api/v1/projects/{id}/plan-template")
def plan_template(id: str, db=Depends(session)):
    p = project(db, id)
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "Код",
            "Этап стройки",
            "Дата и время начала этапа",
            "Дата и время конца этапа",
            *[class_map()[str(c)]["name"] for c in p.class_ids],
        ]
    )
    return Response(
        output.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="plan-template.csv"'},
    )


@app.get("/api/v1/projects/{id}/plans/{plan_id}/export")
def export_plan(id: str, plan_id: str, db=Depends(session)):
    p = project(db, id)
    plan = require(db.get(m.Plan, plan_id), "План не найден")
    if plan.project_id != id:
        raise HTTPException(404, "План не найден")
    # Include current columns so an archived version remains importable after expansion.
    classes = sorted(
        set(p.class_ids) | {int(c) for w in plan.works for c in w["resources"]}
    )
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "Код",
            "Этап стройки",
            "Дата и время начала этапа",
            "Дата и время конца этапа",
            *[class_map()[str(c)]["name"] for c in classes],
        ]
    )
    for work in plan.works:
        writer.writerow(
            [
                work["code"],
                work["title"],
                *[
                    parse_csv_date(work[field], p.timezone)
                    .astimezone(ZoneInfo(p.timezone))
                    .isoformat()
                    for field in ("starts_at", "ends_at")
                ],
                *[work["resources"].get(str(c), 0) for c in classes],
            ]
        )
    return Response(
        output.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="plan-v{plan.version}.csv"'
        },
    )


@app.post("/api/v1/projects/{id}/plan-imports")
async def import_csv(
    id: str,
    file: UploadFile = File(...),
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    content = await file.read(5 * 1024**2 + 1)
    if len(content) > 5 * 1024**2:
        raise HTTPException(413, "CSV больше 5 MiB")

    def operation():
        p = project(db, id, True)
        try:
            text_data = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text_data = content.decode("cp1251")
        if not text_data.strip():
            raise HTTPException(422, "CSV пуст")
        delimiter = ";" if ";" in text_data.splitlines()[0] else ","
        reader = csv.DictReader(io.StringIO(text_data), delimiter=delimiter)
        existing = current_plan(db, p)
        stable_ids = {w["code"]: w["id"] for w in existing.works} if existing else {}
        works = []
        errors = []
        for row_number, row in enumerate(reader, 2):
            try:
                title = row.get("Этап стройки") or row.get("Работа") or row.get("title")
                code = row.get("Код") or row.get("code")
                if not code:
                    matches = [w for w in catalog()["works"] if w["title"] == title]
                    if len(matches) != 1:
                        raise ValueError(
                            "Укажите Код: название отсутствует или неоднозначно"
                        )
                    code = matches[0]["code"]
                resources = {}
                for c in p.class_ids:
                    name = class_map()[str(c)]["name"]
                    raw = row.get(name, row.get(str(c)))
                    if name not in (reader.fieldnames or []) and str(c) not in (
                        reader.fieldnames or []
                    ):
                        raise ValueError(f"Нет колонки «{name}» в CSV")
                    resources[str(c)] = (
                        0 if raw is None or not raw.strip() else int(raw)
                    )
                work = s.Work(
                    code=code,
                    title=title,
                    starts_at=parse_csv_date(
                        row.get("Дата и время начала этапа") or row["starts_at"],
                        p.timezone,
                    ),
                    ends_at=parse_csv_date(
                        row.get("Дата и время конца этапа") or row["ends_at"],
                        p.timezone,
                    ),
                    resources=resources,
                )
                if code in stable_ids:
                    work.id = stable_ids[code]
                works.append(work.model_dump(mode="json"))
            except (ValueError, KeyError, TypeError) as error:
                errors.append({"row": row_number, "message": str(error)})
            if row_number > 1001:
                raise HTTPException(413, "Не более 1000 работ")
        if errors:
            raise HTTPException(422, {"rows": errors})
        if not works:
            raise HTTPException(422, "CSV пуст")
        return publish_plan(db, p, works, p.current_plan_id)

    return mutation(db, f"{id}:csv", key, {"sha256": digest(content)}, operation)


@app.get("/api/v1/projects/{id}/sources")
def sources(id: str, db=Depends(session)):
    project(db, id)
    return [public_source(source, db) for source in repo.Sources(db).all(project_id=id)]


@app.post("/api/v1/projects/{id}/sources")
def create_source(
    id: str,
    data: s.SourceInput,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        project(db, id, True)
        if len(repo.Sources(db).all(project_id=id)) >= 20:
            raise HTTPException(422, "Лимит демонстрации - 20 источников на объект")
        encrypted = validate_rtsp(data.uri)
        source = repo.Sources(db).add(
            project_id=id,
            name=data.name,
            kind="rtsp",
            uri_encrypted=encrypted,
            enabled=True,
            sample_seconds=data.sample_seconds,
        )
        job = repo.Jobs(db).add(project_id=id, source_id=source.id, kind="probe")
        repo.Audits(db).record(
            id, "source.created", source.id, name=source.name, kind="rtsp"
        )
        return {"source": public_source(source), "job": obj(job)}

    return mutation(db, f"{id}:sources", key, data.model_dump(), operation)


@app.post("/api/v1/projects/{id}/media", status_code=202)
async def upload(
    id: str,
    file: UploadFile = File(...),
    name: str = Form(""),
    capture_start: str = Form(""),
    sample_seconds: float = Form(1),
    demo_loop_enabled: bool = Form(False),
    demo_loop_duration_seconds: float | None = Form(None),
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    project(db, id)
    if not 1 <= sample_seconds <= 3600:
        raise HTTPException(422, "Интервал анализа от 1 до 3600 секунд")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".mp4", ".mkv", ".avi", ".mov", ".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(422, "Поддерживаются MP4/MKV/AVI/MOV и PNG/JPG/WebP")
    timestamp = (
        datetime.fromisoformat(capture_start.replace("Z", "+00:00"))
        if capture_start
        else None
    )
    if timestamp and timestamp.tzinfo is None:
        raise HTTPException(422, "Укажите часовой пояс начала видео")
    is_video = suffix in (".mp4", ".mkv", ".avi", ".mov")
    if demo_loop_enabled:
        if not is_video:
            raise HTTPException(422, "Зацикливание доступно только для видеофайла")
        if not timestamp:
            raise HTTPException(
                422, "Для тестового зацикливания укажите начало демонстрации"
            )
        if (
            demo_loop_duration_seconds is None
            or not math.isfinite(demo_loop_duration_seconds)
            or demo_loop_duration_seconds <= 0
        ):
            raise HTTPException(422, "Укажите длительность демонстрации")
    file_key = f"media/{uid()}{suffix}"
    path = DATA / file_key
    temporary = path.with_suffix(".upload")
    size = 0
    hasher = hashlib.sha256()
    try:
        with temporary.open("wb") as out:
            while chunk := await file.read(1024**2):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Превышен лимит загрузки")
                out.write(chunk)
                hasher.update(chunk)
        if size == 0:
            raise HTTPException(422, "Файл пуст")
        temporary.replace(path)

        def operation():
            project(db, id, True)
            if len(repo.Sources(db).all(project_id=id)) >= 20:
                raise HTTPException(422, "Лимит - 20 источников на объект")
            source = repo.Sources(db).add(
                project_id=id,
                name=(name or file.filename or "Медиа")[:200],
                kind="video" if is_video else "image",
                file_key=file_key,
                capture_start=timestamp,
                sample_seconds=sample_seconds,
                metadata_json={
                    "sha256": hasher.hexdigest(),
                    "bytes": size,
                    **(
                        {
                            "demo_loop": {
                                "enabled": True,
                                "duration_seconds": demo_loop_duration_seconds,
                            }
                        }
                        if demo_loop_enabled
                        else {}
                    ),
                },
            )
            job = repo.Jobs(db).add(project_id=id, source_id=source.id, kind="probe")
            repo.Audits(db).record(
                id,
                "media.uploaded",
                source.id,
                name=source.name,
                sha256=hasher.hexdigest(),
                capture_start=capture_start,
                demo_loop=demo_loop_enabled,
                demo_loop_duration_seconds=demo_loop_duration_seconds,
            )
            return {"source": public_source(source), "job": obj(job)}

        result = mutation(
            db,
            f"{id}:media",
            key,
            {
                "sha256": hasher.hexdigest(),
                "name": name,
                "capture_start": capture_start,
                "sample_seconds": sample_seconds,
                "demo_loop_enabled": demo_loop_enabled,
                "demo_loop_duration_seconds": demo_loop_duration_seconds,
            },
            operation,
        )
        if result["source"]["id"] and not db.scalar(
            select(m.Source.id).where(m.Source.file_key == file_key)
        ):
            path.unlink(missing_ok=True)
        return result
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)


@app.get("/api/v1/sources/{id}/preview")
def source_preview(id: str, db=Depends(session)):
    require(db.get(m.Source, id))
    path = DATA / f"previews/{id}.jpg"
    if not path.exists():
        raise HTTPException(404, "Кадр ещё не получен")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/v1/sources/{id}/content")
def source_content(id: str, db=Depends(session)):
    source = require(db.get(m.Source, id))
    if not source.file_key:
        raise HTTPException(404, "Локальный файл отсутствует")
    return FileResponse(DATA / source.file_key)


@app.get("/api/v1/sources/{id}/connection", response_model=s.SourceConnection)
def source_connection(id: str, response: Response, db=Depends(session)):
    source = require(db.get(m.Source, id))
    if source.kind != "rtsp":
        raise HTTPException(422, "Адрес потока доступен только для сетевого источника")
    response.headers["Cache-Control"] = "no-store"
    try:
        uri = decode_uri(source.uri_encrypted)
    except Exception:
        raise HTTPException(
            409, "Не удалось прочитать сохранённый адрес. Введите его заново."
        ) from None
    return {"uri": uri}


@app.patch("/api/v1/sources/{id}")
def source_config(
    id: str,
    data: s.SourceConfig,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        source = require(db.get(m.Source, id))
        project(db, source.project_id, True)
        encrypted = None
        address_changed = False
        if data.uri is not None:
            if source.kind != "rtsp":
                raise HTTPException(
                    422, "Адрес потока доступен только для сетевого источника"
                )
            encrypted = validate_rtsp(data.uri)
            try:
                address_changed = decode_uri(source.uri_encrypted) != data.uri
            except Exception:
                address_changed = True
        if repo.Jobs(db).active(id) and not address_changed:
            raise HTTPException(409, "Сначала остановите обработку")
        if data.name is not None:
            if not data.name.strip():
                raise HTTPException(422, "Укажите название источника")
            source.name = data.name.strip()
        source.sample_seconds = data.sample_seconds
        if address_changed:
            source.uri_encrypted = encrypted
            queue_source_probe(db, source)
        if "capture_start" in data.model_fields_set:
            if source.kind == "rtsp":
                raise HTTPException(
                    422, "Время сетевого потока определяется по приёму сервером"
                )
            source.capture_start = data.capture_start
        if (
            "demo_loop_enabled" in data.model_fields_set
            or "demo_loop_duration_seconds" in data.model_fields_set
        ):
            if source.kind != "video":
                raise HTTPException(422, "Зацикливание доступно только для видеофайла")
            current_loop = demo_loop_config(source) or {}
            enabled = (
                data.demo_loop_enabled
                if data.demo_loop_enabled is not None
                else bool(current_loop)
            )
            duration = (
                data.demo_loop_duration_seconds
                if data.demo_loop_duration_seconds is not None
                else current_loop.get("duration_seconds")
            )
            metadata = {**(source.metadata_json or {})}
            if enabled:
                if duration is None:
                    raise HTTPException(422, "Укажите длительность демонстрации")
                if not source.capture_start:
                    raise HTTPException(
                        422, "Для тестового зацикливания укажите начало демонстрации"
                    )
                metadata["demo_loop"] = {
                    "enabled": True,
                    "duration_seconds": duration,
                }
            else:
                metadata.pop("demo_loop", None)
            source.metadata_json = metadata
        if demo_loop_config(source) and not source.capture_start:
            raise HTTPException(
                422, "Для тестового зацикливания укажите начало демонстрации"
            )
        # Existing bindings may refer to an earlier plan revision. Validate the
        # stage window when analysis starts, not while saving source metadata.
        repo.Audits(db).record(
            source.project_id,
            "source.configured",
            id,
            name=source.name,
            sample_seconds=data.sample_seconds,
            rtsp_address_changed=address_changed,
            capture_start=source.capture_start.isoformat()
            if source.capture_start
            else None,
            demo_loop=demo_loop_config(source),
        )
        return public_source(source)

    return mutation(db, f"{id}:source-config", key, data.model_dump(), operation)


@app.delete("/api/v1/sources/{id}")
def delete_source(
    id: str,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    files = []

    def operation():
        source = require(db.get(m.Source, id))
        project(db, source.project_id, True)
        if repo.Jobs(db).active(id):
            raise HTTPException(409, "Сначала остановите обработку источника")
        observations = list(
            db.scalars(select(m.Observation).where(m.Observation.source_id == id))
        )
        observation_ids = [row.id for row in observations]
        files.extend(DATA / row.evidence_key for row in observations)
        if source.file_key:
            files.append(DATA / source.file_key)
        files.append(DATA / f"previews/{source.id}.jpg")
        db.execute(delete(m.Alert).where(m.Alert.source_id == id))
        if observation_ids:
            db.execute(
                delete(m.Assessment).where(
                    m.Assessment.observation_id.in_(observation_ids)
                )
            )
        db.execute(delete(m.Observation).where(m.Observation.source_id == id))
        db.execute(delete(m.Job).where(m.Job.source_id == id))
        db.execute(delete(m.Binding).where(m.Binding.source_id == id))
        project_id, name, kind = source.project_id, source.name, source.kind
        db.delete(source)
        repo.Audits(db).record(project_id, "source.deleted", id, name=name, kind=kind)
        return {"deleted_source_id": id, "name": name}

    result = mutation(db, f"{id}:source-delete", key, {}, operation)
    for path in files:
        resolved = path.resolve()
        if resolved.is_relative_to(DATA.resolve()):
            resolved.unlink(missing_ok=True)
    return result


@app.get("/api/v1/sources/{id}/bindings")
def bindings(id: str, db=Depends(session)):
    require(db.get(m.Source, id))
    binding = repo.Bindings(db).latest(id)
    return obj(binding) if binding else {"revision": 0, "regions": []}


@app.post("/api/v1/sources/{id}/bindings")
def bind(
    id: str,
    data: s.BindingInput,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        source = require(db.get(m.Source, id))
        p = project(db, source.project_id, True)
        plan = require(current_plan(db, p), "Сначала утвердите план")
        old = repo.Bindings(db).latest(id)
        if (old.revision if old else 0) != data.expected_revision:
            raise HTTPException(409, "Зоны изменились")
        work_ids = {w["id"] for w in plan.works}
        if any(w not in work_ids for r in data.regions for w in r.work_ids):
            raise HTTPException(422, "Работа не принадлежит текущему плану объекта")
        ids = [r.id for r in data.regions]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Повторяются ID зон")
        serialized_regions = [r.model_dump(mode="json") for r in data.regions]
        validate_demo_loop(source, plan, serialized_regions)
        binding = repo.Bindings(db).add(
            project_id=p.id,
            source_id=id,
            revision=data.expected_revision + 1,
            regions=serialized_regions,
        )
        repo.Audits(db).record(
            p.id,
            "zones.updated",
            binding.id,
            source_id=id,
            revision=binding.revision,
            name=source.name,
            regions=len(binding.regions),
        )
        queue_bound_video(db, source)
        return obj(binding)

    return mutation(db, f"{id}:bindings", key, data.model_dump(mode="json"), operation)


@app.post("/api/v1/sources/{id}/analyze", status_code=202)
def analyze(
    id: str,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        source = require(db.get(m.Source, id))
        p = project(db, source.project_id, True)
        if repo.Jobs(db).active(id):
            raise HTTPException(409, "Источник уже обрабатывается")
        status = model_status()
        if not status["ready"]:
            raise HTTPException(
                409, {"code": "model_unavailable", "message": status["reason"]}
            )
        binding = repo.Bindings(db).latest(source.id)
        loop_config = validate_demo_loop(
            source,
            current_plan(db, p),
            binding.regions if binding else [],
            require_media_duration=True,
        )
        source.enabled = source.kind == "rtsp"
        source.status = "queued"
        source.error = None
        job = repo.Jobs(db).add(
            project_id=p.id,
            source_id=id,
            kind="analyze",
            payload=analysis_payload(db, p, source, loop_config),
        )
        repo.Audits(db).record(
            p.id, "analysis.started", job.id, source_id=id, plan_id=p.current_plan_id
        )
        return obj(job)

    return mutation(db, f"{id}:analyze", key, {}, operation)


@app.post("/api/v1/sources/{id}/probe", status_code=202)
def probe(
    id: str,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        source = require(db.get(m.Source, id))
        project(db, source.project_id, True)
        if repo.Jobs(db).active(id) and source.kind != "rtsp":
            raise HTTPException(409, "Источник уже обрабатывается")
        return obj(queue_source_probe(db, source))

    return mutation(db, f"{id}:probe", key, {}, operation)


def queue_source_probe(db, source):
    # Fence the old owner before replacing the connection, including queued retries.
    db.execute(
        update(m.Job)
        .where(m.Job.source_id == source.id, m.Job.status.in_(["queued", "running"]))
        .values(status="cancelled", token=None)
    )
    source.enabled = source.kind == "rtsp"
    source.status = "queued"
    source.error = None
    return repo.Jobs(db).add(
        project_id=source.project_id, source_id=source.id, kind="probe"
    )


@app.post("/api/v1/sources/{id}/stop")
def stop(
    id: str,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        source = require(db.get(m.Source, id))
        source.enabled = False
        source.status = "stopped"
        db.execute(
            update(m.Job)
            .where(m.Job.source_id == id, m.Job.status.in_(["queued", "running"]))
            .values(status="cancelled", token=None)
        )
        repo.Audits(db).record(source.project_id, "source.stopped", id)
        return public_source(source)

    return mutation(db, f"{id}:stop", key, {}, operation)


@app.get("/api/v1/projects/{id}/jobs")
def jobs(id: str, db=Depends(session)):
    project(db, id)
    rows = list(
        db.scalars(
            select(m.Job)
            .where(m.Job.project_id == id)
            .order_by(m.Job.created_at.desc())
            .limit(100)
        )
    )
    totals = {
        run_id: (total, assessed)
        for run_id, total, assessed in db.execute(
            select(
                m.Observation.run_id,
                func.count(m.Observation.id),
                func.count(m.Assessment.id),
            )
            .outerjoin(m.Assessment, m.Assessment.observation_id == m.Observation.id)
            .where(m.Observation.run_id.in_([job.id for job in rows]))
            .group_by(m.Observation.run_id)
        )
    }
    result = []
    for job in rows:
        item = obj(job)
        source = db.get(m.Source, job.source_id)
        total, assessed = totals.get(job.id, (0, 0))
        ready = job.status == "succeeded" and total > 0 and assessed == total
        works = job.payload.get("works")
        if works is None:
            plan = (
                db.get(m.Plan, job.payload.get("plan_id"))
                if job.payload.get("plan_id")
                else None
            )
            binding = (
                db.get(m.Binding, job.payload.get("binding_id"))
                if job.payload.get("binding_id")
                else None
            )
            ids = {
                wid
                for region in (binding.regions if binding else [])
                for wid in region.get("work_ids", [])
            }
            works = [
                {key: work[key] for key in ("id", "code", "title")}
                for work in (plan.works if plan else [])
                if work["id"] in ids
            ]
        item.update(
            works=works,
            source_name=source.name if source else "",
            started_at=job.payload.get("started_at"),
            continuous=bool(source and source.kind == "rtsp"),
            results_ready=ready,
            analysis_state=(
                "ready"
                if ready
                else "assessing"
                if job.status == "succeeded"
                else job.status
            ),
        )
        result.append(item)
    return result


@app.get("/api/v1/sources/{id}/observations")
def observations(id: str, limit: int = Query(100, ge=1, le=1000), db=Depends(session)):
    require(db.get(m.Source, id))
    return [obj(o) for o in repo.Observations(db).recent(id, limit)]


@app.get("/api/v1/observations/{id}")
def observation(id: str, db=Depends(session)):
    return obj(require(db.get(m.Observation, id)))


@app.get("/api/v1/evidence/{id}")
def evidence(id: str, db=Depends(session)):
    observation = require(db.get(m.Observation, id))
    path = DATA / observation.evidence_key
    if not path.exists():
        raise HTTPException(410, "Свидетельство удалено")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/v1/projects/{id}/monitoring")
def monitoring(id: str, db=Depends(session)):
    p = project(db, id)
    plan = current_plan(db, p)
    sources = repo.Sources(db).all(project_id=id)
    cards = []
    states = repo.Plans(db).states(id)
    dependency_warnings = []
    for source in sources:
        binding = repo.Bindings(db).latest(source.id)
        observation = db.scalar(
            select(m.Observation)
            .where(m.Observation.source_id == source.id)
            .order_by(m.Observation.created_at.desc())
            .limit(1)
        )
        assessment = (
            db.scalar(
                select(m.Assessment).where(
                    m.Assessment.observation_id == observation.id
                )
            )
            if observation
            else None
        )
        cards.append(
            {
                "source": public_source(source, db),
                "binding": obj(binding) if binding else None,
                "observation": obj(observation) if observation else None,
                "assessment": obj(assessment) if assessment else None,
                "stale_plan": bool(
                    assessment and assessment.plan_id != p.current_plan_id
                ),
                "stale_context": bool(
                    assessment
                    and (job := db.get(m.Job, observation.run_id))
                    and assessment_context_changed(
                        job.payload, states, effective_project_settings(p)
                    )
                ),
            }
        )
    return {
        "project": public_project_record(p),
        "plan": obj(plan) if plan else None,
        "work_states": states,
        "dependency_warnings": dependency_warnings,
        "cards": cards,
        "alerts": [obj(a) for a in unique_alerts(repo.Alerts(db).recent(id))],
        "model": model_status(),
        "generated_at": now().isoformat(),
    }


@app.get("/api/v1/projects/{id}/timeseries")
def timeseries(
    id: str,
    source_id: str,
    work_id: str | None = Query(None),
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    before: datetime | None = Query(None),
    before_sample_index: int | None = Query(None, ge=0),
    before_id: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    display_seconds: int = Query(0, ge=0, le=86400),
    db=Depends(session),
):
    validate_period(from_time, to_time)
    validate_period(before, None)
    p = project(db, id)
    source = require(db.get(m.Source, source_id))
    if source.project_id != p.id:
        raise HTTPException(404, "Источник не принадлежит объекту")
    plan = current_plan(db, p)
    selected_work = next(
        (work for work in (plan.works if plan else []) if work["id"] == work_id),
        None,
    )
    if work_id and not selected_work:
        raise HTTPException(422, "Этап отсутствует в текущем плане")
    query = select(m.Observation).where(m.Observation.source_id == source_id)
    if selected_work:
        query = query.where(
            m.Observation.captured_at >= aware(selected_work["starts_at"]),
            m.Observation.captured_at < aware(selected_work["ends_at"]),
        )
    if to_time:
        query = query.where(m.Observation.captured_at < to_time)
    # Replay event-time history before selecting display buckets. Request/page
    # boundaries are not observation gaps and must not reset confirmation.
    rows = list(
        db.scalars(
            query.order_by(
                m.Observation.run_id,
                m.Observation.captured_at,
                m.Observation.offset_seconds,
                m.Observation.sample_index,
                m.Observation.id,
            )
        )
    )
    settings = effective_project_settings(p)
    regions_by_run = {}
    steps_by_run = {}
    for run_id in {row.run_id for row in rows}:
        job = db.get(m.Job, run_id)
        steps_by_run[run_id] = (
            job.payload.get("sample_seconds") if job else None
        ) or source.sample_seconds
        binding_id = job.payload.get("binding_id") if job else None
        binding = db.get(m.Binding, binding_id) if binding_id else None
        regions_by_run[run_id] = binding.regions if binding else []
    # Stage queries also need classes absent from the plan for the additional-equipment chart.
    class_ids = sorted(
        set(p.class_ids)
        | {
            int(class_id)
            for row in rows
            for class_id in row.model.get("supported_classes", [])
        }
        | (
            {
                int(class_id)
                for class_id, count in selected_work["resources"].items()
                if count > 0
            }
            if selected_work
            else set()
        )
    )
    points = series_points(
        rows,
        class_ids,
        display_seconds,
        settings["max_gap_seconds"],
        source.sample_seconds,
        settings["absence_confirm_seconds"],
        work_id,
        regions_by_run,
        settings["count_change_confirm_seconds"],
        steps_by_run,
        from_time,
    )
    by_id = {row.id: row for row in rows}

    def cursor(point):
        row = by_id[point["id"]]
        return (aware(row.created_at), row.sample_index, row.id)

    candidates = sorted(points, key=cursor, reverse=True)
    if from_time:
        candidates = [
            point
            for point in candidates
            if point["time"] and aware(point["time"]) >= from_time
        ]
    if before:
        boundary = (before, before_sample_index, before_id)
        candidates = [
            point
            for point in candidates
            if (
                cursor(point) < boundary
                if before_sample_index is not None and before_id
                else cursor(point)[0] < before
            )
        ]
    page, sampled = [], 0
    for point in candidates:
        if page and sampled + point["sample_count"] > limit:
            break
        page.append(point)
        sampled += point["sample_count"]
    truncated = len(page) < len(candidates)
    boundary = cursor(page[-1]) if page and truncated else None
    return {
        "truncated": truncated,
        "next_before": boundary[0].isoformat() if boundary else None,
        "next_before_sample_index": boundary[1] if boundary else None,
        "next_before_id": boundary[2] if boundary else None,
        "display_seconds": display_seconds,
        "work_id": work_id,
        "sampled_from": sampled,
        "points": list(reversed(page)),
    }


@app.get("/api/v1/projects/{id}/alerts")
def alerts(id: str, db=Depends(session)):
    project(db, id)
    return [obj(a) for a in unique_alerts(repo.Alerts(db).recent(id))]


@app.post("/api/v1/alerts/{id}/reviews")
def review(
    id: str,
    data: s.ReviewInput,
    db=Depends(session),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def operation():
        alert = require(db.get(m.Alert, id))
        alert.reviewed = True
        alert.details = {
            **alert.details,
            "review": {
                "decision": data.decision,
                "note": data.note,
                "reviewed_at": now().isoformat(),
                "assessment_id": alert.assessment_id,
            },
        }
        event = repo.Audits(db).record(
            alert.project_id,
            "alert.reviewed",
            id,
            decision=data.decision,
            note=data.note,
            assessment_id=alert.assessment_id,
            episode=alert.details.get("episode", 1),
        )
        return obj(event)

    return mutation(db, f"{id}:review", key, data.model_dump(), operation)


@app.get("/api/v1/alerts/{id}/evidence")
def alert_evidence(
    id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    class_id: str | None = Query(None),
    db=Depends(session),
):
    alert = require(db.get(m.Alert, id))
    source = require(db.get(m.Source, alert.source_id))
    expected = {
        str(key): int(value)
        for key, value in (alert.details.get("expected") or {}).items()
        if alert.kind != "missing" or int(value) > 0
    }
    if alert.kind == "missing":
        signal_counts = alert.details.get("counts") or {}
        relevant_classes = {
            key
            for key, needed in expected.items()
            if signal_counts.get(key) is not None and int(signal_counts[key]) < needed
        }
    else:
        relevant_classes = {
            str(item["class_id"])
            for item in (alert.details.get("classes") or [])
            if int(item.get("count") or 0) > 0
        }
    if class_id is not None and class_id not in relevant_classes:
        raise HTTPException(422, "Класс не относится к этому отклонению")
    base_query = (
        select(m.Observation)
        .where(
            m.Observation.project_id == alert.project_id,
            m.Observation.source_id == alert.source_id,
            m.Observation.run_id == alert.run_id,
            m.Observation.captured_at >= alert.first_seen,
            m.Observation.captured_at <= alert.last_seen,
        )
        .order_by(m.Observation.captured_at, m.Observation.sample_index)
    )
    episode_rows = list(db.scalars(base_query.limit(5001)))
    stats_truncated = len(episode_rows) > 5000
    episode_rows = episode_rows[:5000]
    observation_ids = [row.id for row in episode_rows]
    assessments = (
        list(
            db.scalars(
                select(m.Assessment).where(
                    m.Assessment.observation_id.in_(observation_ids)
                )
            )
        )
        if observation_ids
        else []
    )
    assessment_by_observation = {
        assessment.observation_id: assessment for assessment in assessments
    }

    def matching_result(observation):
        assessment = assessment_by_observation.get(observation.id)
        if not assessment:
            return None
        candidates = [
            result
            for result in assessment.results
            if result.get("work_id") == alert.work_id
        ]
        region_id = alert.details.get("region_id")
        if region_id:
            return next(
                (
                    result
                    for result in candidates
                    if result.get("region_id") == region_id
                ),
                None,
            )
        return next(
            (
                result
                for result in candidates
                if result.get("region_name") == alert.details.get("region_name")
            ),
            candidates[0] if candidates else None,
        )

    basis = db.get(m.Observation, alert.details.get("evidence_id"))
    if basis and (
        basis.project_id != alert.project_id
        or basis.run_id != alert.run_id
        or basis.source_id != alert.source_id
    ):
        basis = None

    confirmation_seconds = float(alert.details.get("confirmation_seconds") or 0)
    expected = {
        key: value for key, value in expected.items() if key in relevant_classes
    }
    states = {key: {"missing_since": None, "state": "unknown"} for key in expected}
    evidence_settings = (
        assessments[0].settings if assessments else s.Settings().model_dump()
    )
    job = db.get(m.Job, alert.run_id)
    allowed_gap = max(
        float(evidence_settings.get("max_gap_seconds", 3)),
        float(
            (job.payload.get("sample_seconds") if job else None)
            or source.sample_seconds
            or 0
        )
        * 1.5,
    )
    totals = {
        key: {"present": 0.0, "absent": 0.0, "pending": 0.0, "unknown": 0.0}
        for key in expected
    }
    confirmed_missing = {}
    for index, observation in enumerate(episode_rows):
        current_time = aware(observation.captured_at).timestamp()
        result = matching_result(observation)
        counts = result.get("counts", {}) if result else {}
        for key, needed in expected.items():
            count = counts.get(key)
            state = states[key]
            if count is None:
                state["missing_since"] = None
                state["state"] = "unknown"
            elif count < needed:
                if state["missing_since"] is None:
                    state["missing_since"] = current_time
                state["state"] = (
                    "absent"
                    if current_time - state["missing_since"] >= confirmation_seconds
                    else "pending"
                )
            else:
                state["missing_since"] = None
                state["state"] = "present"
        confirmed_missing[observation.id] = [
            key for key, state in states.items() if state["state"] == "absent"
        ]
        if index + 1 >= len(episode_rows):
            continue
        next_time = aware(episode_rows[index + 1].captured_at).timestamp()
        seconds = max(0.0, next_time - current_time)
        if seconds > allowed_gap:
            for key in expected:
                totals[key]["unknown"] += seconds
                states[key] = {"missing_since": None, "state": "unknown"}
            continue
        for key in expected:
            totals[key][states[key]["state"]] += seconds

    if alert.kind != "missing":
        for observation in episode_rows:
            result = matching_result(observation)
            finding = next(
                (
                    item
                    for item in (result or {}).get("findings", [])
                    if item.get("kind") == alert.kind
                ),
                None,
            )
            confirmed_missing[observation.id] = [
                str(item["class_id"])
                for item in (finding or {}).get("classes", [])
                if str(item["class_id"]) in relevant_classes
            ]
    actual_missing_ids = sorted(
        {key for keys in confirmed_missing.values() for key in keys} & relevant_classes
    )
    # The alert is already confirmed. Retention or an episode shorter than the
    # replay window must not hide its saved class and basis frame in review.
    if not actual_missing_ids and relevant_classes:
        actual_missing_ids = sorted(relevant_classes)
        if alert.kind == "missing":
            for observation in episode_rows:
                result = matching_result(observation)
                counts = (result or {}).get("counts", {})
                confirmed_missing[observation.id] = [
                    key
                    for key in actual_missing_ids
                    if counts.get(key) is not None and counts[key] < expected[key]
                ]
        if basis:
            confirmed_missing[basis.id] = sorted(
                set(confirmed_missing.get(basis.id, [])) | relevant_classes
            )
    filtered_classes = {class_id} if class_id is not None else set(actual_missing_ids)
    keyframe_candidates = [
        o
        for o in episode_rows
        if filtered_classes.intersection(confirmed_missing[o.id])
    ]
    if (
        not keyframe_candidates
        and basis
        and filtered_classes.intersection(confirmed_missing.get(basis.id, []))
    ):
        keyframe_candidates = [basis]
    if class_id is not None:
        rows = keyframe_candidates[offset : offset + limit + 1]
        if basis and class_id not in confirmed_missing.get(basis.id, []):
            basis = None
    else:
        rows = list(db.scalars(base_query.offset(offset).limit(limit + 1)))
    if len(keyframe_candidates) > 6:
        indexes = {
            round(index * (len(keyframe_candidates) - 1) / 5) for index in range(6)
        }
        keyframe_candidates = [keyframe_candidates[index] for index in sorted(indexes)]

    absence_stats = []
    for key, values in totals.items():
        if alert.kind != "missing":
            continue
        if key not in actual_missing_ids:
            continue
        measured = values["present"] + values["absent"]
        absence_stats.append(
            {
                "class_id": key,
                "present_seconds": values["present"],
                "absent_seconds": values["absent"],
                "pending_seconds": values["pending"],
                "unknown_seconds": values["unknown"],
                "absence_ratio": values["absent"] / measured if measured else None,
            }
        )

    def frame(o):
        return {
            **obj(o),
            "image_url": f"/api/v1/evidence/{o.id}",
            "deviation_class_ids": confirmed_missing.get(o.id, []),
            "missing_class_ids": confirmed_missing.get(o.id, [])
            if alert.kind == "missing"
            else [],
        }

    return {
        "source": public_source(source),
        "frames": [frame(o) for o in rows[:limit]],
        "basis": frame(basis) if basis else None,
        "keyframes": [frame(o) for o in keyframe_candidates],
        "deviation_class_ids": actual_missing_ids,
        "missing_class_ids": actual_missing_ids if alert.kind == "missing" else [],
        "absence_stats": absence_stats,
        "stats_truncated": stats_truncated,
        "next_offset": offset + limit if len(rows) > limit else None,
        "video_url": f"/api/v1/sources/{source.id}/content"
        if source.kind == "video"
        and source.file_key
        and (DATA / source.file_key).is_file()
        else None,
    }


@app.get("/api/v1/projects/{id}/audit")
def audit(id: str, db=Depends(session)):
    project(db, id)
    return [obj(event) for event in repo.Audits(db).recent(id)]


@app.get("/api/v1/projects/{id}/reports/{format}")
def report(
    id: str,
    format: str,
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    db=Depends(session),
):
    validate_period(from_time, to_time)
    if format not in ("csv", "xlsx"):
        raise HTTPException(422, "Формат csv или xlsx")
    p = project(db, id)
    plan = current_plan(db, p)
    alert_query = select(m.Alert).where(m.Alert.project_id == id)
    if from_time:
        alert_query = alert_query.where(m.Alert.last_seen >= from_time)
    if to_time:
        alert_query = alert_query.where(m.Alert.first_seen < to_time)
    report_alerts = unique_alerts(
        list(db.scalars(alert_query)), include_suppressed=True
    )
    plan_ids = {
        alert.details.get("plan_id")
        for alert in report_alerts
        if (alert.details or {}).get("plan_id")
    }
    plan_versions = (
        {
            version.id: version
            for version in db.scalars(select(m.Plan).where(m.Plan.id.in_(plan_ids)))
        }
        if plan_ids
        else {}
    )
    payload = build_report(
        p,
        plan,
        repo.Sources(db).all(project_id=id),
        [],
        {},
        report_alerts,
        format,
        plan_versions,
        from_time,
        to_time,
    )
    return Response(
        payload,
        media_type="text/csv"
        if format == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="stroykontur-{id[:8]}.{format}"'
        },
    )


def validate_period(start, end):
    if any(v is not None and v.tzinfo is None for v in (start, end)):
        raise HTTPException(422, "Период должен содержать часовой пояс")
    if start and end and start >= end:
        raise HTTPException(422, "Конец периода должен быть позже начала")


# Windows registry associations can map JavaScript to text/plain; ES modules
# require a JavaScript MIME type regardless of the host's editor associations.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
static = Path(os.getenv("STATIC_DIR", str(ROOT.parent / "web" / "dist")))
if static.is_dir():
    app.mount("/", StaticFiles(directory=static, html=True), name="frontend")

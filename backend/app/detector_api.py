"""Independent detector experiments. No plan, project or assessment is created."""

import hashlib
from pathlib import Path
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from .api_common import obj, require, mutation
from .config import DATA, MAX_UPLOAD_BYTES
from .db import session, uid, now
from .models import DetectorRun, DetectorFrame
from .model_settings import (
    SettingsConflict,
    public_settings,
    update_product_settings,
)
from .vision import (
    DEFAULT_TILING_PARAMETERS,
    model_status,
    normalize_inference_parameters,
    normalize_tiling_parameters,
)

router = APIRouter(prefix="/api/v1/detector", tags=["Detector lab"])
IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEOS = {".mp4", ".mov", ".mkv", ".avi"}
MAX_BATCH_FRAMES = 100


def count_form(value, one, few, many):
    if value % 10 == 1 and value % 100 != 11:
        return one
    if value % 10 in (2, 3, 4) and value % 100 not in (12, 13, 14):
        return few
    return many


class ProductSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confidence: float = Field(gt=0, le=1)
    iou: float = Field(gt=0, le=1)
    max_detections: int = Field(ge=1, le=1000)
    expected_revision: int = Field(ge=0)


def public_run(row):
    result = obj(row)
    for field in ("file_key", "token", "lease_until"):
        result.pop(field, None)
    result["content_url"] = f"/api/v1/detector/runs/{row.id}/content"
    result["preview_url"] = (
        f"/api/v1/detector/runs/{row.id}/preview" if row.processed_frames else None
    )
    result["inference_parameters"] = (row.metadata_json or {}).get(
        "inference_parameters", {}
    )
    result["tiling"] = (row.metadata_json or {}).get(
        "tiling", DEFAULT_TILING_PARAMETERS
    )
    return result


@router.get("/settings")
def settings():
    return public_settings(DATA, model_status())


@router.put("/settings")
def update_settings(
    data: ProductSettingsInput,
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    if not key or len(key) > 100:
        raise HTTPException(422, "Нужен Idempotency-Key длиной до 100 символов")
    try:
        return update_product_settings(
            DATA,
            model_status(),
            data.model_dump(exclude={"expected_revision"}),
            data.expected_revision,
            key,
        )
    except SettingsConflict as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/runs", status_code=202)
async def upload_run(
    files: list[UploadFile] = File(..., alias="file"),
    mode: Literal["image", "batch", "full", "frame", "segment"] = Form("image"),
    start_seconds: float = Form(0, ge=0, le=604800, allow_inf_nan=False),
    end_seconds: float | None = Form(None, gt=0, le=604800, allow_inf_nan=False),
    sample_seconds: float = Form(1, ge=1, le=3600, allow_inf_nan=False),
    confidence: float | None = Form(None, gt=0, le=1, allow_inf_nan=False),
    iou: float | None = Form(None, gt=0, le=1, allow_inf_nan=False),
    max_detections: int | None = Form(None, ge=1, le=1000),
    tiled: bool = Form(False),
    tile_overlap: float = Form(0.2, gt=0, le=0.5, allow_inf_nan=False),
    key: str | None = Header(None, alias="Idempotency-Key"),
    db=Depends(session),
):
    if not key or len(key) > 100:
        raise HTTPException(422, "Нужен Idempotency-Key длиной до 100 символов")
    if not 1 <= len(files) <= MAX_BATCH_FRAMES:
        raise HTTPException(422, f"Можно загрузить от 1 до {MAX_BATCH_FRAMES} файлов")
    suffixes = [Path(file.filename or "").suffix.lower() for file in files]
    if any(suffix not in IMAGES | VIDEOS for suffix in suffixes):
        raise HTTPException(422, "Поддерживаются PNG/JPG/WebP и MP4/MOV/MKV/AVI")
    batch = len(files) > 1
    if batch:
        if mode != "batch" or any(suffix not in IMAGES for suffix in suffixes):
            raise HTTPException(422, "Пачка должна содержать только изображения")
        kind = "batch"
    else:
        kind = "image" if suffixes[0] in IMAGES else "video"
        if mode == "batch" or ((kind == "image") != (mode == "image")):
            raise HTTPException(422, "Режим не соответствует типу файла")
    if mode in ("image", "batch", "full") and start_seconds != 0:
        raise HTTPException(422, "В этом режиме начало должно быть 0")
    if mode == "segment":
        if end_seconds is None or end_seconds <= start_seconds:
            raise HTTPException(422, "Конец отрезка должен быть позже начала")
    elif end_seconds is not None:
        raise HTTPException(422, "Конец задаётся только для отрезка видео")
    status = model_status()
    if not status.get("ready"):
        raise HTTPException(
            409, "Детектор недоступен: проверьте пакет модели на сервере"
        )
    inference_parameters = normalize_inference_parameters(
        {
            key: value
            for key, value in {
                "confidence": confidence,
                "iou": iou,
                "max_detections": max_detections,
            }.items()
            if value is not None
        },
        status.get("inference_defaults"),
    )
    tiling = normalize_tiling_parameters({"enabled": tiled, "overlap": tile_overlap})
    suffix = ".zip" if batch else suffixes[0]
    file_key = f"detector/inputs/{uid()}{suffix}"
    path = DATA / file_key
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".upload")
    size, hasher = 0, hashlib.sha256()
    try:
        if batch:
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
                for index, file in enumerate(files):
                    name = (file.filename or f"Кадр {index + 1}")[:200]
                    hasher.update(name.encode("utf-8", errors="replace") + b"\0")
                    with archive.open(f"{index:04d}{suffixes[index]}", "w") as out:
                        while chunk := await file.read(1024**2):
                            size += len(chunk)
                            if size > MAX_UPLOAD_BYTES:
                                raise HTTPException(413, "Превышен лимит загрузки")
                            out.write(chunk)
                            hasher.update(chunk)
        else:
            with temporary.open("wb") as out:
                while chunk := await files[0].read(1024**2):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "Превышен лимит загрузки")
                    out.write(chunk)
                    hasher.update(chunk)
        if not size:
            raise HTTPException(422, "Файл пуст")
        temporary.replace(path)
        payload = dict(
            name=(
                f"Пачка кадров · {len(files)} "
                f"{count_form(len(files), 'файл', 'файла', 'файлов')}"
                if batch
                else (files[0].filename or "Файл")[:200]
            ),
            kind=kind,
            input_sha256=hasher.hexdigest(),
            mode="batch" if batch else mode,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            sample_seconds=sample_seconds,
            metadata_json={
                "inference_parameters": inference_parameters,
                "tiling": tiling,
                **(
                    {
                        "frame_count": len(files),
                        "frame_names": [
                            (file.filename or f"Кадр {index + 1}")[:200]
                            for index, file in enumerate(files)
                        ],
                    }
                    if batch
                    else {}
                ),
            },
        )

        def operation():
            row = DetectorRun(
                **payload, file_key=file_key, requested_model_sha=status["sha256"]
            )
            db.add(row)
            db.flush()
            return public_run(row)

        result = mutation(db, "detector:upload", key, payload, operation)
        if not db.scalar(
            select(DetectorRun.id).where(DetectorRun.file_key == file_key)
        ):
            path.unlink(missing_ok=True)
        return result
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        for file in files:
            await file.close()


@router.get("/runs")
def runs(db=Depends(session), limit: int = Query(30, ge=1, le=100)):
    return [
        public_run(r)
        for r in db.scalars(
            select(DetectorRun).order_by(DetectorRun.created_at.desc()).limit(limit)
        )
    ]


@router.get("/runs/{id}")
def run(id: str, db=Depends(session)):
    return public_run(require(db.get(DetectorRun, id)))


@router.get("/runs/{id}/frames")
def frames(
    id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db=Depends(session),
):
    run = require(db.get(DetectorRun, id))
    frame_names = (run.metadata_json or {}).get("frame_names", [])
    rows = list(
        db.scalars(
            select(DetectorFrame)
            .where(DetectorFrame.run_id == id)
            .order_by(DetectorFrame.sample_index)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    items = []
    for row in rows[:limit]:
        item = obj(row)
        item.pop("image_key")
        item["image_url"] = f"/api/v1/detector/frames/{row.id}/image"
        item["source_name"] = (
            frame_names[row.sample_index]
            if row.sample_index < len(frame_names)
            else None
        )
        items.append(item)
    return {
        "frames": items,
        "next_offset": offset + limit if len(rows) > limit else None,
    }


@router.get("/frames/{id}/image")
def frame_image(id: str, db=Depends(session)):
    row = require(db.get(DetectorFrame, id))
    path = DATA / row.image_key
    if not path.is_file():
        raise HTTPException(410, "Кадр больше недоступен")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/runs/{id}/preview")
def run_preview(id: str, db=Depends(session)):
    require(db.get(DetectorRun, id))
    row = db.scalar(
        select(DetectorFrame)
        .where(DetectorFrame.run_id == id)
        .order_by(DetectorFrame.sample_index)
        .limit(1)
    )
    if not row:
        raise HTTPException(404, "Предпросмотр ещё не готов")
    path = DATA / row.image_key
    if not path.is_file():
        raise HTTPException(410, "Предпросмотр больше недоступен")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/runs/{id}/content")
def content(id: str, db=Depends(session)):
    row = require(db.get(DetectorRun, id))
    path = DATA / row.file_key
    if not path.is_file():
        raise HTTPException(410, "Исходный файл больше недоступен")
    return FileResponse(
        path,
        media_type="application/zip" if row.kind == "batch" else None,
        filename=f"{row.name}.zip" if row.kind == "batch" else None,
    )


@router.post("/runs/{id}/cancel")
def cancel(
    id: str,
    key: str | None = Header(None, alias="Idempotency-Key"),
    db=Depends(session),
):
    def operation():
        require(db.get(DetectorRun, id))
        db.execute(
            update(DetectorRun)
            .where(DetectorRun.id == id, DetectorRun.status.in_(["queued", "running"]))
            .values(status="cancelled", updated_at=now(), token=None)
        )
        db.expire_all()
        return public_run(db.get(DetectorRun, id))

    return mutation(db, f"detector:{id}:cancel", key, {}, operation)

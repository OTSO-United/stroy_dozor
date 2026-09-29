"""Lease-fenced model checks, independent of monitoring and business rules."""

import logging
import math
import time
from types import SimpleNamespace
from zipfile import BadZipFile, ZipFile

import cv2
import numpy as np
from sqlalchemy import select

from .config import DATA
from .db import Session
from .jobs import fenced, LeaseLost
from .models import DetectorRun, DetectorFrame
from .vision import (
    detect_tiled,
    frame_quality,
    normalize_inference_parameters,
    normalize_tiling_parameters,
)

log = logging.getLogger("detector-lab")


def heartbeat(db, run, **values):
    fenced(db, run.id, run.token, job_model=DetectorRun, **values)


def detect_frame(run, model, parameters, tiling, frame):
    last_heartbeat = time.monotonic()

    def keep_lease(_completed, _total):
        nonlocal last_heartbeat
        if time.monotonic() - last_heartbeat < 10:
            return
        with Session() as db:
            heartbeat(db, run)
            db.commit()
        last_heartbeat = time.monotonic()

    return detect_tiled(
        model,
        frame,
        parameters,
        tiling,
        on_tile=keep_lease,
    )


def save_frame(run, model, parameters, tiling, frame, index, offset, progress):
    with Session() as db:
        heartbeat(db, run)
        db.commit()
    detections = detect_frame(run, model, parameters, tiling, frame)
    key = f"detector/frames/{run.id}/{run.token}/{index}.jpg"
    from . import worker

    worker.write_jpeg(key, frame)
    with Session() as db:
        heartbeat(
            db,
            run,
            progress=min(0.99, max(0, progress)),
            processed_frames=index + 1,
        )
        db.add(
            DetectorFrame(
                run_id=run.id,
                sample_index=index,
                offset_seconds=offset,
                detections=detections,
                quality=frame_quality(frame),
                image_key=key,
            )
        )
        db.commit()


def process_batch(run, model, parameters, tiling, model_info):
    path = DATA / run.file_key
    try:
        with ZipFile(path) as archive:
            entries = sorted(
                (info for info in archive.infolist() if not info.is_dir()),
                key=lambda info: info.filename,
            )
            expected = (run.metadata_json or {}).get("frame_count")
            if not entries or expected != len(entries):
                raise ValueError("Состав пачки кадров повреждён")
            with Session() as db:
                heartbeat(db, run, model=model_info)
                existing = set(
                    db.scalars(
                        select(DetectorFrame.sample_index).where(
                            DetectorFrame.run_id == run.id
                        )
                    )
                )
                db.commit()
            for index, entry in enumerate(entries):
                if index in existing:
                    with Session() as db:
                        heartbeat(
                            db,
                            run,
                            progress=min(0.99, (index + 1) / len(entries)),
                            processed_frames=index + 1,
                        )
                        db.commit()
                    continue
                frame = cv2.imdecode(
                    np.frombuffer(archive.read(entry), dtype=np.uint8), cv2.IMREAD_COLOR
                )
                if frame is None:
                    raise ValueError(f"Кадр {index + 1} повреждён")
                save_frame(
                    run,
                    model,
                    parameters,
                    tiling,
                    frame,
                    index,
                    float(index),
                    (index + 1) / len(entries),
                )
            with Session() as db:
                heartbeat(
                    db,
                    run,
                    status="succeeded",
                    progress=1,
                    processed_frames=len(entries),
                )
                db.commit()
    except BadZipFile as error:
        raise ValueError("Пачка кадров повреждена") from error


def process(run):
    # Reuse the same loaded model and media decoder as normal monitoring.
    from . import worker

    cap = None
    try:
        model = worker.detector()
        if model.info["sha256"] != run.requested_model_sha:
            raise ValueError(
                "Модель изменилась после запуска. Создайте новую проверку."
            )
        parameters = normalize_inference_parameters(
            (run.metadata_json or {}).get("inference_parameters"),
            model.info.get("inference_defaults"),
        )
        tiling = normalize_tiling_parameters((run.metadata_json or {}).get("tiling"))
        model_info = {
            **model.info,
            "inference_parameters": parameters,
            "tiling": {**tiling, "tile_size": model.info["input_size"]},
        }
        if run.kind == "batch":
            process_batch(run, model, parameters, tiling, model_info)
            return
        source = SimpleNamespace(kind=run.kind, file_key=run.file_key)
        frame, metadata = worker.first_frame(source)
        duration = metadata.get("duration_seconds")
        if run.kind == "video":
            if not duration or not math.isfinite(duration) or duration <= 0:
                raise ValueError("Не удалось определить длительность видео")
            if run.start_seconds >= duration:
                raise ValueError("Начало или стоп-кадр находится за пределами видео")
            if run.end_seconds is not None and run.end_seconds > duration + 0.05:
                raise ValueError("Конец отрезка находится за пределами видео")
            cap = worker.open_capture(source)
        end = run.end_seconds if run.mode == "segment" else duration
        with Session() as db:
            heartbeat(
                db,
                run,
                model=model_info,
                metadata_json={**(run.metadata_json or {}), **metadata},
            )
            existing = set(
                db.scalars(
                    select(DetectorFrame.sample_index).where(
                        DetectorFrame.run_id == run.id
                    )
                )
            )
            db.commit()
        next_sample, index, last_offset = run.start_seconds, 0, -1.0
        last_heartbeat = time.monotonic()
        last_decoded = -1.0
        fps = metadata.get("fps")
        while True:
            if cap:
                ok, frame = cap.read()
                if not ok:
                    # Incomplete files must not be reported as fully processed.
                    tolerance = max(0.25, 2 / fps) if fps else 0.5
                    if index == 0 or last_decoded + tolerance < min(end, duration):
                        raise ValueError("Видео оборвалось раньше выбранного конца")
                    break
                offset = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                if not math.isfinite(offset) or offset <= last_offset:
                    raise ValueError("Некорректные временные метки видео")
                last_offset = last_decoded = offset
                if time.monotonic() - last_heartbeat > 10:
                    with Session() as db:
                        heartbeat(db, run)
                        db.commit()
                    last_heartbeat = time.monotonic()
                if offset + 0.000001 < next_sample:
                    continue
                if run.mode != "frame" and offset >= end - 0.000001:
                    break
            else:
                offset = 0.0
            # Replay deterministically after a worker restart, without duplicate frames.
            if index not in existing:
                with Session() as db:
                    heartbeat(db, run)
                    db.commit()
                detections = detect_frame(run, model, parameters, tiling, frame)
                key = f"detector/frames/{run.id}/{run.token}/{index}.jpg"
                worker.write_jpeg(key, frame)
                progress = (
                    (offset - run.start_seconds) / (end - run.start_seconds)
                    if end and end > run.start_seconds
                    else 0
                )
                with Session() as db:
                    heartbeat(
                        db,
                        run,
                        progress=min(0.99, max(0, progress)),
                        processed_frames=index + 1,
                    )
                    db.add(
                        DetectorFrame(
                            run_id=run.id,
                            sample_index=index,
                            offset_seconds=offset,
                            detections=detections,
                            quality=frame_quality(frame),
                            image_key=key,
                        )
                    )
                    db.commit()
            index += 1
            if run.mode in ("image", "frame"):
                break
            # Anchor samples to requested start; never drift by accumulating frame rounding.
            next_sample = run.start_seconds + index * run.sample_seconds
        if not index:
            raise ValueError("В выбранном интервале нет декодируемых кадров")
        with Session() as db:
            heartbeat(db, run, status="succeeded", progress=1, processed_frames=index)
            db.commit()
    except LeaseLost:
        log.info("Detector check cancelled or lease lost: %s", run.id)
    except Exception as error:
        message = (
            str(error)
            if isinstance(error, (ValueError, FileNotFoundError))
            else type(error).__name__
        )
        with Session() as db:
            try:
                heartbeat(db, run, status="failed", error=message[:1000])
                db.commit()
            except LeaseLost:
                db.rollback()
        log.exception("Detector check failed: %s (%s)", run.id, type(error).__name__)
    finally:
        if cap:
            cap.release()

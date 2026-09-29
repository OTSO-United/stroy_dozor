"""Durable probe/inference worker. Run separately from the API."""

import json
import logging
import math
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import cv2
import numpy as np
from sqlalchemy import select, update
from .config import DATA, WORKER_THREADS
from .db import Session, now, utc
from .models import Source, Job, Observation, DetectorRun
from .jobs import claim, fenced, LeaseLost
from .security import decode_uri
from .vision import (
    OnnxDetector,
    frame_quality,
    model_status,
    normalize_inference_parameters,
)
from .catalog import class_map
from .temporal_cv import (
    ActivityAnalyzer,
    LocalTracker,
    PRESENCE_CONFIRM_FRAMES,
    VERSION as TEMPORAL_CV_VERSION,
)

log = logging.getLogger("worker")
_detector = None
_detector_lock = __import__("threading").Lock()
STREAM_RETRY_SECONDS = 60


class StreamInterrupted(RuntimeError):
    """Recoverable network decoder interruption; a new run starts after a delay."""


class InvalidStreamAddress(ValueError):
    """An explicitly non-media URL cannot recover by retrying."""


def detector():
    global _detector
    with _detector_lock:
        if _detector is None:
            _detector = OnnxDetector()
    return _detector


def check_https_failure(uri):
    """Diagnose a rejected HLS session without persisting the URL or server text."""
    try:
        with urlopen(
            Request(uri, headers={"User-Agent": "StroyDozor/1.0"}), timeout=10
        ) as response:
            content = response.read(65536).decode("utf-8", errors="replace")
    except HTTPError as error:
        if error.code in (401, 403):
            raise InvalidStreamAddress(
                "Сервер потока отказал в доступе (HTTP 401/403). Обновите ссылку или токен."
            ) from None
        if error.code in (404, 410):
            raise InvalidStreamAddress(
                "Видео по этому адресу больше недоступно (HTTP 404/410). Укажите актуальную ссылку на поток."
            ) from None
        return
    except (URLError, OSError, ValueError):
        return
    if not content.lstrip().startswith("#EXTM3U"):
        return
    errors = [
        line.lower()
        for line in content.splitlines()
        if line.startswith("#EXT-X-ERROR:")
    ]
    if any("not allowed" in line or "session end" in line for line in errors):
        raise InvalidStreamAddress(
            "Сервер завершил HLS-сессию или запретил доступ. Обновите ссылку или токен потока."
        )


def open_capture(source):
    uri = (
        decode_uri(source.uri_encrypted)
        if source.kind == "rtsp"
        else str(DATA / source.file_key)
    )
    if source.kind == "rtsp" and urlsplit(uri).path.lower().endswith((".html", ".htm")):
        raise InvalidStreamAddress("Адрес ведёт на HTML-страницу, а не на видеопоток")
    timeout = (
        30000 if source.kind == "rtsp" and urlsplit(uri).scheme == "https" else 10000
    )
    cap = cv2.VideoCapture(
        uri,
        cv2.CAP_FFMPEG,
        [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            timeout,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            timeout,
        ],
    )
    if not cap.isOpened():
        cap.release()
        if source.kind == "rtsp" and urlsplit(uri).scheme == "https":
            check_https_failure(uri)
        raise ValueError("Не удалось открыть источник видео")
    return cap


def first_frame(source, heartbeat=None):
    if source.kind == "image":
        frame = cv2.imdecode(
            np.fromfile(DATA / source.file_key, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if frame is None:
            raise ValueError("Изображение повреждено")
        return frame, {
            "width": frame.shape[1],
            "height": frame.shape[0],
            "duration_seconds": None,
            "fps": None,
        }
    cap = open_capture(source)
    try:
        if heartbeat:
            heartbeat()
        ok, frame = cap.read()
        if not ok and source.kind == "rtsp":
            for attempt in range(2):
                cap.release()
                time.sleep(attempt + 1)
                if heartbeat:
                    heartbeat()
                cap = open_capture(source)
                if heartbeat:
                    heartbeat()
                ok, frame = cap.read()
                if ok:
                    break
        if not ok:
            if source.kind == "rtsp":
                uri = decode_uri(source.uri_encrypted)
                if urlsplit(uri).scheme == "https":
                    check_https_failure(uri)
            raise ValueError("Нет доступного кадра")
        fps = cap.get(cv2.CAP_PROP_FPS)
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        metadata = {
            "width": frame.shape[1],
            "height": frame.shape[0],
            "fps": fps if 0 < fps < 1000 else None,
            "duration_seconds": count / fps
            if source.kind == "video" and fps > 0 and count > 0
            else None,
        }
        if source.kind == "video":
            try:
                result = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "json",
                        str(DATA / source.file_key),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=True,
                )
                metadata["duration_seconds"] = float(
                    json.loads(result.stdout)["format"]["duration"]
                )
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                metadata["duration_estimated"] = True
        return frame, metadata
    finally:
        cap.release()


def write_jpeg(key, frame):
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("Не удалось сохранить кадр")
    path = DATA / key
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encoded.tobytes())
    temporary.replace(path)


def run_probe(job):
    with Session() as db:
        source = db.get(Source, job.source_id)

    def heartbeat():
        with Session() as db:
            fenced(db, job.id, job.token)
            db.commit()

    try:
        frame, metadata = (
            first_frame(source, heartbeat=heartbeat)
            if source.kind == "rtsp"
            else first_frame(source)
        )
    except (ValueError, cv2.error) as error:
        if source.kind != "rtsp" or isinstance(error, InvalidStreamAddress):
            raise
        raise StreamInterrupted() from error
    with Session() as db:
        fenced(db, job.id, job.token, status="succeeded", progress=1)
        write_jpeg(f"previews/{source.id}.jpg", frame)
        current = db.get(Source, source.id)
        current.metadata_json = {**current.metadata_json, **metadata}
        current.status = "ready"
        current.error = None
        from .api import queue_bound_video

        queue_bound_video(db, current)
        db.commit()


def run_analysis(job):
    model = detector()
    if (
        job.payload.get("model_hash")
        and job.payload["model_hash"] != model.info["sha256"]
    ):
        raise ValueError("Версия модели изменилась: создайте новый анализ")
    inference_parameters = normalize_inference_parameters(
        job.payload.get("inference_parameters"),
        model.info.get("inference_defaults"),
    )
    tracking_parameters = {
        **inference_parameters,
        "confidence": min(0.1, inference_parameters["confidence"]),
    }
    presence_grace_frames = job.payload["settings"].get("presence_grace_frames", 3)
    gap = job.payload["settings"].get("max_gap_frames", 3)
    model_info = {
        **model.info,
        "inference_parameters": inference_parameters,
        "temporal_cv": {
            "version": TEMPORAL_CV_VERSION,
            "tracking_confidence": tracking_parameters["confidence"],
            "presence_grace_frames": presence_grace_frames,
            "presence_confirm_frames": PRESENCE_CONFIRM_FRAMES,
            "max_gap_frames": gap,
            "max_interframe_seconds": job.payload["sample_seconds"] * 1.75,
            "reconnect_seconds": min(30.0, gap * job.payload["sample_seconds"]),
            "working_hold_seconds": min(30.0, 2 * job.payload["sample_seconds"]),
            "activity_method": "auto",
            "idle_after_seconds": job.payload["settings"].get(
                "idle_after_seconds", 300
            ),
            "motion_fraction_threshold": job.payload["settings"].get(
                "motion_fraction_threshold", 0.008
            ),
            "mobile_recheck_seconds": 10,
            "stationary_recheck_seconds": 30,
            "stationary_idle_after_seconds": max(
                120, 2 * job.payload["settings"].get("idle_after_seconds", 300)
            ),
        },
    }
    with Session() as db:
        source = db.get(Source, job.source_id)
        existing = set(
            db.scalars(
                select(Observation.sample_index).where(Observation.run_id == job.id)
            )
        )
    if source.kind == "rtsp" and existing:
        raise StreamInterrupted("Прерванная сессия потока требует нового прогона")
    interval = job.payload["sample_seconds"]
    configured_activity = job.payload["settings"].get("equipment_activity", {})
    equipment_activity = {
        key: configured_activity.get(key, value["activity_group"])
        for key, value in class_map().items()
    }

    def temporal_state(next_track_id=1):
        tracker = LocalTracker(
            max_gap=gap,
            publish_confidence=inference_parameters["confidence"],
            presence_grace_frames=presence_grace_frames,
            max_interframe_seconds=interval * 1.75,
        )
        tracker.next_id = next_track_id
        activity = ActivityAnalyzer(
            max_gap=gap,
            sample_seconds=interval,
            equipment_activity=equipment_activity,
            method="auto",
            idle_after=job.payload["settings"].get("idle_after_seconds", 300),
            motion_fraction_threshold=job.payload["settings"].get(
                "motion_fraction_threshold", 0.008
            ),
        )
        return tracker, activity

    tracker, activity = temporal_state()
    loop_config = job.payload.get("demo_loop") or {}
    loop_duration = (
        float(loop_config["duration_seconds"]) if loop_config.get("enabled") else None
    )
    media_duration = (
        float(source.metadata_json.get("duration_seconds") or 0)
        if loop_duration is not None
        else 0
    )
    if loop_duration is not None and media_duration <= 0:
        raise ValueError("Не удалось определить длительность видео для демо-цикла")
    cap = None
    index = 0
    next_sample = 0.0
    cycle_offset = 0.0
    cycle_index = 0
    cycle_first_pts = None
    cycle_last_pts = None
    cycle_frame_step = None
    session_started = now()
    last_heartbeat = time.monotonic()
    try:
        if source.kind == "image":
            frame, _ = first_frame(source)
        else:
            try:
                cap = open_capture(source)
            except (ValueError, cv2.error) as error:
                if source.kind != "rtsp" or isinstance(error, InvalidStreamAddress):
                    raise
                raise StreamInterrupted() from error
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            cycle_frame_step = 1 / fps if math.isfinite(fps) and fps > 0 else 0.04
        while True:
            if cap is not None:
                try:
                    ok, frame = cap.read()
                except cv2.error as error:
                    if source.kind != "rtsp":
                        raise
                    raise StreamInterrupted() from error
                if not ok:
                    if source.kind == "rtsp":
                        raise StreamInterrupted("Поток камеры прерван")
                    if not ok and loop_duration is not None:
                        if cycle_last_pts is None:
                            break
                        next_cycle = cycle_offset + cycle_last_pts + cycle_frame_step
                        if next_cycle < loop_duration:
                            cap.release()
                            cap = open_capture(source)
                            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
                            cycle_frame_step = (
                                1 / fps
                                if math.isfinite(fps) and fps > 0
                                else cycle_frame_step
                            )
                            cycle_offset = next_cycle
                            cycle_index += 1
                            cycle_first_pts = None
                            cycle_last_pts = None
                            next_sample = cycle_offset
                            tracker, activity = temporal_state(tracker.next_id)
                            continue
                    if not ok:
                        break
                source_offset = (now() - session_started).total_seconds()
                if source.kind != "rtsp":
                    raw_pts = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0) / 1000
                    if not math.isfinite(raw_pts) or raw_pts < 0:
                        raw_pts = (
                            cycle_first_pts + cycle_last_pts + cycle_frame_step
                            if cycle_first_pts is not None
                            and cycle_last_pts is not None
                            else 0
                        )
                    if cycle_first_pts is None:
                        cycle_first_pts = raw_pts
                    source_offset = max(0.0, raw_pts - cycle_first_pts)
                    if cycle_last_pts is not None and source_offset > cycle_last_pts:
                        observed_step = source_offset - cycle_last_pts
                        if math.isfinite(observed_step) and observed_step > 0:
                            cycle_frame_step = observed_step
                    cycle_last_pts = source_offset
                offset = cycle_offset + source_offset
                if loop_duration is not None and offset >= loop_duration:
                    break
                if offset + 0.001 < next_sample:
                    activity.observe(frame, offset, tracker.tracks)
                    if time.monotonic() - last_heartbeat > 15:
                        with Session() as db:
                            fenced(db, job.id, job.token)
                            db.commit()
                        last_heartbeat = time.monotonic()
                    continue
                next_sample = offset + interval
            else:
                offset = 0
            if source.kind == "rtsp":
                captured = session_started + timedelta(seconds=offset)
            else:
                captured = (
                    utc(source.capture_start) + timedelta(seconds=offset)
                    if source.capture_start
                    else None
                )
            quality = frame_quality(frame)
            quality["time_basis"] = (
                "server_receive_estimate"
                if source.kind == "rtsp"
                else "demo_loop_start_plus_repeated_pts"
                if loop_duration is not None
                else "user_confirmed_start_plus_pts"
                if source.capture_start
                else "unknown"
            )
            if source.kind == "rtsp":
                quality["limitations"].append(
                    "Время потока оценено по приёму сервером; часы камеры не проверены"
                )
            if loop_duration is not None:
                quality["demo_loop"] = True
                quality["demo_loop_cycle"] = cycle_index
            detections = tracker.update(
                model.detect(frame, tracking_parameters), offset, frame
            )
            if quality["usable"]:
                activity.observe(frame, offset, tracker.tracks)
                activity.annotate(detections, offset, tracker.tracks)
            else:
                activity.invalidate()
                for detection in detections:
                    detection["activity"] = "unknown"
                    detection["activity_basis"] = "poor_frame_quality"
            if index not in existing:
                key = f"evidence/{job.id}/{job.token}/{index:08}.jpg"
                write_jpeg(key, frame)
                duration = loop_duration or source.metadata_json.get("duration_seconds")
                with Session() as db:
                    fenced(
                        db,
                        job.id,
                        job.token,
                        progress=min(0.99, offset / duration) if duration else 0,
                    )
                    db.add(
                        Observation(
                            project_id=job.project_id,
                            source_id=source.id,
                            run_id=job.id,
                            sample_index=index,
                            captured_at=captured,
                            offset_seconds=offset,
                            detections=detections,
                            quality=quality,
                            model=model_info,
                            evidence_key=key,
                        )
                    )
                    db.commit()
            else:
                with Session() as db:
                    fenced(db, job.id, job.token)
                    db.commit()
            last_heartbeat = time.monotonic()
            index += 1
            if cap is None:
                break
        with Session() as db:
            fenced(db, job.id, job.token, status="succeeded", progress=1)
            current = db.get(Source, source.id)
            current.status = "completed"
            current.error = None
            if source.kind == "rtsp" and current.enabled:
                db.add(
                    Job(
                        project_id=job.project_id,
                        source_id=source.id,
                        kind="analyze",
                        payload=job.payload,
                    )
                )
                current.status = "queued"
            db.commit()
    finally:
        if cap:
            cap.release()


def process(job):
    try:
        with Session() as db:
            fenced(db, job.id, job.token)
            db.get(Source, job.source_id).status = "running"
            db.commit()
        if job.kind == "probe":
            run_probe(job)
        elif job.kind == "analyze":
            run_analysis(job)
        else:
            raise ValueError("Неизвестный тип задачи")
    except LeaseLost:
        log.info("Job lease lost: %s", job.id)
    except StreamInterrupted:
        with Session() as db:
            try:
                message = "Поток недоступен: не удалось открыть видео или получить кадр. Проверьте адрес и срок действия ссылки."
                fenced(db, job.id, job.token, status="interrupted", error=message)
                source = db.get(Source, job.source_id)
                if source and source.enabled:
                    db.add(
                        Job(
                            project_id=job.project_id,
                            source_id=source.id,
                            kind=job.kind,
                            payload={
                                **job.payload,
                                "started_at": None,
                                "stream_attempt": job.payload.get("stream_attempt", 1)
                                + 1,
                            },
                            lease_until=now() + timedelta(seconds=STREAM_RETRY_SECONDS),
                        )
                    )
                    source.status = "reconnecting"
                    source.error = message
                elif source:
                    source.status = "stopped"
                db.commit()
            except LeaseLost:
                db.rollback()
        log.warning("Stream job %s interrupted; reconnect scheduled", job.id)
    except Exception as error:
        # Never persist raw decoder exceptions containing RTSP credentials.
        message = (
            str(error)
            if isinstance(error, (ValueError, FileNotFoundError))
            else type(error).__name__
        )
        if "://" in message:
            message = "Ошибка обработки источника"
        with Session() as db:
            try:
                fenced(db, job.id, job.token, status="failed", error=message[:1000])
                source = db.get(Source, job.source_id)
                source.status = "failed"
                source.error = message[:1000]
                db.commit()
            except LeaseLost:
                db.rollback()
        log.error("Job %s failed: %s", job.id, type(error).__name__)


def main():
    logging.basicConfig(level=logging.INFO)
    if model_status()["ready"]:
        model = detector()
        log.info(
            "Detector ready: %s; providers=%s",
            model.info["name"],
            model.info["providers"],
        )
    with ThreadPoolExecutor(max_workers=WORKER_THREADS) as pool:
        futures = set()
        lab_first = False
        while True:
            futures = {f for f in futures if not f.done()}
            if len(futures) < WORKER_THREADS:
                with Session() as db:
                    db.execute(
                        update(Job)
                        .where(
                            Job.status == "running",
                            Job.lease_until < now(),
                            Job.attempts >= 3,
                        )
                        .values(
                            status="failed", error="Исчерпаны попытки восстановления"
                        )
                    )
                    db.commit()
                    db.execute(
                        update(DetectorRun)
                        .where(
                            DetectorRun.status == "running",
                            DetectorRun.lease_until < now(),
                            DetectorRun.attempts >= 3,
                        )
                        .values(
                            status="failed", error="Исчерпаны попытки восстановления"
                        )
                    )
                    db.commit()
                    # Fairly share worker capacity; experiments cannot create business observations.
                    kinds = (DetectorRun, Job) if lab_first else (Job, DetectorRun)
                    job = claim(db, kinds[0]) or claim(db, kinds[1])
                    lab_first = not lab_first
                if job:
                    from .detector_worker import process as process_lab

                    futures.add(
                        pool.submit(
                            process_lab if isinstance(job, DetectorRun) else process,
                            job,
                        )
                    )
                    continue
            time.sleep(0.5)


if __name__ == "__main__":
    main()

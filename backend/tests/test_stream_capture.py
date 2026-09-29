"""Network-source decoder retry contract without external connections."""

from types import SimpleNamespace
import numpy as np
from app import worker


def test_https_capture_uses_longer_timeout_and_retries_empty_segment(monkeypatch):
    captures = []

    class Capture:
        def __init__(self, success):
            self.success = success
            self.released = False

        def isOpened(self):
            return True

        def read(self):
            return (
                self.success,
                np.zeros((24, 32, 3), dtype=np.uint8) if self.success else None,
            )

        def get(self, _property):
            return 0

        def release(self):
            self.released = True

    def video_capture(uri, backend, options):
        assert uri == "https://camera.example/live/index.m3u8?token=secret"
        assert backend == worker.cv2.CAP_FFMPEG
        assert options == [
            worker.cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            30000,
            worker.cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            30000,
        ]
        cap = Capture(bool(captures))
        captures.append(cap)
        return cap

    monkeypatch.setattr(
        worker,
        "decode_uri",
        lambda _: "https://camera.example/live/index.m3u8?token=secret",
    )
    monkeypatch.setattr(worker.cv2, "VideoCapture", video_capture)
    monkeypatch.setattr(worker.time, "sleep", lambda _: None)
    frame, metadata = worker.first_frame(
        SimpleNamespace(kind="rtsp", uri_encrypted="stored")
    )
    assert frame.shape == (24, 32, 3)
    assert metadata["duration_seconds"] is None
    assert len(captures) == 2
    assert all(cap.released for cap in captures)


def test_network_failure_schedules_durable_minute_retry(runtime, project, monkeypatch):
    from datetime import timedelta

    from app import models as m
    from app.db import now
    from app.jobs import claim

    factory, _ = runtime
    with factory() as db:
        source = m.Source(
            project_id=project["id"],
            name="Сетевой поток",
            kind="rtsp",
            enabled=True,
            metadata_json={},
        )
        db.add(source)
        db.flush()
        original = m.Job(
            project_id=project["id"],
            source_id=source.id,
            kind="probe",
            payload={},
        )
        db.add(original)
        db.commit()
        source_id, original_id = source.id, original.id
    with factory() as db:
        running = claim(db)
    monkeypatch.setattr(
        worker,
        "run_probe",
        lambda job: (_ for _ in ()).throw(worker.StreamInterrupted()),
    )
    worker.process(running)
    with factory() as db:
        source = db.get(m.Source, source_id)
        jobs = list(
            db.query(m.Job)
            .filter(m.Job.source_id == source_id)
            .order_by(m.Job.created_at)
        )
        assert source.status == "reconnecting" and "Поток недоступен" in source.error
        assert db.get(m.Job, original_id).status == "interrupted"
        retry = next(job for job in jobs if job.id != original_id)
        assert retry.status == "queued" and retry.kind == "probe"
        assert retry.lease_until >= now() + timedelta(seconds=50)
        assert claim(db) is None
        retry.lease_until = now() - timedelta(seconds=1)
        db.commit()
    with factory() as db:
        resumed = claim(db)
        assert resumed.id == retry.id

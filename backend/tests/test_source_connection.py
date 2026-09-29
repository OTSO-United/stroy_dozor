"""Connection editing, durable recovery and rejected HLS sessions."""

from datetime import timedelta
from io import BytesIO
from urllib.error import HTTPError
import json

import numpy as np
import pytest
from sqlalchemy import select

from app import jobs, models as m, security, worker
from app.db import now
from test_app import post

URI = "https://camera.example/live.m3u8?tkn=private-test-token"


def create(client, project):
    response = post(
        client, f"/projects/{project['id']}/sources", {"name": "Камера", "uri": URI}
    )
    assert response.status_code == 200
    return response.json()["source"]["id"]


def test_connection_read_is_explicit_and_not_cached(client, project, runtime):
    source_id = create(client, project)
    response = client.get(f"/api/v1/sources/{source_id}/connection")
    assert response.json() == {"uri": URI}
    assert response.headers["cache-control"] == "no-store"
    for path in (
        f"/projects/{project['id']}/sources",
        f"/projects/{project['id']}/jobs",
    ):
        assert "private-test-token" not in client.get("/api/v1" + path).text
    factory, _ = runtime
    with factory() as db:
        assert "private-test-token" not in json.dumps(
            [a.data for a in db.scalars(select(m.Audit))]
        )
        assert "private-test-token" not in json.dumps(
            [a.response for a in db.scalars(select(m.Idempotency))]
        )


def test_new_source_retries_then_recovers_and_stop_cancels(
    client, project, runtime, monkeypatch
):
    source_id = create(client, project)
    factory, _ = runtime
    monkeypatch.setattr(
        worker, "open_capture", lambda _: (_ for _ in ()).throw(ValueError("offline"))
    )
    with factory() as db:
        original = jobs.claim(db)
    worker.process(original)
    source = client.get(f"/api/v1/projects/{project['id']}/sources").json()[0]
    assert source["processing_state"] == "reconnecting"
    assert source["retry_at"] and source["connection_attempt"] == 2
    with factory() as db:
        assert jobs.claim(db) is None
        retry = db.scalar(select(m.Job).where(m.Job.status == "queued"))
        retry.lease_until = now() - timedelta(seconds=1)
        db.commit()
    with factory() as db:
        resumed = jobs.claim(db)
    assert (
        client.get(f"/api/v1/projects/{project['id']}/sources").json()[0][
            "processing_state"
        ]
        == "connecting"
    )
    monkeypatch.setattr(
        worker,
        "first_frame",
        lambda *a, **k: (
            np.zeros((24, 32, 3), dtype=np.uint8),
            {"width": 32, "height": 24},
        ),
    )
    worker.process(resumed)
    assert (
        client.get(f"/api/v1/projects/{project['id']}/sources").json()[0]["status"]
        == "ready"
    )
    assert post(client, f"/sources/{source_id}/probe").status_code == 202
    assert post(client, f"/sources/{source_id}/stop").status_code == 200
    with factory() as db:
        assert jobs.claim(db) is None
        assert db.get(m.Source, source_id).status == "stopped"


def test_replace_running_address_fences_old_worker_and_probes_once(
    client, project, runtime, monkeypatch
):
    source_id = create(client, project)
    factory, folder = runtime
    with factory() as db:
        original = jobs.claim(db)
    replacement = "https://camera.example/updated.m3u8?tkn=new-private-token"
    request = {"uri": replacement, "sample_seconds": 10, "name": "Камера 2"}
    for _ in range(2):
        response = client.patch(
            f"/api/v1/sources/{source_id}",
            json=request,
            headers={"Idempotency-Key": "replace-stream"},
        )
        assert response.status_code == 200, response.text
        assert "new-private-token" not in response.text
    monkeypatch.setattr(
        worker,
        "first_frame",
        lambda *a, **k: (np.zeros((24, 32, 3), dtype=np.uint8), {}),
    )
    with pytest.raises(jobs.LeaseLost):
        worker.run_probe(original)
    assert not (folder / f"previews/{source_id}.jpg").exists()
    with factory() as db:
        assert db.get(m.Job, original.id).status == "cancelled"
        queued = list(db.scalars(select(m.Job).where(m.Job.status == "queued")))
        assert len(queued) == 1 and queued[0].kind == "probe"
        source = db.get(m.Source, source_id)
        assert source.enabled and source.sample_seconds == 10
        assert security.decode_uri(source.uri_encrypted) == replacement
    unchanged = client.patch(
        f"/api/v1/sources/{source_id}",
        json=request,
        headers={"Idempotency-Key": "unchanged-stream"},
    )
    assert unchanged.status_code == 409


def test_manual_reconnect_replaces_delayed_retry(client, project, runtime):
    source_id = create(client, project)
    factory, _ = runtime
    with factory() as db:
        old = db.scalar(select(m.Job))
        old.lease_until = now() + timedelta(minutes=1)
        source = db.get(m.Source, source_id)
        source.enabled = False
        source.status = "reconnecting"
        db.commit()
        old_id = old.id
    response = post(client, f"/sources/{source_id}/probe")
    assert response.status_code == 202
    with factory() as db:
        new = jobs.claim(db)
        assert new and new.id != old_id
        assert db.get(m.Job, old_id).status == "cancelled"
        assert db.get(m.Source, source_id).enabled


def test_slow_probe_renews_lease_between_attempts(
    client, project, runtime, monkeypatch
):
    source_id = create(client, project)
    factory, _ = runtime
    instant = [now()]
    monkeypatch.setattr(jobs, "now", lambda: instant[0])
    monkeypatch.setattr(worker.time, "sleep", lambda _: None)
    reads = []

    class Capture:
        def read(self):
            instant[0] += timedelta(seconds=30)
            reads.append(1)
            return (
                (True, np.zeros((24, 32, 3), dtype=np.uint8))
                if len(reads) == 3
                else (False, None)
            )

        def get(self, _):
            return 0

        def release(self):
            pass

    def open_capture(_):
        instant[0] += timedelta(seconds=30)
        return Capture()

    monkeypatch.setattr(worker, "open_capture", open_capture)
    with factory() as db:
        job = jobs.claim(db)
    start = instant[0]
    worker.process(job)
    assert (instant[0] - start).total_seconds() == 180
    with factory() as db:
        assert db.get(m.Job, job.id).status == "succeeded"
        assert db.get(m.Source, source_id).status == "ready"


@pytest.mark.parametrize(
    "reason",
    [
        "Shutting down since this session is not allowed to view this stream",
        "Shutting down due to session end",
    ],
)
@pytest.mark.parametrize("opened", [False, True])
def test_rejected_hls_session_is_terminal_and_has_no_secret(
    client, project, runtime, monkeypatch, reason, opened
):
    source_id = create(client, project)
    factory, _ = runtime
    monkeypatch.setattr(
        worker,
        "urlopen",
        lambda *a, **k: BytesIO(
            (
                "#EXTM3U\n#EXT-X-ERROR: " + reason + " " + URI + "\n#EXT-X-ENDLIST"
            ).encode()
        ),
    )

    class Capture:
        def isOpened(self):
            return opened

        def read(self):
            return False, None

        def release(self):
            pass

    monkeypatch.setattr(worker.cv2, "VideoCapture", lambda *a: Capture())
    monkeypatch.setattr(worker.time, "sleep", lambda _: None)
    with factory() as db:
        job = jobs.claim(db)
    worker.process(job)
    with factory() as db:
        source = db.get(m.Source, source_id)
        assert source.status == "failed"
        assert "Обновите ссылку" in source.error
        assert "private-test-token" not in source.error
        assert db.get(m.Job, job.id).status == "failed"
        assert jobs.claim(db) is None


@pytest.mark.parametrize("status", [401, 403, 404, 410, 500, 503])
def test_http_failures_distinguish_address_and_temporary_errors(monkeypatch, status):
    def fail(*a, **k):
        raise HTTPError(URI, status, "server error", {}, None)

    monkeypatch.setattr(worker, "urlopen", fail)
    if status < 500:
        with pytest.raises(worker.InvalidStreamAddress) as error:
            worker.check_https_failure(URI)
        assert "private-test-token" not in str(error.value)
    else:
        worker.check_https_failure(URI)

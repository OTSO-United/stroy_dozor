"""Cross-cutting checks for automatic video analysis and stable summaries."""

from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from openpyxl import load_workbook

from app import api, models as m, reports, schemas, security, worker
from app.db import now
from app.jobs import claim
from app.telemetry import series_points
from test_app import approved, post, work


def test_early_start_revisions_calendar_without_changing_stage_identity(
    client, project
):
    start = now() + timedelta(days=2)
    end = start + timedelta(days=1)
    stage = work(start=start.isoformat(), end=end.isoformat())
    first = approved(client, project, [stage])
    before = now()
    response = post(
        client,
        f"/projects/{project['id']}/works/{stage['id']}/status",
        {"status": "in_progress"},
    )
    assert response.status_code == 200, response.text
    plans = client.get(f"/api/v1/projects/{project['id']}/plans").json()
    assert plans["work_states"][stage["id"]] == "in_progress"
    latest = plans["plans"][0]
    assert latest["version"] == first["version"] + 1
    assert latest["parent_id"] == first["id"]
    assert latest["works"][0]["id"] == stage["id"]
    assert before <= api.aware(latest["works"][0]["starts_at"]) <= now()
    assert api.aware(latest["works"][0]["ends_at"]) == end


def test_minute_presence_uses_highest_count_seen_for_ten_samples():
    start = now().replace(second=0, microsecond=0)
    rows = []
    for index in range(120):
        count = 4 if index < 60 and index % 5 == 0 else 3
        rows.append(
            SimpleNamespace(
                id=str(index),
                run_id="run",
                captured_at=start + timedelta(seconds=index),
                offset_seconds=index,
                quality={"usable": True},
                model={"supported_classes": ["0"]},
                detections=[{"class_id": "0"}] * count,
            )
        )
    points = series_points(
        rows, [0], 60, 3, expected_step=1, count_change_confirm_seconds=10
    )
    assert [point["counts"]["0"] for point in points] == [4, 3]


def test_thirty_second_bucket_confirms_presence_and_activity_without_overshoot():
    start = now().replace(second=0, microsecond=0)
    rows = []
    for index in range(60):
        count = 4 if index < 30 and index % 3 == 0 else 3
        rows.append(
            SimpleNamespace(
                id=str(index),
                run_id="run",
                captured_at=start + timedelta(seconds=index),
                offset_seconds=index,
                quality={"usable": True},
                model={"supported_classes": ["1"]},
                detections=[
                    {"class_id": "1", "activity": "working"},
                    {"class_id": "1", "activity": "idle"},
                    *[{"class_id": "1", "activity": "unknown"}] * (count - 2),
                ],
            )
        )
    points = series_points(
        rows, [1], 30, 3, expected_step=1, count_change_confirm_seconds=10
    )
    assert [point["counts"]["1"] for point in points] == [4, 3]
    assert all(point["display_basis"]["1"] == "confirmed" for point in points)
    for point in points:
        activity = point["display_activity_by_class"]["1"]
        assert activity == {"working": 1, "idle": 1}
        assert activity["working"] + activity["idle"] <= point["counts"]["1"]


def test_timeseries_marks_recently_observed_work_without_changing_activity_count():
    start = now().replace(second=0, microsecond=0)
    rows = [
        SimpleNamespace(
            id=str(index),
            run_id="run",
            captured_at=start + timedelta(seconds=index * 10),
            offset_seconds=index * 10,
            quality={"usable": True},
            model={"supported_classes": ["1"]},
            detections=[
                {
                    "class_id": "1",
                    "activity": "working",
                    "activity_basis": "recent_motion"
                    if index == 5
                    else "chassis_motion",
                }
            ],
        )
        for index in range(6)
    ]
    points = series_points(rows, [1], 60, 3, expected_step=10)
    assert len(points) == 1
    assert points[0]["display_activity_by_class"]["1"]["working"] == 1
    assert points[0]["continued_working_by_class"] == {"1": 1}


def test_minute_presence_keeps_sparse_samples_visible_without_claiming_confirmation():
    start = now().replace(second=0, microsecond=0)
    rows = [
        SimpleNamespace(
            id=str(index),
            run_id="run",
            captured_at=start + timedelta(seconds=index * 30),
            offset_seconds=index * 30,
            quality={"usable": True},
            model={"supported_classes": ["0", "1"]},
            detections=[
                {"class_id": "0", "activity": "working"},
                {"class_id": "0", "activity": "idle"},
                {"class_id": "1", "activity": "working"},
            ],
        )
        for index in range(4)
    ]
    points = series_points(rows, [0, 1], 60, 3, expected_step=30)
    assert [point["counts"] for point in points] == [
        {"0": 2, "1": 1},
        {"0": 2, "1": 1},
    ]
    assert points[0]["display_basis"] == {"0": "sampled", "1": "sampled"}
    assert points[0]["presence"] == {"0": "unknown", "1": "unknown"}
    assert points[0]["display_activity_by_class"] == {
        "0": {"working": 1, "idle": 1},
        "1": {"working": 1, "idle": 0},
    }
    assert points[0]["display_activity"] == {"working": 2, "idle": 1}


def test_minute_presence_shows_isolated_irregular_measurements_as_samples():
    start = now().replace(second=0, microsecond=0)
    rows = [
        SimpleNamespace(
            id=str(index),
            run_id="run",
            captured_at=start + timedelta(seconds=index * 25),
            offset_seconds=index * 25,
            quality={"usable": True},
            model={"supported_classes": ["0"]},
            detections=[{"class_id": "0", "activity": "working"}],
        )
        for index in range(4)
    ]
    points = series_points(rows, [0], 60, 3, expected_step=1)
    assert len(points) == 4
    assert all(point["counts"]["0"] == 1 for point in points)
    assert all(point["display_basis"]["0"] == "sampled" for point in points)
    assert len({point["segment"] for point in points}) == 4


def test_minute_activity_breakdown_matches_displayed_totals():
    start = now().replace(second=0, microsecond=0)
    rows = [
        SimpleNamespace(
            id=str(index),
            run_id="run",
            captured_at=start + timedelta(seconds=index),
            offset_seconds=index,
            quality={"usable": True},
            model={"supported_classes": ["0", "1"]},
            detections=[
                {"class_id": "0", "activity": "idle"},
                {"class_id": "0", "activity": "idle"},
                {"class_id": "1", "activity": "working"},
            ],
        )
        for index in range(10)
    ]
    [point] = series_points(rows, [0, 1], 60, 3, expected_step=1)
    assert point["display_basis"] == {"0": "confirmed", "1": "confirmed"}
    assert point["display_activity_by_class"] == {
        "0": {"working": 0, "idle": 2},
        "1": {"working": 1, "idle": 0},
    }
    assert point["display_activity"] == {"working": 1, "idle": 2}


def test_minute_activity_two_excavators_split_working_and_idle():
    start = now().replace(second=0, microsecond=0)
    rows = [
        SimpleNamespace(
            id=str(index),
            run_id="run",
            captured_at=start + timedelta(seconds=index),
            offset_seconds=index,
            quality={"usable": True},
            model={"supported_classes": ["1"]},
            detections=[
                {"class_id": "1", "activity": "working"},
                {"class_id": "1", "activity": "idle"},
            ],
        )
        for index in range(10)
    ]
    [point] = series_points(rows, [1], 60, 3, expected_step=1)
    assert point["raw_counts"]["1"] == point["counts"]["1"] == 2
    assert point["display_activity_by_class"]["1"] == {"working": 1, "idle": 1}
    assert point["display_activity"] == {"working": 1, "idle": 1}


def test_historical_default_settings_do_not_invalidate_assessment():
    defaults = schemas.Settings().model_dump()
    old_snapshot = {
        "work_states": {"stage": "in_progress"},
        "settings": {
            key: value
            for key, value in defaults.items()
            if key not in {"idle_after_seconds", "motion_fraction_threshold"}
        },
    }
    assert not api.assessment_context_changed(
        old_snapshot, {"stage": "in_progress"}, defaults
    )
    assert api.assessment_context_changed(
        old_snapshot, {"stage": "completed"}, defaults
    )
    changed = {**defaults, "critical_after_seconds": 1800}
    assert api.assessment_context_changed(
        old_snapshot, {"stage": "in_progress"}, changed
    )


def test_video_bound_before_probe_starts_analysis_when_preview_is_ready(
    client, project, runtime, monkeypatch
):
    start = now() - timedelta(minutes=1)
    stage = work(start=start.isoformat(), end=(start + timedelta(hours=1)).isoformat())
    approved(client, project, [stage])
    factory, _ = runtime
    with factory() as db:
        source = m.Source(
            project_id=project["id"],
            name="Видео",
            kind="video",
            capture_start=start,
            sample_seconds=1,
            metadata_json={},
        )
        db.add(source)
        db.flush()
        source_id = source.id
        db.add(m.Job(project_id=project["id"], source_id=source_id, kind="probe"))
        db.commit()
    binding = post(
        client,
        f"/sources/{source_id}/bindings",
        {
            "regions": [
                {
                    "name": "Весь кадр",
                    "work_ids": [stage["id"]],
                    "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                    "visibility_confirmed": True,
                }
            ],
        },
    )
    assert binding.status_code == 200, binding.text
    monkeypatch.setattr(api, "model_status", lambda: {"ready": True, "sha256": "test"})
    monkeypatch.setattr(
        worker,
        "first_frame",
        lambda _: (
            np.zeros((64, 64, 3), dtype=np.uint8),
            {"width": 64, "height": 64, "duration_seconds": 30, "fps": 1},
        ),
    )
    monkeypatch.setattr(worker, "write_jpeg", lambda *_: None)
    with factory() as db:
        probe = claim(db)
    worker.run_probe(probe)
    jobs = client.get(f"/api/v1/projects/{project['id']}/jobs").json()
    analysis = [job for job in jobs if job["kind"] == "analyze"]
    assert len(analysis) == 1 and analysis[0]["status"] == "queued"


def test_short_count_spike_and_detector_drop_do_not_change_displayed_count():
    start = now().replace(second=0, microsecond=0)
    levels = [3] * 12 + [4] * 4 + [3] * 12 + [2] * 4 + [3] * 12 + [2] * 12
    rows = [
        SimpleNamespace(
            id=str(index),
            run_id="run",
            captured_at=start + timedelta(seconds=index),
            offset_seconds=index,
            quality={"usable": True},
            model={"supported_classes": ["0"]},
            detections=[{"class_id": "0", "activity": "unknown"}] * count,
        )
        for index, count in enumerate(levels)
    ]
    points = series_points(
        rows,
        [0],
        0,
        3,
        expected_step=1,
        count_change_confirm_seconds=10,
    )
    assert all(point["counts"]["0"] == 3 for point in points[:54])
    assert points[-1]["counts"]["0"] == 2
    assert points[0]["display_activity_by_class"]["0"]["working"] is None


def test_network_stream_accepts_any_host_for_supported_schemes(monkeypatch):
    class PlainCipher:
        def encrypt(self, value):
            return value

    monkeypatch.setattr(security, "cipher", PlainCipher)
    uris = [
        "https://stream3.exdesign.ru/hls/ruchi-51/0/index.m3u8?tkn=sample",
        "https://another.example/live.m3u8",
        "rtsp://10.0.0.1/stream",
        "rtsps://camera.example/live",
    ]
    for uri in uris:
        assert security.validate_rtsp(uri) == uri
    for uri in ("http://another.example/live.m3u8", "file:///tmp/video", "https://"):
        with pytest.raises(ValueError):
            security.validate_rtsp(uri)


def test_create_https_hls_source_without_host_allowlist(client, project):
    uri = "https://stream3.exdesign.ru/hls/ruchi-51/0/index.m3u8?tkn=sample"
    response = post(
        client,
        f"/projects/{project['id']}/sources",
        {"name": "HLS камера", "uri": uri, "sample_seconds": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["source"]["kind"] == "rtsp"
    assert response.json()["job"]["kind"] == "probe"
    assert uri not in response.text


def test_reviewed_open_alert_is_not_counted_as_needing_attention():
    alert = SimpleNamespace(
        fingerprint="one",
        status="open",
        reviewed=True,
        last_seen=now(),
        details={},
    )
    assert api.open_alert_count([alert]) == 0
    alert.reviewed = False
    assert api.open_alert_count([alert]) == 1


def test_event_report_includes_project_stage_notification_and_equipment():
    start = now()
    end = start + timedelta(hours=8)
    project = SimpleNamespace(name="Тестовый объект")
    plan = SimpleNamespace(
        id="old",
        works=[
            {
                "id": "work",
                "code": "12.3",
                "title": "Фундамент",
                "starts_at": start.isoformat(),
                "ends_at": end.isoformat(),
            }
        ],
    )
    alert = SimpleNamespace(
        status="open",
        first_seen=start,
        last_seen=start + timedelta(minutes=2),
        source_id="source",
        work_id="work",
        details={
            "message": "Не хватает экскаватора",
            "plan_id": "old",
            "code": "12.3",
            "title": "Фундамент",
            "expected": {"0": 2},
            "counts": {"0": 1},
            "notified_at": (start + timedelta(seconds=30)).isoformat(),
        },
    )
    content = reports.build_report(
        project,
        plan,
        [SimpleNamespace(id="source", name="Камера 1")],
        [],
        {},
        [alert],
        "xlsx",
        {"old": plan},
    )
    sheet = load_workbook(BytesIO(content)).active
    headers = [cell.value for cell in sheet[1]]
    values = [cell.value for cell in sheet[2]]
    assert values[headers.index("Объект")] == "Тестовый объект"
    assert values[headers.index("Индекс этапа")] == "12.3"
    assert values[headers.index("Уведомление, МСК")]
    assert values[headers.index("Начало этапа, МСК")] == start.astimezone(
        reports.ZoneInfo("Europe/Moscow")
    ).strftime("%d.%m.%Y %H:%M:%S")
    assert any(
        reports.class_map()["0"]["name"] in header and header.endswith(": план")
        for header in headers
    )
    assert values[-2:] == [2, 1]


def test_minute_pending_initial_zero_has_no_confirmed_count():
    start = now().replace(second=0, microsecond=0)
    rows = [
        SimpleNamespace(
            id=str(index),
            run_id="run",
            captured_at=start + timedelta(seconds=index),
            offset_seconds=index,
            quality={"usable": True},
            model={"supported_classes": ["0"]},
            detections=[],
        )
        for index in range(5)
    ]
    [point] = series_points(rows, [0], 60, 3, expected_step=1)
    assert point["counts"]["0"] is None
    assert point["display_basis"]["0"] == "unknown"


def test_count_drop_confirms_when_lower_detector_counts_keep_fluctuating():
    start = now().replace(second=0, microsecond=0)
    levels = [4] * 12 + [3 if index % 3 else 2 for index in range(15)]
    rows = [
        SimpleNamespace(
            id=str(index),
            run_id="run",
            captured_at=start + timedelta(seconds=index),
            offset_seconds=index,
            quality={"usable": True},
            model={"supported_classes": ["0"]},
            detections=[{"class_id": "0"}] * count,
        )
        for index, count in enumerate(levels)
    ]
    points = series_points(
        rows,
        [0],
        0,
        3,
        expected_step=1,
        count_change_confirm_seconds=10,
    )
    assert all(point["counts"]["0"] == 4 for point in points[:22])
    assert points[22]["counts"]["0"] == 3

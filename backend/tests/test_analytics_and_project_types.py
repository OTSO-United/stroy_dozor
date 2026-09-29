from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app import models as m
from app.demo import create_demo_project
from app.telemetry import series_points
from test_app import post


def test_workbook_applicability_and_project_filter(client, runtime):
    catalog = client.get("/api/v1/catalog").json()
    assert len(catalog["project_types"]) == 9 and len(catalog["works"]) == 377
    counts = {
        "housing": 254,
        "education": 286,
        "healthcare": 313,
        "sport": 287,
        "culture": 286,
        "administration": 286,
        "preschool": 285,
        "office": 285,
        "roads": 200,
    }
    for key, count in counts.items():
        response = client.get("/api/v1/catalog", params={"project_type_id": key})
        assert response.status_code == 200
        assert len(response.json()["works"]) == count
        assert all(key in w["project_type_ids"] for w in response.json()["works"])
    housing = client.get("/api/v1/catalog?project_type_id=housing").json()["works"]
    roads = client.get("/api/v1/catalog?project_type_id=roads").json()["works"]
    assert "12.3.2" not in {w["code"] for w in housing}
    assert "12.3.2" in {w["code"] for w in roads}
    p = post(client, "/projects", {"name": "Дорога", "project_type_id": "roads"}).json()
    post(client, "/projects", {"name": "Жилой дом", "project_type_id": "housing"})
    assert [
        r["id"] for r in client.get("/api/v1/projects?project_type_id=roads").json()
    ] == [p["id"]]
    assert client.get("/api/v1/catalog?project_type_id=invalid").status_code == 422
    factory, _ = runtime
    with factory() as db:
        db.add(m.ProjectTypeWork(project_type_id="roads", work_code="999999"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_location_update_preserves_legacy_fields_and_validates_pair(client):
    body = {
        "name": "Школа",
        "project_type_id": "education",
        "latitude": 55.75,
        "longitude": 37.62,
    }
    response = post(client, "/projects", body)
    assert response.status_code == 201
    p = response.json()
    update_key = str(uuid4())
    result = client.put(
        f"/api/v1/projects/{p['id']}",
        json={"name": "Школа 2", "expected_revision": p["revision"]},
        headers={"Idempotency-Key": update_key},
    )
    assert result.status_code == 200
    updated = result.json()
    # Omitting the new fields means preserve; explicit null means clear.
    # These are different requests even when Pydantic defaults look identical.
    assert (
        client.put(
            f"/api/v1/projects/{p['id']}",
            json={
                "name": "Школа 2",
                "expected_revision": p["revision"],
                "latitude": None,
                "longitude": None,
            },
            headers={"Idempotency-Key": update_key},
        ).status_code
        == 409
    )
    assert (updated["project_type_id"], updated["latitude"], updated["longitude"]) == (
        "education",
        55.75,
        37.62,
    )
    for fields in (
        {"project_type_id": "bad"},
        {"latitude": 91, "longitude": 0},
        {"latitude": 0},
        {"longitude": 1},
    ):
        assert (
            post(client, "/projects", {"name": "Invalid", **fields}).status_code == 422
        )
    result = client.put(
        f"/api/v1/projects/{p['id']}",
        json={
            "name": "Школа 2",
            "expected_revision": updated["revision"],
            "latitude": None,
            "longitude": None,
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert result.json()["latitude"] is None and result.json()["longitude"] is None


def observation(
    index, *, offset=None, usable=True, run="run", captured=True, activity="unknown"
):
    offset = index if offset is None else offset
    return m.Observation(
        id=str(index),
        project_id="p",
        source_id="s",
        run_id=run,
        sample_index=index,
        captured_at=datetime(2026, 9, 19, tzinfo=timezone.utc)
        + timedelta(seconds=offset)
        if captured
        else None,
        offset_seconds=offset,
        detections=[{"class_id": "0", "activity": activity, "motion": "stationary"}]
        * (index % 3 + 1),
        quality={"usable": usable},
        model={"supported_classes": ["0"], "demo": False},
        evidence_key="x",
    )


def test_display_sampling_uses_last_sample_without_summing_or_bridging_gaps():
    rows = [
        observation(0),
        observation(1),
        observation(2),
        observation(3, offset=20),
        observation(4, offset=21, usable=False),
        observation(5, offset=22),
    ]
    points = series_points(rows, [0, 1], 10, 3, count_change_confirm_seconds=0)
    assert [p["id"] for p in points] == ["2", "3", "4", "5"]
    assert points[0]["counts"] == {"0": 3, "1": None}
    assert points[0]["activity"] == {"working": 0, "idle": 0, "unknown": 3}
    assert points[1]["segment"] != points[0]["segment"]
    assert points[2]["counts"]["0"] is None and points[2]["activity"] is None
    assert len({p["segment"] for p in points}) == 4


def test_activity_requires_explicit_action_and_resets_run_or_clock():
    rows = [
        observation(0, activity="working"),
        observation(1, activity="idle"),
        observation(2, captured=False),
        observation(3, captured=False, run="another"),
    ]
    points = series_points(rows, [0], 60, 3)
    assert points[0]["activity"]["idle"] == 2
    assert points[0]["activity"]["working"] == 0
    assert points[1]["time"] is None
    assert len(points) == 3 and len({p["segment"] for p in points}) == 3
    assert series_points([observation(0)], [99], 0, 3)[0]["activity"] is None


def test_expected_sampling_interval_does_not_create_false_graph_gaps():
    rows = [observation(0), observation(1, offset=10), observation(2, offset=20)]
    points = series_points(rows, [0], 0, 3, expected_step=10)
    assert len({point["segment"] for point in points}) == 1


def test_presence_series_holds_brief_detector_drop_and_confirms_sustained_absence():
    rows = [
        observation(2, offset=0),
        observation(3, offset=10),
        observation(6, offset=20),
        observation(9, offset=30),
        observation(12, offset=40),
    ]
    rows[1].detections = []
    rows[2].detections = rows[0].detections
    rows[3].detections = []
    rows[4].detections = []

    points = series_points(
        rows,
        [0],
        0,
        3,
        expected_step=10,
        absence_confirm_seconds=10,
    )

    assert [point["raw_counts"]["0"] for point in points] == [3, 0, 3, 0, 0]
    assert [point["counts"]["0"] for point in points] == [3, 3, 3, 3, 0]
    assert [point["presence"]["0"] for point in points] == [
        "present",
        "pending",
        "present",
        "pending",
        "absent",
    ]
    assert [point["activity"]["unknown"] for point in points] == [3, 3, 3, 3, 0]


def test_presence_series_keeps_bound_stages_spatially_separate():
    row = observation(0)
    row.detections = [
        {
            "class_id": "0",
            "bbox": [0.05, 0.05, 0.25, 0.25],
            "activity": "unknown",
        },
        {
            "class_id": "0",
            "bbox": [0.75, 0.05, 0.95, 0.25],
            "activity": "unknown",
        },
    ]
    regions = {
        row.run_id: [
            {
                "work_ids": ["left-stage"],
                "polygon": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]],
            },
            {
                "work_ids": ["right-stage"],
                "polygon": [[0.5, 0], [1, 0], [1, 1], [0.5, 1]],
            },
        ]
    }

    left = series_points(
        [row],
        [0],
        0,
        3,
        absence_confirm_seconds=0,
        work_id="left-stage",
        regions_by_run=regions,
    )
    right = series_points(
        [row],
        [0],
        0,
        3,
        absence_confirm_seconds=0,
        work_id="right-stage",
        regions_by_run=regions,
    )

    assert left[0]["counts"]["0"] == 1
    assert right[0]["counts"]["0"] == 1

    missing_snapshot = series_points(
        [row],
        [0],
        0,
        3,
        absence_confirm_seconds=0,
        work_id="left-stage",
        regions_by_run={},
    )
    assert missing_snapshot[0]["counts"]["0"] is None
    assert missing_snapshot[0]["presence"]["0"] == "unknown"


def test_evidence_gallery_is_scoped_to_episode_and_keeps_basis(client, runtime):
    factory, folder = runtime
    with factory() as db:
        result = create_demo_project(db, Path(folder), seed=612)
        alert = db.scalar(
            select(m.Alert).where(
                m.Alert.project_id == result["project_id"],
                m.Alert.kind == "missing",
                m.Alert.status == "open",
            )
        )
        aid, run, sid, work_id = (
            alert.id,
            alert.run_id,
            alert.source_id,
            alert.work_id,
        )
        before = m.Observation(
            project_id=alert.project_id,
            source_id=sid,
            run_id=run,
            sample_index=999,
            captured_at=alert.first_seen - timedelta(seconds=10),
            offset_seconds=-10,
            detections=[],
            quality={"usable": True},
            model={},
            evidence_key="not-episode",
        )
        other_run = m.Job(
            project_id=alert.project_id, source_id=sid, kind="analyze", payload={}
        )
        db.add(other_run)
        db.flush()
        foreign = m.Observation(
            project_id=alert.project_id,
            source_id=sid,
            run_id=other_run.id,
            sample_index=1000,
            captured_at=alert.first_seen,
            offset_seconds=0,
            detections=[],
            quality={"usable": True},
            model={},
            evidence_key="wrong-run",
        )
        db.add_all([before, foreign])
        db.commit()
        excluded = {before.id, foreign.id}
    response = client.get(f"/api/v1/alerts/{aid}/evidence?limit=2")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"]["id"] == sid
    assert payload["basis"]["run_id"] == run
    assert all(
        f["run_id"] == run and f["id"] not in excluded for f in payload["frames"]
    )
    assert payload["keyframes"]
    assert payload["absence_stats"]
    assert all("absence_ratio" in stat for stat in payload["absence_stats"])
    filtered_class = payload["keyframes"][0]["missing_class_ids"][0]
    filtered = client.get(
        f"/api/v1/alerts/{aid}/evidence",
        params={"class_id": filtered_class},
    ).json()
    assert filtered["keyframes"]
    assert all(
        filtered_class in frame["missing_class_ids"] for frame in filtered["keyframes"]
    )
    assert payload["video_url"] is None
    seen = {f["id"] for f in payload["frames"]}
    while payload["next_offset"] is not None:
        payload = client.get(
            f"/api/v1/alerts/{aid}/evidence?limit=2&offset={payload['next_offset']}"
        ).json()
        ids = {f["id"] for f in payload["frames"]}
        assert not (seen & ids)
        seen |= ids
    assert not (seen & excluded)
    series = client.get(
        f"/api/v1/projects/{result['project_id']}/timeseries?source_id={sid}&display_seconds=60"
    ).json()
    assert series["display_seconds"] == 60
    assert all("segment" in p and "activity" in p for p in series["points"])
    stage_series = client.get(
        f"/api/v1/projects/{result['project_id']}/timeseries",
        params={"source_id": sid, "work_id": work_id},
    ).json()
    assert stage_series["work_id"] == work_id
    assert stage_series["points"]
    first_page = client.get(
        f"/api/v1/projects/{result['project_id']}/timeseries?source_id={sid}&limit=2"
    ).json()
    assert first_page["truncated"] and first_page["next_before"]
    second_page = client.get(
        f"/api/v1/projects/{result['project_id']}/timeseries",
        params={
            "source_id": sid,
            "limit": 2,
            "before": first_page["next_before"],
            "before_sample_index": first_page["next_before_sample_index"],
            "before_id": first_page["next_before_id"],
        },
    ).json()
    assert {p["id"] for p in first_page["points"]}.isdisjoint(
        {p["id"] for p in second_page["points"]}
    )
    assert (
        client.get(
            f"/api/v1/projects/{result['project_id']}/timeseries",
            params={"source_id": sid, "display_seconds": 86400},
        ).status_code
        == 200
    )
    with factory() as db:
        source = db.get(m.Source, sid)
        source.file_key = "media/episode.mp4"
        source.kind = "video"
        db.commit()
    # The record link is exposed only while the original file is available.
    original = Path(folder) / "media/episode.mp4"
    original.parent.mkdir(exist_ok=True)
    original.write_bytes(b"test recording reference")
    assert (
        client.get(f"/api/v1/alerts/{aid}/evidence").json()["video_url"]
        == f"/api/v1/sources/{sid}/content"
    )
    original.unlink()
    assert client.get(f"/api/v1/alerts/{aid}/evidence").json()["video_url"] is None
    with factory() as db:
        observations = list(
            db.scalars(select(m.Observation).where(m.Observation.source_id == sid))
        )
        same_time = datetime(2026, 9, 25, tzinfo=timezone.utc)
        for observation in observations:
            observation.created_at = same_time
        db.commit()
    seen = set()
    cursor = {}
    while True:
        page = client.get(
            f"/api/v1/projects/{result['project_id']}/timeseries",
            params={"source_id": sid, "limit": 2, **cursor},
        ).json()
        page_ids = {point["id"] for point in page["points"]}
        assert not (seen & page_ids)
        seen.update(page_ids)
        if not page["next_before"]:
            break
        cursor = {
            "before": page["next_before"],
            "before_sample_index": page["next_before_sample_index"],
            "before_id": page["next_before_id"],
        }
    assert seen == {observation.id for observation in observations}


def test_eight_hour_stage_history_crosses_five_thousand_observation_page(
    client, runtime, project
):
    factory, _ = runtime
    start = datetime(2026, 9, 25, 6, tzinfo=timezone.utc)
    step = 28800 / 5001
    work_id = str(uuid4())
    with factory() as db:
        plan = m.Plan(
            project_id=project["id"],
            version=1,
            works=[
                {
                    "id": work_id,
                    "code": "1.1",
                    "title": "Восьмичасовой этап",
                    "starts_at": start.isoformat(),
                    "ends_at": (start + timedelta(hours=8)).isoformat(),
                    "resources": {"0": 1},
                }
            ],
        )
        source = m.Source(
            project_id=project["id"],
            name="Видео 8 часов",
            kind="video",
            sample_seconds=step,
            capture_start=start,
            metadata_json={},
        )
        db.add_all([plan, source])
        db.flush()
        db.get(m.Project, project["id"]).current_plan_id = plan.id
        job = m.Job(
            project_id=project["id"], source_id=source.id, kind="analyze", payload={}
        )
        db.add(job)
        db.flush()
        db.add_all(
            [
                m.Observation(
                    project_id=project["id"],
                    source_id=source.id,
                    run_id=job.id,
                    sample_index=index,
                    captured_at=start + timedelta(seconds=index * step),
                    offset_seconds=index * step,
                    created_at=start + timedelta(seconds=index * step),
                    detections=[],
                    quality={"usable": True},
                    model={},
                    evidence_key=f"synthetic/{index}",
                )
                for index in range(5001)
            ]
        )
        db.commit()
        source_id = source.id
    pages = []
    cursor = {}
    while True:
        page = client.get(
            f"/api/v1/projects/{project['id']}/timeseries",
            params={
                "source_id": source_id,
                "work_id": work_id,
                "limit": 5000,
                "display_seconds": 60,
                **cursor,
            },
        ).json()
        pages.append(page)
        if not page["next_before"]:
            break
        cursor = {
            "before": page["next_before"],
            "before_sample_index": page["next_before_sample_index"],
            "before_id": page["next_before_id"],
        }
    assert len(pages) == 2
    times = [
        datetime.fromisoformat(point["time"])
        for page in pages
        for point in page["points"]
    ]
    assert max(times) - min(times) > timedelta(hours=7, minutes=50)

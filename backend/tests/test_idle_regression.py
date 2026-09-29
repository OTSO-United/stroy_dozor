"""CV-13: real ROI pixels, event time, and conservative loss of visibility."""

import numpy as np
import pytest

from app.temporal_cv import ActivityAnalyzer, LocalTracker


def frame():
    return np.random.default_rng(37).integers(30, 220, (320, 320, 3), dtype=np.uint8)


def box(x=0.2, class_id="1"):
    return {"class_id": class_id, "confidence": 0.9, "bbox": [x, 0.2, x + 0.2, 0.7]}


@pytest.mark.parametrize("sample", [1, 60])
def test_bbox_jitter_on_identical_pixels_reaches_exact_idle_threshold(sample):
    image = frame()
    tracker = LocalTracker(max_interframe_seconds=sample * 1.75)
    activity = ActivityAnalyzer(sample_seconds=sample, idle_after=10, method="auto")
    states = {}
    identifiers = set()
    for tick in range(49):
        t = tick / 4
        if tick % (sample * 4) == 0:
            detected = tracker.update(
                [box(0.2 + (0.003125 if tick % 8 else 0))], t, image
            )
            identifiers.add(detected[0]["track_id"])
        activity.observe(image, t, tracker.tracks)
        states[t] = activity.annotate(detected, t, tracker.tracks)[0]["activity"]
    assert len(identifiers) == 1
    assert states[9.75] == "unknown"
    assert states[10] == states[12] == "idle"


def test_sparse_images_do_not_prove_continuous_idle():
    image = frame()
    tracker = LocalTracker()
    activity = ActivityAnalyzer(sample_seconds=60, idle_after=10)
    for t in range(0, 601, 60):
        detected = tracker.update([box()], t, image)
        activity.observe(image, t, tracker.tracks)
        assert (
            activity.annotate(detected, t, tracker.tracks)[0]["activity"] == "unknown"
        )
    assert detected[0]["activity_basis"] == "video_gap"


@pytest.mark.parametrize(
    "loss,reason",
    [
        ("dark", "camera_registration_uncertain"),
        ("small", "roi_too_small"),
        ("clipped", "truncated_equipment"),
        ("occluded", "equipment_occluded"),
        ("invisible", "activity_not_visually_observable"),
    ],
)
def test_unobservable_equipment_has_reason_not_idle(loss, reason):
    image = frame()
    if loss == "dark":
        image[:] = 0
    tracker = LocalTracker()
    activity = ActivityAnalyzer(
        idle_after=2, equipment_activity={"11": "stationary_capable"}
    )
    for t in range(6):
        item = box(class_id="11" if loss == "invisible" else "1")
        if loss == "small":
            item["bbox"] = [0.2, 0.2, 0.22, 0.22]
        if loss == "clipped":
            item["bbox"][0] = 0
        boxes = [item, {**box(), "class_id": "0"}] if loss == "occluded" else [item]
        detected = tracker.update(boxes, t, image)
        activity.observe(image, t, tracker.tracks)
        activity.annotate(detected, t, tracker.tracks)
    assert detected[0]["activity"] == "unknown"
    assert detected[0]["activity_basis"] == reason


def test_confirmed_idle_exits_for_mechanism_and_body_motion():
    image = frame()
    tracker = LocalTracker()
    activity = ActivityAnalyzer(idle_after=2, method="auto")
    for tick in range(21):
        t = tick / 2
        current = image.copy()
        if 6 <= tick <= 12:
            current[100:140, 85:110] = 40 if tick % 2 else 240
        x = 0.2 + max(0, tick - 12) * 0.015
        detected = tracker.update([box(x)], t, current)
        activity.observe(current, t, tracker.tracks)
        activity.annotate(detected, t, tracker.tracks)
        if tick == 4:
            assert detected[0]["activity"] == "idle"
        if tick == 8:
            assert detected[0]["activity_basis"] == "visible_mechanism_motion"
    assert detected[0]["activity_basis"] == "vehicle_motion"


def test_gap_and_new_id_restart_idle_evidence():
    image = frame()
    tracker = LocalTracker(max_interframe_seconds=1.75)
    activity = ActivityAnalyzer(idle_after=2)
    for t in [0, 1, 2, 10, 11, 12]:
        detected = tracker.update([box()], t, image)
        activity.observe(image, t, tracker.tracks)
        activity.annotate(detected, t, tracker.tracks)
        assert detected[0]["activity"] == ("idle" if t in [2, 12] else "unknown")
    assert detected[0]["track_id"] == 2


def test_short_detection_loss_requires_visible_recheck():
    image = frame()
    tracker = LocalTracker()
    activity = ActivityAnalyzer(idle_after=3)
    states = []
    for t in range(15):
        detected = tracker.update([] if t == 2 else [box()], t, image)
        activity.observe(image, t, tracker.tracks)
        annotated = activity.annotate(detected, t, tracker.tracks)
        if t == 2:
            assert annotated == []  # Two detections cannot establish retention.
        states.append(annotated[0]["activity"] if annotated else None)
    assert states[:5] == ["unknown", "unknown", None, "unknown", "unknown"]
    assert states[12] == "unknown"
    assert states[13] == "idle"


def test_camera_shift_cannot_be_vehicle_work_or_idle():
    image = frame()
    tracker = LocalTracker()
    activity = ActivityAnalyzer(idle_after=2)
    for t in range(5):
        shifted = np.roll(image, 15 * max(0, t - 2), axis=1)
        detected = tracker.update([box(0.2 + 15 * max(0, t - 2) / 320)], t, shifted)
        activity.observe(shifted, t, tracker.tracks)
        activity.annotate(detected, t, tracker.tracks)
        if t == 2:
            assert detected[0]["activity"] == "idle"
        if t > 2:
            assert detected[0]["activity"] == "unknown"
            assert detected[0]["activity_basis"] == "camera_motion"


def test_new_id_on_continuous_stream_does_not_inherit_idle():
    image = frame()
    tracker = LocalTracker()
    activity = ActivityAnalyzer(idle_after=2)
    for t in range(6):
        detected = tracker.update([box(0.2 if t < 3 else 0.7)], t, image)
        activity.observe(image, t, tracker.tracks)
        activity.annotate(detected, t, tracker.tracks)
        if t == 2:
            assert detected[0]["activity"] == "idle"
        if t == 3:
            assert detected[0]["track_id"] == 2
            assert detected[0]["activity"] == "unknown"
    assert detected[0]["activity"] == "idle"


@pytest.mark.parametrize("sample,duration,threshold", [(1, 65, 10), (60, 360, 300)])
def test_worker_pts_snapshot_storage_and_timeseries(
    client, project, runtime, monkeypatch, sample, duration, threshold
):
    from datetime import timedelta
    from sqlalchemy import select
    from app import worker, monitor, models as m, schemas
    from app.jobs import claim
    from app.db import now
    from test_app import approved, work, post

    factory, folder = runtime
    start = now().replace(second=0, microsecond=0) - timedelta(minutes=10)
    stage = work(
        start=start.isoformat(),
        end=(start + timedelta(hours=1)).isoformat(),
        resources={"0": 0, "1": 1, "16": 0},
    )
    plan = approved(client, project, [stage])
    settings = schemas.Settings(idle_after_seconds=threshold).model_dump()
    settings["equipment_activity"] = {"1": "mobile"}
    with factory() as db:
        source = m.Source(
            project_id=project["id"],
            kind="video",
            name="CV-13 synthetic",
            status="ready",
            capture_start=start,
            sample_seconds=sample,
            metadata_json={"width": 320, "height": 320, "duration_seconds": duration},
        )
        db.add(source)
        db.commit()
        source_id = source.id
    binding = post(
        client,
        f"/sources/{source_id}/bindings",
        {
            "regions": [
                {
                    "name": "Frame",
                    "work_ids": [stage["id"]],
                    "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                    "visibility_confirmed": True,
                }
            ]
        },
    ).json()
    with factory() as db:
        db.add(
            m.Job(
                project_id=project["id"],
                source_id=source_id,
                kind="analyze",
                payload={
                    "plan_id": plan["id"],
                    "binding_id": binding["id"],
                    "sample_seconds": sample,
                    "settings": settings,
                    "works": [{k: stage[k] for k in ("id", "code", "title")}],
                },
            )
        )
        db.commit()
        job = claim(db)

    image = frame()

    class Capture:
        tick = -1

        def read(self):
            self.tick += 1
            return (True, image) if self.tick <= duration * 4 else (False, None)

        def get(self, prop):
            return 4 if prop == worker.cv2.CAP_PROP_FPS else self.tick * 250

        def release(self):
            pass

    class Detector:
        info = {
            "name": "synthetic-cv13",
            "sha256": "synthetic",
            "supported_classes": ["1"],
        }
        index = 0

        def detect(self, current, parameters):
            self.index += 1
            return [box(0.2 + (0.003125 if self.index % 2 else 0))]

    monkeypatch.setattr(worker, "open_capture", lambda _: Capture())
    monkeypatch.setattr(worker, "detector", lambda: Detector())
    worker.run_analysis(job)
    with factory() as db:
        rows = list(
            db.scalars(
                select(m.Observation)
                .where(m.Observation.run_id == job.id)
                .order_by(m.Observation.sample_index)
            )
        )
        assert len(rows) == duration // sample + 1
        assert rows[-1].offset_seconds == duration
        assert len({o.detections[0]["track_id"] for o in rows}) == 1
        for o in rows:
            expected = "idle" if o.offset_seconds >= threshold else "unknown"
            assert o.detections[0]["activity"] == expected
            assert o.captured_at == start + timedelta(seconds=o.offset_seconds)
            assert o.model["temporal_cv"]["idle_after_seconds"] == threshold
            assert o.model["temporal_cv"]["version"] == "temporal-cv-6"
            assert o.model["temporal_cv"]["presence_confirm_frames"] == 4
            monitor.assess_one(db, o)
        db.commit()
    observations = client.get(f"/api/v1/sources/{source_id}/observations").json()
    assert any(
        o["detections"][0]["activity_basis"] == "visible_inactivity"
        for o in observations
    )
    for display in [0, 30, 60]:
        response = client.get(
            f"/api/v1/projects/{project['id']}/timeseries",
            params={
                "source_id": source_id,
                "work_id": stage["id"],
                "display_seconds": display,
            },
        )
        assert response.status_code == 200, response.text
        points = response.json()["points"]
        assert points[-1]["display_activity_by_class"]["1"] == {"working": 0, "idle": 1}
        idle_seconds = sum(
            p["activity_seconds_by_class"]["1"]["idle"] or 0 for p in points
        )
        assert idle_seconds == (duration - threshold if sample == 1 else 0)

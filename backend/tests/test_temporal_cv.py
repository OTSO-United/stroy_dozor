"""Synthetic contract checks; no manually labelled video or extra model weights."""

from types import SimpleNamespace

import numpy as np

from app.temporal_cv import ActivityAnalyzer, LocalTracker
from app.telemetry import series_points


def detection(x=0.2, confidence=0.9):
    return {
        "class_id": "1",
        "confidence": confidence,
        "bbox": [x, 0.2, x + 0.2, 0.7],
    }


def test_low_score_bridge_and_id_reset_never_inflate_presence():
    tracker = LocalTracker(max_gap=3, publish_confidence=0.35)
    for tick in range(-3, 1):
        first = tracker.update([detection()], tick)
    assert len(first) == 1
    bridge = tracker.update([detection(0.205, 0.2)], 1)
    assert len(bridge) == 1 and bridge[0]["observed"] is False
    assert bridge[0]["presence_basis"] == "track_grace"
    after_occlusion = tracker.update([detection(0.21)], 2)
    assert after_occlusion[0]["track_id"] == first[0]["track_id"]
    after_sparse_frame = tracker.update([detection(0.21)], 10)
    assert after_sparse_frame[0]["track_id"] == first[0]["track_id"]
    for tick in range(11, 15):
        tracker.update([], tick)
    after_reset = tracker.update([detection(0.21)], 15)
    assert after_reset[0]["track_id"] != first[0]["track_id"]
    rows = [
        SimpleNamespace(
            id=str(index),
            captured_at=None,
            offset_seconds=index,
            quality={"usable": True},
            model={"supported_classes": ["1"]},
            detections=value,
            run_id="run",
        )
        for index, value in enumerate([first, bridge, after_occlusion, after_reset])
    ]
    assert [point["counts"]["1"] for point in series_points(rows, [1], 0, 3)] == [
        1,
        1,
        1,
        1,
    ]


def test_presence_is_held_for_configured_missing_frame_window_only():
    tracker = LocalTracker(max_gap=10, publish_confidence=0.35, presence_grace_frames=2)
    for tick in range(-3, 1):
        observed = tracker.update([detection()], tick)
    first_missing = tracker.update([], 1)
    second_missing = tracker.update([], 2)
    expired = tracker.update([], 3)
    assert observed[0]["observed"] is True
    assert first_missing[0]["missed_frames"] == 1
    assert second_missing[0]["missed_frames"] == 2
    assert expired == []


def test_activity_requires_continuous_roi_and_resets_after_video_gap(monkeypatch):
    frame = np.random.default_rng(7).integers(30, 220, (320, 320, 3), dtype=np.uint8)
    tracker = LocalTracker(max_gap=3)
    analyzer = ActivityAnalyzer(
        max_gap=3,
        sample_seconds=1,
        equipment_activity={"1": "mobile"},
        method="difference",
        idle_after=2,
    )
    monkeypatch.setattr(analyzer, "_motion", lambda *args: "quiet")
    final = None
    for tick in range(5):
        t = tick * 0.5
        if tick % 2 == 0:
            final = tracker.update([detection()], t)
        analyzer.observe(frame, t, tracker.tracks)
    assert analyzer.annotate(final, 2, tracker.tracks)[0]["activity"] == "idle"
    analyzer.observe(frame, 8, tracker.tracks)
    next_detection = tracker.update([detection()], 8)
    analyzer.observe(frame, 8.5, tracker.tracks)
    assert (
        analyzer.annotate(next_detection, 8.5, tracker.tracks)[0]["activity"]
        == "unknown"
    )


def test_visible_mechanism_motion_overrides_still_bbox(monkeypatch):
    frame = np.random.default_rng(8).integers(30, 220, (320, 320, 3), dtype=np.uint8)
    tracker = LocalTracker(max_gap=3)
    analyzer = ActivityAnalyzer(sample_seconds=1, method="difference")
    monkeypatch.setattr(analyzer, "_motion", lambda *args: "motion")
    for tick in range(4):
        t = tick * 0.5
        if tick % 2 == 0:
            observed = tracker.update([detection()], t)
        analyzer.observe(frame, t, tracker.tracks)
    assert (
        analyzer.annotate(observed, 1.5, tracker.tracks)[0]["activity_basis"]
        == "visible_mechanism_motion"
    )


def test_visible_motion_threshold_changes_activity_evidence():
    previous = np.zeros((100, 100), dtype=np.uint8)
    current = previous.copy()
    current[35:55, 35:55] = 255
    sensitive = ActivityAnalyzer(motion_fraction_threshold=0.01)
    conservative = ActivityAnalyzer(motion_fraction_threshold=0.1)
    assert sensitive._motion(previous, current, False) == "motion"
    assert conservative._motion(previous, current, False) == "quiet"


def test_repeated_detector_losses_require_visual_continuity_and_recheck():
    frame = np.random.default_rng(11).integers(30, 220, (320, 320, 3), dtype=np.uint8)
    tracker = LocalTracker(max_gap=3)
    analyzer = ActivityAnalyzer(
        max_gap=3,
        sample_seconds=1,
        equipment_activity={"1": "mobile"},
        method="difference",
        idle_after=3,
    )
    activities = {}
    for tick in range(15):
        observed = tracker.update([detection()] if tick % 2 == 0 else [], tick)
        analyzer.observe(frame, tick, tracker.tracks)
        if observed and tick % 2 == 0:
            activities[tick] = analyzer.annotate(observed, tick, tracker.tracks)[0][
                "activity"
            ]
    assert activities[4] == "unknown"
    assert activities[6] == "unknown"
    assert activities[10] == "unknown"
    assert activities[12] == "idle"


def test_sparse_detection_with_dense_roi_keeps_track_and_confirms_idle(monkeypatch):
    frame = np.random.default_rng(25).integers(30, 220, (320, 320, 3), dtype=np.uint8)
    tracker = LocalTracker(max_gap=3)
    analyzer = ActivityAnalyzer(
        max_gap=3,
        sample_seconds=60,
        equipment_activity={"1": "mobile"},
        method="difference",
        idle_after=300,
    )
    monkeypatch.setattr(analyzer, "_motion", lambda *args: "quiet")
    identifiers = []
    for t in range(301):
        if t % 60 == 0:
            detections = tracker.update([detection()], t)
            identifiers.append(detections[0]["track_id"])
        analyzer.observe(frame, t, tracker.tracks)
        analyzer.annotate(detections, t, tracker.tracks)
    assert len(set(identifiers)) == 1
    assert detections[0]["activity"] == "idle"


def test_true_sample_discontinuity_resets_track_despite_frame_gap_allowance():
    tracker = LocalTracker(max_gap=3, max_interframe_seconds=90)
    first = tracker.update([detection()], 0)
    next_frame = tracker.update([detection()], 60)
    after_stream_pause = tracker.update([detection()], 180)
    assert first[0]["track_id"] == next_frame[0]["track_id"]
    assert after_stream_pause[0]["track_id"] != first[0]["track_id"]


def test_recent_work_survives_two_detector_misses_but_not_three(monkeypatch):
    image = np.random.default_rng(41).integers(30, 220, (320, 320, 3), dtype=np.uint8)
    tracker = LocalTracker(max_gap=3, max_interframe_seconds=17.5)
    activity = ActivityAnalyzer(max_gap=3, sample_seconds=10, idle_after=60)
    monkeypatch.setattr(activity, "_motion", lambda *args: "quiet")
    results = {}
    for tick in range(241):
        t = tick / 4
        if tick % 40 == 0:
            x = {0: 0.2, 10: 0.235, 20: 0.27, 30: 0.305}.get(t)
            detected = tracker.update([detection(x)] if x is not None else [], t, image)
        activity.observe(image, t, tracker.tracks)
        if tick % 40 == 0:
            results[t] = activity.annotate(detected, t, tracker.tracks)[0]
    assert results[30]["activity_basis"] == "vehicle_motion"
    for t in (40, 50):
        assert results[t]["activity"] == "working"
        assert results[t]["activity_basis"] == "recent_motion"
        assert results[t]["observed"] is False
    assert results[60]["activity"] == "unknown"
    assert results[60]["activity_basis"] == "track_grace"


def test_mobile_idle_resumes_only_after_same_place_and_ten_visible_seconds(monkeypatch):
    image = np.random.default_rng(42).integers(30, 220, (320, 320, 3), dtype=np.uint8)
    tracker = LocalTracker(max_gap=3, max_interframe_seconds=17.5)
    activity = ActivityAnalyzer(max_gap=3, sample_seconds=10, idle_after=60)
    monkeypatch.setattr(activity, "_motion", lambda *args: "quiet")
    results = {}
    for tick in range(321):
        t = tick / 4
        if tick % 40 == 0:
            detected = tracker.update([] if t == 60 else [detection()], t, image)
        activity.observe(image, t, tracker.tracks)
        if tick % 40 == 0:
            results[t] = activity.annotate(detected, t, tracker.tracks)[0]
    assert results[60]["activity"] == "unknown"
    assert results[70]["track_id"] == results[50]["track_id"]
    assert results[70]["activity_basis"] == "idle_recheck"
    assert results[80]["activity"] == "idle"


def test_mobile_idle_credit_is_discarded_when_reacquired_elsewhere(monkeypatch):
    image = np.random.default_rng(43).integers(30, 220, (320, 320, 3), dtype=np.uint8)
    tracker = LocalTracker(max_gap=3, max_interframe_seconds=17.5)
    activity = ActivityAnalyzer(max_gap=3, sample_seconds=10, idle_after=60)
    monkeypatch.setattr(activity, "_motion", lambda *args: "quiet")
    results = {}
    for tick in range(321):
        t = tick / 4
        if tick % 40 == 0:
            detected = tracker.update(
                [] if t == 60 else [detection(0.3 if t >= 70 else 0.2)], t, image
            )
        activity.observe(image, t, tracker.tracks)
        if tick % 40 == 0:
            results[t] = activity.annotate(detected, t, tracker.tracks)[0]
    assert results[70]["track_id"] == results[50]["track_id"]
    assert results[80]["activity"] != "idle"


def test_visual_change_during_detector_loss_cannot_become_idle(monkeypatch):
    image = np.random.default_rng(45).integers(30, 220, (320, 320, 3), dtype=np.uint8)
    changed = image.copy()
    changed[45:260, 45:150] = 0
    tracker = LocalTracker(max_gap=3, max_interframe_seconds=17.5)
    activity = ActivityAnalyzer(max_gap=3, sample_seconds=10, idle_after=60)
    monkeypatch.setattr("app.temporal_cv.cv2.phaseCorrelate", lambda *args: ((0, 0), 1))
    results = {}
    for tick in range(321):
        t = tick / 4
        current = changed if 60 <= t < 70 else image
        if tick % 40 == 0:
            detected = tracker.update([] if t == 60 else [detection()], t, current)
        activity.observe(current, t, tracker.tracks)
        if tick % 40 == 0:
            results[t] = activity.annotate(detected, t, tracker.tracks)[0]
    assert results[70]["track_id"] == results[50]["track_id"]
    assert results[80]["activity"] != "idle"


def test_mechanism_motion_between_detector_samples_reaches_next_observation(
    monkeypatch,
):
    image = np.random.default_rng(46).integers(30, 220, (320, 320, 3), dtype=np.uint8)
    tracker = LocalTracker(max_interframe_seconds=17.5)
    activity = ActivityAnalyzer(sample_seconds=10, idle_after=60)
    monkeypatch.setattr(
        activity,
        "_motion",
        lambda *args: "motion" if 5 <= activity.last_frame_t <= 5.25 else "quiet",
    )
    for tick in range(41):
        t = tick / 4
        if tick % 40 == 0:
            detected = tracker.update([detection()], t, image)
        activity.observe(image, t, tracker.tracks)
    assert activity.annotate(detected, 10, tracker.tracks)[0]["activity_basis"] == (
        "visible_mechanism_motion"
    )


def test_stationary_capable_requires_longer_visible_window():
    image = np.random.default_rng(44).integers(30, 220, (320, 320, 3), dtype=np.uint8)
    tracker = LocalTracker()
    activity = ActivityAnalyzer(
        sample_seconds=1,
        idle_after=10,
        equipment_activity={"1": "stationary_capable"},
    )
    results = {}
    for t in range(153):
        detected = tracker.update([] if t == 121 else [detection()], t, image)
        activity.observe(image, t, tracker.tracks)
        if t in (10, 119, 120, 121, 122, 151, 152):
            results[t] = activity.annotate(detected, t, tracker.tracks)[0]["activity"]
    assert results == {
        10: "unknown",
        119: "unknown",
        120: "idle",
        121: "unknown",
        122: "unknown",
        151: "unknown",
        152: "idle",
    }

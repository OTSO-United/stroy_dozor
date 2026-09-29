"""Retention must not multiply isolated detector mistakes into presence evidence."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.rules import evaluate
from app.telemetry import series_points
from app.temporal_cv import LocalTracker


def box(class_id="1", x=0.2, confidence=0.9):
    return {
        "class_id": class_id,
        "confidence": confidence,
        "bbox": [x, 0.2, x + 0.2, 0.7],
    }


@pytest.mark.parametrize("hits", [1, 2, 3, 4])
def test_only_four_real_confirmations_allow_three_held_samples(hits):
    tracker = LocalTracker()
    for tick in range(hits):
        observed = tracker.update([box()], tick)
        assert len(observed) == 1 and observed[0]["observed"] is True
    for missed in range(1, 5):
        held = tracker.update([], hits - 1 + missed)
        if hits == 4 and missed <= 3:
            assert len(held) == 1
            assert held[0]["track_id"] == observed[0]["track_id"]
            assert held[0]["observed"] is False
            assert held[0]["presence_basis"] == "track_grace"
            assert held[0]["missed_frames"] == missed
        else:
            assert held == []


@pytest.mark.parametrize("gap", [[], [box(confidence=0.2)]])
def test_tentative_id_survives_gap_but_confirmation_starts_again(gap):
    tracker = LocalTracker()
    for tick in range(3):
        first = tracker.update([box()], tick)
    assert tracker.update(gap, 3) == []
    for tick in range(4, 7):
        observed = tracker.update([box()], tick)
        assert observed[0]["track_id"] == first[0]["track_id"]
    assert tracker.update(gap, 7) == []
    for tick in range(8, 12):
        tracker.update([box()], tick)
    assert tracker.update([], 12)[0]["observed"] is False


def test_weak_boxes_do_not_extend_confirmation_past_retention_window():
    tracker = LocalTracker()
    for tick in range(4):
        observed = tracker.update([box()], tick)
    for tick in range(4, 7):
        assert tracker.update([box(confidence=0.2)], tick)[0]["observed"] is False
    assert tracker.update([box(confidence=0.2)], 7) == []
    reacquired = tracker.update([box()], 8)
    assert reacquired[0]["track_id"] == observed[0]["track_id"]
    assert tracker.update([], 9) == []


def test_confirmed_track_can_resume_after_short_occlusion():
    tracker = LocalTracker()
    for tick in range(4):
        first = tracker.update([box()], tick)
    tracker.update([], 4)
    again = tracker.update([box()], 5)
    held = tracker.update([], 6)
    assert first[0]["track_id"] == again[0]["track_id"] == held[0]["track_id"]
    assert held[0]["observed"] is False and held[0]["missed_frames"] == 1


@pytest.mark.parametrize("initial_hits", [3, 4])
def test_class_switch_revokes_old_memory_and_requires_new_confirmation(initial_hits):
    tracker = LocalTracker()
    for tick in range(initial_hits):
        tracker.update([box()], tick)
    changed = tracker.update([box(class_id="4")], initial_hits)
    assert len(changed) == 1 and changed[0]["class_id"] == "4"
    assert tracker.update([], initial_hits + 1) == []
    # Alternating labels must not restore either class from memory.
    for tick in range(initial_hits + 2, initial_hits + 8):
        result = tracker.update([box(class_id="1" if tick % 2 else "4")], tick)
        assert len(result) == 1 and result[0]["observed"] is True
    assert tracker.update([], initial_hits + 8) == []
    for tick in range(initial_hits + 9, initial_hits + 13):
        tracker.update([box(class_id="4")], tick)
    held = tracker.update([], initial_hits + 13)
    assert len(held) == 1 and held[0]["class_id"] == "4"


def test_another_object_of_different_class_does_not_revoke_confirmation():
    tracker = LocalTracker()
    for tick in range(4):
        tracker.update([box()], tick)
    result = tracker.update([box(class_id="4", x=0.7)], 4)
    assert [(d["class_id"], d["observed"]) for d in result] == [
        ("4", True),
        ("1", False),
    ]


@pytest.mark.parametrize("restart_at", [10, 0])
def test_discontinuity_does_not_inherit_confirmation(restart_at):
    tracker = LocalTracker(max_interframe_seconds=1.75)
    for tick in range(4):
        first = tracker.update([box()], tick)
    restarted = tracker.update([box()], restart_at)
    assert restarted[0]["track_id"] != first[0]["track_id"]
    assert tracker.update([], restart_at + 1) == []


def test_retention_can_still_be_disabled():
    tracker = LocalTracker(presence_grace_frames=0)
    for tick in range(4):
        tracker.update([box()], tick)
    assert tracker.update([], 4) == []


def test_single_false_excavator_is_not_repeated_in_counts_or_assessment():
    tracker = LocalTracker(max_interframe_seconds=105)
    for tick in range(-4, 0):
        tracker.update([box()], tick * 60)
    start = datetime(2026, 9, 29, tzinfo=timezone.utc)
    work = {
        "id": "work",
        "code": "12.3.1",
        "title": "Excavation",
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=1)).isoformat(),
        "resources": {"1": 1},
    }
    region = {
        "id": "region",
        "name": "Frame",
        "work_ids": ["work"],
        "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
        "visibility_confirmed": True,
        "primary": True,
    }
    rows, counts, excess = [], [], []
    for tick in range(6):
        detected = tracker.update(
            [box(), box(x=0.7)] if tick == 0 else [box()], tick * 60
        )
        captured_at = start + timedelta(minutes=tick)
        assessed = evaluate([work], [region], detected, captured_at, {"1"}, True)[0]
        counts.append(assessed["counts"]["1"])
        excess.append(any(f["kind"] == "excess" for f in assessed["findings"]))
        rows.append(
            SimpleNamespace(
                id=str(tick),
                captured_at=captured_at,
                offset_seconds=tick * 60,
                quality={"usable": True},
                model={"supported_classes": ["1"]},
                detections=detected,
                run_id="run",
            )
        )
    points = series_points(rows, [1], 0, 3, expected_step=60)
    assert counts == [2, 1, 1, 1, 1, 1]
    assert excess == [True, False, False, False, False, False]
    assert [p["raw_counts"]["1"] for p in points] == counts
    # The separate 10-second chart confirmation still waits for another sample.
    assert [p["counts"]["1"] for p in points] == [2, 2, 1, 1, 1, 1]

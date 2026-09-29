"""Session-local tracking and visible activity from sequential video frames.

Track IDs are evidence references, never equipment counts or physical identities.
No state survives a run, camera discontinuity, or a gap beyond ``max_gap`` processed frames.
"""

from collections import deque
from dataclasses import dataclass, field
import math

import cv2
import numpy as np


VERSION = "temporal-cv-6"
PRESENCE_CONFIRM_FRAMES = 4
VISUALLY_CHECKABLE = {"0", "1", "4", "8", "16", "17", "19", "21", "26"}


def _center(box):
    return ((box[0] + box[2]) / 2, box[3])


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


def _bounded(box):
    result = [min(1.0, max(0.0, float(value))) for value in box]
    return result if result[2] > result[0] and result[3] > result[1] else None


def _appearance(frame, box):
    if frame is None:
        return None
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = (
        max(0, min(limit, round(value * limit)))
        for value, limit in zip(box, (width, height, width, height))
    )
    if x2 - x1 < 12 or y2 - y1 < 12:
        return None
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [8, 4], [0, 180, 0, 256])
    return cv2.normalize(hist, hist).reshape(-1)


@dataclass
class _Track:
    id: int
    class_id: str
    bbox: list[float]
    last_t: float
    appearance: np.ndarray | None = None
    velocity: tuple[float, float] = (0.0, 0.0)
    positions: deque = field(default_factory=lambda: deque(maxlen=8))
    motion: str = "unknown"
    trusted: bool = True
    last_confidence: float = 0.0
    missed_frames: int = 0
    last_frame: int = 0
    confirmation_hits: int = 1

    def predicted(self, t):
        dt = min(max(t - self.last_t, 0), 2.0)
        dx, dy = self.velocity[0] * dt, self.velocity[1] * dt
        return [
            self.bbox[0] + dx,
            self.bbox[1] + dy,
            self.bbox[2] + dx,
            self.bbox[3] + dy,
        ]

    def observe(self, box, t, appearance, *, trusted, confidence):
        old = _center(self.bbox)
        current = _center(box)
        dt = t - self.last_t
        if trusted and dt > 0:
            instant = ((current[0] - old[0]) / dt, (current[1] - old[1]) / dt)
            self.velocity = tuple(
                0.5 * previous + 0.5 * measured
                for previous, measured in zip(self.velocity, instant)
            )
        self.bbox, self.last_t = list(box), t
        self.trusted = trusted
        if trusted:
            self.confirmation_hits = min(
                PRESENCE_CONFIRM_FRAMES, self.confirmation_hits + 1
            )
            self.last_confidence = confidence
            self.missed_frames = 0
        else:
            self.missed_frames += 1
        if trusted and appearance is not None:
            self.appearance = appearance
        if not trusted:
            self.positions.clear()
            self.motion = "unknown"
            return
        self.positions.append((t, current))
        if len(self.positions) < 3 or t - self.positions[0][0] < 1:
            self.motion = "unknown"
            return
        start = self.positions[0][1]
        displacement = math.dist(start, current)
        height = max(0.01, box[3] - box[1])
        threshold = max(0.008, 0.12 * height)
        self.motion = (
            "moving_observed" if displacement > threshold else "stationary_observed"
        )


class LocalTracker:
    """Two-pass bbox association with short lost-track retention.

    High-confidence boxes become observations. Low-confidence boxes can only
    continue an existing track, and never create or confirm an equipment instance.
    Only four consecutive trusted observations of the same class allow retention.
    """

    def __init__(
        self,
        max_gap=3,
        publish_confidence=0.35,
        presence_grace_frames=3,
        max_interframe_seconds=None,
    ):
        self.max_gap = max_gap
        self.max_interframe_seconds = max_interframe_seconds
        self.publish_confidence = publish_confidence
        self.presence_grace_frames = presence_grace_frames
        self.tracks: dict[int, _Track] = {}
        self.next_id = 1
        self.last_t = None
        self.frame_index = 0

    def _score(self, track, detection, appearance, t):
        if track.class_id != str(detection["class_id"]):
            return None
        box = detection["bbox"]
        predicted = track.predicted(t)
        overlap = _iou(predicted, box)
        distance = math.dist(_center(predicted), _center(box))
        size = max(
            predicted[2] - predicted[0],
            predicted[3] - predicted[1],
            box[2] - box[0],
            box[3] - box[1],
        )
        radius = max(
            0.025,
            0.7 * size + 0.015 * min(self.max_gap, self.frame_index - track.last_frame),
        )
        if overlap < 0.05 and distance > 0.45 * radius:
            return None
        if distance > radius:
            return None
        similarity = 0.5
        if appearance is not None and track.appearance is not None:
            similarity = max(
                0.0,
                float(
                    cv2.compareHist(track.appearance, appearance, cv2.HISTCMP_CORREL)
                ),
            )
            if overlap < 0.15 and similarity < 0.2:
                return None
        return 0.65 * overlap + 0.25 * (1 - distance / radius) + 0.1 * similarity

    def _match(self, indices, detections, appearances, t, available):
        candidates = []
        for index in indices:
            for track_id in available:
                score = self._score(
                    self.tracks[track_id], detections[index], appearances[index], t
                )
                if score is not None:
                    candidates.append((score, index, track_id))
        matches, used_detections, used_tracks = {}, set(), set()
        for _, index, track_id in sorted(candidates, reverse=True):
            if index not in used_detections and track_id not in used_tracks:
                matches[index] = track_id
                used_detections.add(index)
                used_tracks.add(track_id)
        available.difference_update(used_tracks)
        return matches

    def update(self, detections, t, frame=None):
        if self.last_t is not None and (
            t <= self.last_t
            or (
                self.max_interframe_seconds is not None
                and t - self.last_t > self.max_interframe_seconds
            )
        ):
            self.tracks.clear()
        self.last_t = t
        self.frame_index += 1
        self.tracks = {
            key: track
            for key, track in self.tracks.items()
            if self.frame_index - track.last_frame <= self.max_gap
        }
        appearances = [_appearance(frame, d["bbox"]) for d in detections]
        high = [
            i
            for i, d in enumerate(detections)
            if d["confidence"] >= self.publish_confidence
        ]
        low = [
            i
            for i, d in enumerate(detections)
            if d["confidence"] < self.publish_confidence
        ]
        available = set(self.tracks)
        high_matches = self._match(high, detections, appearances, t, available)
        low_matches = self._match(low, detections, appearances, t, available)
        matches = {**high_matches, **low_matches}
        for index, track_id in matches.items():
            self.tracks[track_id].observe(
                detections[index]["bbox"],
                t,
                appearances[index],
                trusted=index in high,
                confidence=detections[index]["confidence"],
            )
            self.tracks[track_id].last_frame = self.frame_index
        matched_track_ids = set(matches.values())
        for track_id, track in self.tracks.items():
            if track_id not in matched_track_ids:
                track.missed_frames += 1
            if track.missed_frames:
                # Tentative tracks need consecutive detector confirmations. A mature
                # track keeps its confirmation only within the bounded grace window.
                expired = track.missed_frames > min(
                    self.presence_grace_frames, self.max_gap
                )
                class_conflict = any(
                    str(detections[index]["class_id"]) != track.class_id
                    and _iou(track.predicted(t), detections[index]["bbox"]) >= 0.5
                    for index in high
                )
                if (
                    track.confirmation_hits < PRESENCE_CONFIRM_FRAMES
                    or expired
                    or class_conflict
                ):
                    track.confirmation_hits = 0
        published = []
        observed_track_ids = set()
        for index in high:
            if index not in matches:
                track_id = self.next_id
                self.next_id += 1
                box = detections[index]["bbox"]
                track = _Track(
                    track_id,
                    str(detections[index]["class_id"]),
                    list(box),
                    t,
                    appearances[index],
                    last_confidence=detections[index]["confidence"],
                    last_frame=self.frame_index,
                )
                track.positions.append((t, _center(box)))
                self.tracks[track_id] = track
            else:
                track_id = matches[index]
                track = self.tracks[track_id]
            observed_track_ids.add(track_id)
            published.append(
                {
                    **detections[index],
                    "track_id": track_id,
                    "motion": track.motion,
                    "activity": "unknown",
                    "observed": True,
                    "presence_basis": "detector",
                }
            )
        for track_id, track in self.tracks.items():
            if (
                track_id in observed_track_ids
                or track.confirmation_hits < PRESENCE_CONFIRM_FRAMES
                or track.missed_frames <= 0
                or track.missed_frames > self.presence_grace_frames
                or self.frame_index - track.last_frame > self.max_gap
            ):
                continue
            bbox = _bounded(track.predicted(t))
            if bbox is None:
                continue
            published.append(
                {
                    "class_id": track.class_id,
                    "confidence": track.last_confidence,
                    "bbox": bbox,
                    "track_id": track_id,
                    "motion": "unknown",
                    "activity": "unknown",
                    "activity_basis": "track_grace",
                    "observed": False,
                    "presence_basis": "track_grace",
                    "missed_frames": track.missed_frames,
                }
            )
        return published


@dataclass
class _Activity:
    last_roi_t: float | None = None
    previous_roi: np.ndarray | None = None
    region: tuple[int, int, int, int] | None = None
    quiet_since: float | None = None
    unseen_seconds: float = 0.0
    motion_hits: int = 0
    mechanism_until: float | None = None
    last_flow_t: float | None = None
    last_bbox: list[float] | None = None
    last_appearance: np.ndarray | None = None
    lost_since: float | None = None
    visual_grace: bool = False
    recheck_since: float | None = None
    working_until: float | None = None
    last_chassis_t: float | None = None
    reason: str = "insufficient_video"

    def reset_visibility(self, reason="insufficient_video"):
        self.last_roi_t = None
        self.previous_roi = None
        self.region = None
        self.quiet_since = None
        self.unseen_seconds = 0.0
        self.motion_hits = 0
        self.mechanism_until = None
        self.last_flow_t = None
        self.last_bbox = None
        self.last_appearance = None
        self.lost_since = None
        self.visual_grace = False
        self.recheck_since = None
        self.working_until = None
        self.last_chassis_t = None
        self.reason = reason


class ActivityAnalyzer:
    """Conservative visible-motion evidence with bounded detector dropouts."""

    def __init__(
        self,
        *,
        max_gap=3,
        sample_seconds=1,
        equipment_activity=None,
        method="difference",
        idle_after=300,
        motion_fraction_threshold=0.008,
    ):
        if method not in {"difference", "flow", "auto"}:
            raise ValueError("Неизвестный метод анализа движения")
        self.max_gap = max_gap
        self.sample_seconds = sample_seconds
        self.equipment_activity = equipment_activity or {}
        self.method = method
        self.idle_after = idle_after
        self.motion_fraction_threshold = motion_fraction_threshold
        self.states: dict[int, _Activity] = {}
        self.last_frame_t = None
        self.previous_scene = None
        self.minimum_step = 0.25
        # A remembered observation is never evidence for the unseen interval.
        self.reconnect_seconds = min(30.0, max_gap * sample_seconds)
        self.working_hold_seconds = min(30.0, 2 * sample_seconds)

    def _same_position(self, state, track):
        previous = state.last_bbox
        if previous is None or _iou(previous, track.bbox) < 0.45:
            return False
        size = min(
            previous[2] - previous[0],
            previous[3] - previous[1],
            track.bbox[2] - track.bbox[0],
            track.bbox[3] - track.bbox[1],
        )
        if math.dist(_center(previous), _center(track.bbox)) > max(0.008, 0.15 * size):
            return False
        if state.last_appearance is not None and track.appearance is not None:
            similarity = cv2.compareHist(
                state.last_appearance, track.appearance, cv2.HISTCMP_CORREL
            )
            if not math.isfinite(similarity) or similarity < 0.4:
                return False
        return True

    def _idle_threshold(self, track):
        if (
            self.equipment_activity.get(str(track.class_id), "mobile")
            == "stationary_capable"
        ):
            return max(120, 2 * self.idle_after)
        return self.idle_after

    def _recheck_seconds(self, track):
        return (
            30
            if self.equipment_activity.get(str(track.class_id), "mobile")
            == "stationary_capable"
            else 10
        )

    def _observe_grace(self, frame, t, state):
        """Accrue provisional quiet evidence only while the same ROI stays visible."""

        def break_continuity():
            state.previous_roi = None
            state.region = None
            state.last_roi_t = None
            state.quiet_since = None
            state.unseen_seconds = 0.0
            state.visual_grace = False
            state.reason = "track_grace"

        if (
            state.region is None
            or state.previous_roi is None
            or state.last_roi_t is None
            or t - state.last_roi_t > max(0.6, min(self.sample_seconds, 2.0) * 1.75)
        ):
            break_continuity()
            return
        x1, y1, x2, y2 = state.region
        roi = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        roi = cv2.resize(roi, (128, 128), interpolation=cv2.INTER_AREA)
        if roi.mean() < 12 or roi.mean() > 248 or roi.std() < 4:
            break_continuity()
            return
        use_flow = self.method == "flow" or (
            self.method == "auto"
            and (state.last_flow_t is None or t - state.last_flow_t >= 1)
        )
        result = self._motion(state.previous_roi, roi, use_flow)
        if use_flow:
            state.last_flow_t = t
        if result != "quiet":
            # Changed pixels could be the machine leaving or another object.
            break_continuity()
            return
        state.previous_roi = roi
        state.last_roi_t = t
        state.visual_grace = True
        state.reason = "visual_track_grace"

    def _region(self, box, shape):
        height, width = shape[:2]
        x1, y1, x2, y2 = box
        bw, bh = x2 - x1, y2 - y1
        return (
            max(0, round((x1 - 0.12 * bw) * width)),
            max(0, round((y1 - 0.12 * bh) * height)),
            min(width, round((x2 + 0.12 * bw) * width)),
            min(height, round((y2 + 0.12 * bh) * height)),
        )

    def _motion(self, previous, current, use_flow):
        # Aligning on the equipment crop can erase the very mechanism motion
        # we need. Camera movement is checked against the whole scene instead.
        difference = cv2.absdiff(previous, current)
        difference = cv2.GaussianBlur(difference, (3, 3), 0)
        _, mask = cv2.threshold(difference, 18, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        margin = max(4, mask.shape[0] // 12)
        mask[:margin] = 0
        mask[-margin:] = 0
        mask[:, :margin] = 0
        mask[:, -margin:] = 0
        fraction = float(np.count_nonzero(mask)) / max(1, mask.size)
        if fraction > 0.35:
            return "uncertain"
        if fraction >= self.motion_fraction_threshold:
            return "motion"
        if use_flow:
            try:
                flow = cv2.calcOpticalFlowFarneback(
                    previous, current, None, 0.5, 2, 15, 2, 5, 1.2, 0
                )
            except cv2.error:
                # The cheaper frame-difference result remains usable.
                return "quiet"
            magnitude = cv2.magnitude(flow[:, :, 0], flow[:, :, 1])
            flow_fraction = float(np.count_nonzero(magnitude > 1.5)) / magnitude.size
            if flow_fraction > 0.35:
                return "uncertain"
            if flow_fraction >= 0.015:
                return "motion"
        return "quiet"

    def observe(self, frame, t, tracks):
        # Detector sampling can be sparse while decoded ROI frames stay dense.
        # Missing decoded time must never become evidence of inactivity.
        evidence_step = min(self.sample_seconds, 2.0)
        frame_gap = max(0.6, evidence_step * 1.75)
        tolerated_gap = max(0.6, (self.max_gap + 0.5) * evidence_step)
        reset_reason = None
        if self.last_frame_t is not None:
            if t <= self.last_frame_t or t - self.last_frame_t > frame_gap:
                reset_reason = "video_gap"
                for state in self.states.values():
                    state.reset_visibility(reset_reason)
                self.previous_scene = None
            if 0 <= t - self.last_frame_t < self.minimum_step:
                return
        self.last_frame_t = t
        if not tracks:
            self.states.clear()
            self.previous_scene = None
            return
        scene = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        scene = cv2.resize(scene, (320, 180), interpolation=cv2.INTER_AREA)
        if self.previous_scene is not None:
            shift, response = cv2.phaseCorrelate(
                self.previous_scene.astype(np.float32), scene.astype(np.float32)
            )
            shift_pixels = math.hypot(
                shift[0] * frame.shape[1] / 320,
                shift[1] * frame.shape[0] / 180,
            )
            if (
                not math.isfinite(response)
                or response < 0.2
                or not math.isfinite(shift_pixels)
                or shift_pixels > 3
            ):
                for state in self.states.values():
                    state.reset_visibility(
                        "camera_motion"
                        if response >= 0.2
                        else "camera_registration_uncertain"
                    )
                # Camera displacement is not evidence of chassis movement.
                for track in tracks.values():
                    track.positions.clear()
                    track.motion = "unknown"
                    track.velocity = (0.0, 0.0)
                self.previous_scene = scene
                return
        self.previous_scene = scene
        self.states = {key: self.states.get(key, _Activity()) for key in tracks}
        for track_id, track in tracks.items():
            state = self.states[track_id]
            if not track.trusted or track.missed_frames:
                if state.lost_since is None:
                    state.lost_since = t
                self._observe_grace(frame, t, state)
                continue
            if t - track.last_t > self.sample_seconds * 1.75:
                state.reset_visibility("stale_detection")
                continue
            if track.motion == "moving_observed":
                if state.last_chassis_t != track.last_t:
                    state.reset_visibility("vehicle_motion")
                    state.last_chassis_t = track.last_t
                    state.working_until = t + self.working_hold_seconds
                continue
            if any(value <= 0.005 for value in track.bbox[:2]) or any(
                value >= 0.995 for value in track.bbox[2:]
            ):
                state.reset_visibility("truncated_equipment")
                continue
            x1, y1, x2, y2 = self._region(track.bbox, frame.shape)
            if x2 - x1 < 48 or y2 - y1 < 48:
                state.reset_visibility("roi_too_small")
                continue
            if any(
                other.id != track_id and _iou(track.bbox, other.bbox) > 0.1
                for other in tracks.values()
            ):
                state.reset_visibility("equipment_occluded")
                continue
            if state.lost_since is not None:
                if (
                    state.last_roi_t is None
                    or not state.visual_grace
                    or t - state.lost_since > self.reconnect_seconds
                    or not self._same_position(state, track)
                ):
                    state.reset_visibility("roi_changed")
                else:
                    # The fixed ROI remained quiet on decoded frames. Its time
                    # is provisional until the detector confirms the same place.
                    if state.recheck_since is None:
                        state.recheck_since = t
                    state.reason = "idle_recheck"
                state.lost_since = None
                state.visual_grace = False
            region = (x1, y1, x2, y2)
            if state.region is not None:
                height, width = frame.shape[:2]
                box_pixels = [
                    value * limit
                    for value, limit in zip(track.bbox, (width, height, width, height))
                ]
                contained = all(
                    box_pixels[i] >= state.region[i] for i in (0, 1)
                ) and all(box_pixels[i] <= state.region[i] for i in (2, 3))
                if (
                    contained
                    and max(abs(a - b) for a, b in zip(region, state.region)) <= 12
                ):
                    # Keep image coordinates fixed through detector bbox jitter.
                    # Comparing resized crops from different pixels fakes motion.
                    region = state.region
                    x1, y1, x2, y2 = region
                else:
                    state.reset_visibility("roi_changed")
            roi = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
            roi = cv2.resize(roi, (128, 128), interpolation=cv2.INTER_AREA)
            if roi.mean() < 12 or roi.mean() > 248 or roi.std() < 4:
                state.reset_visibility("roi_unusable")
                continue
            if (
                state.previous_roi is None
                or state.region is None
                or state.last_roi_t is None
                or t - state.last_roi_t > tolerated_gap
            ):
                if state.recheck_since is None:
                    reason = reset_reason or state.reason
                    state.reset_visibility(reason)
                    state.quiet_since = t
                    state.unseen_seconds = 0.0
                elif state.quiet_since is None:
                    state.quiet_since = t
                    state.unseen_seconds = 0.0
                state.previous_roi = roi
                state.region = region
                state.last_roi_t = t
                state.motion_hits = 0
                state.last_bbox = list(track.bbox)
                state.last_appearance = (
                    track.appearance.copy() if track.appearance is not None else None
                )
                continue
            state.unseen_seconds += max(0.0, t - state.last_roi_t - evidence_step)
            use_flow = self.method == "flow" or (
                self.method == "auto"
                and (state.last_flow_t is None or t - state.last_flow_t >= 1)
            )
            result = self._motion(state.previous_roi, roi, use_flow)
            if use_flow:
                state.last_flow_t = t
            state.previous_roi, state.region, state.last_roi_t = roi, region, t
            state.last_bbox = list(track.bbox)
            state.last_appearance = (
                track.appearance.copy() if track.appearance is not None else None
            )
            if result == "uncertain":
                state.reset_visibility("roi_motion_uncertain")
            elif result == "motion":
                state.reason = "motion_pending"
                state.quiet_since = t
                state.unseen_seconds = 0.0
                state.recheck_since = None
                state.motion_hits += 1
                if state.motion_hits >= 2:
                    state.mechanism_until = t + min(20, max(2, self.sample_seconds))
            else:
                state.motion_hits = 0
                if (
                    state.recheck_since is not None
                    and t - state.recheck_since < self._recheck_seconds(track)
                ):
                    state.reason = "idle_recheck"
                else:
                    state.recheck_since = None
                    state.reason = "idle_pending"

    def annotate(self, detections, t, tracks):
        for detection in detections:
            track = tracks[detection["track_id"]]
            state = self.states.get(track.id)
            detection["activity"] = "unknown"
            detection["activity_basis"] = (
                state.reason if state else "insufficient_video"
            )
            if detection.get("observed") is False:
                if (
                    state
                    and track.missed_frames <= 2
                    and (
                        (state.working_until is not None and t <= state.working_until)
                        or (
                            state.mechanism_until is not None
                            and t <= state.mechanism_until
                        )
                    )
                ):
                    detection["activity"] = "working"
                    detection["activity_basis"] = "recent_motion"
                else:
                    detection["activity_basis"] = (
                        "visual_track_grace"
                        if state and state.visual_grace
                        else "track_grace"
                    )
            elif track.motion == "moving_observed":
                detection["activity"] = "working"
                detection["activity_basis"] = "vehicle_motion"
                if state:
                    state.working_until = t + self.working_hold_seconds
            elif state and state.mechanism_until and t <= state.mechanism_until:
                detection["activity"] = "working"
                detection["activity_basis"] = "visible_mechanism_motion"
                state.working_until = t + self.working_hold_seconds
            elif state and state.working_until is not None and t <= state.working_until:
                detection["activity"] = "working"
                detection["activity_basis"] = "recent_motion"
            elif state and state.quiet_since is not None:
                group = self.equipment_activity.get(str(track.class_id), "mobile")
                eligible = group == "mobile" or track.class_id in VISUALLY_CHECKABLE
                if not eligible:
                    detection["activity_basis"] = "activity_not_visually_observable"
                if (
                    eligible
                    and state.last_roi_t is not None
                    and t - state.last_roi_t
                    <= max(0.6, min(self.sample_seconds, 2.0) * 1.75)
                    and state.last_roi_t - state.quiet_since - state.unseen_seconds
                    >= self._idle_threshold(track)
                    and state.recheck_since is None
                ):
                    detection["activity"] = "idle"
                    detection["activity_basis"] = "visible_inactivity"
        return detections

    def invalidate(self):
        for state in self.states.values():
            state.reset_visibility()
        self.last_frame_t = None
        self.previous_scene = None

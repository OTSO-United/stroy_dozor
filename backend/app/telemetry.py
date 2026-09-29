"""Display sampling never sums equipment counts or infers actions from motion."""

from .db import utc
from .rules import inside


def display_activity(by_class):
    if not by_class:
        return None
    result = {}
    for state in ("working", "idle"):
        values = [item[state] for item in by_class.values()]
        result[state] = (
            sum(value or 0 for value in values)
            if all(value is not None for value in values) or any(values)
            else None
        )
    return result if any(value is not None for value in result.values()) else None


def series_points(
    rows,
    class_ids,
    display_seconds,
    max_gap,
    expected_step=0,
    absence_confirm_seconds=30,
    work_id=None,
    regions_by_run=None,
    count_change_confirm_seconds=10,
    steps_by_run=None,
    display_from=None,
):
    raw_points, previous, segment = [], None, 0
    display_samples = {}
    states = {}
    for o in rows:
        step = (steps_by_run or {}).get(o.run_id, expected_step)
        allowed_gap = max(max_gap, step * 1.5)
        clock = utc(o.captured_at).timestamp() if o.captured_at else o.offset_seconds
        usable = o.quality.get("usable", False)
        supported = {str(c) for c in o.model.get("supported_classes", [])}
        if previous:
            prior, prior_clock = previous
            if (
                o.run_id != prior.run_id
                or getattr(o, "source_id", None) != getattr(prior, "source_id", None)
                or bool(o.captured_at) != bool(prior.captured_at)
                or clock - prior_clock > allowed_gap
                or clock <= prior_clock
                or not usable
                or not prior.quality.get("usable", False)
                or supported
                != {str(c) for c in prior.model.get("supported_classes", [])}
            ):
                segment += 1
                states = {}
        detections = o.detections
        stage_scope_available = True
        if work_id is not None:
            polygons = [
                region["polygon"]
                for region in (regions_by_run or {}).get(o.run_id, [])
                if work_id in region.get("work_ids", [])
            ]
            stage_scope_available = bool(polygons)
            detections = [
                detection
                for detection in detections
                if any(
                    inside(
                        (
                            (detection["bbox"][0] + detection["bbox"][2]) / 2,
                            detection["bbox"][3],
                        ),
                        polygon,
                    )
                    for polygon in polygons
                )
            ]
        raw_counts = {
            str(c): sum(str(d["class_id"]) == str(c) for d in detections)
            if usable and stage_scope_available and str(c) in supported
            else None
            for c in class_ids
        }
        counts, presence = {}, {}
        for class_id, raw_count in raw_counts.items():
            state = states.get(class_id)
            if raw_count is None:
                states.pop(class_id, None)
                counts[class_id] = None
                presence[class_id] = "unknown"
                continue
            if state is None:
                state = {
                    "stable_count": None,
                    "drop_since": clock if raw_count == 0 else None,
                    "candidate": None,
                    "candidate_since": None,
                    "candidate_direction": None,
                }
                states[class_id] = state
            stable_count = state["stable_count"]
            if stable_count is None:
                if raw_count > 0 or absence_confirm_seconds <= 0:
                    state["stable_count"] = raw_count
                    state["drop_since"] = None
                    counts[class_id] = raw_count
                    presence[class_id] = "present" if raw_count > 0 else "absent"
                else:
                    if state["drop_since"] is None:
                        state["drop_since"] = clock
                    elapsed = clock - state["drop_since"]
                    if elapsed >= absence_confirm_seconds:
                        state["stable_count"] = 0
                        state["drop_since"] = None
                        counts[class_id] = 0
                        presence[class_id] = "absent"
                    else:
                        counts[class_id] = None
                        presence[class_id] = "pending"
            elif raw_count == stable_count:
                state["drop_since"] = None
                state["candidate"] = None
                state["candidate_since"] = None
                state["candidate_direction"] = None
                counts[class_id] = stable_count
                presence[class_id] = "present" if stable_count > 0 else "absent"
            else:
                # Complete absence has its own clock; a partial deficit must not
                # borrow the time spent at zero, or delay a continuous zero.
                if raw_count == 0:
                    if state["drop_since"] is None:
                        state["drop_since"] = clock
                    state["candidate"] = None
                    state["candidate_direction"] = None
                    if clock - state["drop_since"] >= absence_confirm_seconds:
                        state["stable_count"] = 0
                        counts[class_id] = 0
                        presence[class_id] = "absent"
                    else:
                        counts[class_id] = stable_count
                        presence[class_id] = "pending"
                    continue
                state["drop_since"] = None
                direction = "up" if raw_count > stable_count else "down"
                if state["candidate_direction"] != direction:
                    state["candidate"] = raw_count
                    state["candidate_since"] = clock
                    state["candidate_direction"] = direction
                elif direction == "up":
                    state["candidate"] = min(state["candidate"], raw_count)
                else:
                    state["candidate"] = max(state["candidate"], raw_count)
                candidate = state["candidate"]
                threshold = count_change_confirm_seconds
                if clock - state["candidate_since"] >= threshold:
                    state["stable_count"] = candidate
                    state["candidate"] = None
                    state["candidate_since"] = None
                    state["candidate_direction"] = None
                    counts[class_id] = candidate
                    presence[class_id] = "present" if candidate > 0 else "absent"
                else:
                    counts[class_id] = stable_count
                    presence[class_id] = "pending"
        activities = (
            {"working": 0, "idle": 0, "unknown": 0}
            if usable and any(v is not None for v in counts.values())
            else None
        )
        if activities is not None:
            for detection in detections:
                if (
                    str(detection["class_id"]) not in counts
                    or counts[str(detection["class_id"])] is None
                ):
                    continue
                activity = detection.get("activity", "unknown")
                # Only explicit classified states, never the centroid track's motion.
                activities[
                    activity if activity in ("working", "idle") else "unknown"
                ] += 1
            for class_id, count in counts.items():
                raw_count = raw_counts[class_id]
                if presence[class_id] == "pending" and count is not None:
                    activities["unknown"] += max(0, count - (raw_count or 0))
        activity_by_class = {}
        continued_working_by_class = {}
        if activities is not None:
            for class_id, count in counts.items():
                if count is None:
                    continue
                idle = sum(
                    str(d["class_id"]) == class_id and d.get("activity") == "idle"
                    for d in detections
                )
                working = sum(
                    str(d["class_id"]) == class_id and d.get("activity") == "working"
                    for d in detections
                )
                unknown = max(0, max(count, raw_counts[class_id]) - idle - working)
                activity_by_class[class_id] = {
                    "working": working if working or not unknown else None,
                    "idle": idle if idle or not unknown else None,
                }
                continued_working_by_class[class_id] = sum(
                    str(d["class_id"]) == class_id
                    and d.get("activity") == "working"
                    and d.get("activity_basis") == "recent_motion"
                    for d in detections
                )
        point = {
            "id": o.id,
            "time": o.captured_at,
            "offset_seconds": o.offset_seconds,
            "quality": o.quality,
            "raw_counts": raw_counts,
            "counts": counts,
            "presence": presence,
            "activity": activities,
            "display_activity_by_class": activity_by_class,
            "continued_working_by_class": continued_working_by_class,
            "display_activity": display_activity(activity_by_class),
            "run_id": o.run_id,
            "segment": segment,
            "demo": bool(o.model.get("demo")),
            "sample_count": 1,
            "sample_seconds": step,
        }
        raw_points.append(point)
        previous = (o, clock)

    def point_clock(point):
        return (
            utc(point["time"]).timestamp() if point["time"] else point["offset_seconds"]
        )

    if display_from:
        raw_points = [
            point
            for point in raw_points
            if point["time"] and utc(point["time"]) >= display_from
        ]
    result, bucket_key = [], None
    for index, point in enumerate(raw_points):
        following = raw_points[index + 1] if index + 1 < len(raw_points) else None
        seconds = 0
        if (
            following
            and following["run_id"] == point["run_id"]
            and following["segment"] == point["segment"]
            and point["sample_seconds"] <= 2
        ):
            seconds = max(
                0,
                min(
                    point_clock(following) - point_clock(point),
                    point["sample_seconds"] or 1,
                ),
            )
        point["activity_seconds_by_class"] = {
            class_id: {
                state: value * seconds if value is not None and seconds else None
                for state, value in counts.items()
            }
            for class_id, counts in point["display_activity_by_class"].items()
        }
        point["presence_seconds_by_class"] = {
            class_id: {
                state: seconds if state == presence else 0
                for state in ("present", "absent", "pending", "unknown")
            }
            for class_id, presence in point["presence"].items()
        }
        key = (
            (
                point["run_id"],
                point["segment"],
                int(point_clock(point) // display_seconds),
            )
            if display_seconds
            else (point["id"],)
        )
        if display_seconds in (30, 60):
            display_samples.setdefault(key, []).append(point)
        if key == bucket_key:
            point["sample_count"] += result[-1]["sample_count"]
            for field in ("activity_seconds_by_class", "presence_seconds_by_class"):
                for class_id, counts in result[-1][field].items():
                    current = point[field].setdefault(class_id, {})
                    for state, value in counts.items():
                        if value is not None:
                            current[state] = (current.get(state) or 0) + value
            result[-1] = point
        else:
            result.append(point)
        bucket_key = key
    if display_seconds in (30, 60):
        for point in result:
            key = (
                point["run_id"],
                point["segment"],
                int(
                    (
                        utc(point["time"]).timestamp()
                        if point["time"]
                        else point["offset_seconds"]
                    )
                    // display_seconds
                ),
            )
            samples = display_samples.get(key, [])

            def sample_span(index, sample):
                current_clock = (
                    utc(sample["time"]).timestamp()
                    if sample["time"]
                    else sample["offset_seconds"]
                )
                next_clock = (
                    (
                        utc(samples[index + 1]["time"]).timestamp()
                        if samples[index + 1]["time"]
                        else samples[index + 1]["offset_seconds"]
                    )
                    if index + 1 < len(samples)
                    else current_clock + (sample["sample_seconds"] or 1)
                )
                return max(
                    0.0,
                    min(next_clock - current_clock, sample["sample_seconds"] or 1, 2.0),
                )

            for class_id in point["counts"]:

                def minute_count(sample):
                    return sample["counts"][class_id]

                measured = [minute_count(sample) for sample in samples]
                eligible = []
                for level in {value for value in measured if value is not None}:
                    confirmed_seconds = sum(
                        sample_span(index, sample)
                        for index, sample in enumerate(samples)
                        if measured[index] is not None and measured[index] >= level
                    )
                    if confirmed_seconds >= 10:
                        eligible.append(level)
                # Keep the latest stable level. A minute maximum can conceal
                # a confirmed decrease or restore a rejected raw-count spike.
                value = measured[-1]
                point["counts"][class_id] = value
                confirmed = value in eligible
                point.setdefault("display_basis", {})[class_id] = (
                    "confirmed"
                    if confirmed
                    else "sampled"
                    if value is not None
                    else "unknown"
                )
                point["presence"][class_id] = (
                    "present"
                    if confirmed and value > 0
                    else "absent"
                    if confirmed
                    else "unknown"
                )
            by_class = {}
            for class_id, count in point["counts"].items():
                if count is None:
                    continue
                # Activity is already classified by temporal_cv. Do not add a
                # second minute-local threshold or turn unknown into zero.
                by_class[class_id] = dict(
                    samples[-1]["display_activity_by_class"][class_id]
                )
            point["display_activity_by_class"] = by_class
            point["continued_working_by_class"] = dict(
                samples[-1]["continued_working_by_class"]
            )
            point["display_activity"] = display_activity(by_class)
    return result

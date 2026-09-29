import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper
from sqlalchemy import select, func

from app import api as app_api
from app import detector_api, detector_worker, models as m, vision, worker
from app.jobs import claim, fenced, LeaseLost
from app.db import now


@pytest.fixture
def lab(runtime, monkeypatch):
    factory, folder = runtime
    graph = helper.make_graph(
        [
            helper.make_node(
                "Constant",
                [],
                ["out"],
                value=helper.make_tensor(
                    "value", TensorProto.FLOAT, [1, 1, 6], [8, 8, 48, 48, 0.9, 0]
                ),
            )
        ],
        "TEST_ONLY",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 64, 64])],
        [helper.make_tensor_value_info("out", TensorProto.FLOAT, [1, 1, 6])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 10
    weights = folder / "test.onnx"
    onnx.save(model, weights)
    manifest = folder / "manifest.json"
    manifest.write_text(
        json.dumps(
            dict(
                name="TEST_ONLY",
                weights=weights.name,
                sha256=hashlib.sha256(weights.read_bytes()).hexdigest(),
                input_size=64,
                format="xyxy6",
                class_map={"0": "0"},
            )
        )
    )
    adapter = vision.OnnxDetector(str(manifest), providers=["CPUExecutionProvider"])
    monkeypatch.setattr(worker, "detector", lambda: adapter)
    monkeypatch.setattr(
        app_api, "model_status", lambda: {"ready": True, **adapter.info}
    )
    monkeypatch.setattr(
        detector_api, "model_status", lambda: {"ready": True, **adapter.info}
    )
    video = folder / "clip.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 5, (64, 64))
    assert writer.isOpened()
    rng = np.random.default_rng(31)
    frame = rng.integers(40, 220, (64, 64, 3), dtype=np.uint8)
    for _ in range(16):
        writer.write(frame)
    writer.release()
    ok, png = cv2.imencode(".png", frame)
    assert ok
    return factory, video.read_bytes(), png.tobytes()


def upload(client, content, *, name="clip.avi", key="upload", **options):
    return client.post(
        "/api/v1/detector/runs",
        files={"file": (name, content)},
        data=options,
        headers={"Idempotency-Key": key},
    )


def upload_batch(client, files, *, key="batch", **options):
    return client.post(
        "/api/v1/detector/runs",
        files=[("file", (name, content)) for name, content in files],
        data={"mode": "batch", **options},
        headers={"Idempotency-Key": key},
    )


def test_tiled_inference_preserves_resolution_and_merges_overlap():
    callbacks = []

    class WideFrameDetector:
        info = {
            "input_size": 960,
            "inference_defaults": {
                "confidence": 0.25,
                "iou": 0.5,
                "max_detections": 300,
            },
        }

        def detect(self, tile, _parameters):
            callbacks.append(tile.shape[:2])
            return []

    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    assert (
        vision.detect_tiled(
            WideFrameDetector(),
            frame,
            tiling={"enabled": True, "overlap": 0.2},
        )
        == []
    )
    assert callbacks == [(960, 960)] * 6

    class DuplicateDetector:
        info = {
            "input_size": 100,
            "inference_defaults": {
                "confidence": 0.25,
                "iou": 0.5,
                "max_detections": 300,
            },
        }

        def __init__(self):
            self.calls = 0

        def detect(self, _tile, _parameters):
            self.calls += 1
            bbox = [0.8, 0.2, 0.95, 0.5] if self.calls == 1 else [0, 0.2, 0.15, 0.5]
            return [{"class_id": "1", "confidence": 1 - self.calls / 10, "bbox": bbox}]

    merged = vision.detect_tiled(
        DuplicateDetector(),
        np.zeros((100, 180, 3), dtype=np.uint8),
        tiling={"enabled": True, "overlap": 0.2},
    )
    assert len(merged) == 1
    assert merged[0]["bbox"] == pytest.approx([80 / 180, 0.2, 95 / 180, 0.5])


def test_tiled_inference_merges_clipped_seam_boxes_with_low_iou():
    class SeamDetector:
        info = {
            "input_size": 100,
            "inference_defaults": {
                "confidence": 0.25,
                "iou": 0.5,
                "max_detections": 300,
            },
        }

        def __init__(self):
            self.calls = 0

        def detect(self, _tile, _parameters):
            self.calls += 1
            box = [0.65, 0.2, 1, 0.5] if self.calls == 1 else [0, 0.2, 0.5, 0.5]
            return [{"class_id": "1", "confidence": 0.9, "bbox": box}]

    merged = vision.detect_tiled(
        SeamDetector(),
        np.zeros((100, 180, 3), dtype=np.uint8),
        tiling={"enabled": True, "overlap": 0.2},
    )
    assert len(merged) == 1
    assert merged[0]["bbox"] == pytest.approx([80 / 180, 0.2, 130 / 180, 0.5])


def test_tiled_inference_rejects_unbounded_tile_count():
    class TinyTileDetector:
        info = {
            "input_size": 10,
            "inference_defaults": {
                "confidence": 0.25,
                "iou": 0.5,
                "max_detections": 300,
            },
        }

        def detect(self, _tile, _parameters):
            return []

    with pytest.raises(ValueError, match="максимум 100"):
        vision.detect_tiled(
            TinyTileDetector(),
            np.zeros((100, 100, 3), dtype=np.uint8),
            tiling={"enabled": True, "overlap": 0.2},
        )


@pytest.mark.parametrize(
    "mode,options,offsets",
    [
        ("full", {}, [0, 1, 2, 3]),
        ("segment", {"start_seconds": 0.55, "end_seconds": 2.15}, [0.6, 1.6]),
        ("frame", {"start_seconds": 1.35}, [1.4]),
        ("image", {}, [0]),
    ],
)
def test_modes_real_onnx_with_isolated_results(client, lab, mode, options, offsets):
    factory, video, png = lab
    response = upload(
        client,
        png if mode == "image" else video,
        name="image.png" if mode == "image" else "clip.avi",
        mode=mode,
        **options,
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    with factory() as db:
        task = claim(db, m.DetectorRun)
    detector_worker.process(task)
    result = client.get(f"/api/v1/detector/runs/{run_id}").json()
    assert result["status"] == "succeeded", result
    assert result["processed_frames"] == len(offsets)
    assert result["model"]["providers"] == ["CPUExecutionProvider"]
    assert not {"file_key", "token", "lease_until"} & result.keys()
    items = client.get(f"/api/v1/detector/runs/{run_id}/frames").json()["frames"]
    assert [f["offset_seconds"] for f in items] == pytest.approx(offsets)
    assert all(f["detections"][0]["class_id"] == "0" for f in items)
    assert all(
        f["detections"][0]["bbox"] == pytest.approx([0.125, 0.125, 0.75, 0.75])
        for f in items
    )
    assert client.get(items[0]["image_url"]).headers["content-type"] == "image/jpeg"
    assert client.get(result["content_url"]).status_code == 200
    with factory() as db:
        for model in (m.Project, m.Source, m.Job, m.Observation, m.Assessment, m.Alert):
            assert db.scalar(select(func.count()).select_from(model)) == 0
    page = client.get(f"/api/v1/detector/runs/{run_id}/frames?limit=1").json()
    assert len(page["frames"]) == 1
    assert page["next_offset"] == (1 if len(offsets) > 1 else None)


def test_batch_is_one_run_with_selectable_frames_and_custom_parameters(client, lab):
    factory, _, png = lab
    response = upload_batch(
        client,
        [("first.png", png), ("second.png", png), ("third.png", png)],
        confidence=0.95,
        iou=0.4,
        max_detections=12,
        tiled="true",
        tile_overlap=0.2,
    )
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["kind"] == "batch" and run["mode"] == "batch"
    assert run["metadata_json"]["frame_count"] == 3
    assert run["inference_parameters"] == {
        "confidence": 0.95,
        "iou": 0.4,
        "max_detections": 12,
    }
    assert run["tiling"] == {"enabled": True, "overlap": 0.2}
    with factory() as db:
        task = claim(db, m.DetectorRun)
    detector_worker.process(task)
    completed = client.get(f"/api/v1/detector/runs/{run['id']}").json()
    assert completed["status"] == "succeeded"
    assert completed["model"]["tiling"] == {
        "enabled": True,
        "overlap": 0.2,
        "tile_size": 64,
    }
    assert completed["processed_frames"] == 3
    assert completed["preview_url"]
    frames = client.get(f"/api/v1/detector/runs/{run['id']}/frames").json()["frames"]
    assert [frame["source_name"] for frame in frames] == [
        "first.png",
        "second.png",
        "third.png",
    ]
    assert all(frame["detections"] == [] for frame in frames)
    assert client.get(completed["preview_url"]).status_code == 200
    content = client.get(completed["content_url"])
    assert content.status_code == 200
    assert content.headers["content-type"] == "application/zip"


def test_batch_rejects_video_and_model_settings_are_revisioned(client, lab):
    factory, video, png = lab
    invalid = upload_batch(client, [("frame.png", png), ("clip.avi", video)])
    assert invalid.status_code == 422

    settings = client.get("/api/v1/detector/settings").json()
    assert settings["product_mode_name"] == "Фоновый мониторинг объектов"
    assert settings["defaults"] == {
        "confidence": 0.25,
        "iou": 0.5,
        "max_detections": 300,
    }
    body = {
        "confidence": 0.4,
        "iou": 0.65,
        "max_detections": 80,
        "expected_revision": 0,
    }
    headers = {"Idempotency-Key": "product-settings"}
    changed = client.put("/api/v1/detector/settings", json=body, headers=headers).json()
    assert changed["product"]["revision"] == 1
    assert changed["product"]["parameters"] == {
        "confidence": 0.4,
        "iou": 0.65,
        "max_detections": 80,
    }
    assert (
        client.put("/api/v1/detector/settings", json=body, headers=headers).json()
        == changed
    )
    assert (
        client.put(
            "/api/v1/detector/settings",
            json={**body, "confidence": 0.5},
            headers=headers,
        ).status_code
        == 409
    )
    with factory() as db:
        project = m.Project(
            name="Settings project",
            address="",
            timezone="Europe/Moscow",
            class_ids=[0],
            settings={},
        )
        db.add(project)
        db.flush()
        source = m.Source(
            project_id=project.id,
            name="Frame",
            kind="image",
            file_key="frame.png",
        )
        db.add(source)
        db.flush()
        payload = app_api.analysis_payload(db, project, source)
    assert payload["inference_parameters"] == changed["product"]["parameters"]


def test_product_settings_reset_to_defaults_when_model_changes(
    client, lab, monkeypatch
):
    current = client.get("/api/v1/detector/settings").json()
    changed = client.put(
        "/api/v1/detector/settings",
        json={
            "confidence": 0.4,
            "iou": 0.65,
            "max_detections": 80,
            "expected_revision": current["product"]["revision"],
        },
        headers={"Idempotency-Key": "settings-before-model-change"},
    ).json()
    next_status = {
        "ready": True,
        "name": "yolo26m-equipment-v3",
        "sha256": "1" * 64,
        "inference_defaults": {
            "confidence": 0.35,
            "iou": 0.7,
            "max_detections": 300,
        },
    }
    monkeypatch.setattr(detector_api, "model_status", lambda: next_status)
    monkeypatch.setattr(app_api, "model_status", lambda: next_status)

    reset = client.get("/api/v1/detector/settings").json()
    assert reset["defaults"] == next_status["inference_defaults"]
    assert reset["product"]["parameters"] == next_status["inference_defaults"]
    assert reset["product"]["revision"] == changed["product"]["revision"] + 1
    stale = client.put(
        "/api/v1/detector/settings",
        json={
            "confidence": 0.45,
            "iou": 0.65,
            "max_detections": 80,
            "expected_revision": changed["product"]["revision"],
        },
        headers={"Idempotency-Key": "stale-settings-after-model-change"},
    )
    assert stale.status_code == 409

    factory = lab[0]
    with factory() as db:
        project = m.Project(
            name="New model settings project",
            address="",
            timezone="Europe/Moscow",
            class_ids=[0],
            settings={},
        )
        db.add(project)
        db.flush()
        source = m.Source(
            project_id=project.id,
            name="Frame",
            kind="image",
            file_key="frame.png",
        )
        db.add(source)
        db.flush()
        payload = app_api.analysis_payload(db, project, source)
    assert payload["inference_parameters"] == next_status["inference_defaults"]


def test_idempotency_cancel_and_fencing(client, lab):
    factory, video, _ = lab
    first = upload(client, video, mode="full").json()
    assert upload(client, video, mode="full").json()["id"] == first["id"]
    assert upload(client, video, mode="frame").status_code == 409
    with factory() as db:
        task = claim(db, m.DetectorRun)
    response = client.post(
        f"/api/v1/detector/runs/{first['id']}/cancel",
        headers={"Idempotency-Key": "cancel"},
    )
    assert response.json()["status"] == "cancelled"
    with factory() as db:
        with pytest.raises(LeaseLost):
            fenced(db, task.id, task.token, job_model=m.DetectorRun, status="succeeded")
        db.rollback()
        assert len(list(db.scalars(select(m.DetectorRun)))) == 1
    detector_worker.process(task)
    assert (
        client.get(f"/api/v1/detector/runs/{first['id']}/frames").json()["frames"] == []
    )


def test_expired_run_resumes_without_duplicate_frames(client, lab, monkeypatch):
    factory, video, _ = lab
    result = upload(client, video, mode="full").json()
    adapter = worker.detector()
    detect = adapter.detect
    calls = 0
    with factory() as db:
        first = claim(db, m.DetectorRun)

    def interrupted(frame, parameters=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            with factory() as db:
                db.get(m.DetectorRun, first.id).lease_until = now() - timedelta(
                    seconds=1
                )
                db.commit()
            raise LeaseLost("Test worker interruption")
        return detect(frame, parameters)

    monkeypatch.setattr(adapter, "detect", interrupted)
    detector_worker.process(first)
    old_frames = client.get(f"/api/v1/detector/runs/{first.id}/frames").json()["frames"]
    assert len(old_frames) == 1
    with factory() as db:
        resumed = claim(db, m.DetectorRun)
    assert resumed.token != first.token and resumed.attempts == 2
    with factory() as db:
        with pytest.raises(LeaseLost):
            fenced(db, first.id, first.token, job_model=m.DetectorRun)
    monkeypatch.setattr(adapter, "detect", detect)
    detector_worker.process(resumed)
    data = client.get(f"/api/v1/detector/runs/{result['id']}").json()
    assert data["status"] == "succeeded" and data["processed_frames"] == 4
    frames = client.get(f"/api/v1/detector/runs/{first.id}/frames").json()["frames"]
    assert [f["offset_seconds"] for f in frames] == [0, 1, 2, 3]
    assert frames[0]["id"] == old_frames[0]["id"]


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "segment", "start_seconds": 2, "end_seconds": 1},
        {"mode": "segment"},
        {"mode": "full", "end_seconds": 2},
        {"mode": "image"},
        {"mode": "full", "sample_seconds": 0.1},
        {"mode": "frame", "start_seconds": "nan"},
        {"mode": "image", "tiled": "true", "tile_overlap": 0},
    ],
)
def test_reject_invalid_request(client, lab, options):
    assert upload(client, lab[1], **options).status_code == 422


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "frame", "start_seconds": 4},
        {"mode": "segment", "start_seconds": 1, "end_seconds": 4},
    ],
)
def test_server_checks_video_bounds(client, lab, options):
    response = upload(client, lab[1], **options)
    assert response.status_code == 202
    with lab[0]() as db:
        task = claim(db, m.DetectorRun)
    detector_worker.process(task)
    result = client.get(f"/api/v1/detector/runs/{task.id}").json()
    assert result["status"] == "failed" and "пределами" in result["error"]
    assert result["processed_frames"] == 0


def test_missing_changed_model_and_corrupt_file(client, lab, monkeypatch):
    monkeypatch.setattr(detector_api, "model_status", lambda: {"ready": False})
    assert upload(client, lab[2], name="x.png", mode="image").status_code == 409
    monkeypatch.setattr(
        detector_api, "model_status", lambda: {"ready": True, "sha256": "0" * 64}
    )
    response = upload(client, lab[2], name="x.png", mode="image")
    with lab[0]() as db:
        task = claim(db, m.DetectorRun)
    detector_worker.process(task)
    assert (
        "Модель изменилась"
        in client.get(f"/api/v1/detector/runs/{task.id}").json()["error"]
    )
    monkeypatch.setattr(
        detector_api, "model_status", lambda: {"ready": True, **worker.detector().info}
    )
    bad = upload(client, b"corrupt", name="bad.png", key="bad", mode="image")
    with lab[0]() as db:
        task = claim(db, m.DetectorRun)
    detector_worker.process(task)
    assert (
        client.get(f"/api/v1/detector/runs/{bad.json()['id']}").json()["status"]
        == "failed"
    )
    assert response.status_code == 202


def test_packaged_model_in_detector_lab(client, runtime, monkeypatch):
    filename = os.getenv("STROY_TEST_IMAGE")
    if not filename:
        pytest.skip("Set STROY_TEST_IMAGE for real packaged inference")
    model = vision.OnnxDetector(providers=["CPUExecutionProvider"])
    monkeypatch.setattr(worker, "detector", lambda: model)
    monkeypatch.setattr(
        detector_api, "model_status", lambda: {"ready": True, **model.info}
    )
    result = upload(
        client,
        Path(filename).read_bytes(),
        name="local.png",
        mode="image",
        tiled="true",
        tile_overlap=0.2,
    )
    assert result.status_code == 202
    with runtime[0]() as db:
        task = claim(db, m.DetectorRun)
    detector_worker.process(task)
    data = client.get(f"/api/v1/detector/runs/{task.id}").json()
    assert data["status"] == "succeeded", data
    assert data["model"]["sha256"] == model.info["sha256"]
    assert data["model"]["tiling"] == {
        "enabled": True,
        "overlap": 0.2,
        "tile_size": model.info["input_size"],
    }

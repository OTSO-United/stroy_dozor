"""Generated test-only model verifies plumbing, never detector accuracy."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app import api, models as m, monitor, vision, worker
from app.jobs import claim
from test_app import approved, work


def test_real_cpu_onnx_video_assessment_and_export(
    client, project, runtime, monkeypatch
):
    factory, folder = runtime
    # One artificial box of class 0 per frame, used only in isolated fixtures.
    output = helper.make_tensor(
        "constant", TensorProto.FLOAT, [1, 1, 6], [8, 8, 48, 48, 0.9, 0]
    )
    graph = helper.make_graph(
        [helper.make_node("Constant", [], ["detections"], value=output)],
        "TEST_ONLY",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 64, 64])],
        [helper.make_tensor_value_info("detections", TensorProto.FLOAT, [1, 1, 6])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 10
    weights = folder / "test-only.onnx"
    onnx.save(model, weights)
    manifest = folder / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "TEST_ONLY",
                "weights": weights.name,
                "sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
                "input_size": 64,
                "format": "xyxy6",
                "class_map": {"0": "0", "1": "1", "2": "16"},
            }
        )
    )
    adapter = vision.OnnxDetector(str(manifest))
    assert adapter.info["providers"] == ["CPUExecutionProvider"]
    monkeypatch.setattr(worker, "detector", lambda: adapter)
    monkeypatch.setattr(
        api, "model_status", lambda: {"ready": True, "sha256": adapter.info["sha256"]}
    )
    video = folder / "test-only.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 5, (64, 64))
    assert writer.isOpened()
    rng = np.random.default_rng(12)
    for _ in range(16):
        writer.write(rng.integers(40, 220, (64, 64, 3), dtype=np.uint8))
    writer.release()
    stage = work("12.1", resources={"0": 2, "1": 0, "16": 0})
    plan = approved(client, project, [stage])
    response = client.post(
        f"/api/v1/projects/{project['id']}/media",
        files={"file": ("test.avi", video.read_bytes())},
        data={
            "capture_start": stage["starts_at"],
            "demo_loop_enabled": "true",
            "demo_loop_duration_seconds": "7",
        },
        headers={"Idempotency-Key": "video"},
    )
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]
    with factory() as db:
        job = claim(db)
    worker.process(job)
    with factory() as db:
        source = db.get(m.Source, source_id)
        persisted_project = db.get(m.Project, project["id"])
        persisted_project.settings = {
            **persisted_project.settings,
            "absence_confirm_seconds": 0,
        }
        assert source.metadata_json["demo_loop"] == {
            "enabled": True,
            "duration_seconds": 7.0,
        }
        assert 3 <= source.metadata_json["duration_seconds"] < 4
        actual_media_duration = source.metadata_json["duration_seconds"]
        # Container duration can be inaccurate for VFR/broken metadata. The loop
        # clock must follow decoded PTS instead of leaving a graph-sized hole.
        source.metadata_json = {
            **source.metadata_json,
            "duration_seconds": actual_media_duration + 60,
        }
        db.commit()
    response = client.post(
        f"/api/v1/sources/{source_id}/bindings",
        json={
            "regions": [
                {
                    "name": "Test zone",
                    "work_ids": [stage["id"]],
                    "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                    "visibility_confirmed": True,
                }
            ]
        },
        headers={"Idempotency-Key": "zones"},
    )
    assert response.status_code == 200
    # The prepared and bound video starts automatically.
    queued = client.get(f"/api/v1/projects/{project['id']}/jobs").json()
    assert any(
        job["source_id"] == source_id and job["kind"] == "analyze" for job in queued
    )
    with factory() as db:
        job = claim(db)
        assert job.payload["demo_loop"] == {
            "enabled": True,
            "duration_seconds": 7.0,
            "requested_duration_seconds": 7.0,
            "trimmed_to_stage": False,
        }
    worker.process(job)
    with factory() as db:
        assert db.get(m.Job, job.id).status == "succeeded"
        observations = list(
            db.scalars(select(m.Observation).order_by(m.Observation.sample_index))
        )
        assert len(observations) == 9
        assert observations[-1].offset_seconds < 7
        assert observations[-1].offset_seconds > actual_media_duration
        assert (
            max(
                current.offset_seconds - previous.offset_seconds
                for previous, current in zip(observations, observations[1:])
            )
            <= 1.01
        )
        assert {o.quality["demo_loop_cycle"] for o in observations} == {0, 1, 2}
        assert all(o.quality["demo_loop"] for o in observations)
        assert all(
            o.quality["time_basis"] == "demo_loop_start_plus_repeated_pts"
            for o in observations
        )
        assert all(len(o.detections) == 1 for o in observations)
        assert all(o.model["name"] == "TEST_ONLY" for o in observations)
        assert len({o.detections[0]["track_id"] for o in observations}) == 3
    assert monitor.tick() == len(observations)
    with factory() as db:
        alert = db.scalar(select(m.Alert))
        assert alert.kind == "missing" and 6 <= alert.duration_seconds < 7
        assert alert.details["plan_id"] == plan["id"]
        assert not any(
            "Тестовый демо-режим" in text for text in alert.details["limitations"]
        )
    assert client.get(f"/api/v1/evidence/{observations[0].id}").status_code == 200
    assert (
        client.get(f"/api/v1/projects/{project['id']}/reports/xlsx").status_code == 200
    )


def test_model_aliases_share_canonical_nms(tmp_path):
    raw = np.zeros((1, 24, 2), dtype=np.float32)
    raw[0, :4, :] = np.array([[32, 32], [32, 32], [24, 24], [24, 24]])
    raw[0, 4 + 7, 0] = 0.9
    raw[0, 4 + 14, 1] = 0.8
    output = helper.make_tensor(
        "constant", TensorProto.FLOAT, raw.shape, raw.reshape(-1).tolist()
    )
    graph = helper.make_graph(
        [helper.make_node("Constant", [], ["detections"], value=output)],
        "ALIASED_CLASSES",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 64, 64])],
        [helper.make_tensor_value_info("detections", TensorProto.FLOAT, [1, 24, 2])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 10
    weights = tmp_path / "aliases.onnx"
    onnx.save(model, weights)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "ALIASED_CLASSES",
                "weights": weights.name,
                "sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
                "input_size": 64,
                "format": "yolo_raw",
                "confidence": 0.25,
                "iou": 0.7,
                "class_map": {"7": "7", "14": "7"},
            }
        )
    )

    detections = vision.OnnxDetector(
        str(manifest), providers=["CPUExecutionProvider"]
    ).detect(np.zeros((64, 64, 3), dtype=np.uint8))

    assert len(detections) == 1
    assert detections[0]["class_id"] == "7"
    assert detections[0]["confidence"] == pytest.approx(0.9)


def test_migration_fresh_database_and_restart(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": str(root / "backend"),
        "STROY_DATA_DIR": str(tmp_path / "storage"),
        "DATABASE_URL": f"sqlite:///{tmp_path / 'migration.db'}",
    }
    command = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(root / "backend/alembic.ini"),
        "upgrade",
        "head",
    ]
    result = subprocess.run(
        command[:-1] + ["001"], cwd=root, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    engine = create_engine(env["DATABASE_URL"])
    with Session(engine) as db:
        project_id = "migration-project"
        db.execute(
            text(
                "INSERT INTO projects (id,name,address,timezone,class_ids,settings,revision,created_at) VALUES (:id,'Migration','','Europe/Moscow','[0]','{}',1,'2026-09-18 00:00:00')"
            ),
            {"id": project_id},
        )
        old = m.Plan(
            project_id=project_id,
            version=1,
            status="draft",
            works=[{"title": "Keep this work"}],
        )
        current = m.Plan(project_id=project_id, version=2, status="approved", works=[])
        db.add_all([old, current])
        db.flush()
        db.execute(
            text("UPDATE projects SET current_plan_id=:plan WHERE id=:id"),
            {"plan": current.id, "id": project_id},
        )
        db.commit()
        old_id, current_id = old.id, current.id
    for _ in range(2):
        result = subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
    with Session(engine) as db:
        assert db.get(m.Plan, old_id).status == "archived"
        assert db.get(m.Plan, old_id).works == [{"title": "Keep this work"}]
        assert db.get(m.Plan, old_id).approved_at is None
        assert db.get(m.Plan, current_id).status == "approved"
        assert db.get(m.Project, project_id).current_plan_id == current_id
        assert db.get(m.Project, project_id).project_type_id is None
        assert len(list(db.scalars(select(m.ProjectType)))) == 9
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


def test_period_validation_and_capture_time_edit(client, project, runtime):
    factory, _ = runtime
    with factory() as db:
        source = m.Source(project_id=project["id"], name="X", kind="video")
        db.add(source)
        db.commit()
        source_id = source.id
    result = client.patch(
        f"/api/v1/sources/{source_id}",
        json={"sample_seconds": 2, "capture_start": "2026-09-16T10:00:00+03:00"},
        headers={"Idempotency-Key": "capture"},
    )
    assert result.status_code == 200
    assert result.json()["capture_start"].startswith("2026-09-16T10:")
    url = f"/api/v1/projects/{project['id']}/reports/csv"
    assert client.get(url, params={"from": "2026-09-16T10:00:00"}).status_code == 422
    assert (
        client.get(
            url, params={"from": "2026-09-17T10:00:00Z", "to": "2026-09-16T10:00:00Z"}
        ).status_code
        == 422
    )

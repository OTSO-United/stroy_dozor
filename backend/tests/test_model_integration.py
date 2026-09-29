"""Integration of the remote model contract with APP-03, not an accuracy test."""

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app import api, models as m, monitor, vision, worker
from app.jobs import claim
from test_app import approved, post, work

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "models/yolo26m/manifest.json"


def test_packaged_model_classes_are_available_in_typed_project(client):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    catalog = client.get("/api/v1/catalog?project_type_id=roads").json()
    ids = {str(c["id"]) for c in catalog["classes"]}
    assert len(ids) == 28
    assert set(manifest["class_map"].values()) <= ids
    assert len(manifest["class_names"]) == 20
    assert set(manifest["class_map"].values()) == {
        *map(str, range(14)),
        *map(str, range(16, 21)),
        "28",
    }
    assert manifest["class_map"]["7"] == "7"
    assert manifest["class_map"]["14"] == "28"
    names = {str(item["id"]): item["name"] for item in catalog["classes"]}
    assert names["7"] == "Погрузчик большой"
    assert names["28"] == "Погрузчик маленький"
    assert names["13"] == "Автопоезд с тралом"
    project = post(
        client,
        "/projects",
        {
            "name": "Mapping test",
            "class_ids": [0, 17, 20, 26, 27],
            "project_type_id": "roads",
        },
    ).json()
    stage = work(resources={"0": 1, "17": 0, "20": 0, "26": 1, "27": 0})
    approved(client, project, [stage])
    summary = client.get(f"/api/v1/projects/{project['id']}").json()
    assert summary["project_type_id"] == "roads"
    assert summary["class_ids"] == [0, 17, 20, 26, 27]
    assert summary["current_plan_id"]


def test_real_packaged_model_worker_analytics_and_evidence(
    client, runtime, monkeypatch
):
    image_path = os.getenv("STROY_TEST_IMAGE")
    if not image_path:
        pytest.skip("Set STROY_TEST_IMAGE for read-only inference on a local image")
    image = Path(image_path)
    assert image.is_file(), "STROY_TEST_IMAGE must exist"
    detector = vision.OnnxDetector(str(MANIFEST), providers=["CPUExecutionProvider"])
    monkeypatch.setattr(worker, "detector", lambda: detector)
    monkeypatch.setattr(api, "model_status", lambda: {"ready": True, **detector.info})
    project = post(
        client,
        "/projects",
        {
            "name": "Real model compatibility",
            "class_ids": [0, 1, 14, 17, 20, 26, 27],
            "project_type_id": "housing",
        },
    ).json()
    # Large test-only requirements reliably exercise the signal gallery, not ML accuracy.
    stage = work(
        resources={
            "0": 10000,
            "1": 0,
            "14": 0,
            "17": 0,
            "20": 0,
            "26": 0,
            "27": 0,
        }
    )
    approved(client, project, [stage])
    response = client.post(
        f"/api/v1/projects/{project['id']}/media",
        files={"file": ("test.png", image.read_bytes(), "image/png")},
        data={"capture_start": stage["starts_at"]},
        headers={"Idempotency-Key": "real-model-image"},
    )
    assert response.status_code == 202, response.text
    source_id = response.json()["source"]["id"]
    factory, _ = runtime
    with factory() as db:
        persisted_project = db.get(m.Project, project["id"])
        persisted_project.settings = {
            **persisted_project.settings,
            "absence_confirm_seconds": 0,
        }
        db.commit()
        job = claim(db)
    worker.process(job)
    response = post(
        client,
        f"/sources/{source_id}/bindings",
        {
            "regions": [
                {
                    "name": "Full test frame",
                    "work_ids": [stage["id"]],
                    "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                    "visibility_confirmed": True,
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    response = post(client, f"/sources/{source_id}/analyze", {})
    # Saving the ready image's binding already queued this run.
    assert response.status_code == 409, response.text
    with factory() as db:
        job = claim(db)
        assert job.kind == "analyze" and job.payload["binding_id"]
    worker.process(job)
    assert monitor.tick() == 1
    with factory() as db:
        assert db.get(m.Job, job.id).status == "succeeded"
        observation = db.scalar(
            select(m.Observation).where(m.Observation.run_id == job.id)
        )
        assert observation.quality["usable"], "Choose a usable verification frame"
        assert observation.model["sha256"] == detector.info["sha256"]
        assert observation.model["providers"] == ["CPUExecutionProvider"]
        assert all(
            d["class_id"] in detector.info["supported_classes"]
            for d in observation.detections
        )
    points = client.get(
        f"/api/v1/projects/{project['id']}/timeseries?source_id={source_id}&display_seconds=60"
    ).json()["points"]
    assert len(points) == 1
    assert isinstance(points[0]["counts"]["17"], int)
    assert isinstance(points[0]["counts"]["20"], int)
    assert points[0]["counts"]["14"] is None
    assert points[0]["counts"]["26"] is None
    assert points[0]["counts"]["27"] is None
    assert points[0]["activity"]["working"] == 0 and points[0]["activity"]["idle"] == 0
    assert points[0]["activity"]["unknown"] == sum(
        value for value in points[0]["counts"].values() if value is not None
    )
    alerts = client.get(f"/api/v1/projects/{project['id']}/alerts").json()
    assert alerts
    for alert in alerts:
        assert isinstance(alert["details"]["counts"]["17"], int)
        assert isinstance(alert["details"]["counts"]["20"], int)
        assert alert["details"]["counts"]["14"] is None
        assert alert["details"]["counts"]["26"] is None
        assert alert["details"]["counts"]["27"] is None
        evidence = client.get(f"/api/v1/alerts/{alert['id']}/evidence").json()
        assert evidence["basis"]["id"] == observation.id
        assert len(evidence["frames"]) == 1
        assert client.get(evidence["basis"]["image_url"]).status_code == 200
    assert (
        client.get(f"/api/v1/projects/{project['id']}/reports/xlsx").status_code == 200
    )

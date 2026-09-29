from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app import models as m
from app.demo import DEMO_MODEL_NAME, create_demo_project


def test_seeded_demo_populates_ui_alert_audit_and_report_paths(client, runtime):
    factory, folder = runtime
    with factory() as db:
        result = create_demo_project(
            db,
            Path(folder),
            seed=42,
            reference_time=datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc),
        )
        project_id = result["project_id"]

    assert result == {
        "created": True,
        "project_id": project_id,
        "observations": 24,
        "assessments": 24,
        "alerts": 3,
        "audit_events": 12,
        "url": f"http://127.0.0.1:8000/?project={project_id}&tab=overview",
    }

    monitoring = client.get(f"/api/v1/projects/{project_id}/monitoring")
    assert monitoring.status_code == 200
    payload = monitoring.json()
    assert len(payload["cards"]) == 2
    assert all(card["observation"]["model"]["demo"] for card in payload["cards"])
    assert all(
        card["observation"]["model"]["name"] == DEMO_MODEL_NAME
        for card in payload["cards"]
    )
    alerts = payload["alerts"]
    assert {alert["status"] for alert in alerts} == {"open", "resolved"}
    assert any(
        alert["severity"] == "critical" and alert["kind"] == "missing"
        for alert in alerts
    )
    assert any(alert["kind"] == "excess" for alert in alerts)
    excess = next(alert for alert in alerts if alert["kind"] == "excess")
    excess_evidence = client.get(f"/api/v1/alerts/{excess['id']}/evidence")
    assert excess_evidence.status_code == 200
    assert excess_evidence.json()["missing_class_ids"] == []
    assert "deviation_class_ids" in excess_evidence.json()
    assert any(alert["reviewed"] for alert in alerts)

    audit = client.get(f"/api/v1/projects/{project_id}/audit").json()
    assert any(event["action"] == "demo.generated" for event in audit)
    assert any(event["action"] == "alert.reviewed" for event in audit)
    assert client.get(f"/api/v1/projects/{project_id}/reports/xlsx").status_code == 200
    csv = client.get(f"/api/v1/projects/{project_id}/reports/csv")
    assert csv.status_code == 200 and "Событие".encode() in csv.content
    assert (
        client.get(
            f"/api/v1/evidence/{alerts[0]['details']['evidence_id']}"
        ).status_code
        == 200
    )

    with factory() as db:
        again = create_demo_project(db, Path(folder), seed=42)
        assert again["created"] is False
        assert len(list(db.scalars(select(m.Project)))) == 1
        assert len(list(db.scalars(select(m.Observation)))) == 24

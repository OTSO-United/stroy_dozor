import csv
import io
from datetime import datetime

from sqlalchemy import select

from app import models as m
from app.planning_presets import PLANNING_CLASS_IDS
from test_app import approved, post, work


def test_start_is_noop_when_already_started_and_completed_cannot_restart(
    client, project, runtime
):
    stage = work()
    approved(client, project, [stage])
    url = f"/projects/{project['id']}/works/{stage['id']}/status"
    assert post(client, url, {"status": "in_progress"}).status_code == 200
    with runtime[0]() as db:
        stamp = db.scalar(select(m.WorkState)).updated_at
    assert (
        post(client, url, {"status": "in_progress"}).json()["status"] == "in_progress"
    )
    with runtime[0]() as db:
        assert db.scalar(select(m.WorkState)).updated_at == stamp
        events = list(
            db.scalars(select(m.Audit).where(m.Audit.action == "work.status_changed"))
        )
        assert len(events) == 1
    assert post(client, url, {"status": "completed"}).status_code == 200
    assert post(client, url, {"status": "in_progress"}).status_code == 409
    assert post(client, url, {"status": "planned"}).status_code == 200
    assert post(client, url, {"status": "in_progress"}).status_code == 200


def test_reorder_and_csv_roundtrip_preserve_identity_status_and_history(
    client, project
):
    a, b, c = (
        work("10.2"),
        work("10.2.1"),
        work("10.1", title='Переселение; корпус "А"\nВторая строка'),
    )
    first = approved(client, project, [a, b])
    post(
        client,
        f"/projects/{project['id']}/works/{a['id']}/status",
        {"status": "in_progress"},
    )
    second = post(
        client,
        f"/projects/{project['id']}/plans",
        {
            "works": [c, a, b],
            "base_plan_id": first["id"],
        },
    ).json()
    url = f"/api/v1/projects/{project['id']}/plans/{second['id']}/export"
    exported = client.get(url)
    assert exported.status_code == 200
    assert exported.content.startswith(b"\xef\xbb\xbf")
    assert "plan-v2.csv" in exported.headers["content-disposition"]
    rows = list(
        csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig")), delimiter=";")
    )
    assert [r["Код"] for r in rows] == ["10.1", "10.2", "10.2.1"]
    assert rows[0]["Этап стройки"] == c["title"]
    assert rows[0]["Автокран"] == "0"
    imported = client.post(
        f"/api/v1/projects/{project['id']}/plan-imports",
        files={"file": ("plan.csv", exported.content)},
        headers={"Idempotency-Key": "roundtrip"},
    )
    assert imported.status_code == 200, imported.text
    for actual, expected in zip(imported.json()["works"], [c, a, b]):
        assert (actual["id"], actual["code"], actual["title"], actual["resources"]) == (
            expected["id"],
            expected["code"],
            expected["title"],
            expected["resources"],
        )
        for key in ("starts_at", "ends_at"):
            assert datetime.fromisoformat(
                actual[key].replace("Z", "+00:00")
            ) == datetime.fromisoformat(expected[key])
    saved = client.get(f"/api/v1/projects/{project['id']}/plans").json()
    assert saved["work_states"][a["id"]] == "in_progress"
    assert (
        next(p for p in saved["plans"] if p["id"] == first["id"])["works"]
        == first["works"]
    )
    other = post(client, "/projects", {"name": "Другой объект"}).json()
    assert client.get(url.replace(project["id"], other["id"])).status_code == 404


def test_resource_profiles_and_atomic_class_extension(client, project):
    catalog = client.get("/api/v1/catalog").json()
    assert catalog["planning_class_ids"] == PLANNING_CLASS_IDS
    assert 18 in PLANNING_CLASS_IDS and 15 not in PLANNING_CLASS_IDS
    works = {w["code"]: w for w in catalog["works"]}
    assert len(works) == 377
    for row in works.values():
        preset = row["resource_preset"]
        assert set(preset["counts"]) == {str(c) for c in PLANNING_CLASS_IDS}
        assert all(n in (0, 1) for n in preset["counts"].values())
        if preset["is_section"]:
            assert not any(preset["counts"].values())
        for c in preset["episodic_class_ids"]:
            assert preset["counts"].get(str(c), 0) == 0
    assert works["10.2.2"]["resource_preset"]["counts"]["1"] == 1
    assert works["10.2.2"]["resource_preset"]["counts"]["4"] == 1
    assert works["10.5"]["resource_preset"]["counts"]["1"] == 1
    assert 1 not in works["10.5"]["resource_preset"]["episodic_class_ids"]
    assert works["10.5"]["resource_preset"]["counts"]["7"] == 0
    assert not any(works["10.1"]["resource_preset"]["counts"].values())
    old = approved(client, project, [work()])
    stage = {
        **old["works"][0],
        "resources": {
            **{str(c): 0 for c in PLANNING_CLASS_IDS},
            **old["works"][0]["resources"],
        },
    }
    data = {
        "works": [stage],
        "base_plan_id": old["id"],
        "additional_class_ids": PLANNING_CLASS_IDS,
    }
    bad = post(
        client,
        f"/projects/{project['id']}/plans",
        {**data, "additional_class_ids": [99]},
    )
    assert bad.status_code == 422
    assert (
        client.get(f"/api/v1/projects/{project['id']}").json()["class_ids"]
        == project["class_ids"]
    )
    created = post(client, f"/projects/{project['id']}/plans", data)
    assert created.status_code == 201, created.text
    assert (
        client.get(f"/api/v1/projects/{project['id']}").json()["class_ids"]
        == sorted(PLANNING_CLASS_IDS)
    )
    assert client.get(f"/api/v1/plans/{old['id']}/validation").json()["errors"] == []
    versions = client.get(f"/api/v1/projects/{project['id']}/plans").json()["plans"]
    assert next(p for p in versions if p["id"] == old["id"])["works"] == old["works"]
    archived = client.get(f"/api/v1/projects/{project['id']}/plans/{old['id']}/export")
    assert "Асфальтоукладчик" in archived.content.decode("utf-8-sig")

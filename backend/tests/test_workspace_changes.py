from datetime import datetime, timedelta
from sqlalchemy import select
from app import api, models as m
from app.db import now
from app.rules import evaluate, inside
from test_app import approved, post, work


def test_publish_and_csv_blank_cells_have_no_intermediate_status(client, project):
    template = client.get(
        f"/api/v1/projects/{project['id']}/plan-template"
    ).content.decode("utf-8-sig")
    content = template + "10.2.3;Импорт;18.09.2026 09:00;18.09.2026 12:00;2; ;\r\n"
    response = client.post(
        f"/api/v1/projects/{project['id']}/plan-imports",
        files={"file": ("plan.csv", content.encode("utf-8-sig"))},
        headers={"Idempotency-Key": "blank-cells"},
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["status"] == "approved" and plan["approved_at"] is not None
    assert plan["works"][0]["resources"] == {"0": 2, "1": 0, "16": 0}
    assert plan["validation"]["warnings"] == []
    assert (
        client.get(f"/api/v1/projects/{project['id']}").json()["current_plan_id"]
        == plan["id"]
    )
    assert (
        client.get(f"/api/v1/projects/{project['id']}/monitoring").json()[
            "dependency_warnings"
        ]
        == []
    )
    assert post(
        client, f"/plans/{plan['id']}/approve", {"expected_revision": 1}
    ).status_code in (404, 405)


def test_csv_keeps_validation_for_missing_headers_and_invalid_counts(client, project):
    url = f"/api/v1/projects/{project['id']}/plan-imports"
    for key, text in [
        (
            "missing",
            "code;title;starts_at;ends_at;0;1\n10.2.3;Test;2026-09-18T09:00Z;2026-09-18T12:00Z;2;1",
        ),
        (
            "negative",
            "code;title;starts_at;ends_at;0;1;16\n10.2.3;Test;2026-09-18T09:00Z;2026-09-18T12:00Z;-1;;",
        ),
    ]:
        response = client.post(
            url,
            files={"file": ("plan.csv", text.encode())},
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 422
    assert client.get(f"/api/v1/projects/{project['id']}/plans").json()["plans"] == []


def test_deletion_detaches_plan_cancels_jobs_and_preserves_evidence(
    client, project, runtime
):
    factory, _ = runtime
    stage = work()
    plan = approved(client, project, [stage])
    with factory() as db:
        source = m.Source(
            project_id=project["id"],
            name="X",
            kind="video",
            enabled=True,
            status="running",
        )
        db.add(source)
        db.flush()
        job = m.Job(
            project_id=project["id"],
            source_id=source.id,
            kind="analyze",
            status="running",
            token="old-token",
        )
        db.add(job)
        db.flush()
        observation = m.Observation(
            project_id=project["id"],
            source_id=source.id,
            run_id=job.id,
            sample_index=0,
            offset_seconds=0,
            detections=[],
            quality={"usable": True},
            model={"supported_classes": ["0"]},
            evidence_key="retained",
        )
        db.add(observation)
        db.commit()
        source_id, job_id, observation_id = source.id, job.id, observation.id
    url = f"/api/v1/projects/{project['id']}/plan"
    args = {
        "json": {"expected_plan_id": plan["id"]},
        "headers": {"Idempotency-Key": "delete"},
    }
    first = client.request("DELETE", url, **args)
    again = client.request("DELETE", url, **args)
    assert first.status_code == 200 and again.json() == first.json()
    assert (
        client.get(f"/api/v1/projects/{project['id']}").json()["current_plan_id"]
        is None
    )
    with factory() as db:
        assert db.get(m.Plan, plan["id"]).status == "deleted"
        assert db.get(m.Plan, plan["id"]).works == plan["works"]
        assert db.get(m.Observation, observation_id).evidence_key == "retained"
        assert (
            db.get(m.Job, job_id).status == "cancelled"
            and db.get(m.Job, job_id).token is None
        )
        assert not db.get(m.Source, source_id).enabled
    assert any(
        e["action"] == "plan.deleted"
        for e in client.get(f"/api/v1/projects/{project['id']}/audit").json()
    )
    replacement = approved(client, project, [work("10.1")])
    assert replacement["version"] == 2
    assert (
        client.request(
            "DELETE",
            url,
            json={"expected_plan_id": plan["id"]},
            headers={"Idempotency-Key": "stale"},
        ).status_code
        == 409
    )


def test_equipment_activity_is_validated_and_scoped_to_project(client, project):
    other = post(client, "/projects", {"name": "Другой объект"}).json()
    settings = {
        **project["settings"],
        "equipment_activity": {"0": "stationary_capable"},
        "idle_after_seconds": 120,
        "motion_fraction_threshold": 0.02,
        "expected_revision": project["revision"],
    }
    response = client.put(
        f"/api/v1/projects/{project['id']}/settings",
        json=settings,
        headers={"Idempotency-Key": "activity"},
    )
    assert response.status_code == 200, response.text
    saved = client.get(f"/api/v1/projects/{project['id']}").json()
    assert saved["settings"]["equipment_activity"]["0"] == "stationary_capable"
    assert saved["settings"]["idle_after_seconds"] == 120
    assert saved["settings"]["motion_fraction_threshold"] == 0.02
    assert not client.get(f"/api/v1/projects/{other['id']}").json()["settings"][
        "equipment_activity"
    ]
    settings.update(
        expected_revision=saved["revision"], equipment_activity={"99": "mobile"}
    )
    assert (
        client.put(
            f"/api/v1/projects/{project['id']}/settings",
            json=settings,
            headers={"Idempotency-Key": "bad-id"},
        ).status_code
        == 422
    )
    settings["equipment_activity"] = {"0": "flying"}
    assert (
        client.put(
            f"/api/v1/projects/{project['id']}/settings",
            json=settings,
            headers={"Idempotency-Key": "bad-type"},
        ).status_code
        == 422
    )
    settings["equipment_activity"] = {"0": "mobile"}
    settings["motion_fraction_threshold"] = 0.5
    assert (
        client.put(
            f"/api/v1/projects/{project['id']}/settings",
            json=settings,
            headers={"Idempotency-Key": "bad-motion-threshold"},
        ).status_code
        == 422
    )


def test_polygon_display_preferences_persist_without_disabling_analysis(
    client, project, runtime
):
    stage = work()
    approved(client, project, [stage])
    factory, _ = runtime
    with factory() as db:
        source = m.Source(project_id=project["id"], name="X", kind="image")
        db.add(source)
        db.commit()
        source_id = source.id
    region = {
        "name": "Область",
        "work_ids": [stage["id"]],
        "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
        "color": "#6366d9",
        "visible": False,
    }
    response = post(client, f"/sources/{source_id}/bindings", {"regions": [region]})
    assert response.status_code == 200, response.text
    saved = client.get(f"/api/v1/sources/{source_id}/bindings").json()["regions"][0]
    assert saved["color"] == "#6366d9" and saved["visible"] is False
    assert saved["visibility_confirmed"] is True
    result = evaluate(
        [stage],
        [saved],
        [],
        datetime.fromisoformat(stage["starts_at"]),
        ["0", "1", "16"],
        True,
    )[0]
    assert result["readiness"] == "ready" and result["counts"]["0"] == 0
    assert any(f["kind"] == "missing" for f in result["findings"])
    unknown = evaluate(
        [stage],
        [saved],
        [],
        datetime.fromisoformat(stage["starts_at"]),
        ["0", "1", "16"],
        False,
    )[0]
    assert unknown["counts"]["0"] is None


def test_full_frame_region_includes_detection_on_image_boundary():
    polygon = [[0, 0], [1, 0], [1, 1], [0, 1]]
    assert inside((0, 0), polygon)
    assert inside((0.5, 1), polygon)
    assert inside((1, 0.5), polygon)


def test_demo_loop_is_trimmed_to_the_bound_stage_end(client, project, runtime):
    stage = work(
        start="2026-09-24T09:00:00+03:00",
        end="2026-09-24T10:00:00+03:00",
    )
    plan_data = approved(client, project, [stage])
    factory, _ = runtime
    with factory() as db:
        source = m.Source(
            project_id=project["id"],
            name="Циклическое демо",
            kind="video",
            capture_start=datetime.fromisoformat(stage["starts_at"]),
            sample_seconds=1,
            metadata_json={
                "duration_seconds": 30,
                "demo_loop": {"enabled": True, "duration_seconds": 3600},
            },
        )
        db.add(source)
        db.commit()
        source_id = source.id
    region = {
        "name": "Весь кадр",
        "work_ids": [stage["id"]],
        "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
    }
    saved = post(
        client,
        f"/sources/{source_id}/bindings",
        {"expected_revision": 0, "regions": [region]},
    )
    assert saved.status_code == 200, saved.text
    updated = client.patch(
        f"/api/v1/sources/{source_id}",
        json={
            "sample_seconds": 1,
            "capture_start": stage["starts_at"],
            "demo_loop_enabled": True,
            "demo_loop_duration_seconds": 3601,
        },
        headers={"Idempotency-Key": "loop-overflow"},
    )
    assert updated.status_code == 200, updated.text
    with factory() as db:
        source = db.get(m.Source, source_id)
        plan = db.get(m.Plan, plan_data["id"])
        binding = db.scalar(select(m.Binding).where(m.Binding.source_id == source_id))
        assert source.metadata_json["demo_loop"]["duration_seconds"] == 3601
        loop = api.validate_demo_loop(source, plan, binding.regions)
        assert loop == {
            "enabled": True,
            "duration_seconds": 3600,
            "requested_duration_seconds": 3601,
            "trimmed_to_stage": True,
        }


def test_demo_loop_can_start_before_its_bound_stage(client, project, runtime):
    stage = work(
        start="2026-09-24T10:00:00+03:00",
        end="2026-09-24T11:00:00+03:00",
    )
    plan_data = approved(client, project, [stage])
    factory, _ = runtime
    with factory() as db:
        source = m.Source(
            project_id=project["id"],
            name="Камера этапа",
            kind="video",
            capture_start=datetime.fromisoformat("2026-09-24T09:00:00+03:00"),
            sample_seconds=1,
            metadata_json={
                "duration_seconds": 30,
                "demo_loop": {"enabled": True, "duration_seconds": 10800},
            },
        )
        db.add(source)
        db.commit()
        source_id = source.id
    saved = post(
        client,
        f"/sources/{source_id}/bindings",
        {
            "expected_revision": 0,
            "regions": [
                {
                    "name": "Весь кадр",
                    "work_ids": [stage["id"]],
                    "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                }
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    with factory() as db:
        source = db.get(m.Source, source_id)
        plan = db.get(m.Plan, plan_data["id"])
        loop = api.validate_demo_loop(source, plan, saved.json()["regions"])
        assert loop["duration_seconds"] == 7200
        assert loop["trimmed_to_stage"] is True


def test_demo_loop_rejects_images_and_requires_confirmed_start(client, project):
    headers = {"Idempotency-Key": "loop-image"}
    image = client.post(
        f"/api/v1/projects/{project['id']}/media",
        files={"file": ("frame.jpg", b"not-an-image")},
        data={
            "demo_loop_enabled": "true",
            "demo_loop_duration_seconds": "3600",
            "capture_start": "2026-09-24T09:00:00+03:00",
        },
        headers=headers,
    )
    assert image.status_code == 422
    video = client.post(
        f"/api/v1/projects/{project['id']}/media",
        files={"file": ("clip.mp4", b"not-a-video")},
        data={
            "demo_loop_enabled": "true",
            "demo_loop_duration_seconds": "3600",
        },
        headers={"Idempotency-Key": "loop-no-start"},
    )
    assert video.status_code == 422


def test_demo_loop_accepts_any_positive_finite_duration(client, project, runtime):
    duration_seconds = 40_000_000.5
    response = client.post(
        f"/api/v1/projects/{project['id']}/media",
        files={"file": ("long-demo.mp4", b"not-a-video")},
        data={
            "capture_start": "2026-09-24T09:00:00+03:00",
            "demo_loop_enabled": "true",
            "demo_loop_duration_seconds": str(duration_seconds),
        },
        headers={"Idempotency-Key": "loop-any-positive-duration"},
    )
    assert response.status_code == 202, response.text
    factory, _ = runtime
    with factory() as db:
        source = db.get(m.Source, response.json()["source"]["id"])
        assert source.metadata_json["demo_loop"]["duration_seconds"] == duration_seconds


def test_project_rename_uses_revision_and_writes_audit(client, project):
    url = f"/api/v1/projects/{project['id']}"
    payload = {
        "name": "Новое имя",
        "address": "Москва",
        "expected_revision": project["revision"],
    }
    first = client.put(url, json=payload, headers={"Idempotency-Key": "rename"})
    again = client.put(url, json=payload, headers={"Idempotency-Key": "rename"})
    assert first.status_code == 200, first.text
    assert again.json() == first.json()
    saved = first.json()
    assert saved["name"] == "Новое имя" and saved["address"] == "Москва"
    assert saved["revision"] == project["revision"] + 1
    stale = client.put(
        url,
        json={**payload, "name": "Другое", "expected_revision": project["revision"]},
        headers={"Idempotency-Key": "rename-stale"},
    )
    assert stale.status_code == 409
    assert any(
        event["action"] == "project.updated"
        for event in client.get(f"{url}/audit").json()
    )


def test_project_delete_removes_tree_and_list_entry(client, project, runtime):
    factory, _ = runtime
    stage = work()
    plan = approved(client, project, [stage])
    with factory() as db:
        source = m.Source(
            project_id=project["id"],
            name="Cam",
            kind="image",
            enabled=True,
            status="running",
        )
        db.add(source)
        db.flush()
        job = m.Job(
            project_id=project["id"],
            source_id=source.id,
            kind="analyze",
            status="running",
            token="live",
        )
        db.add(job)
        db.flush()
        observation = m.Observation(
            project_id=project["id"],
            source_id=source.id,
            run_id=job.id,
            sample_index=0,
            offset_seconds=0,
            detections=[],
            quality={"usable": True},
            model={"supported_classes": ["0"]},
            evidence_key="gone",
        )
        db.add(observation)
        db.commit()
        source_id, job_id, observation_id = source.id, job.id, observation.id
    url = f"/api/v1/projects/{project['id']}"
    args = {
        "json": {"expected_revision": project["revision"] + 1},
        "headers": {"Idempotency-Key": "delete-project"},
    }
    first = client.request("DELETE", url, **args)
    again = client.request("DELETE", url, **args)
    assert first.status_code == 200, first.text
    assert again.json() == first.json()
    assert first.json()["deleted_project_id"] == project["id"]
    assert client.get(url).status_code == 404
    assert project["id"] not in {
        row["id"] for row in client.get("/api/v1/projects").json()
    }
    with factory() as db:
        assert db.get(m.Project, project["id"]) is None
        assert db.get(m.Plan, plan["id"]) is None
        assert db.get(m.Source, source_id) is None
        assert db.get(m.Job, job_id) is None
        assert db.get(m.Observation, observation_id) is None
    stale = client.request(
        "DELETE",
        url,
        json={"expected_revision": 1},
        headers={"Idempotency-Key": "delete-missing"},
    )
    assert stale.status_code == 404


def test_source_can_be_edited_and_deleted_with_its_private_artifacts(
    client, project, runtime
):
    factory, folder = runtime
    with factory() as db:
        source = m.Source(
            project_id=project["id"],
            name="Старое имя",
            kind="video",
            file_key="media/source.mp4",
            sample_seconds=1,
            status="completed",
            metadata_json={"demo_loop": {"enabled": True, "duration_seconds": 3600}},
        )
        db.add(source)
        db.flush()
        job = m.Job(
            project_id=project["id"],
            source_id=source.id,
            kind="analyze",
            status="succeeded",
        )
        db.add(job)
        db.flush()
        observation = m.Observation(
            project_id=project["id"],
            source_id=source.id,
            run_id=job.id,
            sample_index=0,
            offset_seconds=0,
            detections=[],
            quality={"usable": True},
            model={"supported_classes": ["0"]},
            evidence_key="evidence/frame.jpg",
        )
        db.add(observation)
        db.flush()
        assessment = m.Assessment(
            project_id=project["id"],
            observation_id=observation.id,
            results=[],
            settings={},
        )
        db.add(assessment)
        db.flush()
        db.add(
            m.Alert(
                project_id=project["id"],
                source_id=source.id,
                run_id=job.id,
                work_id="work",
                fingerprint="source-delete",
                kind="missing",
                severity="warning",
                status="resolved",
                first_seen=now(),
                last_seen=now(),
                assessment_id=assessment.id,
                details={},
            )
        )
        db.add(
            m.Binding(
                project_id=project["id"],
                source_id=source.id,
                revision=1,
                regions=[{
                    "id": "stale-region",
                    "name": "Прежний этап",
                    "work_ids": ["old-work"],
                    "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                    "primary": True,
                    "visibility_confirmed": True,
                }],
            )
        )
        db.commit()
        ids = source.id, job.id, observation.id, assessment.id
    for relative in (
        "media/source.mp4",
        "previews/" + ids[0] + ".jpg",
        "evidence/frame.jpg",
    ):
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"artifact")
    edited = client.patch(
        f"/api/v1/sources/{ids[0]}",
        json={
            "name": "Новое имя",
            "sample_seconds": 5,
            "capture_start": "2026-09-24T09:00:00+03:00",
        },
        headers={"Idempotency-Key": "edit-source"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Новое имя"
    assert edited.json()["sample_seconds"] == 5
    assert datetime.fromisoformat(edited.json()["capture_start"]) == datetime.fromisoformat("2026-09-24T09:00:00+03:00")
    invalid = client.patch(
        f"/api/v1/sources/{ids[0]}",
        json={"name": "Новое имя", "sample_seconds": 5, "capture_start": None},
        headers={"Idempotency-Key": "edit-source-without-loop-start"},
    )
    assert invalid.status_code == 422
    deleted = client.delete(
        f"/api/v1/sources/{ids[0]}",
        headers={"Idempotency-Key": "delete-source"},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_source_id"] == ids[0]
    with factory() as db:
        assert db.get(m.Source, ids[0]) is None
        assert db.get(m.Job, ids[1]) is None
        assert db.get(m.Observation, ids[2]) is None
        assert db.get(m.Assessment, ids[3]) is None
        assert any(
            event.action == "source.deleted"
            for event in db.query(m.Audit).filter_by(project_id=project["id"])
        )
    assert not (folder / "media/source.mp4").exists()
    assert not (folder / "previews" / f"{ids[0]}.jpg").exists()
    assert not (folder / "evidence/frame.jpg").exists()


def test_active_source_must_be_stopped_before_delete(client, project, runtime):
    factory, _ = runtime
    with factory() as db:
        source = m.Source(project_id=project["id"], name="Live", kind="rtsp")
        db.add(source)
        db.flush()
        db.add(
            m.Job(
                project_id=project["id"],
                source_id=source.id,
                kind="analyze",
                status="running",
            )
        )
        db.commit()
        source_id = source.id
    response = client.delete(
        f"/api/v1/sources/{source_id}",
        headers={"Idempotency-Key": "delete-active-source"},
    )
    assert response.status_code == 409
    with factory() as db:
        assert db.get(m.Source, source_id) is not None


def test_project_list_summary_reports_stage_cameras_and_open_alerts(
    client, project, runtime
):
    factory, _ = runtime
    instant = now()
    current = work(
        "10.2.3",
        start=(instant - timedelta(hours=1)).isoformat(),
        end=(instant + timedelta(hours=2)).isoformat(),
        title="Земляные работы",
    )
    extra = work(
        "10.2.4",
        start=(instant - timedelta(minutes=30)).isoformat(),
        end=(instant + timedelta(hours=3)).isoformat(),
        title="Фундамент",
    )
    past = work(
        "10.1",
        start=(instant - timedelta(days=2)).isoformat(),
        end=(instant - timedelta(days=1)).isoformat(),
        title="Подготовка",
    )
    plan = approved(client, project, [current, extra, past])
    empty = client.get("/api/v1/projects").json()[0]["summary"]
    assert empty["sources"] == empty["live_sources"] == empty["open_alerts"] == 0
    assert [item["code"] for item in empty["current_works"]] == ["10.2.3", "10.2.4"]
    post(
        client,
        f"/projects/{project['id']}/works/{current['id']}/status",
        {"status": "completed", "note": "закрыт"},
    )
    with factory() as db:
        live = m.Source(
            project_id=project["id"],
            name="Live",
            kind="rtsp",
            status="running",
            enabled=True,
        )
        idle = m.Source(
            project_id=project["id"],
            name="Idle",
            kind="video",
            status="stopped",
            enabled=False,
        )
        db.add_all([live, idle])
        db.flush()
        job = m.Job(
            project_id=project["id"],
            source_id=live.id,
            kind="analyze",
            status="running",
        )
        db.add(job)
        db.flush()
        observation = m.Observation(
            project_id=project["id"],
            source_id=live.id,
            run_id=job.id,
            sample_index=0,
            offset_seconds=0,
            detections=[],
            quality={"usable": True},
            model={"supported_classes": ["0"]},
            evidence_key="card",
        )
        db.add(observation)
        db.flush()
        assessment = m.Assessment(
            project_id=project["id"],
            observation_id=observation.id,
            plan_id=plan["id"],
            results=[],
            settings={},
        )
        db.add(assessment)
        db.flush()
        open_alert = m.Alert(
            project_id=project["id"],
            source_id=live.id,
            run_id=job.id,
            work_id=extra["id"],
            fingerprint="open",
            kind="missing",
            severity="warning",
            status="open",
            first_seen=instant,
            last_seen=instant,
            duration_seconds=0,
            assessment_id=assessment.id,
            details={},
        )
        closed = m.Alert(
            project_id=project["id"],
            source_id=live.id,
            run_id=job.id,
            work_id=extra["id"],
            fingerprint="closed",
            kind="missing",
            severity="warning",
            status="resolved",
            first_seen=instant,
            last_seen=instant,
            duration_seconds=0,
            assessment_id=assessment.id,
            details={},
        )
        hidden = m.Alert(
            project_id=project["id"],
            source_id=live.id,
            run_id=job.id,
            work_id=extra["id"],
            fingerprint="hidden",
            kind="missing",
            severity="warning",
            status="open",
            first_seen=instant,
            last_seen=instant,
            duration_seconds=0,
            assessment_id=assessment.id,
            details={"suppressed": True},
        )
        legacy_duplicate = m.Alert(
            project_id=project["id"],
            source_id=live.id,
            run_id=job.id,
            work_id=extra["id"],
            fingerprint="open",
            kind="missing",
            severity="warning",
            status="resolved",
            first_seen=instant - timedelta(seconds=2),
            last_seen=instant - timedelta(seconds=1),
            duration_seconds=1,
            assessment_id=assessment.id,
            details={},
        )
        db.add_all([open_alert, closed, hidden, legacy_duplicate])
        db.commit()
    card = client.get("/api/v1/projects").json()[0]
    summary = card["summary"]
    assert [item["title"] for item in summary["current_works"]] == ["Фундамент"]
    assert summary["sources"] == 2 and summary["live_sources"] == 1
    assert summary["open_alerts"] == 1
    assert client.get(f"/api/v1/projects/{project['id']}").json()["summary"] == summary
    visible_alerts = client.get(f"/api/v1/projects/{project['id']}/alerts").json()
    assert len(visible_alerts) == 2
    assert sum(alert["fingerprint"] == "open" for alert in visible_alerts) == 1


def test_project_summary_pause_when_plan_has_no_current_window(client, project):
    instant = now()
    past = work(
        "10.1",
        start=(instant - timedelta(days=2)).isoformat(),
        end=(instant - timedelta(days=1)).isoformat(),
    )
    approved(client, project, [past])
    card = client.get("/api/v1/projects").json()[0]
    assert card["current_plan_id"] and card["summary"]["current_works"] == []

import csv
import io
from app import models as m


def test_unassessed_observation_does_not_become_significant_event(
    client, project, runtime
):
    factory, _ = runtime
    with factory() as db:
        source = m.Source(project_id=project["id"], name="Test", kind="image")
        db.add(source)
        db.flush()
        job = m.Job(project_id=project["id"], source_id=source.id, kind="analyze")
        db.add(job)
        db.flush()
        db.add(
            m.Observation(
                project_id=project["id"],
                source_id=source.id,
                run_id=job.id,
                sample_index=0,
                offset_seconds=0,
                detections=[],
                quality={"usable": True},
                model={"supported_classes": ["0"]},
                evidence_key="test-only",
            )
        )
        db.commit()
    response = client.get(f"/api/v1/projects/{project['id']}/reports/csv")
    rows = list(
        csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";")
    )
    assert rows == []

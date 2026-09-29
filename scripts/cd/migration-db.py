"""Controller-owned PostgreSQL checks. Runs only inside reviewed app images."""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, "/app")


def require(condition):
    if not condition:
        raise ValueError("Database/schema/smoke invariant failed")


def file_hash(path):
    return hashlib.sha256(Path(path).read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()


def validate_tree(previous, current, approved_hash):
    previous, current = Path(previous), Path(current)
    old = {p.relative_to(previous).as_posix(): file_hash(p) for p in previous.rglob("*.py")}
    new = {p.relative_to(current).as_posix(): file_hash(p) for p in current.rglob("*.py")}
    addition = "versions/004_detector_lab.py"
    if set(new) != set(old) | {addition} or addition in old:
        raise ValueError("Expected exactly one reviewed migration addition")
    if any(new[name] != digest for name, digest in old.items()):
        raise ValueError("Applied migration files changed")
    if new[addition] != approved_hash:
        raise ValueError("Unreviewed migration 004 content")
    chain = {}
    for path in current.joinpath("versions").glob("*.py"):
        values = {}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {"revision", "down_revision", "branch_labels", "depends_on"}:
                        values[target.id] = ast.literal_eval(node.value)
        if "revision" in values:
            if values["revision"] in chain or values.get("branch_labels") or values.get("depends_on"):
                raise ValueError("Ambiguous migration chain")
            chain[values["revision"]] = values["down_revision"]
    if chain != {"001": None, "002": "001", "003": "002", "004": "003"}:
        raise ValueError("Only the reviewed 003 to 004 chain is supported")
    return {"chain": ["003", "004"], "immutableAppliedFiles": True}


def engine():
    from sqlalchemy import create_engine
    return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


def revision(connection):
    from sqlalchemy import text
    values = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if len(values) != 1 or values[0] not in {"003", "004"}:
        raise ValueError("Unexpected/ambiguous current revision")
    return values[0]


def snapshot():
    from sqlalchemy import inspect, text
    with engine().connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        inspector = inspect(connection)
        result = {"revision": revision(connection), "tables": {}, "sequences": []}
        for name in sorted(inspector.get_table_names(schema="public")):
            if name in {"alembic_version", "detector_runs", "detector_frames"}:
                continue
            quoted = connection.dialect.identifier_preparer.quote(name)
            digest, count = hashlib.sha256(), 0
            rows = connection.execution_options(stream_results=True).execute(
                text(f'SELECT to_jsonb(t)::text FROM public.{quoted} AS t ORDER BY to_jsonb(t)::text COLLATE "C"')
            )
            for row in rows:
                digest.update(row[0].encode() + b"\n")
                count += 1
            connection = connection.execution_options(stream_results=False)
            columns = [{"name": c["name"], "type": str(c["type"]), "nullable": c["nullable"], "default": c["default"]}
                       for c in inspector.get_columns(name, schema="public")]
            result["tables"][name] = {"sha256": digest.hexdigest(), "count": count, "columns": columns,
                                      "pk": inspector.get_pk_constraint(name),
                                      "fk": inspector.get_foreign_keys(name),
                                      "unique": inspector.get_unique_constraints(name),
                                      "indexes": inspector.get_indexes(name)}
        for name in sorted(inspector.get_sequence_names(schema="public")):
            quoted = connection.dialect.identifier_preparer.quote(name)
            row = connection.execute(text(f"SELECT last_value, is_called FROM public.{quoted}")).one()
            result["sequences"].append([name, row[0], row[1]])
        return result


def assert_preserved(before, after):
    if before["tables"] != after["tables"] or before["sequences"] != after["sequences"]:
        raise ValueError("Existing data or schema changed")


def check_schema():
    from sqlalchemy import inspect, text
    from sqlalchemy import types
    with engine().connect() as connection:
        if revision(connection) != "004":
            raise ValueError("Target revision not installed")
        inspector = inspect(connection)
        expected = {
            "detector_runs": {"id", "name", "kind", "file_key", "input_sha256", "mode", "start_seconds", "end_seconds",
                              "sample_seconds", "requested_model_sha", "model", "metadata_json", "status", "progress",
                              "processed_frames", "attempts", "token", "lease_until", "error", "created_at", "updated_at"},
            "detector_frames": {"id", "run_id", "sample_index", "offset_seconds", "detections", "quality", "image_key"},
        }
        nullable = {"end_seconds", "token", "lease_until", "error"}
        lengths = {"id": 36, "run_id": 36, "name": 200, "kind": 16, "file_key": 200,
                   "input_sha256": 64, "mode": 16, "requested_model_sha": 64, "status": 20,
                   "token": 36, "image_key": 250}
        for table, names in expected.items():
            columns = inspector.get_columns(table)
            require({c["name"] for c in columns} == names)
            require({c["name"] for c in columns if c["nullable"]} == (nullable if table == "detector_runs" else set()))
            require(inspector.get_pk_constraint(table)["constrained_columns"] == ["id"])
            for column in columns:
                name, kind = column["name"], column["type"]
                if name in lengths:
                    require(isinstance(kind, types.String) and kind.length == lengths[name])
                elif name in {"model", "metadata_json", "detections", "quality"}:
                    require(isinstance(kind, types.JSON))
                elif name in {"created_at", "updated_at", "lease_until"}:
                    require(isinstance(kind, types.DateTime) and kind.timezone)
                elif name in {"processed_frames", "attempts", "sample_index"}:
                    require(isinstance(kind, types.Integer))
                elif name == "error":
                    require(isinstance(kind, types.Text))
                else:
                    require(isinstance(kind, types.Float))
        require(any(i["name"] == "ix_detector_runs_status" and i["column_names"] == ["status"] for i in inspector.get_indexes("detector_runs")))
        require(any(i["name"] == "ix_detector_frames_run_id" and i["column_names"] == ["run_id"] for i in inspector.get_indexes("detector_frames")))
        require(any(c["column_names"] == ["run_id", "sample_index"] for c in inspector.get_unique_constraints("detector_frames")))
        require(any(c["constrained_columns"] == ["run_id"] and c["referred_table"] == "detector_runs"
                    and c["referred_columns"] == ["id"] for c in inspector.get_foreign_keys("detector_frames")))
        require(connection.execute(text("SELECT count(*) FROM pg_index WHERE NOT indisvalid AND indrelid IN ('detector_runs'::regclass,'detector_frames'::regclass)")).scalar_one() == 0)
    return {"revision": "004", "tables": True, "indexes": True, "constraints": True}


def smoke():
    # No global worker loop: only this newly-created lab run can be processed.
    if os.environ.get("CD_ISOLATED_REHEARSAL") != "1":
        raise ValueError("Smoke is permitted only in the isolated rehearsal")
    import cv2
    import numpy as np
    from datetime import timedelta
    from urllib.request import Request, urlopen
    from app.db import Session, now, uid
    from app.models import DetectorRun
    from app.detector_worker import process

    ok, image = cv2.imencode(".png", np.zeros((96, 96, 3), dtype=np.uint8))
    require(ok)
    boundary = "stroycd" + uid().replace("-", "")
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="mode"\r\n\r\nimage\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="SMOKE.png"\r\nContent-Type: image/png\r\n\r\n').encode()
    body += image.tobytes() + f"\r\n--{boundary}--\r\n".encode()
    request = Request("http://127.0.0.1:8000/api/v1/detector/runs", data=body,
                      headers={"Content-Type": "multipart/form-data; boundary=" + boundary, "Idempotency-Key": uid()})
    with urlopen(request, timeout=20) as response:
        require(response.status == 202)
        payload = json.load(response)
    with Session() as db:
        run = db.get(DetectorRun, payload["id"])
        require(run is not None and run.status == "queued")
        run.status, run.token, run.attempts = "running", uid(), 1
        run.lease_until = now() + timedelta(seconds=120)
        db.commit()
        db.expunge(run)
    process(run)
    with Session() as db:
        run = db.get(DetectorRun, run.id)
        require(run.status == "succeeded" and run.processed_frames == 1)
    with urlopen("http://127.0.0.1:8000/api/v1/detector/runs/" + run.id + "/frames", timeout=15) as response:
        frames = json.load(response)["frames"]
        require(len(frames) == 1)
    with urlopen("http://127.0.0.1:8000" + frames[0]["image_url"], timeout=15) as response:
        require(response.status == 200 and response.read(2) == b"\xff\xd8")
    return {"detectorUpload": True, "inference": True, "frame": True, "productionWrites": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["chain", "snapshot", "schema", "upgrade", "smoke", "compatibility"])
    parser.add_argument("--previous")
    parser.add_argument("--approved-hash")
    args = parser.parse_args()
    if args.command == "chain":
        result = validate_tree(args.previous, "/app/migrations", args.approved_hash)
    elif args.command == "snapshot":
        result = snapshot()
    elif args.command == "schema":
        result = check_schema()
    elif args.command == "upgrade":
        from alembic import command
        from alembic.config import Config
        with engine().connect() as connection:
            if revision(connection) != "003":
                raise ValueError("Expected revision 003 before upgrade")
        command.upgrade(Config("/app/alembic.ini"), "004")
        result = check_schema()
    elif args.command == "compatibility":
        from app.db import Base
        from app import models  # noqa: F401
        from sqlalchemy import select
        with engine().connect() as connection:
            require(revision(connection) == "004")
            for table in Base.metadata.sorted_tables:
                connection.execute(select(table).limit(1)).first()
        result = {"oldApplicationReadsExpandedSchema": True}
    else:
        result = smoke()
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("CD database check failed", file=sys.stderr)
        raise SystemExit(1)

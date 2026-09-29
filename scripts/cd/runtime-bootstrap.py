"""Reviewed runtime entrypoint: exact revision, durable maintenance, no auto-upgrade."""

import hmac
import json
import os
from pathlib import Path
import runpy
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, "/app")
MARKER = Path("/run/stroy-cd/maintenance.json")


def require(condition):
    if not condition:
        raise RuntimeError("Runtime probe invariant failed")


def marker():
    try:
        return json.loads(MARKER.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"token": ""}  # Fail closed after a partial/inaccessible state write.


def check_revision():
    from sqlalchemy import text
    from app.db import engine

    expected = os.environ["CD_EXPECTED_REVISION"]
    if expected not in {"003", "004"}:
        raise RuntimeError("Unreviewed runtime revision")
    with engine.connect() as connection:
        revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if revisions != [expected]:
        raise RuntimeError("Database revision does not match reviewed runtime")


class Maintenance:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        state = marker()
        if state is not None and scope["type"] in {"http", "websocket"}:
            headers = dict(scope.get("headers", []))
            client = scope.get("client") or ("", 0)
            supplied = headers.get(b"x-stroy-cd-probe", b"").decode("ascii", errors="ignore")
            allowed = (
                client[0] == "127.0.0.1"
                and scope.get("method") in {"GET", "HEAD"}
                and bool(state.get("token"))
                and hmac.compare_digest(supplied, state["token"])
            )
            if not allowed:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1013})
                else:
                    await send({"type": "http.response.start", "status": 503,
                                "headers": [(b"content-type", b"text/plain"), (b"retry-after", b"60")]})
                    await send({"type": "http.response.body", "body": b"Maintenance"})
                return
        await self.app(scope, receive, send)


def get(path):
    state = marker()
    headers = {"X-Stroy-CD-Probe": state["token"]} if state else {}
    try:
        with urlopen(Request("http://127.0.0.1:8000" + path, headers=headers), timeout=15) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def probe(detector=False):
    check_revision()
    status, raw = get("/api/v1/health")
    health = json.loads(raw)
    require(status == 200 and health["status"] == "ok" and health["model"]["ready"])
    status, raw = get("/")
    require(status == 200 and b"<html" in raw)
    status, raw = get("/api/v1/projects")
    require(status == 200 and isinstance(json.loads(raw), list))
    result = {"health": health, "ui": True, "projects": True}
    if detector:
        status, raw = get("/api/v1/detector/runs?limit=1")
        require(status == 200 and isinstance(json.loads(raw), list))
        status, raw = get("/api/v1/detector/runs/00000000-0000-0000-0000-000000000000/frames")
        require(status == 404)
        status, raw = get("/api/v1/map/reverse?latitude=55.75&longitude=37.61")
        require(status in {200, 503})
        payload = json.loads(raw)
        if os.getenv("GEOCODER_REVERSE_URL") == "":
            require(status == 503)
        if status == 200:
            require("address" in payload and payload.get("approximate") is True)
        else:
            require(isinstance(payload.get("detail"), str))
        result.update(detector=True, geocoder="available" if status == 200 else "fallback")
    return result


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "api"
    if mode in {"probe", "probe-detector", "health"}:
        result = probe(detector=mode == "probe-detector")
        if mode != "health":
            print(json.dumps(result))
        return
    if mode not in {"api", "worker", "monitor"}:
        raise ValueError("Unsupported runtime role")
    check_revision()
    if mode == "api":
        import uvicorn
        from app.api import app

        uvicorn.run(Maintenance(app), host="0.0.0.0", port=8000, access_log=False)
    else:
        while marker() is not None:
            time.sleep(1)
        check_revision()
        runpy.run_module("app." + mode, run_name="__main__")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never emit user rows, URLs, SQL parameters or DB credentials into Actions.
        print("CD runtime check failed", file=sys.stderr)
        raise SystemExit(1)

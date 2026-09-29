"""Exercise a running deployment with a real image in a new SMOKE project."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Explicit isolated test deployment URL")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args()
    target = urlsplit(args.url)
    if target.hostname != "127.0.0.1" or target.scheme != "http" or target.port in (None, 80, 8000, 8080):
        parser.error("Use an isolated Compose deployment on a separate loopback port")
    image = args.image.read_bytes()
    base = args.url.rstrip("/") + "/api/v1"

    def request(path, data=None, *, content_type="application/json", raw=False):
        payload = json.dumps(data).encode() if isinstance(data, dict) else data
        headers = {"Content-Type": content_type, "Idempotency-Key": str(uuid4())}
        with urlopen(
            Request(base + path, data=payload, headers=headers), timeout=30
        ) as response:
            body = response.read()
        return body if raw else json.loads(body)

    def wait_for(path, predicate):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = request(path)
            if predicate(result):
                return result
            time.sleep(1)
        raise TimeoutError(f"Timed out waiting for {path}")

    health = request("/health")
    if health["status"] != "ok" or not health["model"]["ready"]:
        raise RuntimeError("Deployment/model is not ready")
    supported = health["model"]["supported_classes"]
    project = request(
        "/projects",
        {"name": "SMOKE - model deployment", "class_ids": [int(c) for c in supported]},
    )
    project_id = project["id"]
    now = datetime.now(timezone.utc)
    work_id = str(uuid4())
    request(
        f"/projects/{project_id}/plans",
        {
            "works": [
                {
                    "id": work_id,
                    "code": "12.1",
                    "title": "SMOKE - manual verification scenario",
                    "starts_at": (now - timedelta(hours=1)).isoformat(),
                    "ends_at": (now + timedelta(hours=1)).isoformat(),
                    "resources": {c: 0 for c in supported},
                }
            ]
        },
    )
    boundary = "stroy-smoke-" + uuid4().hex
    suffix = args.image.suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png"):
        raise ValueError("Use a JPEG or PNG verification image")
    payload = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="capture_start"\r\n\r\n{now.isoformat()}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="smoke{suffix}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + image
        + f"\r\n--{boundary}--\r\n".encode()
    )
    uploaded = request(
        f"/projects/{project_id}/media",
        payload,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    source_id = uploaded["source"]["id"]

    def completed_jobs(jobs):
        failed = [j for j in jobs if j["status"] in ("failed", "cancelled")]
        if failed:
            raise RuntimeError(f"Deployment job failed: {failed[0].get('error')}")
        return jobs and all(j["status"] == "succeeded" for j in jobs)

    wait_for(f"/projects/{project_id}/jobs", completed_jobs)
    request(
        f"/sources/{source_id}/bindings",
        {
            "regions": [
                {
                    "name": "SMOKE full frame",
                    "work_ids": [work_id],
                    "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                    "visibility_confirmed": True,
                }
            ]
        },
    )
    request(f"/sources/{source_id}/analyze", {})
    wait_for(f"/projects/{project_id}/jobs", completed_jobs)
    observations = request(f"/sources/{source_id}/observations")
    if len(observations) != 1 or not observations[0]["detections"]:
        raise RuntimeError(
            "Expected a real observation with detections; use an image containing equipment"
        )
    observation = observations[0]
    if observation["model"]["sha256"] != health["model"]["sha256"] or observation[
        "model"
    ].get("demo"):
        raise RuntimeError("Observation has unexpected model provenance")
    if (
        args.require_cuda
        and "CUDAExecutionProvider" not in observation["model"]["providers"]
    ):
        raise RuntimeError("Observation was not processed by the CUDA provider")
    wait_for(
        f"/projects/{project_id}/monitoring",
        lambda result: any(card.get("assessment") for card in result["cards"]),
    )
    if not request(f"/evidence/{observation['id']}", raw=True).startswith(b"\xff\xd8"):
        raise RuntimeError("Evidence is not a JPEG")
    if not request(f"/projects/{project_id}/reports/xlsx", raw=True).startswith(b"PK"):
        raise RuntimeError("XLSX report is invalid")
    print(
        json.dumps(
            {
                "status": "passed",
                "project_id": project_id,
                "source_id": source_id,
                "detections": len(observation["detections"]),
                "model": observation["model"]["name"],
                "sha256": observation["model"]["sha256"],
                "providers": observation["model"]["providers"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()

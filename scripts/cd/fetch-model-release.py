"""Fetch the approved private ONNX release asset for trusted main CI only."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import httpx

REPO = "OTSO-United/stroykontur-deployment"
API = f"https://api.github.com/repos/{REPO}"
ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "models" / "yolo26m" / "manifest.json"
MODEL = MANIFEST.with_name("best.onnx")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def approved_entry(manifest_bytes: bytes, catalog: dict) -> tuple[dict, dict]:
    manifest = json.loads(manifest_bytes)
    if catalog.get("schema_version") != 1:
        raise RuntimeError("Invalid approved model catalog")
    matches = [
        item
        for item in catalog.get("models", [])
        if item.get("manifest_sha256") == sha256(manifest_bytes)
    ]
    if len(matches) != 1:
        raise RuntimeError("Source model manifest is not uniquely approved")
    entry = matches[0]
    name = manifest.get("name", "")
    expected_tag = f"model-{name}"
    if (
        not re.fullmatch(r"yolo26m-equipment-v[1-9][0-9]*", name)
        or entry.get("name") != name
        or entry.get("directory_name") != name
        or manifest.get("weights") != "best.onnx"
        or entry.get("model_sha256") != manifest.get("sha256")
        or not re.fullmatch(r"[a-f0-9]{64}", manifest["sha256"])
        or entry.get("release_tag") != expected_tag
        or entry.get("release_asset") != "best.onnx"
    ):
        raise RuntimeError("Source model differs from approved private release")
    return manifest, entry


def main() -> None:
    token = os.environ.get("MODEL_RELEASE_TOKEN")
    if not token:
        raise RuntimeError("Private model release token is required")
    manifest_bytes = MANIFEST.read_bytes()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        response = client.get(f"{API}/contents/scripts/cd/approved-models.json?ref=main", headers=headers)
        response.raise_for_status()
        import base64

        encoded = response.json()["content"]
        catalog = json.loads(base64.b64decode(encoded))
        manifest, entry = approved_entry(manifest_bytes, catalog)
        response = client.get(f"{API}/releases/tags/{entry['release_tag']}", headers=headers)
        response.raise_for_status()
        release = response.json()
        if release.get("tag_name") != entry["release_tag"] or release.get("draft") or release.get("prerelease"):
            raise RuntimeError("Approved model release is not published")
        assets = [asset for asset in release.get("assets", []) if asset.get("name") == "best.onnx"]
        if len(assets) != 1 or assets[0].get("size", 0) < 1:
            raise RuntimeError("Approved model release asset is missing")
        asset_url = f"{API}/releases/assets/{assets[0]['id']}"
        if assets[0].get("url") != asset_url:
            raise RuntimeError("Unexpected model asset URL")
        binary_headers = {**headers, "Accept": "application/octet-stream"}
        handle = tempfile.NamedTemporaryFile(prefix=".best-", suffix=".onnx", dir=MODEL.parent, delete=False)
        temporary = Path(handle.name)
        digest = hashlib.sha256()
        try:
            with handle, client.stream("GET", asset_url, headers=binary_headers) as download:
                download.raise_for_status()
                for chunk in download.iter_bytes():
                    handle.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != entry["model_sha256"]:
                raise RuntimeError("Downloaded model SHA256 mismatch")
            temporary.replace(MODEL)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"Approved detector: {manifest['name']} {entry['model_sha256']}")


if __name__ == "__main__":
    main()

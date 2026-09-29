"""Contract checks for the private model release gate used by CI."""

import hashlib
import json
import runpy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/cd/fetch-model-release.py"
approved_entry = runpy.run_path(str(SCRIPT))["approved_entry"]


def package():
    manifest = {
        "name": "yolo26m-equipment-v4",
        "weights": "best.onnx",
        "sha256": "a" * 64,
    }
    encoded = json.dumps(manifest).encode()
    entry = {
        "name": manifest["name"],
        "directory_name": manifest["name"],
        "model_sha256": manifest["sha256"],
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
        "release_tag": "model-yolo26m-equipment-v4",
        "release_asset": "best.onnx",
    }
    return encoded, {"schema_version": 1, "models": [entry]}


def test_approved_release_requires_exact_manifest_and_coordinates():
    manifest, catalog = package()
    _, entry = approved_entry(manifest, catalog)
    assert entry["release_tag"] == "model-yolo26m-equipment-v4"

    with pytest.raises(RuntimeError, match="uniquely approved"):
        approved_entry(manifest + b"\n", catalog)

    catalog["models"][0]["release_tag"] = "model-yolo26m-equipment-v3"
    with pytest.raises(RuntimeError, match="differs"):
        approved_entry(manifest, catalog)

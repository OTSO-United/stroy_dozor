import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("url", [None, "http://127.0.0.1:8080", "http://127.0.0.1:8000", "https://example.org"])
def test_smoke_rejects_production_before_reading_image(url):
    script = Path(__file__).resolve().parents[2] / "scripts/smoke-deployment.py"
    command = [sys.executable, str(script), "--image", "nonexistent-test-image.jpg"]
    if url:
        command += ["--url", url]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "FileNotFoundError" not in result.stderr
    assert "--url" in result.stderr or "isolated Compose" in result.stderr

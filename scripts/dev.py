"""Local development supervisor; Docker Compose remains the primary runtime."""

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
env = {**os.environ, "PYTHONPATH": str(root / "backend")}
children = []
port = int(os.getenv("STROY_DEV_PORT", "8000"))

try:
    api = subprocess.Popen([sys.executable, "-m", "app.serve"], cwd=root, env=env)
    children.append(api)
    for _ in range(60):
        if api.poll() is not None:
            raise RuntimeError("API exited during startup")
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/v1/health", timeout=1
            ):
                break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError("API startup timed out")
    for module in ("app.worker", "app.monitor"):
        children.append(
            subprocess.Popen([sys.executable, "-m", module], cwd=root, env=env)
        )
    print(f"СтройДозор: http://127.0.0.1:{port} · Ctrl+C to stop", flush=True)
    while all(child.poll() is None for child in children):
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    for child in children:
        if child.poll() is None:
            child.terminate()
    for child in children:
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()

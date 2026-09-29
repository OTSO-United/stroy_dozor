"""Regenerate the implementation's request/route contract; no server required."""

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from app.api import app

(root / "contracts/openapi.json").write_text(
    json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

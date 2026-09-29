import os
from pathlib import Path

os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", "40000000")

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv("STROY_DATA_DIR", str(ROOT / "data"))).resolve()
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA / 'stroykontur.db'}")
CATALOG_PATH = ROOT / "seed" / "catalog.json"
MODEL_MANIFEST = os.getenv(
    "MODEL_MANIFEST", str(ROOT.parent / "models" / "yolo26m" / "manifest.json")
)
REQUIRE_CUDA = os.getenv("REQUIRE_CUDA", "0") == "1"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(2 * 1024**3)))
WORKER_THREADS = int(os.getenv("WORKER_THREADS", "20"))
LEASE_SECONDS = 120


def prepare_storage():
    for folder in ("media", "previews", "evidence", "reports"):
        (DATA / folder).mkdir(parents=True, exist_ok=True)

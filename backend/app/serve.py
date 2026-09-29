import subprocess
import sys
import os
import uvicorn
from .config import ROOT

if __name__ == "__main__":
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ROOT / "alembic.ini"),
            "upgrade",
            "head",
        ],
        check=True,
    )
    uvicorn.run(
        "app.api:app",
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("STROY_DEV_PORT", "8000")),
    )

"""Create the reproducible synthetic object used for manual web UI checks."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import DATA  # noqa: E402
from app.db import Session  # noqa: E402
from app.demo import create_demo_project  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Создать тестовый объект с псевдослучайной заглушкой детекций"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260918,
        help="seed генератора; повтор того же seed безопасен и не создаёт дубликат",
    )
    args = parser.parse_args()
    with Session() as db:
        result = create_demo_project(db, DATA, seed=args.seed)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

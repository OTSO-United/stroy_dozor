from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--replacement", type=Path, required=True)
    parser.add_argument("--dataset-key", required=True)
    args = parser.parse_args()

    base = json.loads(args.base.read_text(encoding="utf-8"))
    replacement = json.loads(args.replacement.read_text(encoding="utf-8"))
    replacement_rows = {
        row["key"]: row for row in replacement["datasets"]
    }
    if args.dataset_key not in replacement_rows:
        raise KeyError(f"Replacement does not contain {args.dataset_key}")
    found = False
    for index, row in enumerate(base["datasets"]):
        if row["key"] == args.dataset_key:
            base["datasets"][index] = replacement_rows[args.dataset_key]
            found = True
            break
    if not found:
        base["datasets"].append(replacement_rows[args.dataset_key])
    args.base.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

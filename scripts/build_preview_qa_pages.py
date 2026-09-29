from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sample_train_class_bboxes as renderer  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview-root", required=True, type=Path)
    parser.add_argument("--page-size", type=int, default=20)
    args = parser.parse_args()

    for class_dir in sorted(path for path in args.preview_root.iterdir() if path.is_dir()):
        try:
            class_id = int(class_dir.name.split("_", 1)[0])
        except ValueError:
            continue
        annotated = sorted((class_dir / "annotated").glob("*"))
        page_dir = class_dir / "qa_pages"
        page_dir.mkdir(parents=True, exist_ok=True)
        for offset in range(0, len(annotated), args.page_size):
            page = annotated[offset : offset + args.page_size]
            renderer.make_contact_sheet(
                page,
                class_id,
                page_dir / f"page_{offset // args.page_size + 1:02d}.jpg",
            )


if __name__ == "__main__":
    main()

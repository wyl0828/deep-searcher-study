from __future__ import annotations

import sys
from pathlib import Path

import pdfplumber


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    try:
        with pdfplumber.open(Path(sys.argv[1])) as document:
            page_count = len(document.pages)
    except Exception:
        return 3
    sys.stdout.write(str(page_count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

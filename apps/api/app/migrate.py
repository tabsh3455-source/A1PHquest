from __future__ import annotations

import os
import sys

from .main import _run_alembic_upgrade


def main() -> int:
    revision = sys.argv[1] if len(sys.argv) > 1 else os.getenv("TARGET_REVISION", "head")
    _run_alembic_upgrade(revision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

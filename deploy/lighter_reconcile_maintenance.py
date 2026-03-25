#!/usr/bin/env python3
"""Wrapper entrypoint for Lighter reconcile maintenance.

This wrapper keeps operator command stable under `deploy/` while executing the
shared implementation from `app.tools.lighter_reconcile_maintenance`.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "apps" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.tools.lighter_reconcile_maintenance import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Check strategy runtime consistency through API endpoint."""

from __future__ import annotations

import argparse
import json
from urllib import error, request


def _get_json(base_url: str, path: str, token: str) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {token}"}
    req = request.Request(f"{base_url}{path}", headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        return exc.code, json.loads(raw) if raw else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check runtime consistency for one strategy.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--strategy-id", required=True, type=int)
    args = parser.parse_args()

    status, data = _get_json(
        args.base_url,
        f"/api/strategies/{args.strategy_id}/runtime/consistency",
        token=args.token,
    )
    print(json.dumps({"status": status, "data": data}, ensure_ascii=False))
    return 0 if status == 200 and data.get("consistent") else 1


if __name__ == "__main__":
    raise SystemExit(main())

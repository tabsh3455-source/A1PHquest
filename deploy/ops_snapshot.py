#!/usr/bin/env python3
"""Collect minimal operational snapshot for on-call diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import subprocess
from urllib import error, request


def _probe(url: str) -> dict:
    try:
        with request.urlopen(url, timeout=3) as response:
            body = response.read().decode("utf-8")
            return {"url": url, "ok": response.status == 200, "status": response.status, "body": body}
    except error.HTTPError as exc:
        return {"url": url, "ok": False, "status": exc.code, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"url": url, "ok": False, "status": None, "error": str(exc)}


def _probe_ops_metrics(base_url: str, bearer_token: str) -> dict:
    url = f"{base_url.rstrip('/')}/api/ops/metrics"
    req = request.Request(url, headers={"Authorization": f"Bearer {bearer_token}"})
    try:
        with request.urlopen(req, timeout=5) as response:
            body = response.read().decode("utf-8")
            return {"url": url, "ok": response.status == 200, "status": response.status, "body": json.loads(body)}
    except error.HTTPError as exc:
        return {"url": url, "ok": False, "status": exc.code, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"url": url, "ok": False, "status": None, "error": str(exc)}


def _docker_stats() -> dict:
    cmd = [
        "docker",
        "stats",
        "--no-stream",
        "--format",
        "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}|{{.BlockIO}}|{{.PIDs}}",
    ]
    try:
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"ok": False, "rows": [], "error": str(exc)}

    rows: list[dict] = []
    for line in output.strip().splitlines():
        name, cpu, mem, net_io, block_io, pids = line.split("|")
        rows.append(
            {
                "name": name,
                "cpu": cpu,
                "memory": mem,
                "net_io": net_io,
                "block_io": block_io,
                "pids": pids,
            }
        )
    return {"ok": True, "rows": rows}


def main() -> int:
    api_base = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
    ops_token = os.getenv("OPS_BEARER_TOKEN", "")
    ops_metrics = (
        _probe_ops_metrics(api_base, ops_token)
        if ops_token
        else {"url": f"{api_base.rstrip('/')}/api/ops/metrics", "ok": False, "status": None, "error": "missing OPS_BEARER_TOKEN"}
    )
    snapshot = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "health": [
            _probe("http://127.0.0.1:8000/healthz"),
            _probe("http://127.0.0.1:8010/healthz"),
        ],
        "ops_metrics": ops_metrics,
        "docker_stats": _docker_stats(),
    }
    print(json.dumps(snapshot, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

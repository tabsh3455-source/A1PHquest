#!/usr/bin/env python3
"""Continuous health monitor for API and worker-supervisor endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
from urllib import error, request
import argparse


@dataclass(slots=True)
class ProbeResult:
    url: str
    ok: bool
    status_code: int | None
    error_message: str | None


def probe(url: str, timeout: int = 3, retries: int = 2, retry_delay_seconds: float = 0.3) -> ProbeResult:
    """
    Probe health endpoint with lightweight retry to reduce transient timeout flakiness.
    """
    attempts = max(int(retries), 1)
    last_result: ProbeResult | None = None
    for attempt in range(attempts):
        try:
            with request.urlopen(url, timeout=timeout) as response:
                return ProbeResult(url=url, ok=response.status == 200, status_code=response.status, error_message=None)
        except error.HTTPError as exc:
            last_result = ProbeResult(url=url, ok=False, status_code=exc.code, error_message=f"http_error:{exc.code}")
        except Exception as exc:  # pragma: no cover - network dependent
            last_result = ProbeResult(url=url, ok=False, status_code=None, error_message=str(exc))
        if attempt < attempts - 1:
            time.sleep(max(float(retry_delay_seconds), 0))
    return last_result or ProbeResult(url=url, ok=False, status_code=None, error_message="probe_failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor health endpoints for a fixed duration.")
    parser.add_argument(
        "--urls",
        nargs="+",
        default=["http://127.0.0.1:8000/healthz", "http://127.0.0.1:8010/healthz"],
    )
    parser.add_argument("--duration-seconds", type=int, default=600)
    parser.add_argument("--interval-seconds", type=int, default=5)
    parser.add_argument("--probe-timeout-seconds", type=int, default=3)
    parser.add_argument("--probe-retries", type=int, default=2)
    parser.add_argument("--probe-retry-delay-seconds", type=float, default=0.3)
    parser.add_argument(
        "--grace-seconds",
        type=int,
        default=20,
        help="Ignore probe failures during initial startup grace window.",
    )
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc).isoformat()
    grace_deadline = time.time() + max(args.grace_seconds, 0)
    deadline = time.time() + max(args.duration_seconds, 1)
    totals: dict[str, int] = {url: 0 for url in args.urls}
    failures: dict[str, int] = {url: 0 for url in args.urls}
    samples: list[dict] = []

    while time.time() < deadline:
        for url in args.urls:
            result = probe(
                url=url,
                timeout=args.probe_timeout_seconds,
                retries=args.probe_retries,
                retry_delay_seconds=args.probe_retry_delay_seconds,
            )
            totals[url] += 1
            in_grace = time.time() < grace_deadline
            if not result.ok and not in_grace:
                failures[url] += 1
            samples.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "url": result.url,
                    "ok": result.ok,
                    "status_code": result.status_code,
                    "error": result.error_message,
                    "ignored_in_grace": in_grace and not result.ok,
                }
            )
        time.sleep(max(args.interval_seconds, 1))

    summary = {
        "started_at": started_at,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": args.duration_seconds,
        "grace_seconds": args.grace_seconds,
        "totals": totals,
        "failures": failures,
        "all_healthy": all(failures[url] == 0 for url in args.urls),
        # Keep recent samples concise in CLI output.
        "recent_samples": samples[-10:],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["all_healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

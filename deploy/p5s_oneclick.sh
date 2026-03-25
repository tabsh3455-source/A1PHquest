#!/usr/bin/env bash
set -euo pipefail

# P5-S one-click pipeline:
# 1) start required services in Linux Docker engine
# 2) verify health endpoints continuously
# 3) run end-to-end runtime flow test

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HEALTH_SECONDS="${HEALTH_SECONDS:-600}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-5}"
HEALTH_GRACE_SECONDS="${HEALTH_GRACE_SECONDS:-20}"
HEALTH_PROBE_TIMEOUT_SECONDS="${HEALTH_PROBE_TIMEOUT_SECONDS:-3}"
HEALTH_PROBE_RETRIES="${HEALTH_PROBE_RETRIES:-3}"
HEALTH_PROBE_RETRY_DELAY_SECONDS="${HEALTH_PROBE_RETRY_DELAY_SECONDS:-0.5}"
HEALTH_CURL_CONNECT_TIMEOUT_SECONDS="${HEALTH_CURL_CONNECT_TIMEOUT_SECONDS:-2}"
HEALTH_CURL_MAX_TIME_SECONDS="${HEALTH_CURL_MAX_TIME_SECONDS:-3}"
API_BASE="${API_BASE:-http://127.0.0.1:8000}"
EXCHANGE="${EXCHANGE:-binance}"
EXCHANGES="${EXCHANGES:-$EXCHANGE}"
BUILD_IMAGES="${BUILD_IMAGES:-0}"

health_ready() {
  local url="$1"
  curl --connect-timeout "$HEALTH_CURL_CONNECT_TIMEOUT_SECONDS" --max-time "$HEALTH_CURL_MAX_TIME_SECONDS" -fsS "$url" >/dev/null 2>&1
}

echo "[1/3] Starting postgres + worker-supervisor + migrate + api..."
BUILD_IMAGES="$BUILD_IMAGES" bash deploy/stack.sh up postgres worker-supervisor api

echo "[2/3] Waiting for initial readiness..."
for i in $(seq 1 120); do
  if health_ready "${API_BASE}/healthz" && health_ready "http://127.0.0.1:8010/healthz"; then
    echo "Services are reachable."
    break
  fi
  sleep 1
  if [ "$i" -eq 120 ]; then
    echo "Timed out waiting for health endpoints."
    exit 1
  fi
done

echo "[2/3] Running health monitor for ${HEALTH_SECONDS}s..."
python3 deploy/health_monitor.py \
  --duration-seconds "$HEALTH_SECONDS" \
  --interval-seconds "$HEALTH_INTERVAL" \
  --grace-seconds "$HEALTH_GRACE_SECONDS" \
  --probe-timeout-seconds "$HEALTH_PROBE_TIMEOUT_SECONDS" \
  --probe-retries "$HEALTH_PROBE_RETRIES" \
  --probe-retry-delay-seconds "$HEALTH_PROBE_RETRY_DELAY_SECONDS" \
  --urls "${API_BASE}/healthz" "http://127.0.0.1:8010/healthz"

echo "[3/3] Running E2E runtime flow (${EXCHANGES})..."
for exchange in $EXCHANGES; do
  echo " - exchange: ${exchange}"
  python3 deploy/e2e_runtime_flow.py --base-url "$API_BASE" --exchange "$exchange"
done

echo "P5-S pipeline completed."

# A1phquest Handoff

## 1. Project Summary

- Project name: `A1phquest`
- Goal: production-grade quantitative trading platform based on `vn.py`
- Main live exchanges: `Binance + OKX`
- Additional exchange: `Lighter` in client-signature passthrough mode only
- Explicitly excluded: `Coinbase`
- Production target: `Linux VPS Docker Engine`
- Security baseline:
  - local `AES` master key
  - `Google Authenticator` login verification
  - step-up token required for high-risk operations

## 2. Fixed Constraints

- Do not add Coinbase-related code or plans.
- Do not introduce server-side private key custody for Lighter.
- Lighter must remain `tx_type + tx_info` passthrough.
- Live runtime is enabled for `grid`, `futures_grid`, `dca`, and `combo_grid_dca`.
- `custom` strategies may exist, but must not enter live start flow.
- New core logic must include comments and tests.
- Production deployment target is a Linux VPS, not WSL.

## 3. Current Architecture

- Frontend: `Vue 3 + Element Plus`
- API backend: `FastAPI + SQLAlchemy + PostgreSQL`
- Strategy runtime: `worker-supervisor` + `vn.py CTA`
- Reverse proxy: `nginx`
- Deployment: `Docker Compose`

Repo layout:

- `apps/api`: FastAPI backend
- `apps/worker-supervisor`: strategy runtime process manager
- `apps/frontend`: web UI
- `deploy`: compose, gate scripts, soak/release tooling
- `tests`: unit/integration regression coverage

## 4. Current Verified State

Latest verified baseline on `2026-03-26`:

- Targeted backend regression suite passed (`risk + strategy runtime + strategy control`): `28 passed`
- Frontend Docker production build passed
- CTA runtime start/stop flow remains working in Docker
- WS replay remains DB-backed capable (`memory` and `db` modes)
- Live runtime gate now fails closed when risk rule is not configured

Note:

- WSL was manually shut down after the latest validation, so local WSL services are not currently running.

## 5. Major Completed Work

### Runtime and strategy lifecycle

- Real `vn.py` runtime startup path is in place
- `worker-supervisor` now drives:
  - gateway connect
  - CTA engine bootstrap
  - strategy add/init/start/stop
- Fixed CTA startup bug:
  - `vt_symbol` suffix now uses CTA-compatible exchange suffix
  - async `init_strategy()` future is explicitly awaited before `start_strategy()`

### Security

- `logout` now invalidates prior access/step-up tokens through `token_version`
- step-up token cannot be reused as a normal access token
- proxy header trust is disabled by default and nginx forwarding was hardened
- risk evaluation no longer trusts client-supplied position ratio for live orders
- live order submit and live strategy start now fail closed when no risk rule exists

### Exchange and data consistency

- Binance initial trade cursor bootstrap improved
- historical order deletion bug in sync path was fixed
- Lighter reconcile behavior was tightened to avoid ambiguous false matches

### WebSocket and replay

- unified event envelope is in place
- `event_seq` ordering is user-scoped
- replay endpoint exists at `/api/events/replay`
- DB-backed replay store added:
  - `user_event_sequences`
  - `user_events`

## 6. Important Files

Core runtime:

- [runtime.py](C:/Users/Administrator/Documents/New%20project%202/apps/worker-supervisor/supervisor/runtime.py)
- [main.py](C:/Users/Administrator/Documents/New%20project%202/apps/worker-supervisor/supervisor/main.py)

API runtime orchestration:

- [strategies.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/app/routers/strategies.py)
- [strategy_supervisor.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/app/services/strategy_supervisor.py)

Eventing and replay:

- [ws_manager.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/app/ws_manager.py)
- [events.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/app/routers/events.py)
- [events.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/app/events.py)

Security:

- [auth.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/app/routers/auth.py)
- [security.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/app/security.py)
- [deps.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/app/deps.py)

Exchange/order/risk:

- [exchange_accounts.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/app/routers/exchange_accounts.py)
- [orders.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/app/routers/orders.py)
- [gateway_service.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/app/services/gateway_service.py)
- [risk_service.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/app/services/risk_service.py)

Deploy and gates:

- [docker-compose.yml](C:/Users/Administrator/Documents/New%20project%202/deploy/docker-compose.yml)
- [db_migrate.sh](C:/Users/Administrator/Documents/New%20project%202/deploy/db_migrate.sh)
- [e2e_runtime_flow.py](C:/Users/Administrator/Documents/New%20project%202/deploy/e2e_runtime_flow.py)
- [release_gate.sh](C:/Users/Administrator/Documents/New%20project%202/deploy/release_gate.sh)
- [.env.template](C:/Users/Administrator/Documents/New%20project%202/.env.template)

Recent migrations:

- [20260323_0002_strategy_runtime_observability.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/migrations/versions/20260323_0002_strategy_runtime_observability.py)
- [20260324_0003_user_token_version.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/migrations/versions/20260324_0003_user_token_version.py)
- [20260324_0004_user_event_replay_store.py](C:/Users/Administrator/Documents/New%20project%202/apps/api/migrations/versions/20260324_0004_user_event_replay_store.py)

## 7. How To Run

### Local / WSL validation

Recommended gate commands:

```bash
pytest -q
python -m bandit -q -r apps/api apps/worker-supervisor
bash deploy/db_migrate.sh
python3 deploy/e2e_runtime_flow.py --base-url http://127.0.0.1:8000 --exchange okx
```

Docker stack:

```bash
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
```

## 8. Secrets Handling

- Do not hand off the real `.env` in plain text unless absolutely required.
- Hand off [.env.template](C:/Users/Administrator/Documents/New%20project%202/.env.template) instead.
- Real secrets should be delivered separately:
  - `JWT_SECRET`
  - `AES_MASTER_KEY`
  - `SUPERVISOR_SHARED_TOKEN`
  - database credentials
  - exchange credentials
  - telegram / smtp credentials

## 9. Current Recommended Next Steps

Priority order:

1. Continue `P19/P20` style operational hardening
2. Expand ops/read-only observability around replay backlog and runtime drift
3. Finalize release/runbook workflows for Linux VPS deployment
4. Keep replay/event-order behavior verified in Docker gates
5. Continue Lighter reconciliation hardening without changing signing boundaries

## 10. Known Operational Notes

- `WS_REPLAY_BACKEND=db` is now the recommended production setting.
- `WS_REPLAY_BACKEND=memory` is only safe with `API_REPLICA_COUNT=1`.
- Docker image pulls may intermittently fail because of external network access to Docker Hub. Re-run before treating it as a code failure.
- The current repo has many deploy artifacts and evidence files in `deploy/`; do not confuse those with source-of-truth runtime code.

## 11. Suggested Prompt For The Next Codex

Use this prompt as the starting handoff message:

```text
This repository is A1phquest, a vn.py-based quantitative trading platform.

Fixed constraints:
1. Production target is Linux VPS Docker Engine
2. Binance and OKX are the live exchanges
3. Lighter remains client-signature passthrough only (tx_type + tx_info)
4. Coinbase is permanently excluded
5. Security is local AES master key + Google Authenticator + step-up token
6. Live runtime is enabled for grid, futures_grid, dca, and combo_grid_dca
7. New core logic must include comments and tests

Current verified state:
1. pytest -q is green
2. bandit is green
3. db_migrate.sh passed
4. Docker runtime e2e has passed
5. CTA lifecycle start/stop is working
6. WS replay now supports DB backend

Read these files first:
- HANDOFF.md
- deploy/docker-compose.yml
- deploy/db_migrate.sh
- deploy/e2e_runtime_flow.py
- apps/api/app/ws_manager.py
- apps/api/app/routers/strategies.py
- apps/worker-supervisor/supervisor/runtime.py

Then continue the next planned operational hardening work without rebuilding the scaffold from scratch.
```

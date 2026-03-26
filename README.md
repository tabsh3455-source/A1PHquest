# A1phquest

A1phquest is a monorepo implementation based on the VeighNa(vn.py) ecosystem for multi-user crypto quant trading.

## What Is Implemented

- Monorepo structure for API, worker supervisor, frontend, packages, deployment and docs
- FastAPI backend with:
  - JWT authentication
  - Login-time Google Authenticator (TOTP) verification
  - High-risk endpoint guard requiring step-up token (`X-StepUp-Token`)
  - Multi-tenant data model (`user_id` enforced in core tables)
  - Exchange account storage with local AES master-key encryption
  - Strategy lifecycle endpoints
  - Risk rule endpoints (fail-closed gate for live runtime)
  - Tenant-scoped WebSocket endpoint
  - Audit trail recording
- Worker supervisor service skeleton for per-user process lifecycle
- Crypto domain package for funding/mark-price/leveraged context modeling
- Security package for local encryption and TOTP utility
- Docker Compose baseline (api, worker-supervisor, postgres, frontend, nginx, backup)
- Basic tests for auth and tenant isolation contract

## Repository Layout

```text
apps/
  api/
  worker-supervisor/
  frontend/
packages/
  a1phquest_compat/
  a1phquest_crypto/
  a1phquest_security/
deploy/
docs/
tests/
```

## Quick Start (One Command)

On a Linux host, run:

```bash
bash install.sh
```

The installer will:

- Install Docker Engine and the Docker Compose plugin if they are missing
- Generate a production `.env` automatically when one does not exist
- Start the core Docker stack (`postgres`, `migrate`, `worker-supervisor`, `api`, `frontend`, `backup`) and verify health checks

By default, frontend and backend stay on separate ports, and `nginx` is not required on the first deployment. The app will come up on:

- Frontend: `http://127.0.0.1:5173/`
- Backend API: `http://127.0.0.1:8000/`
- API docs: `http://127.0.0.1:8000/docs`

Open the login page after deployment and use the built-in `Register` flow to create your first user. Account data is stored in PostgreSQL, not in `.env`.

When you are ready to add `nginx` and HTTPS later, rerun:

```bash
DEPLOY_NGINX=1 bash install.sh
```

That second pass will generate a self-signed TLS certificate automatically when one does not exist and expose:

- Frontend: `https://127.0.0.1/`
- API docs: `https://127.0.0.1/docs`

## Deploy Directly From GitHub

On a Linux host, bootstrap directly from GitHub in one command:

```bash
curl -fsSL https://raw.githubusercontent.com/tabsh3455-source/A1PHquest/main/bootstrap-from-github.sh | bash -s -- --repo https://github.com/tabsh3455-source/A1PHquest.git
```

For private repositories, configure a server-side deploy key and use a single clone-and-install command instead:

```bash
git clone --branch main git@github.com:<OWNER>/<REPO>.git /opt/a1phquest && cd /opt/a1phquest && bash install.sh
```

Optional flags:

- `--ref <branch-or-tag>`: deploy a specific branch or release tag
- `--dir <target-dir>`: choose where the repo will live on the server. Default: `/opt/a1phquest`

The bootstrap script will:

- install `git` automatically if the host is missing it
- clone or update the repository
- run `install.sh` for Docker install, TLS bootstrap, `.env` generation, migration, and stack startup

After first deployment, update to the latest GitHub `main` with:

```bash
cd /opt/a1phquest
bash deploy/update-from-github.sh
```

If you want upgrade + health/smoke verification in one step:

```bash
cd /opt/a1phquest
bash deploy/update-and-verify.sh
```

Optional environment flags during update:

- `BUILD_IMAGES=0 bash deploy/update-from-github.sh` (skip image rebuild)
- `DEPLOY_NGINX=1 bash deploy/update-from-github.sh` (apply update and enable nginx/HTTPS)
- `RUN_SMOKE_TEST=0 bash deploy/update-and-verify.sh` (skip API smoke test)

If you already manage Docker and `.env` yourself, you can still use the manual path:

1. Generate and validate `.env` automatically:
   - `bash install.sh`
2. If you want manual compose control after that, always include `.env` explicitly:
   - `docker compose --env-file .env -f deploy/docker-compose.yml up -d --build`
3. Or use wrapper:
   - `bash deploy/stack.sh up postgres migrate worker-supervisor api frontend backup`

### Common Deployment Errors

- `POSTGRES_USER is required` / `POSTGRES_DB is required`
  - Cause: `.env` is missing or compose was started without loading `.env`.
  - Fix: `bash install.sh` (first-time) or use `docker compose --env-file .env ...`.
- `workflow readiness ... 404`
  - Cause: host is running old API code/image.
  - Fix: `cd /opt/a1phquest && bash deploy/update-from-github.sh` then rerun smoke.

## Notes

- `KMS_MODE=local_aes` enables local AES-GCM encryption with `AES_MASTER_KEY`.
- Login validates Google Authenticator code when user has 2FA configured.
- Binance/OKX/Lighter integration points are provided through service abstraction; direct exchange wiring can be extended incrementally without changing API contracts.
- Lighter order submit/cancel follows signed-transaction flow (`tx_type`, `tx_info`) via `exchange_payload`.
- WS event payloads now include normalized envelope: `type/timestamp/resource_id/payload`.
- WS replay endpoint is available at `GET /api/events/replay`.
- Ops metrics endpoint is available at `GET /api/ops/metrics`.
- Workflow readiness endpoint is available at `GET /api/workflow/readiness` for one-glance setup blockers and next actions.
- Market data runtime settings endpoint is available at `GET/PUT/DELETE /api/system-config/market-data`.
- Frontend settings page lets you tune low-latency market data behavior interactively without hand-editing `.env`.
- Frontend settings page also includes risk guardrail configuration; live starts stay blocked until a risk rule is saved.
- Lighter reconcile backlog endpoint is available at `GET /api/exchange-accounts/{id}/lighter-reconcile/pending`.
- Lighter reconcile backlog response includes retry window stats/hints (`retry_due_count`, `retry_blocked_count`, `next_retry_at`).
- Lighter reconcile sync errors apply cooldown backoff before next error-count increment.
- Daily-loss risk checks are computed from persisted trade fills on the server side (realized PnL).
- Live order submit and live strategy start are fail-closed when no risk rule exists.
- Lighter trade sync includes best-effort pagination (`next_cursor` or has-more fallback) with duplicate-page protection.
- Worker runtime strategies (`grid/futures_grid/dca/combo_grid_dca`) now use concrete CTA order hooks instead of no-op callbacks.
- API startup now applies Alembic migrations to `head` automatically.
- PostgreSQL migration lock (`pg_advisory_lock`) is used to avoid concurrent upgrade races.
- Worker runtime detects heartbeat timeout and marks runtime failed with process cleanup.
- Docker Compose now runs one-shot `migrate` service before API startup.
- Operations runbook is available in `docs/runbook.md`.

## Standard Trading Loop

The recommended setup and execution order is:

1. Register and complete Google Authenticator enrollment
2. Add exchange account
3. Configure risk rule (live fail-closed gate)
4. Create strategy from template
5. Issue step-up token and start strategy runtime
6. Configure AI provider/policy and run dry-run (then optionally enable auto mode)

## Linux VPS Stack Control

- Start core services: `bash deploy/stack.sh up postgres worker-supervisor api`
- Check status: `bash deploy/stack.sh ps`
- One-click P5-S verification: `bash deploy/p5s_oneclick.sh`
- One-click P5-S verification (Binance + OKX): `EXCHANGES="binance okx" bash deploy/p5s_oneclick.sh`
- Manual DB migration: `bash deploy/db_migrate.sh`
- Release candidate full gate: `bash deploy/release_gate.sh`
- Release gate without soak smoke: `RUN_SOAK_SMOKE=0 bash deploy/release_gate.sh`
- P16 long-run soak: `bash deploy/p16_soak.sh`
- P16 background soak controller: `bash deploy/p16_soak_background.sh start|status|logs|stop`
- P16 soak finalize helper: `bash deploy/p16_soak_background.sh finalize`
- P16 auto-finalize watcher controls: `bash deploy/p16_soak_background.sh watch-start|watch-status|watch-stop`
- P16 soak live progress file: `cat deploy/p16_soak_progress.json`
- P16 soak threshold analysis: `python3 deploy/p16_soak_analyze.py --health-report deploy/p16_soak_health.json --stats-log deploy/p16_soak_stats.log --report deploy/p16_soak_report.json`
- P16 soak evidence archive: `ARCHIVE_EVIDENCE=1 EVIDENCE_LABEL=vps-nightly bash deploy/p16_soak.sh`
- P16 acceptance report: `python3 deploy/p16_acceptance_report.py --soak-report deploy/p16_soak_report.json --evidence-dir deploy/evidence/p16 --output deploy/p16_acceptance_report.json`
- P16 finalize pipeline: `python3 deploy/p16_finalize_after_soak.py`
- P16 auto-finalize watcher: `nohup bash deploy/p16_auto_finalize_watch.sh >/dev/null 2>&1 &`
- Lighter reconcile maintenance dry-run: `python3 deploy/lighter_reconcile_maintenance.py --dry-run --include-unchanged`
- Lighter reconcile maintenance execute: `python3 deploy/lighter_reconcile_maintenance.py`
- Lighter reconcile maintenance via API container: `docker compose -f deploy/docker-compose.yml exec -T api python -m app.tools.lighter_reconcile_maintenance --dry-run --include-unchanged`
- P20 RC gate: `bash deploy/p20_rc_gate.sh`
- P20 RC gate strict checklist: `P20_CHECKLIST_STRICT=1 bash deploy/p20_rc_gate.sh`
- P20 release checklist: `python3 deploy/p20_release_checklist.py --strict`
- P20 release manifest: `python3 deploy/p20_release_manifest.py`
- Release documentation consistency check: verify `README.md`, `HANDOFF.md`, and `docs/api.md` against the current shipped API/runtime behavior before production approval.
- Legacy compatibility wrapper: `bash deploy/stack_wsl.sh ...` (internally redirects to `deploy/stack.sh`)

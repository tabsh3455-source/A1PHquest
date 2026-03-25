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
  - Risk rule endpoints
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
- Generate a self-signed TLS certificate when one does not exist
- Prompt for bootstrap admin username, email, and password during interactive installs
- Fall back to auto-generated bootstrap admin credentials for unattended installs
- Start the full Docker stack and verify health checks

For unattended deployments, you can preseed the first admin account yourself:

```bash
BOOTSTRAP_ADMIN_USERNAME=admin \
BOOTSTRAP_ADMIN_EMAIL=admin@example.com \
BOOTSTRAP_ADMIN_PASSWORD='replace-with-a-strong-password' \
bash install.sh
```

After install:

- Frontend: `https://127.0.0.1/`
- API docs: `https://127.0.0.1/docs`

## Deploy Directly From GitHub

Once this repository is published to GitHub, a Linux host can bootstrap from the repo in one command for public repositories:

```bash
curl -fsSL https://raw.githubusercontent.com/<OWNER>/<REPO>/main/bootstrap-from-github.sh | bash -s -- --repo https://github.com/<OWNER>/<REPO>.git
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
- run the existing `install.sh` for Docker install, TLS bootstrap, `.env` generation, and stack startup

If you already manage Docker and `.env` yourself, you can still use the manual path:

1. Copy `.env.template` to `.env` and fill sensitive values.
   Runtime market-data tuning can be changed later from the web UI, so `.env` mainly stays as deployment defaults and secrets.
2. Start stack:
   - `docker compose -f deploy/docker-compose.yml up --build -d`

## Notes

- `KMS_MODE=local_aes` enables local AES-GCM encryption with `AES_MASTER_KEY`.
- Login validates Google Authenticator code when user has 2FA configured.
- Binance/OKX/Lighter integration points are provided through service abstraction; direct exchange wiring can be extended incrementally without changing API contracts.
- Lighter order submit/cancel follows signed-transaction flow (`tx_type`, `tx_info`) via `exchange_payload`.
- WS event payloads now include normalized envelope: `type/timestamp/resource_id/payload`.
- WS replay endpoint is available at `GET /api/events/replay`.
- Ops metrics endpoint is available at `GET /api/ops/metrics`.
- Market data runtime settings endpoint is available at `GET/PUT/DELETE /api/system-config/market-data`.
- Frontend settings page lets you tune low-latency market data behavior interactively without hand-editing `.env`.
- Lighter reconcile backlog endpoint is available at `GET /api/exchange-accounts/{id}/lighter-reconcile/pending`.
- Lighter reconcile backlog response includes retry window stats/hints (`retry_due_count`, `retry_blocked_count`, `next_retry_at`).
- Lighter reconcile sync errors apply cooldown backoff before next error-count increment.
- Daily-loss risk checks are computed from persisted trade fills on the server side (realized PnL).
- Lighter trade sync includes best-effort pagination (`next_cursor` or has-more fallback) with duplicate-page protection.
- Worker runtime strategies (`grid/dca`) now use concrete CTA order hooks instead of no-op callbacks.
- API startup now applies Alembic migrations to `head` automatically.
- PostgreSQL migration lock (`pg_advisory_lock`) is used to avoid concurrent upgrade races.
- Worker runtime detects heartbeat timeout and marks runtime failed with process cleanup.
- Docker Compose now runs one-shot `migrate` service before API startup.
- Operations runbook is available in `docs/runbook.md`.

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
- Legacy compatibility wrapper: `bash deploy/stack_wsl.sh ...` (internally redirects to `deploy/stack.sh`)

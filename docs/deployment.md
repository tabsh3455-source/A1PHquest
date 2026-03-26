# Deployment Guide

## Prerequisites

- Docker Engine + Docker Compose
- Linux VM recommended (Ubuntu 22.04+)
- Recommended target: cloud VPS (Linux Docker Engine).

## First Deploy (Recommended)

1. Clone repository to Linux host:
   - `git clone --branch main https://github.com/tabsh3455-source/A1PHquest.git /opt/a1phquest`
2. Run one-command installer:
   - `cd /opt/a1phquest`
   - `bash install.sh`
3. Installer behavior:
   - installs Docker/Compose when missing
   - auto-generates secure `.env` when missing or invalid
   - runs `migrate` one-shot before `api`
   - starts `postgres`, `worker-supervisor`, `api`, `frontend`, `backup` (and optional `nginx`)
4. Validate health:
   - `curl http://localhost:8000/healthz`
   - `curl http://localhost:8010/healthz`
5. Optional manual migration:
   - `bash deploy/db_migrate.sh`
6. Trigger exchange sync (after binding account):
   - `POST /api/exchange-accounts/{id}/sync`
   - High-risk routes require step-up token header:
     - `POST /api/auth/2fa/step-up` then set `X-StepUp-Token: <token>`

## GitHub One-Command Bootstrap

```bash
curl -fsSL https://raw.githubusercontent.com/tabsh3455-source/A1PHquest/main/bootstrap-from-github.sh | bash -s -- --repo https://github.com/tabsh3455-source/A1PHquest.git
```

## Upgrade Existing Deployment

```bash
cd /opt/a1phquest
bash deploy/update-from-github.sh
```

Upgrade + verify (health + smoke) in one command:

```bash
cd /opt/a1phquest
bash deploy/update-and-verify.sh
```

Optional flags:

- `BUILD_IMAGES=0 bash deploy/update-from-github.sh`
- `DEPLOY_NGINX=1 bash deploy/update-from-github.sh`
- `RUN_SMOKE_TEST=0 bash deploy/update-and-verify.sh`

## Manual Compose Mode (Advanced)

Use this only after `.env` exists:

- `docker compose --env-file .env -f deploy/docker-compose.yml up -d --build`
- or wrapper: `bash deploy/stack.sh up postgres migrate worker-supervisor api frontend backup`

Without `--env-file .env`, compose variable interpolation can fail on required DB variables.

## Service Readiness

- Compose now uses health checks for `postgres`, `worker-supervisor`, and `api`.
- API startup includes database retry logic (`DB_STARTUP_MAX_RETRIES`, `DB_STARTUP_RETRY_SECONDS`).
- Schema versioning is managed by Alembic (`apps/api/migrations`, `apps/api/alembic.ini`).
- PostgreSQL migration lock is controlled by:
  - `MIGRATION_PG_ADVISORY_LOCK_ENABLED`
  - `MIGRATION_PG_ADVISORY_LOCK_KEY`
  - `MIGRATION_PG_ADVISORY_LOCK_TIMEOUT_SECONDS`
- Runtime consistency check API:
  - `GET /api/strategies/{id}/runtime/consistency`

## Common Failures

- `required variable POSTGRES_USER/POSTGRES_DB is missing`
  - Root cause: `.env` missing or not loaded by compose.
  - Fix: run `bash install.sh`, or add `--env-file .env` to compose command.
- `/api/workflow/readiness` returns `404`
  - Root cause: stale API image/code.
  - Fix: run `bash deploy/update-from-github.sh`, then rerun smoke tests.

## One-Click P5-S Verification

- Linux shell:
  - `bash deploy/p5s_oneclick.sh`
  - `BUILD_IMAGES=1 bash deploy/p5s_oneclick.sh` (force image rebuild)
  - `EXCHANGES="binance okx" bash deploy/p5s_oneclick.sh` (run both exchanges serially)
  - `HEALTH_CURL_CONNECT_TIMEOUT_SECONDS=2 HEALTH_CURL_MAX_TIME_SECONDS=3 bash deploy/p5s_oneclick.sh` (avoid readiness curl hangs)
  - `HEALTH_PROBE_RETRIES=5 HEALTH_PROBE_RETRY_DELAY_SECONDS=0.8 bash deploy/p5s_oneclick.sh` (health probe anti-flake tuning)
- Standalone health monitor:
  - `python3 deploy/health_monitor.py --duration-seconds 600 --urls http://127.0.0.1:8000/healthz http://127.0.0.1:8010/healthz`
- Standalone runtime E2E flow:
  - `python3 deploy/e2e_runtime_flow.py --base-url http://127.0.0.1:8000 --exchange binance`
  - `python3 deploy/e2e_runtime_flow.py --base-url http://127.0.0.1:8000 --exchange okx`
- Ops snapshot (health + docker stats):
  - `python3 deploy/ops_snapshot.py`

## Backups

- Backup job runs daily from `deploy/backup.sh`
- Encrypted dump files are kept for 30 days under `deploy/backups`
- Optional COS upload:
  - set `COS_BACKUP_BUCKET=cos://<bucket>/<prefix>`
  - ensure `coscli` exists in backup container/runtime
- Restore:
  - `bash deploy/restore_backup.sh <backup_file.sql.enc>`

## AES Master Key Rotation

- Precheck:
  - `OLD_AES_MASTER_KEY=old NEW_AES_MASTER_KEY=new python3 deploy/rotate_aes_master_key.py --mode precheck`
- Execute:
  - `OLD_AES_MASTER_KEY=old NEW_AES_MASTER_KEY=new python3 deploy/rotate_aes_master_key.py --mode execute`
- Rollback:
  - `OLD_AES_MASTER_KEY=new NEW_AES_MASTER_KEY=old python3 deploy/rotate_aes_master_key.py --mode rollback`
- Resume from checkpoint target after failure:
  - `OLD_AES_MASTER_KEY=old NEW_AES_MASTER_KEY=new python3 deploy/rotate_aes_master_key.py --mode execute --resume-from exchange_accounts.api_secret_encrypted`
- Audit trail:
  - Rotation writes `aes_rotation_started`, `aes_rotation_checkpoint`, `aes_rotation_failed`, and mode-final events (`aes_rotation_precheck|execute|rollback`) into `audit_events`.

## P9 Drill

- One-shot upgrade/rollback + backup/restore rehearsal:
  - `bash deploy/p9_drill.sh`
  - `DRILL_BUILD_IMAGES=0 bash deploy/p9_drill.sh` (skip image rebuild when validating rollback flow only)
  - `DRILL_ALLOW_BUILD_FALLBACK=1 bash deploy/p9_drill.sh` (auto-fallback to no-build recreate if Docker Hub/network is unstable)
- Drill report output:
  - `deploy/p9_drill_report.json`

## Release Gate

- Full release gate (tests + migrate + oneclick + e2e + drill):
  - `bash deploy/release_gate.sh`
- Registry/network fallback:
  - release gate retries one-click with `BUILD_IMAGES=0` when build pull fails (`GATE_ALLOW_BUILD_FALLBACK=1` by default).
- Disable soak-smoke substep if runtime budget is limited:
  - `RUN_SOAK_SMOKE=0 bash deploy/release_gate.sh`
- Gate report output:
  - `deploy/release_gate_report.json`
- Smoke soak artifacts use dedicated filenames (`p16_soak_smoke_*`) to avoid overwriting long-run 24h reports.

## P16 Soak

- Long-run soak monitor (default 24h):
  - `bash deploy/p16_soak.sh`
- Background controller (start/status/stop):
  - `bash deploy/p16_soak_background.sh start`
  - `bash deploy/p16_soak_background.sh status`
  - `bash deploy/p16_soak_background.sh logs`
  - `bash deploy/p16_soak_background.sh stop`
  - `bash deploy/p16_soak_background.sh finalize`
  - `bash deploy/p16_soak_background.sh watch-start`
  - `bash deploy/p16_soak_background.sh watch-status`
  - `bash deploy/p16_soak_background.sh watch-stop`
  - Watch heartbeat status: `deploy/p16_auto_finalize.status.json`
- Soak live progress snapshot:
  - `cat deploy/p16_soak_progress.json`
- Soak report output:
  - `deploy/p16_soak_report.json`
- Threshold analyzer can be run manually:
  - `python3 deploy/p16_soak_analyze.py --health-report deploy/p16_soak_health.json --stats-log deploy/p16_soak_stats.log --report deploy/p16_soak_report.json`
- Optional evidence archive (timestamp folder + checksums):
  - `ARCHIVE_EVIDENCE=1 EVIDENCE_LABEL=vps-nightly bash deploy/p16_soak.sh`
  - `python3 deploy/p16_archive_evidence.py --label vps-nightly`
- Final acceptance report (full soak + evidence checksum verification):
  - `python3 deploy/p16_acceptance_report.py --soak-report deploy/p16_soak_report.json --evidence-dir deploy/evidence/p16 --output deploy/p16_acceptance_report.json`
- Finalize helper (checks progress, picks latest evidence, generates acceptance, optional RC):
  - `python3 deploy/p16_finalize_after_soak.py`
  - Finalize pipeline now includes strict checklist and release manifest output.

## P20 RC Gate

- Composite RC gate (release gate + acceptance report):
  - `bash deploy/p20_rc_gate.sh`
- RC gate now auto-runs release checklist (`deploy/p20_release_checklist.py`) by default.
- Control checklist behavior:
  - `RUN_P20_CHECKLIST=0` (skip checklist substep)
  - `P20_CHECKLIST_STRICT=1` (fail gate when checklist is not release-ready)
- Pin RC acceptance to a specific evidence run:
  - `RUN_RELEASE_GATE=0 EVIDENCE_PATH=deploy/evidence/p16/<run_id> bash deploy/p20_rc_gate.sh`
- RC report output:
  - `deploy/p20_rc_report.json`
- Release checklist summary:
  - `python3 deploy/p20_release_checklist.py --strict`
  - Output: `deploy/p20_release_checklist.json`
- Release manifest summary:
  - `python3 deploy/p20_release_manifest.py`
  - Output: `deploy/p20_release_manifest.json`
- Production release approval rule:
  - Only when `python3 deploy/p20_release_checklist.py --strict` returns exit code `0`.
  - Non-strict checklist runs are rehearsal-only and cannot enter production window.

## P17-P20 Merged Gate

- Unified merged execution (security + Lighter + observability + RC):
  - `bash deploy/p17_p20_merged_gate.sh`
- Auto strict behavior:
  - `P20_CHECKLIST_STRICT_MODE=auto` enables strict only when `deploy/p16_soak_progress.json` reports `status=completed`.
  - Non-completed soak state runs non-strict checklist for rehearsal continuity.
- Force behavior:
  - `P20_CHECKLIST_STRICT_MODE=1 bash deploy/p17_p20_merged_gate.sh` (always strict)
  - `P20_CHECKLIST_STRICT_MODE=0 bash deploy/p17_p20_merged_gate.sh` (always non-strict)
- Output report:
  - `deploy/p17_p20_merged_report.json`
  - Includes stage-level status, strict decision reason, and release-readiness summary fields.

## Lighter Reconcile Maintenance (VPS Cron)

- Dry-run (no persistence):
  - `python3 deploy/lighter_reconcile_maintenance.py --dry-run --include-unchanged`
- Execute maintenance:
  - `python3 deploy/lighter_reconcile_maintenance.py`
- Docker compose path (recommended when host python lacks DB drivers):
  - `docker compose -f deploy/docker-compose.yml exec -T api python -m app.tools.lighter_reconcile_maintenance --dry-run --include-unchanged`
  - If module path is missing in an old container image, rebuild once:
    - `BUILD_IMAGES=1 bash deploy/p5s_oneclick.sh`
- Optional scope filters:
  - `python3 deploy/lighter_reconcile_maintenance.py --user-id <id> --account-id <id>`
- Output:
  - JSON summary with `expired_now_total`, `pruned_now_total`, and per-account status transitions.

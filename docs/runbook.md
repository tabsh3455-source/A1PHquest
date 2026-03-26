# Operations Runbook

## 1) Standard Start (Linux Docker Engine / VPS)

1. `cd /opt/a1phquest`
2. `bash install.sh`
   - installer auto-generates `.env` when missing/invalid.
   - `migrate` one-shot service executes before API becomes healthy.
3. (Optional) force migration run:
   - `bash deploy/db_migrate.sh`
4. Health checks:
   - `curl -fsS http://127.0.0.1:8000/healthz`
   - `curl -fsS http://127.0.0.1:8010/healthz`

## 2) Upgrade

1. Pull latest GitHub code and rollout:
   - `cd /opt/a1phquest`
   - `bash deploy/update-from-github.sh`
   - optional fast rollout: `BUILD_IMAGES=0 bash deploy/update-from-github.sh`
2. Verify:
   - `pytest -q`
   - `HEALTH_SECONDS=30 BUILD_IMAGES=0 bash deploy/p5s_oneclick.sh`
   - `python3 deploy/e2e_runtime_flow.py --base-url http://127.0.0.1:8000 --exchange okx`

## 3) Rollback

1. If previous image ids are retained, retag old ids to `deploy-api:latest` and `deploy-worker-supervisor:latest`.
2. Recreate services without rebuild:
   - `docker compose --env-file .env -f deploy/docker-compose.yml up -d --no-build api worker-supervisor`
3. Re-run health and e2e checks from section 2.

## 4) Backup and Restore

1. Backup:
   - `docker exec a1phquest-backup /bin/bash /scripts/backup.sh`
2. Locate latest file:
   - `ls -1t deploy/backups/a1phquest_*.sql.enc | head -n 1`
3. Restore:
   - `POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 bash deploy/restore_backup.sh <backup_file>`

## 5) Continuous Drill

1. Run full rehearsal:
   - `bash deploy/p9_drill.sh`
   - Optional network-flake guard during image build:
     - `DRILL_ALLOW_BUILD_FALLBACK=1 bash deploy/p9_drill.sh`
2. Review report:
   - `cat deploy/p9_drill_report.json`

## 6) Release Gate

1. Run release candidate gate (tests + migrate + oneclick + e2e + drill):
   - `bash deploy/release_gate.sh`
   - Build network fallback enabled by default (oneclick can retry with `BUILD_IMAGES=0` if registry pull fails).
2. Review gate report:
   - `cat deploy/release_gate_report.json`
3. Gate includes default P16 soak smoke; disable if needed:
   - `RUN_SOAK_SMOKE=0 bash deploy/release_gate.sh`
   - Smoke artifacts are isolated under:
     - `deploy/p16_soak_smoke_health.json`
     - `deploy/p16_soak_smoke_stats.log`
     - `deploy/p16_soak_smoke_report.json`
4. Before production approval, run documentation consistency check:
   - Ensure `README.md`, `HANDOFF.md`, and `docs/api.md` all match the current shipped interfaces and runtime behavior.

## 7) Ops Snapshot

1. Generate lightweight diagnostics snapshot:
   - `OPS_BEARER_TOKEN=<token> python3 deploy/ops_snapshot.py > deploy/ops_snapshot.json`
2. Snapshot includes health probes, `/api/ops/metrics`, and `docker stats` sample.

## 8) P16 Soak Test

1. Run long-run soak monitor (default 24h):
   - `bash deploy/p16_soak.sh`
2. Fast verification run (for script smoke test):
   - `DURATION_SECONDS=20 INTERVAL_SECONDS=5 bash deploy/p16_soak.sh`
3. Review reports:
   - `cat deploy/p16_soak_report.json`
4. Optional threshold override (2c4g budget tuning):
   - `MAX_TOTAL_MEM_MIB=3072 MAX_CONTAINER_MEM_MIB=768 MAX_CPU_PCT=170 bash deploy/p16_soak.sh`
5. Optional probe retry tuning (reduce transient network false alarms):
   - `HEALTH_PROBE_RETRIES=5 HEALTH_PROBE_RETRY_DELAY_SECONDS=0.8 bash deploy/p16_soak.sh`
6. Archive soak evidence bundle (report + health + stats + checksums):
   - `ARCHIVE_EVIDENCE=1 EVIDENCE_LABEL=vps-nightly bash deploy/p16_soak.sh`
   - or manual archive: `python3 deploy/p16_archive_evidence.py --label vps-nightly`
7. Run 24h soak in background (recommended on VPS):
   - Start: `bash deploy/p16_soak_background.sh start`
     - script performs startup alive-check and prints recent log automatically if process exits immediately.
   - Status: `bash deploy/p16_soak_background.sh status`
   - Recent logs: `bash deploy/p16_soak_background.sh logs`
   - Stop: `bash deploy/p16_soak_background.sh stop`
   - Finalize (after run completed): `bash deploy/p16_soak_background.sh finalize`
   - Auto-finalize watcher:
     - Start: `bash deploy/p16_soak_background.sh watch-start`
     - Status: `bash deploy/p16_soak_background.sh watch-status`
     - Stop: `bash deploy/p16_soak_background.sh watch-stop`
     - Watch status file: `deploy/p16_auto_finalize.status.json` (`started_at/updated_at/elapsed_seconds/last_action`)
8. Soak progress file (updated every stats sample):
   - `cat deploy/p16_soak_progress.json`
   - Includes `status`, `elapsed_seconds`, `remaining_seconds`, and `samples_collected`.
9. Generate final P16 acceptance report after full 24h run:
   - `python3 deploy/p16_acceptance_report.py --soak-report deploy/p16_soak_report.json --evidence-dir deploy/evidence/p16 --output deploy/p16_acceptance_report.json`
   - Acceptance report validates evidence checksums and summarizes gate verdict.
10. One-shot finalize helper (auto-detect latest evidence, generate acceptance, optional RC):
   - `python3 deploy/p16_finalize_after_soak.py`
11. Finalize output now also emits release manifest:
   - `deploy/p20_release_manifest.json`
   - Aggregates release gate, acceptance, RC, checklist and evidence binding.

## 9) Health Probe Tuning

1. If readiness check is slow/flaky, set curl timeout guards in one-click flow:
   - `HEALTH_CURL_CONNECT_TIMEOUT_SECONDS=2 HEALTH_CURL_MAX_TIME_SECONDS=3 bash deploy/p5s_oneclick.sh`
2. Increase probe retries for transient network jitter:
   - `HEALTH_PROBE_RETRIES=5 HEALTH_PROBE_RETRY_DELAY_SECONDS=0.8 bash deploy/p5s_oneclick.sh`

## 10) P20 RC Gate

1. Run release-candidate composite gate:
   - `bash deploy/p20_rc_gate.sh`
   - Gate includes checklist generation by default.
   - Force strict checklist verdict: `P20_CHECKLIST_STRICT=1 bash deploy/p20_rc_gate.sh`
2. Optional: skip nested release gate and only validate acceptance report:
   - `RUN_RELEASE_GATE=0 RUN_P16_ACCEPTANCE=1 bash deploy/p20_rc_gate.sh`
   - For a specific evidence run:
     - `RUN_RELEASE_GATE=0 EVIDENCE_PATH=deploy/evidence/p16/<run_id> bash deploy/p20_rc_gate.sh`
3. Review RC report:
   - `cat deploy/p20_rc_report.json`
4. Build release checklist summary (strict mode for release window):
   - `python3 deploy/p20_release_checklist.py --strict`
   - Output: `deploy/p20_release_checklist.json`
5. Build unified release manifest:
   - `python3 deploy/p20_release_manifest.py`
   - Output: `deploy/p20_release_manifest.json`
6. Strict release rule:
   - Only when `python3 deploy/p20_release_checklist.py --strict` exits `0` may the run enter production window.
   - `--strict` disabled is rehearsal-only and cannot be used for production release approval.

## 11) P17-P20 Merged Execution

1. Run merged gate (security + Lighter + observability + RC):
   - `bash deploy/p17_p20_merged_gate.sh`
2. Strict mode decision:
   - Default `P20_CHECKLIST_STRICT_MODE=auto`:
     - `soak_progress.status=completed` -> strict checklist enabled.
     - other soak status -> non-strict rehearsal mode.
   - Force strict:
     - `P20_CHECKLIST_STRICT_MODE=1 bash deploy/p17_p20_merged_gate.sh`
   - Force non-strict:
     - `P20_CHECKLIST_STRICT_MODE=0 bash deploy/p17_p20_merged_gate.sh`
3. Review merged report:
   - `cat deploy/p17_p20_merged_report.json`
   - Verify `stage_status`, `strict_decision`, and `release_readiness` fields.

## 12) Incident Triage Path (Admin)

1. Read aggregate health and backlog:
   - `GET /api/ops/admin/metrics`
   - Check `failed_audit_event_rate_last_hour`, `runtime_status_drift_count`, and Lighter backlog counters.
2. Inspect trend and drift details:
   - `error_trend_last_hour` for 5-minute error buckets.
   - `runtime_drift_samples` for mismatched `strategy_status` vs `runtime_status`.
3. Follow evidence chain:
   - Runtime detail: `GET /api/strategies/{id}/runtime` and `.../runtime/consistency`
   - Reconcile detail: `GET /api/exchange-accounts/{id}/lighter-reconcile/pending`
   - Container resource baseline: `OPS_BEARER_TOKEN=<token> python3 deploy/ops_snapshot.py`
4. Resolve long-running Lighter backlog in batch (maintenance job):
   - Preview: `python3 deploy/lighter_reconcile_maintenance.py --dry-run --include-unchanged`
   - Execute: `python3 deploy/lighter_reconcile_maintenance.py`
   - Container mode: `docker compose -f deploy/docker-compose.yml exec -T api python -m app.tools.lighter_reconcile_maintenance --dry-run --include-unchanged`
   - If container command reports missing module, rebuild API image once:
     - `BUILD_IMAGES=1 bash deploy/p5s_oneclick.sh`

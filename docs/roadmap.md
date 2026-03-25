# Roadmap

## Completed in This Implementation

- Monorepo baseline
- API service contract and core models
- Multi-tenant enforcement by `user_id`
- Strategy supervisor skeleton
- Frontend starter pages
- Deployment stack with compose/nginx/backup
- Exchange account sync snapshots (balances/positions/orders)
- Real exchange credential validation (Binance/OKX)
- Order execution APIs with risk pre-check, audit, and WebSocket events
- Daily-loss circuit breaker now performs active user strategy stop
- Cancel-rate rejection can now trigger circuit breaker stop-all (when user risk rule enables breaker)
- Trade/fill persistence (`trade_fill_snapshots`) and user-isolated WS trade events
- Lighter reconciliation pending records (`lighter_reconcile_records`) with post-sync status backfill
- Lighter reconcile lifecycle now includes `pending/reconciled/expired` with tenant-scoped pending query endpoint
- Linux VPS stack scripts and one-click P5-S verification pipeline
- Runtime observability consistency check endpoint (`/api/strategies/{id}/runtime/consistency`)
- Unified WS event envelope (`type/timestamp/resource_id/payload`) with compatibility flattening
- Per-user WS sequence field (`event_seq`) for deterministic event ordering
- High-risk endpoint policy now requires 2FA enrollment
- High-risk endpoint policy upgraded to short-lived step-up token (`/api/auth/2fa/step-up`)
- High-risk route matrix now has automated test coverage for step-up dependency enforcement
- Login anomaly detection upgraded to risk scoring (`IP + UA + geo`) with threshold-based alerts
- AES local master-key rotation script (dry-run + execute)
- AES local master-key rotation script now supports `precheck/execute/rollback` modes with audit events
- Exchange sync now supports incremental trade cursor hints (Binance symbol+startTime, OKX begin window)
- Exchange consistency endpoint added (`/api/exchange-accounts/{id}/consistency`)
- User event replay endpoint added (`/api/events/replay`)
- Ops metrics endpoint added (`/api/ops/metrics`)
- Daily-loss risk gating now uses server-side realized PnL reconstruction from persisted fills (no client loss dependency)
- Lighter trade sync now supports multi-page pull with cursor/has-more fallback and loop protection
- Worker runtime grid/dca now executes concrete CTA hooks (grid ladder seeding + cycle DCA order submission)
- Alembic migration baseline added (`apps/api/migrations`) and API startup now upgrades schema to head
- Migration orchestration hardened with PostgreSQL advisory lock + compose migrate service gating
- Worker runtime now enforces heartbeat timeout failure and stopped-runtime retention cleanup
- Runtime execution observability loop added: worker trace events (`order_submitted/order_status_update/trade_filled`) with sequence + counters
- Strategy runtime API now persists runtime event audit watermarks and emits user-scoped runtime trace WS events
- Strategy runtime observability fields are now versioned in schema (`20260323_0002`) and included in runtime consistency checks
- WS consistency hardened with dedupe keys + replay ordering by `event_seq`
- Strategy runtime WS emission order now guarantees replay reconstruction (`order_submitted -> trade_filled -> strategy_runtime_update`)
- Lighter reconcile service now expires records after repeated sync failures (error threshold)
- Lighter pending endpoint now exposes backlog age and recent failure reasons for operations visibility
- Step-up dependency now enforces `purpose=high_risk` token scope to prevent cross-purpose reuse
- Login anomaly audit now records proxy signal source (`client_ip_source/client_geo_source`) and alert channel delivery status
- AES rotation now emits start/checkpoint/failure audit events and supports checkpoint resume (`--resume-from`)
- Ops metrics endpoint expanded with process count, hourly error rate, and critical audit signal counters
- Release candidate gate script added (`deploy/release_gate.sh`) and validated with full drill pipeline
- P16 soak runner added (`deploy/p16_soak.sh`) with health + docker stats reports (smoke run validated)
- P16 soak now includes threshold analyzer (`deploy/p16_soak_analyze.py`) with pass/fail verdict and required-service checks
- P16 soak now supports evidence archive bundles (`deploy/p16_archive_evidence.py`) with checksums/metadata for audit retention
- Lighter reconcile matching now supports multi-key candidates (`order_id/order_index/client_order_id`) for faster pending convergence
- Lighter trade pagination now de-duplicates overlapping pages before persistence/reconciliation
- One-click/drill readiness checks now use bounded curl timeouts to prevent health wait hangs under transient network stalls
- Runtime consistency endpoint now syncs latest supervisor snapshot before compare to avoid heartbeat false negatives in E2E gates
- P16 background soak controller added (`deploy/p16_soak_background.sh`) for VPS start/status/stop long-run execution
- Lighter pending reconcile now emits retry-window stats (`retry_due/retry_blocked/no_retry_hint`) and per-record retry hints (`next_retry_at`)
- Lighter sync-error handling now respects retry backoff window (cooldown records are not over-counted toward expiry)
- Ops metrics now include Lighter reconcile backlog/retry counters for dashboard readiness
- Lighter manual reconcile endpoint added (`POST /api/exchange-accounts/{id}/lighter-reconcile/retry-sync`) with step-up protection
- Lighter manual reconcile endpoint now writes audit events for success/failure/skip branches and has tenant-isolation regression tests
- Ops metrics failed/critical counters now include `lighter_reconcile_retry_sync` failure events from audit details
- P9 drill upgrade step now supports build-failure fallback to no-build recreate for network-flaky environments
- P16 soak scripts now emit live progress snapshots (`deploy/p16_soak_progress.json`) and support `p16_soak_background.sh logs` for long-run visibility
- Release gate one-click step now supports build-failure fallback (`BUILD_IMAGES=0`) for transient registry pull failures
- P16 background soak controller now performs startup alive-check and stale PID cleanup to avoid false-running state
- Login anomaly alerts now support cooldown/hourly suppression policy with audit traceability (`alert_sent/suppressed_reason`)
- Lighter reconcile now prunes aged expired records and exposes `expired_pruned_now` in pending/retry responses
- Lighter signed payload validation now emits machine-readable audit error codes for submit/cancel rejection
- Ops metrics now include runtime drift count, pending backlog oldest age, and audit action trend map
- P16 final acceptance script added (`deploy/p16_acceptance_report.py`) with archive checksum verification
- P20 RC composite gate added (`deploy/p20_rc_gate.sh`)
- Release gate soak-smoke outputs now use dedicated `p16_soak_smoke_*` files to avoid clobbering active 24h soak artifacts
- P16 acceptance report now enforces soak-report checksum match against selected evidence bundle
- P16 post-run finalize pipeline added (`deploy/p16_finalize_after_soak.py`) and exposed via `p16_soak_background.sh finalize`
- Auto-finalize watcher added (`deploy/p16_auto_finalize_watch.sh`) to close soak -> acceptance flow without manual intervention
- Auto-finalize watcher now persists heartbeat/status contract (`deploy/p16_auto_finalize.status.json`) with stale recovery logic
- Background soak controller now provides unified watcher controls (`watch-start/watch-status/watch-stop`)
- Admin operations aggregate endpoint added (`GET /api/ops/admin/metrics`) for cross-tenant read-only observability
- Admin ops metrics now include fixed-window error trend buckets and runtime drift sample rows for incident triage
- Lighter global maintenance script added (`deploy/lighter_reconcile_maintenance.py`) for batch expire/prune with audit trail
- Ops metrics now include normalized threshold-based `alert_items` for warning/critical signal aggregation
- P20 release checklist generator added (`deploy/p20_release_checklist.py`) for one-shot release readiness verdict
- P20 RC gate now auto-invokes release checklist with optional strict-fail mode (`P20_CHECKLIST_STRICT=1`)
- P16 finalize pipeline now enforces strict checklist and evidence binding consistency (`path/run_id`) across acceptance/RC/checklist
- P20 release manifest aggregator added (`deploy/p20_release_manifest.py`) for production release evidence handoff
- P17-P20 merged gate added (`deploy/p17_p20_merged_gate.sh`) with auto strict-mode decision and unified report output

## Next Priority (Phase 2 Deepening)

- Monitor active 24h VPS soak run and verify final evidence bundle under `deploy/evidence/p16/` (with archive checksums)

## Phase 3 Hardening

- Strict 2FA enforcement on all high-risk endpoints
- Device/IP anomaly scoring and notifications
- Migration toolchain (Alembic) and schema versioning

## Phase 4

- Lighter execution hardening (order-index reconciliation and cursor trade sync)
- Full admin dashboard metrics and operational controls
- COS integration for backup upload automation

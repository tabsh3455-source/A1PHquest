# API Contract (v0.1)

## Auth

- `POST /api/auth/register/start`
- `POST /api/auth/register/complete`
- `POST /api/auth/login` (for users with 2FA configured, `otp_code` or `recovery_code` is required)
- `GET /api/auth/session`
- `POST /api/auth/2fa/enroll/start`
- `POST /api/auth/2fa/enroll/complete`
- `POST /api/auth/2fa/setup`
- `POST /api/auth/2fa/verify`
- `POST /api/auth/2fa/step-up` (returns short-lived `step_up_token` for high-risk endpoints)
- `POST /api/auth/logout`
- Login/session response now includes:
  - `authenticated`
  - `enrollment_required`
- Auth transport uses `HttpOnly` access cookie + CSRF token flow.
- Login anomaly alerts now include suppression policy (cooldown + hourly cap) in audit details:
  - `suppressed_reason`, `alert_sent`, `alerts_sent_last_hour_before`.

## Exchange Accounts

- `POST /api/exchange-accounts`
- `GET /api/exchange-accounts`
- `POST /api/exchange-accounts/{account_id}/validate`
- `POST /api/exchange-accounts/{account_id}/sync`
- `GET /api/exchange-accounts/{account_id}/balances`
- `GET /api/exchange-accounts/{account_id}/positions`
- `GET /api/exchange-accounts/{account_id}/orders?status=<status>&limit=<n>`
- `GET /api/exchange-accounts/{account_id}/trades?symbol=<symbol>&limit=<n>`
- `GET /api/exchange-accounts/{account_id}/consistency`
- `GET /api/exchange-accounts/{account_id}/lighter-reconcile/pending?limit=<n>` (lighter only)
- pending response now includes `expired_pruned_now` to show aged-expired cleanup count.
- `POST /api/exchange-accounts/{account_id}/lighter-reconcile/retry-sync` (lighter only, step-up required)
  - returns reconcile sync summary: `pending_before/pending_after/reconciled_now/expired_now` and snapshot sync counters.
  - retry response now includes `expired_pruned_now`.
- Binance/OKX/Lighter validate endpoint calls real exchange credential APIs.
- Sync endpoint pulls real balances/positions/open-orders/recent-fills, stores snapshots, then emits WebSocket events.
- Sync endpoint reuses local trade cursors to perform incremental fills query (Binance/OKX), reducing repeated full pulls.
- For OKX, `passphrase` is required on account creation.
- For Lighter, `api_key` should be the `account_index`; `api_secret` is used as optional `auth` token for private query paths.
- Lighter reconcile records now include lifecycle states: `pending`, `reconciled`, `expired`.
- Lighter pending reconcile response now includes retry-window observability:
  - aggregate: `retry_due_count`, `retry_blocked_count`, `no_retry_hint_count`, `next_retry_at`
  - per-record: `next_retry_at`, `next_retry_after_seconds`
- Lighter sync-error backoff is enforced: records inside retry cooldown do not increase `sync_error_count` again until `next_retry_at`.
- High-risk exchange-account operations now require user to enable 2FA first.
- High-risk endpoints now require `X-StepUp-Token` header from `/api/auth/2fa/step-up`.

## Orders

- `POST /api/orders` (supports `LIMIT` and `MARKET`; MARKET requires `reference_price` for risk notional)
- `POST /api/orders/{order_id}/cancel`
- Submit flow checks risk rules (`max_order_notional`, `max_daily_loss`, `max_position_ratio`) before exchange API call.
- Cancel flow checks `max_cancel_rate_per_minute` before cancel API call.
- Lighter submit/cancel requires `exchange_payload.tx_type` and `exchange_payload.tx_info` (signed tx for `/api/v1/sendTx`).
- If submit is blocked by daily-loss circuit breaker, running strategies for that user are force-stopped.
- Successful submit/cancel writes `order_snapshots`; if exchange returns fills, also writes `trade_fill_snapshots`.
- Successful submit/cancel appends audit events and pushes WebSocket events.
- Order submit/cancel now require 2FA-enabled user session.
- Order submit/cancel now require `X-StepUp-Token`.

## Strategies

- `POST /api/strategies`
- `GET /api/strategies`
- `POST /api/strategies/{strategy_id}/start`
- `POST /api/strategies/{strategy_id}/stop`
- `GET /api/strategies/{strategy_id}/runtime`
- `GET /api/strategies/{strategy_id}/runtime/consistency`
- Live startup currently supports `strategy_type=grid|futures_grid|dca|combo_grid_dca` with template config validation and risk fail-closed gate.
- `GET /api/strategies/{strategy_id}/runtime` returns `last_heartbeat` and `last_error`.
- `runtime/consistency` compares DB runtime observability fields (`status,last_heartbeat,last_error`) with worker-supervisor view.
- If `worker-supervisor` is unavailable, start/stop returns `503` (no local fake-running fallback).
- Strategy start/stop now require 2FA-enabled user session.
- Strategy start/stop now require `X-StepUp-Token`.

## Strategy Templates

- `GET /api/strategy-templates`
- Template registry includes `live_supported` and `draft_only` templates.
- Current live-supported templates:
  - `spot_grid`
  - `futures_grid`
  - `dca`
  - `combo_grid_dca`

## Events Replay

- `GET /api/events/replay?after_seq=<n>&since_seconds=<n>&limit=<n>`
- Returns in-memory recent event history for reconnect replay.
- Replay remains user-scoped and only for short disconnect windows.

## Market Data

- Private market history:
  - `GET /api/market/klines`
- Public market history/symbols:
  - `GET /api/public/market/klines`
  - `GET /api/public/market/symbols`
- Public market WebSocket:
  - `GET /ws/market`
  - supports `subscribe_market` / `unsubscribe_market`
- User event WebSocket:
  - `GET /ws/events`
  - supports user-scoped market subscription plus strategy/runtime events.
- Market stream events:
  - `market_candle`
  - `market_stream_status`

## Ops

- `GET /api/ops/metrics`
- Returns observability fields: websocket counts, strategy runtime status counts, runtime drift count, lighter reconcile backlog/retry status, pending oldest age, failed/critical audit counts in last hour, and audit action trend map.
- `alert_items` provides threshold-based warning/critical signals (`code/severity/metric/value/threshold/message`) for direct dashboard consumption.
  - thresholds are configurable via env:
    - `OPS_ALERT_FAILED_AUDIT_RATE_THRESHOLD`
    - `OPS_ALERT_RUNTIME_DRIFT_COUNT_THRESHOLD`
    - `OPS_ALERT_LIGHTER_PENDING_THRESHOLD`
    - `OPS_ALERT_LIGHTER_RETRY_BLOCKED_THRESHOLD`
    - `OPS_ALERT_CRITICAL_AUDIT_EVENTS_THRESHOLD`
- `GET /api/ops/admin/metrics` (admin only)
- Returns cross-tenant aggregates for operations console:
  - user totals/active users
  - global runtime + drift counts
  - global lighter backlog + retry buckets
  - last-hour failed/critical audit summaries
  - top users by lighter pending backlog.
  - fixed-window error trend buckets (`error_trend_last_hour`) for charting.
  - drift sample rows (`runtime_drift_samples`) to quickly locate mismatched strategy/runtime states.
  - `alert_items` with same normalized signal structure as user metrics endpoint.

## Workflow Readiness

- `GET /api/workflow/readiness`
- Aggregated status for the trading loop:
  - `authenticated`
  - `enrollment_required`
  - `has_risk_rule`
  - `exchange_accounts_summary`
  - `live_supported_templates`
  - `ai_ready`
  - `next_required_actions`

## Risk Rules

- `PUT /api/risk-rules`
- `GET /api/risk-rules`
- `POST /api/risk-rules/dry-run-check`

## Real-Time

- `GET /ws/events?token=<jwt>`
- Event types include `exchange_sync`, `trade_fills_synced`, `order_submitted`, `order_canceled`, `trade_filled`, `risk_blocked`, `strategy_runtime_update`, `strategy_runtime_error`.
- WS payload now follows unified envelope: `type`, `timestamp`, `resource_id`, `payload`, `user_id`, `event_seq`.
- `event_seq` is a per-user monotonic sequence number for deterministic client-side ordering.
- For compatibility, key payload fields are still flattened to top-level.

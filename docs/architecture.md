# Architecture

## Runtime Topology

- `api` service: auth, RBAC, account/risk/strategy APIs, WebSocket events, audit logs
- `worker-supervisor` service: one process per user strategy runtime
- `postgres`: multi-tenant persistent storage with `user_id` ownership
- `frontend`: Vue3 + Element Plus UI
- `nginx`: reverse proxy for API/WebSocket/frontend
- `backup`: scheduled database backup and encryption

## Multi-Tenant Rule

- Every business table includes `user_id`
- Every query path filters by current user
- Admin role is explicit and opt-in

## Security Baseline

- Password hash: bcrypt
- Auth token: JWT
- 2FA: Google Authenticator (TOTP) validated during login
- Credential encryption:
  - `KMS_MODE=local_aes`: local AES-GCM using `AES_MASTER_KEY`
- Audit log: append-only insert model

## Exchange Integration Strategy

- Phase-1/2: Binance and OKX production path
- Current extension: Lighter adapter (signed transaction passthrough)
- Integration boundary is isolated in API gateway service and compat package
- Account state sync pipeline:
  - Pull account state from exchange APIs (balances/positions/open orders)
  - Normalize to internal schema
  - Upsert into snapshot tables
  - Push tenant-scoped WebSocket sync events

import axios from "axios";
import { readonly, shallowRef } from "vue";

function resolveApiBase() {
  const configuredBase = String(import.meta.env.VITE_API_BASE || "").trim();
  if (configuredBase) {
    return configuredBase;
  }

  const { protocol, hostname, origin, port } = window.location;
  if (port === "5173" || port === "4173") {
    return `${protocol}//${hostname}:8000`;
  }
  return origin;
}

export const API_BASE = resolveApiBase();

export const http = axios.create({
  baseURL: API_BASE,
  withCredentials: true
});

export type SessionUser = {
  id: number;
  username: string;
  email: string;
  role: "user" | "admin";
  is_active: boolean;
  created_at: string;
};

export type AuthSession = {
  authenticated: boolean;
  user: SessionUser;
  csrf_token: string;
};

const sessionState = shallowRef<AuthSession | null>(null);
let sessionRequest: Promise<AuthSession | null> | null = null;

function isSafeMethod(method?: string) {
  const normalized = String(method || "get").toUpperCase();
  return ["GET", "HEAD", "OPTIONS", "TRACE"].includes(normalized);
}

function shouldClearSessionFromError(error: any) {
  const status = Number(error?.response?.status || 0);
  if (status !== 401) {
    return false;
  }
  const detail = String(error?.response?.data?.detail || "").toLowerCase();
  return [
    "invalid authentication",
    "authentication required",
    "user not found",
    "session has been revoked"
  ].includes(detail);
}

function applySession(session: AuthSession | null) {
  sessionState.value = session;
  return session;
}

export function clearSessionState() {
  sessionState.value = null;
}

export function useSessionState() {
  return readonly(sessionState);
}

export function readStoredClaims() {
  const session = sessionState.value;
  if (!session) {
    return null;
  }
  return {
    sub: String(session.user.id),
    role: session.user.role,
    twofaPending: false
  };
}

export function getErrorMessage(error: any, fallback: string) {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }
        if (item && typeof item === "object") {
          const fieldPath = Array.isArray(item.loc) ? item.loc.join(".") : "";
          const message = String(item.msg || item.message || "").trim();
          return fieldPath && message ? `${fieldPath}: ${message}` : message || JSON.stringify(item);
        }
        return String(item);
      })
      .filter(Boolean)
      .join("; ");
  }
  if (typeof error?.response?.data === "string" && error.response.data.trim()) {
    return error.response.data;
  }
  if (typeof error?.message === "string" && error.message.trim()) {
    return error.message;
  }
  return fallback;
}

http.interceptors.request.use((config) => {
  config.withCredentials = true;
  if (!isSafeMethod(config.method) && sessionState.value?.csrf_token) {
    const headers = (config.headers || {}) as Record<string, string>;
    headers["X-CSRF-Token"] = sessionState.value.csrf_token;
    config.headers = headers;
  }
  return config;
});

http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (shouldClearSessionFromError(error)) {
      clearSessionState();
    }
    return Promise.reject(error);
  }
);

export async function loadSession(forceRefresh = false) {
  if (!forceRefresh && sessionState.value) {
    return sessionState.value;
  }
  if (!forceRefresh && sessionRequest) {
    return sessionRequest;
  }
  sessionRequest = http
    .get("/api/auth/session")
    .then((response) => applySession(response.data as AuthSession))
    .catch((error) => {
      if (shouldClearSessionFromError(error)) {
        clearSessionState();
        return null;
      }
      throw error;
    })
    .finally(() => {
      sessionRequest = null;
    });
  return sessionRequest;
}

export async function ensureSession() {
  const session = await loadSession();
  if (!session) {
    throw new Error("Login expired. Please sign in again.");
  }
  return session;
}

export async function login(username: string, password: string, otpCode?: string) {
  clearSessionState();
  const payload: Record<string, string> = { username, password };
  if (otpCode) {
    payload.otp_code = otpCode;
  }
  const resp = await http.post("/api/auth/login", payload);
  return applySession(resp.data as AuthSession);
}

export async function register(username: string, email: string, password: string) {
  const resp = await http.post("/api/auth/register", {
    username,
    email,
    password
  });
  return resp.data as SessionUser;
}

export async function logout() {
  try {
    await http.post("/api/auth/logout", {});
  } finally {
    clearSessionState();
  }
}

export async function requestStepUpToken(code: string) {
  await ensureSession();
  const resp = await http.post("/api/auth/2fa/step-up", { code });
  return resp.data as { step_up_token: string; expires_in_seconds: number; token_type: string };
}

export async function listStrategies() {
  await ensureSession();
  const resp = await http.get("/api/strategies");
  return resp.data as StrategyItem[];
}

export async function getOpsMetrics() {
  await ensureSession();
  const resp = await http.get("/api/ops/metrics");
  return resp.data;
}

export type MarketDataSettings = {
  market_ws_reconnect_base_seconds: number;
  market_ws_reconnect_max_seconds: number;
  market_ws_idle_timeout_seconds: number;
  market_candle_cache_size: number;
  market_rest_backfill_limit: number;
  has_overrides: boolean;
  updated_at: string | null;
  updated_by_user_id: number | null;
  default_values: Record<string, number>;
};

export async function getMarketDataSettings() {
  await ensureSession();
  const resp = await http.get("/api/system-config/market-data");
  return resp.data as MarketDataSettings;
}

export async function updateMarketDataSettings(
  payload: Omit<MarketDataSettings, "has_overrides" | "updated_at" | "updated_by_user_id" | "default_values">,
  stepUpToken: string
) {
  await ensureSession();
  const resp = await http.put("/api/system-config/market-data", payload, {
    headers: buildStepUpHeaders(stepUpToken)
  });
  return resp.data as MarketDataSettings;
}

export async function resetMarketDataSettings(stepUpToken: string) {
  await ensureSession();
  const resp = await http.delete("/api/system-config/market-data", {
    headers: buildStepUpHeaders(stepUpToken)
  });
  return resp.data as MarketDataSettings;
}

export type ExchangeType = "binance" | "okx" | "lighter";

export type ExchangeAccountItem = {
  id: number;
  exchange: ExchangeType;
  account_alias: string;
  is_testnet: boolean;
  created_at: string;
};

export type ExchangeAccountCreatePayload = {
  exchange: ExchangeType;
  account_alias: string;
  api_key: string;
  api_secret: string;
  passphrase?: string | null;
  is_testnet: boolean;
};

function buildStepUpHeaders(stepUpToken?: string) {
  const headers: Record<string, string> = {};
  if (stepUpToken) {
    headers["X-StepUp-Token"] = stepUpToken;
  }
  return headers;
}

export async function listExchangeAccounts() {
  await ensureSession();
  const resp = await http.get("/api/exchange-accounts");
  return resp.data as ExchangeAccountItem[];
}

export async function createExchangeAccount(
  payload: ExchangeAccountCreatePayload,
  stepUpToken: string
) {
  await ensureSession();
  const resp = await http.post("/api/exchange-accounts", payload, {
    headers: buildStepUpHeaders(stepUpToken)
  });
  return resp.data as ExchangeAccountItem;
}

export async function validateExchangeAccount(accountId: number, stepUpToken: string) {
  await ensureSession();
  const resp = await http.post(
    `/api/exchange-accounts/${accountId}/validate`,
    {},
    {
      headers: buildStepUpHeaders(stepUpToken)
    }
  );
  return resp.data as {
    account_id: number;
    exchange: string;
    validated: boolean;
    message: string;
  };
}

export async function syncExchangeAccount(accountId: number, stepUpToken: string) {
  await ensureSession();
  const resp = await http.post(
    `/api/exchange-accounts/${accountId}/sync`,
    {},
    {
      headers: buildStepUpHeaders(stepUpToken)
    }
  );
  return resp.data as {
    account_id: number;
    exchange: string;
    balances_synced: number;
    positions_synced: number;
    orders_synced: number;
    trades_synced: number;
    message: string;
    synced_at: string;
  };
}

export type RiskRulePayload = {
  max_order_notional: number;
  max_daily_loss: number;
  max_position_ratio: number;
  max_cancel_rate_per_minute: number;
  circuit_breaker_enabled: boolean;
};

export async function getRiskRule() {
  await ensureSession();
  const resp = await http.get("/api/risk-rules");
  return resp.data as RiskRulePayload & { id: number; user_id: number; created_at: string; updated_at: string };
}

export async function upsertRiskRule(payload: RiskRulePayload, stepUpToken: string) {
  await ensureSession();
  const resp = await http.put("/api/risk-rules", payload, {
    headers: buildStepUpHeaders(stepUpToken)
  });
  return resp.data as RiskRulePayload & { id: number; user_id: number; created_at: string; updated_at: string };
}

export type StrategyType = "grid" | "dca";

export type StrategyCreatePayload = {
  name: string;
  strategy_type: StrategyType;
  config: Record<string, unknown>;
};

export type StrategyItem = {
  id: number;
  name: string;
  strategy_type: string;
  config: Record<string, unknown>;
  status: string;
  runtime_ref: string | null;
  created_at: string;
  updated_at: string;
};

export async function createStrategy(payload: StrategyCreatePayload) {
  await ensureSession();
  const resp = await http.post("/api/strategies", payload);
  return resp.data as StrategyItem;
}

export async function updateStrategy(strategyId: number, payload: StrategyCreatePayload) {
  await ensureSession();
  const resp = await http.put(`/api/strategies/${strategyId}`, payload);
  return resp.data as StrategyItem;
}

export type StrategyRuntime = {
  strategy_id: number;
  runtime_ref: string | null;
  status: string;
  process_id: string | null;
  started_at: string | null;
  stopped_at: string | null;
  last_heartbeat: string | null;
  last_error: string | null;
  last_event_seq: number;
  order_submitted_count: number;
  order_update_count: number;
  trade_fill_count: number;
  recent_events: Array<Record<string, unknown>>;
};

export async function startStrategy(strategyId: number, stepUpToken: string) {
  await ensureSession();
  const resp = await http.post(`/api/strategies/${strategyId}/start`, {}, {
    headers: buildStepUpHeaders(stepUpToken)
  });
  return resp.data as StrategyRuntime;
}

export async function stopStrategy(strategyId: number, stepUpToken: string) {
  await ensureSession();
  const resp = await http.post(`/api/strategies/${strategyId}/stop`, {}, {
    headers: buildStepUpHeaders(stepUpToken)
  });
  return resp.data as StrategyRuntime;
}

export async function getStrategyRuntime(strategyId: number) {
  await ensureSession();
  const resp = await http.get(`/api/strategies/${strategyId}/runtime`);
  return resp.data as StrategyRuntime;
}

export type MarketKlineItem = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type MarketKlineResponse = {
  exchange_account_id: number;
  exchange: string;
  symbol: string;
  interval: string;
  candles: MarketKlineItem[];
};

export async function getMarketKlines(
  exchangeAccountId: number,
  symbol: string,
  interval: string,
  limit = 300
) {
  await ensureSession();
  const resp = await http.get("/api/market/klines", {
    params: {
      exchange_account_id: exchangeAccountId,
      symbol,
      interval,
      limit
    }
  });
  return resp.data as MarketKlineResponse;
}

export type AiProviderItem = {
  id: number;
  name: string;
  provider_type: "openai_compatible";
  base_url: string;
  model_name: string;
  is_active: boolean;
  has_api_key: boolean;
  created_at: string;
  updated_at: string;
};

export type AiProviderPayload = {
  name: string;
  provider_type: "openai_compatible";
  base_url: string;
  model_name: string;
  api_key: string;
  is_active: boolean;
};

export type AiProviderUpdatePayload = Omit<AiProviderPayload, "api_key"> & {
  api_key?: string | null;
};

export type AiPolicyItem = {
  id: number;
  name: string;
  provider_id: number;
  provider_name: string;
  exchange_account_id: number;
  symbol: string;
  interval: "1m" | "5m" | "15m" | "1h";
  strategy_ids: number[];
  allowed_actions: Array<"activate_strategy" | "stop_strategy" | "create_strategy_version">;
  execution_mode: "dry_run" | "auto";
  status: "disabled" | "enabled";
  decision_interval_seconds: number;
  minimum_confidence: number;
  max_actions_per_hour: number;
  custom_prompt: string | null;
  last_run_at: string | null;
  last_decision_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

export type AiPolicyPayload = {
  name: string;
  provider_id: number;
  exchange_account_id: number;
  symbol: string;
  interval: "1m" | "5m" | "15m" | "1h";
  strategy_ids: number[];
  allowed_actions: Array<"activate_strategy" | "stop_strategy" | "create_strategy_version">;
  execution_mode: "dry_run" | "auto";
  status: "disabled" | "enabled";
  decision_interval_seconds: number;
  minimum_confidence: number;
  max_actions_per_hour: number;
  custom_prompt?: string | null;
};

export type AiDecisionItem = {
  id: number;
  policy_id: number;
  provider_id: number;
  exchange_account_id: number;
  trigger_source: string;
  status: string;
  action: string;
  target_strategy_id: number | null;
  confidence: number;
  rationale: string | null;
  factors: Record<string, unknown>;
  context: Record<string, unknown>;
  raw_response: Record<string, unknown>;
  execution_result: Record<string, unknown>;
  created_at: string;
};

export async function listAiProviders() {
  await ensureSession();
  const resp = await http.get("/api/ai/providers");
  return resp.data as AiProviderItem[];
}

export async function createAiProvider(payload: AiProviderPayload, stepUpToken: string) {
  await ensureSession();
  const resp = await http.post("/api/ai/providers", payload, {
    headers: buildStepUpHeaders(stepUpToken)
  });
  return resp.data as AiProviderItem;
}

export async function updateAiProvider(providerId: number, payload: AiProviderUpdatePayload, stepUpToken: string) {
  await ensureSession();
  const resp = await http.put(`/api/ai/providers/${providerId}`, payload, {
    headers: buildStepUpHeaders(stepUpToken)
  });
  return resp.data as AiProviderItem;
}

export async function listAiPolicies() {
  await ensureSession();
  const resp = await http.get("/api/ai/policies");
  return resp.data as AiPolicyItem[];
}

export async function createAiPolicy(payload: AiPolicyPayload, stepUpToken: string) {
  await ensureSession();
  const resp = await http.post("/api/ai/policies", payload, {
    headers: buildStepUpHeaders(stepUpToken)
  });
  return resp.data as AiPolicyItem;
}

export async function updateAiPolicy(policyId: number, payload: AiPolicyPayload, stepUpToken: string) {
  await ensureSession();
  const resp = await http.put(`/api/ai/policies/${policyId}`, payload, {
    headers: buildStepUpHeaders(stepUpToken)
  });
  return resp.data as AiPolicyItem;
}

export async function enableAiPolicy(policyId: number, stepUpToken: string) {
  await ensureSession();
  const resp = await http.post(`/api/ai/policies/${policyId}/enable`, {}, {
    headers: buildStepUpHeaders(stepUpToken)
  });
  return resp.data as AiPolicyItem;
}

export async function disableAiPolicy(policyId: number, stepUpToken: string) {
  await ensureSession();
  const resp = await http.post(`/api/ai/policies/${policyId}/disable`, {}, {
    headers: buildStepUpHeaders(stepUpToken)
  });
  return resp.data as AiPolicyItem;
}

export async function runAiPolicy(policyId: number, stepUpToken: string, dry_run_override?: boolean | null) {
  await ensureSession();
  const resp = await http.post(
    `/api/ai/policies/${policyId}/run`,
    { dry_run_override: dry_run_override ?? null },
    {
      headers: buildStepUpHeaders(stepUpToken)
    }
  );
  return resp.data as AiDecisionItem;
}

export async function listAiDecisions(policyId?: number, limit = 50) {
  await ensureSession();
  const resp = await http.get("/api/ai/decisions", {
    params: {
      policy_id: policyId,
      limit
    }
  });
  return resp.data as AiDecisionItem[];
}

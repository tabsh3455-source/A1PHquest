<template>
  <AppShell
    :title="t('settings.title')"
    :subtitle="t('settings.subtitle')"
  >
    <template #toolbar>
      <router-link class="aq-auth-link settings-toolbar-link" to="/ops">{{ t("settings.openOps") }}</router-link>
      <el-button type="primary" @click="reload" :loading="loading">{{ t("settings.refresh") }}</el-button>
    </template>

    <el-alert
      v-if="feedbackMessage"
      :title="feedbackMessage"
      :type="feedbackType"
      show-icon
      class="aq-fade-up"
    />

    <section class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Runtime Override State</h2>
          <p class="aq-section-copy">
            This control plane only governs market-data runtime behavior. It does not change deployment secrets, exchange credentials, or server bootstrap settings.
          </p>
        </div>
        <span class="aq-chip">{{ settings.has_overrides ? "Database override active" : "Deploy defaults in use" }}</span>
      </div>

      <div class="aq-summary-strip">
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Storage Mode</span>
          <strong class="aq-metric-value">{{ settings.has_overrides ? "DB" : "ENV" }}</strong>
          <span class="aq-metric-copy">Overrides live in the database and can be cleared at any time.</span>
        </div>
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Reconnect Base</span>
          <strong class="aq-metric-value">{{ form.market_ws_reconnect_base_seconds }}s</strong>
          <span class="aq-metric-copy">First backoff interval before the streamer retries a broken feed.</span>
        </div>
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Idle Timeout</span>
          <strong class="aq-metric-value">{{ form.market_ws_idle_timeout_seconds }}s</strong>
          <span class="aq-metric-copy">Silence window before a market connection is marked stale.</span>
        </div>
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Cache / Backfill</span>
          <strong class="aq-metric-value">{{ form.market_candle_cache_size }} / {{ form.market_rest_backfill_limit }}</strong>
          <span class="aq-metric-copy">Warm candle window and REST seed depth for cold starts or reconnects.</span>
        </div>
      </div>
    </section>

    <section class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Market Data Runtime Profile</h2>
          <p class="aq-section-copy">
            Shape reconnect cadence, stale detection, memory depth, and backfill behavior from one operator surface.
          </p>
        </div>
      </div>

      <div class="settings-stage">
        <section class="aq-soft-block aq-stack">
          <div>
            <h3>Connection Recovery</h3>
            <p class="aq-form-note">Lower values react faster but can reconnect too aggressively on flaky networks.</p>
          </div>
          <el-form label-position="top">
            <el-form-item label="Reconnect Base Seconds">
              <el-input-number
                v-model="form.market_ws_reconnect_base_seconds"
                :min="0.5"
                :max="30"
                :step="0.5"
                :precision="1"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="Reconnect Max Seconds">
              <el-input-number
                v-model="form.market_ws_reconnect_max_seconds"
                :min="1"
                :max="120"
                :step="1"
                :precision="1"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="Idle Timeout Seconds">
              <el-input-number
                v-model="form.market_ws_idle_timeout_seconds"
                :min="5"
                :max="120"
                :step="1"
                :precision="1"
                style="width: 100%"
              />
            </el-form-item>
          </el-form>
        </section>

        <section class="aq-soft-block aq-stack">
          <div>
            <h3>Candle Memory</h3>
            <p class="aq-form-note">Increase depth if you want longer warm history, but keep backfill lower than total cache.</p>
          </div>
          <el-form label-position="top">
            <el-form-item label="Candle Cache Size">
              <el-input-number
                v-model="form.market_candle_cache_size"
                :min="100"
                :max="5000"
                :step="50"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="REST Backfill Limit">
              <el-input-number
                v-model="form.market_rest_backfill_limit"
                :min="10"
                :max="2000"
                :step="10"
                style="width: 100%"
              />
            </el-form-item>
          </el-form>
        </section>
      </div>
    </section>

    <section class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Live Risk Guardrails</h2>
          <p class="aq-section-copy">
            Live order placement and live strategy starts are fail-closed. Configure a risk rule here before enabling live runtime.
          </p>
        </div>
        <span class="aq-chip">{{ riskRuleConfigured ? "Live guardrails configured" : "Live blocked by risk setup" }}</span>
      </div>

      <el-alert
        v-if="!riskRuleConfigured"
        title="Risk rule is not configured yet. Live trading remains blocked until you save this form."
        type="warning"
        show-icon
      />

      <div class="settings-stage">
        <section class="aq-soft-block aq-stack">
          <div>
            <h3>Risk Limits</h3>
            <p class="aq-form-note">Set 0 to disable notional or daily-loss caps, keep position ratio and cancel-rate as mandatory hard limits.</p>
          </div>
          <el-form label-position="top">
            <el-form-item label="Max Order Notional (quote)">
              <el-input-number
                v-model="riskForm.max_order_notional"
                :min="0"
                :step="10"
                :precision="2"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="Max Daily Realized Loss (quote)">
              <el-input-number
                v-model="riskForm.max_daily_loss"
                :min="0"
                :step="10"
                :precision="2"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="Max Position Ratio (0-1)">
              <el-input-number
                v-model="riskForm.max_position_ratio"
                :min="0.01"
                :max="1"
                :step="0.01"
                :precision="2"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="Max Cancel Rate / Minute">
              <el-input-number
                v-model="riskForm.max_cancel_rate_per_minute"
                :min="1"
                :max="500"
                :step="1"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="Circuit Breaker Enabled">
              <el-switch v-model="riskForm.circuit_breaker_enabled" />
            </el-form-item>
          </el-form>
        </section>

        <section class="aq-soft-block aq-stack">
          <div>
            <h3>Risk Runtime State</h3>
            <p class="aq-form-note">Once saved, these guardrails are enforced on order submit and strategy live-start paths.</p>
          </div>
          <div class="aq-note-list">
            <div class="aq-note-row">
              <strong>Live gate</strong>
              <small>{{ riskRuleConfigured ? "Open (risk rule configured)" : "Closed (risk setup required)" }}</small>
            </div>
            <div class="aq-note-row">
              <strong>Last risk update</strong>
              <small>{{ riskRuleUpdatedAt || "Not configured yet." }}</small>
            </div>
            <div class="aq-note-row">
              <strong>Current limits summary</strong>
              <small>
                notional={{ riskForm.max_order_notional }},
                daily_loss={{ riskForm.max_daily_loss }},
                ratio={{ riskForm.max_position_ratio }},
                cancel_rate={{ riskForm.max_cancel_rate_per_minute }}/min
              </small>
            </div>
          </div>
        </section>
      </div>
    </section>

    <section class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Deploy Snapshot</h2>
          <p class="aq-section-copy">
            These are the install-time defaults the stack will fall back to when you clear database overrides.
          </p>
        </div>
      </div>

      <div class="aq-note-list">
        <div class="aq-note-row">
          <strong>Reconnect base / max</strong>
          <small>
            {{ settings.default_values.market_ws_reconnect_base_seconds ?? "-" }}s /
            {{ settings.default_values.market_ws_reconnect_max_seconds ?? "-" }}s
          </small>
        </div>
        <div class="aq-note-row">
          <strong>Idle timeout</strong>
          <small>{{ settings.default_values.market_ws_idle_timeout_seconds ?? "-" }}s before the feed is marked stale.</small>
        </div>
        <div class="aq-note-row">
          <strong>Cache and cold backfill</strong>
          <small>
            cache={{ settings.default_values.market_candle_cache_size ?? "-" }},
            backfill={{ settings.default_values.market_rest_backfill_limit ?? "-" }}
          </small>
        </div>
      </div>
    </section>

    <template #inspector>
      <section class="aq-soft-block aq-stack">
        <div>
          <h3>Access Control</h3>
          <p class="aq-form-note">Saving or resetting runtime overrides requires a fresh 2FA step-up token.</p>
        </div>
        <el-form label-position="top">
          <el-form-item label="Current 2FA Code">
            <el-input v-model="stepUpCode" maxlength="6" placeholder="Enter current 2FA code" />
          </el-form-item>
          <el-form-item>
            <el-space wrap>
              <el-button type="primary" :loading="stepUpLoading" @click="issueStepUpToken">Issue Settings Token</el-button>
              <el-tag :type="stepUpTokenValid ? 'success' : 'info'">
                {{ stepUpTokenValid ? `Ready / ${stepUpRemainingLabel}` : "No active token" }}
              </el-tag>
            </el-space>
          </el-form-item>
        </el-form>
      </section>

      <section class="aq-soft-block aq-stack">
        <div>
          <h3>Quick Actions</h3>
          <p class="aq-form-note">Persist market-data overrides, save risk guardrails, discard local edits, or roll back to deploy defaults.</p>
        </div>
        <el-space wrap>
          <el-button type="primary" :loading="saveLoading" @click="saveSettings">Save Settings</el-button>
          <el-button type="warning" :loading="riskSaveLoading" @click="saveRiskRule">Save Risk Rule</el-button>
          <el-button :loading="resetLoading" @click="resetToDefaults">Reset To Defaults</el-button>
          <el-button @click="restoreFromServer">Discard Local Edits</el-button>
        </el-space>
        <div class="aq-note-list">
          <div class="aq-note-row">
            <strong>Last updated</strong>
            <small>{{ settings.updated_at || "No override has been saved yet." }}</small>
          </div>
          <div class="aq-note-row">
            <strong>Updated by user</strong>
            <small>{{ settings.updated_by_user_id || "-" }}</small>
          </div>
          <div class="aq-note-row">
            <strong>Risk rule state</strong>
            <small>{{ riskRuleConfigured ? "Configured" : "Not configured" }}</small>
          </div>
        </div>
      </section>

      <section class="aq-soft-block aq-stack">
        <div>
          <h3>Guardrails</h3>
          <p class="aq-form-note">The form validates basic bounds before the server accepts a change.</p>
        </div>
        <div class="aq-note-list">
          <div class="aq-note-row">
            <strong>Reconnect max must stay above reconnect base.</strong>
            <small>This keeps exponential backoff from collapsing into an invalid schedule.</small>
          </div>
          <div class="aq-note-row">
            <strong>Backfill cannot exceed cache.</strong>
            <small>Cold starts must fit inside the warm memory window reserved for each market feed.</small>
          </div>
        </div>
      </section>
    </template>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import AppShell from "../components/AppShell.vue";
import { useI18n } from "../i18n";
import {
  ensureSession,
  getRiskRule,
  getMarketDataSettings,
  hasConfiguredRiskRule,
  notifyWorkflowReadinessRefresh,
  requestStepUpToken,
  resetMarketDataSettings,
  upsertRiskRule,
  updateMarketDataSettings,
  type MarketDataSettings,
  type RiskRulePayload
} from "../api";

const { t } = useI18n();
type EditableMarketDataSettings = Omit<
  MarketDataSettings,
  "has_overrides" | "updated_at" | "updated_by_user_id" | "default_values"
>;

const loading = ref(false);
const saveLoading = ref(false);
const resetLoading = ref(false);
const riskSaveLoading = ref(false);
const stepUpLoading = ref(false);
const feedbackMessage = ref("");
const feedbackType = ref<"success" | "warning" | "error" | "info">("info");
const stepUpCode = ref("");
const stepUpToken = ref("");
const stepUpExpireAt = ref<number | null>(null);
const riskRuleConfigured = ref(false);
const riskRuleUpdatedAt = ref<string | null>(null);
const settings = reactive<MarketDataSettings>({
  market_ws_reconnect_base_seconds: 1,
  market_ws_reconnect_max_seconds: 15,
  market_ws_idle_timeout_seconds: 25,
  market_candle_cache_size: 1000,
  market_rest_backfill_limit: 500,
  has_overrides: false,
  updated_at: null,
  updated_by_user_id: null,
  default_values: {}
});
const form = reactive<EditableMarketDataSettings>({
  market_ws_reconnect_base_seconds: 1,
  market_ws_reconnect_max_seconds: 15,
  market_ws_idle_timeout_seconds: 25,
  market_candle_cache_size: 1000,
  market_rest_backfill_limit: 500
});
const riskForm = reactive<RiskRulePayload>({
  max_order_notional: 0,
  max_daily_loss: 0,
  max_position_ratio: 1,
  max_cancel_rate_per_minute: 60,
  circuit_breaker_enabled: true
});

const stepUpTokenValid = computed(() => Boolean(stepUpToken.value && stepUpExpireAt.value && stepUpExpireAt.value > Date.now()));
const stepUpRemainingLabel = computed(() => {
  if (!stepUpExpireAt.value) {
    return "0s";
  }
  return `${Math.max(Math.floor((stepUpExpireAt.value - Date.now()) / 1000), 0)}s`;
});

async function ensureSessionOrRedirect() {
  await ensureSession();
}

function ensureStepUpToken() {
  if (!stepUpTokenValid.value) {
    throw new Error("Issue a fresh settings token before saving global configuration.");
  }
  return stepUpToken.value;
}

function setFeedback(message: string, type: "success" | "warning" | "error" | "info" = "info") {
  feedbackMessage.value = message;
  feedbackType.value = type;
}

function applySettings(next: MarketDataSettings) {
  settings.market_ws_reconnect_base_seconds = next.market_ws_reconnect_base_seconds;
  settings.market_ws_reconnect_max_seconds = next.market_ws_reconnect_max_seconds;
  settings.market_ws_idle_timeout_seconds = next.market_ws_idle_timeout_seconds;
  settings.market_candle_cache_size = next.market_candle_cache_size;
  settings.market_rest_backfill_limit = next.market_rest_backfill_limit;
  settings.has_overrides = next.has_overrides;
  settings.updated_at = next.updated_at;
  settings.updated_by_user_id = next.updated_by_user_id;
  settings.default_values = next.default_values || {};

  form.market_ws_reconnect_base_seconds = next.market_ws_reconnect_base_seconds;
  form.market_ws_reconnect_max_seconds = next.market_ws_reconnect_max_seconds;
  form.market_ws_idle_timeout_seconds = next.market_ws_idle_timeout_seconds;
  form.market_candle_cache_size = next.market_candle_cache_size;
  form.market_rest_backfill_limit = next.market_rest_backfill_limit;
}

function validateForm() {
  if (form.market_ws_reconnect_max_seconds < form.market_ws_reconnect_base_seconds) {
    throw new Error("Reconnect max seconds must be greater than or equal to reconnect base seconds.");
  }
  if (form.market_rest_backfill_limit > form.market_candle_cache_size) {
    throw new Error("REST backfill limit cannot be larger than candle cache size.");
  }
}

function applyRiskRule(payload: RiskRulePayload, updatedAt: string | null) {
  riskForm.max_order_notional = payload.max_order_notional;
  riskForm.max_daily_loss = payload.max_daily_loss;
  riskForm.max_position_ratio = payload.max_position_ratio;
  riskForm.max_cancel_rate_per_minute = payload.max_cancel_rate_per_minute;
  riskForm.circuit_breaker_enabled = payload.circuit_breaker_enabled;
  riskRuleUpdatedAt.value = updatedAt;
}

function validateRiskForm() {
  if (riskForm.max_order_notional < 0) {
    throw new Error("Max order notional cannot be negative.");
  }
  if (riskForm.max_daily_loss < 0) {
    throw new Error("Max daily loss cannot be negative.");
  }
  if (riskForm.max_position_ratio <= 0 || riskForm.max_position_ratio > 1) {
    throw new Error("Max position ratio must be between 0 and 1.");
  }
  if (riskForm.max_cancel_rate_per_minute <= 0) {
    throw new Error("Max cancel rate per minute must be greater than 0.");
  }
}

async function reloadRiskRule() {
  const configured = await hasConfiguredRiskRule();
  if (!configured) {
    riskRuleConfigured.value = false;
    applyRiskRule(
      {
        max_order_notional: 0,
        max_daily_loss: 0,
        max_position_ratio: 1,
        max_cancel_rate_per_minute: 60,
        circuit_breaker_enabled: true
      },
      null
    );
    return;
  }
  const rule = await getRiskRule();
  riskRuleConfigured.value = true;
  applyRiskRule(rule, rule.updated_at || null);
}

async function reload() {
  try {
    loading.value = true;
    await ensureSessionOrRedirect();
    const [response] = await Promise.all([getMarketDataSettings(), reloadRiskRule()]);
    applySettings(response);
    setFeedback("Settings loaded from the active deployment.", "info");
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || "Failed to load system settings.", "error");
  } finally {
    loading.value = false;
  }
}

function restoreFromServer() {
  applySettings({ ...settings });
  setFeedback("Local edits discarded. Form restored from the current server state.", "info");
}

async function issueStepUpToken() {
  if (!stepUpCode.value.trim()) {
    setFeedback("Enter a current 2FA code first.", "warning");
    return;
  }
  try {
    stepUpLoading.value = true;
    await ensureSessionOrRedirect();
    const response = await requestStepUpToken(stepUpCode.value.trim());
    stepUpToken.value = response.step_up_token;
    stepUpExpireAt.value = Date.now() + response.expires_in_seconds * 1000;
    setFeedback("Settings token issued. You can now save or reset global configuration.", "success");
  } catch (error: any) {
    stepUpToken.value = "";
    stepUpExpireAt.value = null;
    setFeedback(error?.response?.data?.detail || "Failed to issue settings token.", "error");
  } finally {
    stepUpLoading.value = false;
  }
}

async function saveSettings() {
  try {
    validateForm();
    saveLoading.value = true;
    await ensureSessionOrRedirect();
    const response = await updateMarketDataSettings({ ...form }, ensureStepUpToken());
    applySettings(response);
    setFeedback("Market data runtime settings saved and hot-applied.", "success");
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to save system settings.", "error");
  } finally {
    saveLoading.value = false;
  }
}

async function saveRiskRule() {
  try {
    validateRiskForm();
    riskSaveLoading.value = true;
    await ensureSessionOrRedirect();
    const response = await upsertRiskRule({ ...riskForm }, ensureStepUpToken());
    riskRuleConfigured.value = true;
    applyRiskRule(response, response.updated_at || null);
    notifyWorkflowReadinessRefresh();
    setFeedback("Risk rule saved. Live orders and live strategy start are now unblocked.", "success");
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to save risk rule.", "error");
  } finally {
    riskSaveLoading.value = false;
  }
}

async function resetToDefaults() {
  try {
    resetLoading.value = true;
    await ensureSessionOrRedirect();
    const response = await resetMarketDataSettings(ensureStepUpToken());
    applySettings(response);
    setFeedback("Database overrides cleared. The deployment is now using environment defaults again.", "success");
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to reset system settings.", "error");
  } finally {
    resetLoading.value = false;
  }
}

onMounted(async () => {
  await reload();
});
</script>

<style scoped>
.settings-toolbar-link {
  min-width: 120px;
}

.settings-stage {
  display: grid;
  gap: 16px;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
}
</style>

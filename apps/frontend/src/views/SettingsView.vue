<template>
  <AppShell>
    <template #toolbar>
      <el-button @click="toDashboard">Dashboard</el-button>
      <el-button @click="toOps">Ops</el-button>
      <el-button type="primary" @click="reload" :loading="loading">Refresh</el-button>
    </template>

    <div class="aq-panel aq-fade-up">
      <div class="aq-title-row">
        <div>
          <h1>System Settings</h1>
          <p class="aq-subtitle">
            Configure low-latency market data behavior from the web UI. These values are stored in the database and hot-applied without editing `.env`.
          </p>
        </div>
        <el-tag :type="settings.has_overrides ? 'success' : 'info'">
          {{ settings.has_overrides ? "Custom overrides active" : "Using deploy defaults" }}
        </el-tag>
      </div>

      <el-alert
        v-if="feedbackMessage"
        :title="feedbackMessage"
        :type="feedbackType"
        show-icon
        style="margin-top: 14px"
      />

      <div class="settings-grid">
        <section class="aq-soft-block">
          <h2>Access Control</h2>
          <p class="settings-copy">
            Saving global runtime settings requires a short-lived 2FA step-up token.
          </p>

          <el-form label-width="150px" class="settings-form">
            <el-form-item label="2FA Code">
              <el-input v-model="stepUpCode" maxlength="6" placeholder="Enter current 2FA code" />
            </el-form-item>
            <el-form-item>
              <el-space wrap>
                <el-button type="primary" :loading="stepUpLoading" @click="issueStepUpToken">
                  Issue Settings Token
                </el-button>
                <el-tag :type="stepUpTokenValid ? 'success' : 'info'">
                  {{ stepUpTokenValid ? `Token ready (${stepUpRemainingLabel})` : "No active token" }}
                </el-tag>
              </el-space>
            </el-form-item>
          </el-form>
        </section>

        <section class="aq-soft-block">
          <h2>Current State</h2>
          <p class="settings-copy">
            Deployment defaults remain available. Use reset to drop database overrides and return to those defaults.
          </p>

          <el-descriptions :column="1" border size="small" class="settings-descriptions">
            <el-descriptions-item label="Storage Mode">
              {{ settings.has_overrides ? "Database override" : "Environment default" }}
            </el-descriptions-item>
            <el-descriptions-item label="Last Updated">
              {{ settings.updated_at || "-" }}
            </el-descriptions-item>
            <el-descriptions-item label="Updated By User">
              {{ settings.updated_by_user_id || "-" }}
            </el-descriptions-item>
            <el-descriptions-item label="Deploy Default Snapshot">
              reconnect={{ settings.default_values.market_ws_reconnect_base_seconds ?? "-" }}s /
              max={{ settings.default_values.market_ws_reconnect_max_seconds ?? "-" }}s /
              idle={{ settings.default_values.market_ws_idle_timeout_seconds ?? "-" }}s /
              cache={{ settings.default_values.market_candle_cache_size ?? "-" }} /
              backfill={{ settings.default_values.market_rest_backfill_limit ?? "-" }}
            </el-descriptions-item>
          </el-descriptions>
        </section>
      </div>

      <el-divider />

      <div class="aq-title-row">
        <div>
          <h2>Market Data Runtime</h2>
          <p class="aq-subtitle">
            Tune reconnect behavior, idle detection, candle cache depth, and REST cold-start backfill.
          </p>
        </div>
      </div>

      <el-form label-width="210px" class="settings-form">
        <el-form-item label="Reconnect Base Seconds">
          <el-input-number v-model="form.market_ws_reconnect_base_seconds" :min="0.5" :max="30" :step="0.5" :precision="1" />
        </el-form-item>
        <el-form-item label="Reconnect Max Seconds">
          <el-input-number v-model="form.market_ws_reconnect_max_seconds" :min="1" :max="120" :step="1" :precision="1" />
        </el-form-item>
        <el-form-item label="Idle Timeout Seconds">
          <el-input-number v-model="form.market_ws_idle_timeout_seconds" :min="5" :max="120" :step="1" :precision="1" />
        </el-form-item>
        <el-form-item label="Candle Cache Size">
          <el-input-number v-model="form.market_candle_cache_size" :min="100" :max="5000" :step="50" />
        </el-form-item>
        <el-form-item label="REST Backfill Limit">
          <el-input-number v-model="form.market_rest_backfill_limit" :min="10" :max="2000" :step="10" />
        </el-form-item>
        <el-form-item>
          <el-space wrap>
            <el-button type="primary" :loading="saveLoading" @click="saveSettings">Save Settings</el-button>
            <el-button :loading="resetLoading" @click="resetToDefaults">Reset To Defaults</el-button>
            <el-button @click="restoreFromServer">Discard Local Changes</el-button>
          </el-space>
        </el-form-item>
      </el-form>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import AppShell from "../components/AppShell.vue";
import {
  ensureSession,
  getMarketDataSettings,
  requestStepUpToken,
  resetMarketDataSettings,
  updateMarketDataSettings,
  type MarketDataSettings
} from "../api";

type EditableMarketDataSettings = Omit<
  MarketDataSettings,
  "has_overrides" | "updated_at" | "updated_by_user_id" | "default_values"
>;

const router = useRouter();
const loading = ref(false);
const saveLoading = ref(false);
const resetLoading = ref(false);
const stepUpLoading = ref(false);
const feedbackMessage = ref("");
const feedbackType = ref<"success" | "warning" | "error" | "info">("info");
const stepUpCode = ref("");
const stepUpToken = ref("");
const stepUpExpireAt = ref<number | null>(null);
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

const stepUpTokenValid = computed(() => Boolean(stepUpToken.value && stepUpExpireAt.value && stepUpExpireAt.value > Date.now()));
const stepUpRemainingLabel = computed(() => {
  if (!stepUpExpireAt.value) {
    return "0s";
  }
  return `${Math.max(Math.floor((stepUpExpireAt.value - Date.now()) / 1000), 0)}s`;
});

async function ensureSessionOrRedirect() {
  try {
    await ensureSession();
  } catch {
    router.push("/login");
    throw new Error("Login expired. Please sign in again.");
  }
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

async function reload() {
  try {
    loading.value = true;
    await ensureSessionOrRedirect();
    const response = await getMarketDataSettings();
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

function toDashboard() {
  router.push("/dashboard");
}

function toOps() {
  router.push("/ops");
}

onMounted(async () => {
  await reload();
});
</script>

<style scoped>
.settings-grid {
  margin-top: 16px;
  display: grid;
  gap: 16px;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
}

.settings-copy {
  margin: 8px 0 0;
  color: var(--aq-ink-soft);
  line-height: 1.6;
}

.settings-form {
  margin-top: 14px;
}

.settings-descriptions {
  margin-top: 14px;
}
</style>

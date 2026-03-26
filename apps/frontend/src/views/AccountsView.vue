<template>
  <AppShell
    title="Account Vault"
    subtitle="Store exchange credentials behind a short-lived control token, monitor live versus testnet coverage, and keep validation or sync actions inside one secure terminal."
  >
    <template #toolbar>
      <router-link class="aq-auth-link accounts-toolbar-link" to="/market">Open Market</router-link>
      <el-button @click="loadAccounts" :loading="loading">Refresh</el-button>
    </template>

    <el-alert
      v-if="message"
      :title="message"
      :type="messageType"
      show-icon
      class="aq-fade-up"
    />

    <section class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Coverage Snapshot</h2>
          <p class="aq-section-copy">
            This page is the credential vault for the runtime. Accounts stay encrypted server-side, while validation and sync still require a fresh 2FA control token.
          </p>
        </div>
        <span class="aq-chip">{{ stepUpToken ? `Step-up live / ${stepUpExpiresText}` : "Step-up required" }}</span>
      </div>

      <div class="aq-summary-strip">
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Saved Accounts</span>
          <strong class="aq-metric-value">{{ rows.length }}</strong>
          <span class="aq-metric-copy">Credential entries stored for runtime sync and strategy routing.</span>
        </div>
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Live Accounts</span>
          <strong class="aq-metric-value">{{ liveCount }}</strong>
          <span class="aq-metric-copy">Production venues currently available for strategy startup.</span>
        </div>
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Testnet Accounts</span>
          <strong class="aq-metric-value">{{ testnetCount }}</strong>
          <span class="aq-metric-copy">Sandbox routes you can use before switching a version to live.</span>
        </div>
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Venues Wired</span>
          <strong class="aq-metric-value">{{ venueCount }}</strong>
          <span class="aq-metric-copy">Binance, OKX, and Lighter footprints represented in your vault.</span>
        </div>
      </div>
    </section>

    <section class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Credential Ledger</h2>
          <p class="aq-section-copy">
            Validate keys, sync balances and positions, and keep a readable status trail without leaving the trading workspace.
          </p>
        </div>
        <span class="aq-chip">{{ rows.length ? "Vault active" : "No accounts yet" }}</span>
      </div>

      <div v-if="!rows.length && !loading" class="aq-empty-state">
        <div>
          <h3>No exchange accounts saved yet.</h3>
          <p>Issue a step-up token, add an account in the inspector, then validate it before creating live-ready strategies.</p>
        </div>
      </div>

      <div v-else class="aq-stack">
        <div class="accounts-table-desktop">
          <el-table :data="rows" v-loading="loading" style="width: 100%">
            <el-table-column prop="account_alias" label="Alias" min-width="180" />
            <el-table-column prop="exchange" label="Exchange" width="120" />
            <el-table-column label="Mode" width="110">
              <template #default="{ row }">
                <el-tag :type="row.is_testnet ? 'warning' : 'success'">
                  {{ row.is_testnet ? "Testnet" : "Live" }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="Created" min-width="180" />
            <el-table-column label="Status Trail" min-width="280">
              <template #default="{ row }">
                <span class="account-status-copy">{{ rowStatus[row.id] || "No validation or sync run yet." }}</span>
              </template>
            </el-table-column>
            <el-table-column label="Actions" min-width="200" fixed="right">
              <template #default="{ row }">
                <el-space wrap>
                  <el-button size="small" :loading="isBusy(row.id, 'validate')" @click="runValidate(row.id)">Validate</el-button>
                  <el-button size="small" type="primary" :loading="isBusy(row.id, 'sync')" @click="runSync(row.id)">Sync</el-button>
                </el-space>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div class="accounts-cards-mobile">
          <article v-for="row in rows" :key="row.id" class="aq-soft-block aq-stack">
            <div class="aq-title-row">
              <div>
                <h3>{{ row.account_alias }}</h3>
                <p class="aq-form-note">{{ row.exchange }} / {{ row.created_at }}</p>
              </div>
              <el-tag :type="row.is_testnet ? 'warning' : 'success'">
                {{ row.is_testnet ? "Testnet" : "Live" }}
              </el-tag>
            </div>
            <div class="account-status-copy">{{ rowStatus[row.id] || "No validation or sync run yet." }}</div>
            <el-space wrap>
              <el-button size="small" :loading="isBusy(row.id, 'validate')" @click="runValidate(row.id)">Validate</el-button>
              <el-button size="small" type="primary" :loading="isBusy(row.id, 'sync')" @click="runSync(row.id)">Sync</el-button>
            </el-space>
          </article>
        </div>
      </div>
    </section>

    <section class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Vault Operating Notes</h2>
          <p class="aq-section-copy">
            Keep credential hygiene tight so runtime control, AI routing, and strategy startup all stay predictable.
          </p>
        </div>
      </div>

      <div class="aq-note-list">
        <div class="aq-note-row">
          <strong>Use alias names as routing labels.</strong>
          <small>Give each key a desk-style alias so strategies, AI policies, and audits stay readable.</small>
        </div>
        <div class="aq-note-row">
          <strong>Validate before sync.</strong>
          <small>Run key validation first, then sync balances and positions to confirm permissions and market scope.</small>
        </div>
        <div class="aq-note-row">
          <strong>Keep live and testnet separate.</strong>
          <small>Use dedicated aliases for paper routes. It keeps template previews and runtime switching less error-prone.</small>
        </div>
      </div>
    </section>

    <template #inspector>
      <section class="aq-soft-block aq-stack">
        <div>
          <h3>Step-up Control</h3>
          <p class="aq-form-note">Creating, validating, or syncing credentials requires a fresh Google Authenticator code.</p>
        </div>
        <el-form label-position="top">
          <el-form-item label="Current 2FA Code">
            <el-input v-model="stepUpCode" maxlength="6" placeholder="Enter 6-digit OTP" />
          </el-form-item>
          <el-form-item>
            <el-space wrap>
              <el-button type="primary" :loading="stepUpLoading" @click="issueStepUpToken">Issue Token</el-button>
              <el-tag :type="stepUpToken ? 'success' : 'info'">
                {{ stepUpToken ? `Active / ${stepUpExpiresText}` : "Inactive" }}
              </el-tag>
            </el-space>
          </el-form-item>
        </el-form>
      </section>

      <section class="aq-soft-block aq-stack">
        <div>
          <h3>New Account</h3>
          <p class="aq-form-note">Store a new exchange route for strategy execution, balance sync, or autopilot decisions.</p>
        </div>

        <el-form label-position="top" class="aq-stack">
          <el-form-item label="Exchange">
            <el-segmented v-model="form.exchange" :options="exchangeOptions" />
          </el-form-item>
          <el-form-item label="Alias">
            <el-input v-model="form.account_alias" placeholder="main-okx / binance-grid-a" />
          </el-form-item>
          <el-form-item label="Testnet Route">
            <el-switch v-model="form.is_testnet" />
          </el-form-item>
          <el-form-item label="API Key">
            <el-input v-model="form.api_key" placeholder="Enter API key" />
          </el-form-item>
          <el-form-item label="API Secret">
            <el-input
              v-model="form.api_secret"
              type="password"
              show-password
              placeholder="Enter API secret"
            />
          </el-form-item>
          <el-form-item label="Passphrase">
            <el-input
              v-model="form.passphrase"
              :disabled="form.exchange !== 'okx'"
              placeholder="Required for OKX only"
            />
          </el-form-item>
          <el-space wrap>
            <el-button type="primary" :loading="creating" @click="submitCreate">Create Account</el-button>
            <el-button @click="loadAccounts" :loading="loading">Reload Vault</el-button>
          </el-space>
        </el-form>
      </section>

      <section class="aq-soft-block aq-stack">
        <div>
          <h3>Safety Rules</h3>
          <p class="aq-form-note">The system stores credentials server-side only. Keep live keys restricted to trading and read scopes.</p>
        </div>
        <div class="aq-note-list">
          <div class="aq-note-row">
            <strong>No withdrawals on bot keys.</strong>
            <small>Keep exchange API permissions limited to what the runtime actually needs.</small>
          </div>
          <div class="aq-note-row">
            <strong>Pair aliases with environment.</strong>
            <small>Use suffixes like <code>-test</code> or <code>-live</code> so mistakes stand out before startup.</small>
          </div>
        </div>
      </section>
    </template>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import AppShell from "../components/AppShell.vue";
import {
  createExchangeAccount,
  ensureSession,
  listExchangeAccounts,
  notifyWorkflowReadinessRefresh,
  requestStepUpToken,
  syncExchangeAccount,
  type ExchangeAccountCreatePayload,
  type ExchangeType,
  validateExchangeAccount
} from "../api";

const rows = ref<any[]>([]);
const loading = ref(false);
const creating = ref(false);
const stepUpLoading = ref(false);
const message = ref("");
const messageType = ref<"success" | "error" | "warning" | "info">("info");
const stepUpCode = ref("");
const stepUpToken = ref("");
const stepUpExpireAt = ref<number | null>(null);
const rowStatus = ref<Record<number, string>>({});
const busyMap = ref<Record<string, boolean>>({});

const form = reactive<ExchangeAccountCreatePayload>({
  exchange: "binance" as ExchangeType,
  account_alias: "",
  api_key: "",
  api_secret: "",
  passphrase: "",
  is_testnet: false
});

const exchangeOptions = [
  { label: "Binance", value: "binance" },
  { label: "OKX", value: "okx" },
  { label: "Lighter", value: "lighter" }
];

const liveCount = computed(() => rows.value.filter((row) => !row.is_testnet).length);
const testnetCount = computed(() => rows.value.filter((row) => row.is_testnet).length);
const venueCount = computed(() => new Set(rows.value.map((row) => row.exchange)).size);

const stepUpExpiresText = computed(() => {
  if (!stepUpExpireAt.value) {
    return "0s";
  }
  const left = Math.max(Math.floor((stepUpExpireAt.value - Date.now()) / 1000), 0);
  return `${left}s left`;
});

async function ensureSessionOrRedirect() {
  await ensureSession();
}

function requireStepUpToken() {
  if (!stepUpToken.value) {
    throw new Error("Issue a step-up token first.");
  }
  return stepUpToken.value;
}

function setMessage(text: string, type: "success" | "error" | "warning" | "info" = "info") {
  message.value = text;
  messageType.value = type;
}

function isBusy(accountId: number, action: "validate" | "sync") {
  return !!busyMap.value[`${accountId}:${action}`];
}

async function loadAccounts() {
  loading.value = true;
  try {
    await ensureSessionOrRedirect();
    rows.value = await listExchangeAccounts();
  } catch (error: any) {
    setMessage(error?.response?.data?.detail || error?.message || "Failed to load account list.", "error");
  } finally {
    loading.value = false;
  }
}

async function issueStepUpToken() {
  if (!/^\d{6}$/.test(stepUpCode.value.trim())) {
    setMessage("Enter a valid 6-digit OTP code.", "warning");
    return;
  }
  stepUpLoading.value = true;
  try {
    await ensureSessionOrRedirect();
    const data = await requestStepUpToken(stepUpCode.value.trim());
    stepUpToken.value = data.step_up_token;
    stepUpExpireAt.value = Date.now() + (data.expires_in_seconds || 0) * 1000;
    setMessage("Step-up token issued.", "success");
  } catch (error: any) {
    setMessage(error?.response?.data?.detail || error?.message || "Failed to issue step-up token.", "error");
  } finally {
    stepUpLoading.value = false;
  }
}

async function submitCreate() {
  if (!form.account_alias.trim() || !form.api_key.trim() || !form.api_secret.trim()) {
    setMessage("Alias, API key, and API secret are required.", "warning");
    return;
  }
  if (form.exchange === "okx" && !String(form.passphrase || "").trim()) {
    setMessage("OKX accounts require a passphrase.", "warning");
    return;
  }

  creating.value = true;
  try {
    await ensureSessionOrRedirect();
    await createExchangeAccount(
      {
        ...form,
        account_alias: form.account_alias.trim(),
        api_key: form.api_key.trim(),
        api_secret: form.api_secret.trim(),
        passphrase: form.exchange === "okx" ? String(form.passphrase || "").trim() : null
      },
      requireStepUpToken()
    );
    setMessage("Exchange account created successfully.", "success");
    form.api_key = "";
    form.api_secret = "";
    form.passphrase = "";
    notifyWorkflowReadinessRefresh();
    await loadAccounts();
  } catch (error: any) {
    setMessage(error?.response?.data?.detail || error?.message || "Failed to create account.", "error");
  } finally {
    creating.value = false;
  }
}

async function runValidate(accountId: number) {
  const key = `${accountId}:validate`;
  busyMap.value[key] = true;
  try {
    await ensureSessionOrRedirect();
    const result = await validateExchangeAccount(accountId, requireStepUpToken());
    rowStatus.value[accountId] = `Validate: ${result.validated ? "success" : "failed"} / ${result.message}`;
  } catch (error: any) {
    rowStatus.value[accountId] = `Validate error: ${error?.response?.data?.detail || error?.message || "Unknown error"}`;
  } finally {
    busyMap.value[key] = false;
  }
}

async function runSync(accountId: number) {
  const key = `${accountId}:sync`;
  busyMap.value[key] = true;
  try {
    await ensureSessionOrRedirect();
    const result = await syncExchangeAccount(accountId, requireStepUpToken());
    rowStatus.value[accountId] =
      `Sync complete: balances=${result.balances_synced}, positions=${result.positions_synced}, `
      + `orders=${result.orders_synced}, trades=${result.trades_synced}`;
  } catch (error: any) {
    rowStatus.value[accountId] = `Sync error: ${error?.response?.data?.detail || error?.message || "Unknown error"}`;
  } finally {
    busyMap.value[key] = false;
  }
}

onMounted(loadAccounts);
</script>

<style scoped>
.accounts-toolbar-link {
  min-width: 136px;
}

.account-status-copy {
  color: var(--aq-ink-soft);
  line-height: 1.6;
}

.accounts-cards-mobile {
  display: none;
  gap: 10px;
}

@media (max-width: 960px) {
  .accounts-table-desktop {
    display: none;
  }

  .accounts-cards-mobile {
    display: grid;
  }
}
</style>

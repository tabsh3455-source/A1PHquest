<template>
  <AppShell>
    <template #toolbar>
      <el-button @click="toDashboard">Dashboard</el-button>
    </template>

    <div class="aq-panel aq-fade-up">
      <div class="aq-title-row">
        <div>
          <h2>Exchange Accounts</h2>
          <p class="aq-subtitle">
            Store Binance, OKX, and Lighter credentials, then validate and sync them from one place.
          </p>
        </div>
        <span class="aq-chip">A1phquest Credentials</span>
      </div>

      <el-alert
        style="margin-top: 12px"
        type="info"
        :closable="false"
        title="High-risk actions such as create, validate, and sync require a short-lived step-up token."
      />

      <div class="aq-grid aq-grid-2" style="margin-top: 14px">
        <section class="aq-soft-block">
          <h3 class="section-title">1. Step-up Token</h3>
          <p class="aq-form-note">
            Enter your current Google Authenticator code to unlock account management actions.
          </p>
          <el-row :gutter="10">
            <el-col :xs="24" :md="11">
              <el-input v-model="stepUpCode" maxlength="6" placeholder="Enter 6-digit OTP" />
            </el-col>
            <el-col :xs="24" :md="13">
              <el-space wrap>
                <el-button type="primary" :loading="stepUpLoading" @click="issueStepUpToken">
                  Issue Token
                </el-button>
                <el-tag v-if="stepUpToken" type="success">
                  Active ({{ stepUpExpiresText }})
                </el-tag>
              </el-space>
            </el-col>
          </el-row>
        </section>

        <section class="aq-soft-block">
          <h3 class="section-title">2. Create Account</h3>
          <el-form label-position="top">
            <el-row :gutter="12">
              <el-col :xs="24" :md="8">
                <el-form-item label="Exchange">
                  <el-select v-model="form.exchange" style="width: 100%">
                    <el-option label="Binance" value="binance" />
                    <el-option label="OKX" value="okx" />
                    <el-option label="Lighter" value="lighter" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :md="8">
                <el-form-item label="Alias">
                  <el-input v-model="form.account_alias" placeholder="main-okx / trader-a" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :md="8">
                <el-form-item label="Testnet">
                  <el-switch v-model="form.is_testnet" />
                </el-form-item>
              </el-col>
            </el-row>

            <el-row :gutter="12">
              <el-col :xs="24" :md="12">
                <el-form-item label="API Key">
                  <el-input v-model="form.api_key" placeholder="Enter API key" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :md="12">
                <el-form-item label="API Secret">
                  <el-input
                    v-model="form.api_secret"
                    type="password"
                    show-password
                    placeholder="Enter API secret"
                  />
                </el-form-item>
              </el-col>
            </el-row>

            <el-row :gutter="12">
              <el-col :xs="24" :md="12">
                <el-form-item label="Passphrase (OKX only)">
                  <el-input
                    v-model="form.passphrase"
                    :disabled="form.exchange !== 'okx'"
                    placeholder="Required only for OKX"
                  />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :md="12" class="action-col">
                <el-space wrap>
                  <el-button type="primary" :loading="creating" @click="submitCreate">
                    Create Account
                  </el-button>
                  <el-button :loading="loading" @click="loadAccounts">Refresh</el-button>
                </el-space>
              </el-col>
            </el-row>
          </el-form>
        </section>
      </div>

      <el-alert
        v-if="message"
        style="margin-top: 12px"
        :title="message"
        :type="messageType"
        show-icon
      />

      <el-divider content-position="left">3. Account List</el-divider>
      <el-table :data="rows" v-loading="loading" style="width: 100%">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="exchange" label="Exchange" width="120" />
        <el-table-column prop="account_alias" label="Alias" min-width="160" />
        <el-table-column label="Testnet" width="100">
          <template #default="scope">
            <el-tag :type="scope.row.is_testnet ? 'warning' : 'success'">
              {{ scope.row.is_testnet ? "Yes" : "No" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="Created" min-width="180" />
        <el-table-column label="Actions" min-width="300">
          <template #default="scope">
            <el-space wrap>
              <el-button size="small" :loading="isBusy(scope.row.id, 'validate')" @click="runValidate(scope.row.id)">
                Validate
              </el-button>
              <el-button size="small" type="primary" :loading="isBusy(scope.row.id, 'sync')" @click="runSync(scope.row.id)">
                Sync
              </el-button>
            </el-space>
            <div v-if="rowStatus[scope.row.id]" class="row-status">
              {{ rowStatus[scope.row.id] }}
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import AppShell from "../components/AppShell.vue";
import {
  createExchangeAccount,
  ensureSession,
  listExchangeAccounts,
  requestStepUpToken,
  syncExchangeAccount,
  type ExchangeAccountCreatePayload,
  type ExchangeType,
  validateExchangeAccount
} from "../api";

const router = useRouter();
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

const stepUpExpiresText = computed(() => {
  if (!stepUpExpireAt.value) {
    return "0s";
  }
  const left = Math.max(Math.floor((stepUpExpireAt.value - Date.now()) / 1000), 0);
  return `${left}s left`;
});

async function ensureSessionOrRedirect() {
  try {
    await ensureSession();
  } catch {
    router.push("/login");
    throw new Error("Login expired. Please sign in again.");
  }
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

function toDashboard() {
  router.push("/dashboard");
}

onMounted(loadAccounts);
</script>

<style scoped>
.section-title {
  margin: 0 0 6px;
  color: var(--aq-ink-strong);
}

.action-col {
  display: flex;
  align-items: center;
}

.row-status {
  margin-top: 6px;
  color: var(--aq-ink-soft);
  font-size: 12px;
  line-height: 1.45;
}
</style>

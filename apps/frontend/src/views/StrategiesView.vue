<template>
  <AppShell>
    <template #toolbar>
      <el-button @click="toDashboard">Dashboard</el-button>
      <el-button @click="resetEditor">New Strategy</el-button>
      <el-button type="primary" @click="loadData" :loading="loading">Refresh</el-button>
    </template>

    <div class="aq-grid aq-grid-2 strategy-layout">
      <section class="aq-panel aq-fade-up">
        <div class="aq-title-row">
          <div>
            <h2>Strategy Workspace</h2>
            <p class="aq-subtitle">
              Create new strategies, edit stopped ones, keep old versions, and choose which one to run.
            </p>
          </div>
          <span class="aq-chip">{{ rows.length }} saved</span>
        </div>

        <el-alert
          v-if="feedbackMessage"
          :title="feedbackMessage"
          :type="feedbackType"
          show-icon
          style="margin-top: 12px"
        />

        <el-alert
          v-if="!accounts.length"
          title="Create or sync an exchange account first before saving strategies."
          type="warning"
          show-icon
          style="margin-top: 12px"
        />

        <el-form label-width="160px" class="strategy-form">
          <el-form-item label="Editor Mode">
            <el-tag :type="editingStrategyId ? 'warning' : 'success'">
              {{ editingStrategyId ? `Editing #${editingStrategyId}` : "Creating New" }}
            </el-tag>
          </el-form-item>
          <el-form-item label="Strategy Name">
            <el-input v-model="form.name" maxlength="128" show-word-limit />
          </el-form-item>
          <el-form-item label="Strategy Type">
            <el-select v-model="form.strategy_type" style="width: 220px">
              <el-option label="Grid" value="grid" />
              <el-option label="DCA" value="dca" />
            </el-select>
          </el-form-item>
          <el-form-item label="Exchange Account">
            <el-select v-model="form.exchange_account_id" style="width: 100%" filterable>
              <el-option
                v-for="account in accounts"
                :key="account.id"
                :label="`${account.account_alias} (${account.exchange})`"
                :value="account.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="Symbol">
            <el-input v-model="form.symbol" placeholder="BTCUSDT" />
          </el-form-item>

          <template v-if="form.strategy_type === 'grid'">
            <el-form-item label="Grid Count">
              <el-input-number v-model="form.grid_count" :min="2" :max="1000" />
            </el-form-item>
            <el-form-item label="Grid Step %">
              <el-input-number v-model="form.grid_step_pct" :min="0.0001" :max="100" :precision="4" />
            </el-form-item>
            <el-form-item label="Base Order Size">
              <el-input-number v-model="form.base_order_size" :min="0.00000001" :precision="8" />
            </el-form-item>
          </template>

          <template v-else>
            <el-form-item label="Cycle Seconds">
              <el-input-number v-model="form.cycle_seconds" :min="1" :max="86400" />
            </el-form-item>
            <el-form-item label="Amount Per Cycle">
              <el-input-number v-model="form.amount_per_cycle" :min="0.00000001" :precision="8" />
            </el-form-item>
          </template>

          <el-form-item>
            <el-space wrap>
              <el-button type="primary" :loading="saveLoading" :disabled="!canSubmit" @click="saveStrategy">
                {{ editingStrategyId ? "Save Changes" : "Create Strategy" }}
              </el-button>
              <el-button :loading="duplicateLoading" :disabled="!canSubmit" @click="saveAsNewStrategy">
                Save As New Strategy
              </el-button>
              <el-button :disabled="!editingStrategyId" @click="resetEditor">Clear Editor</el-button>
            </el-space>
          </el-form-item>
        </el-form>
      </section>

      <section class="aq-panel aq-fade-up">
        <div class="aq-title-row">
          <div>
            <h2>Execution Control</h2>
            <p class="aq-subtitle">
              Start or stop any saved strategy after issuing a short-lived step-up token.
            </p>
          </div>
        </div>

        <el-form label-width="150px">
          <el-form-item label="2FA Code">
            <el-input v-model="stepUpCode" maxlength="6" placeholder="Enter current 2FA code" />
          </el-form-item>
          <el-form-item>
            <el-space wrap>
              <el-button type="primary" :loading="stepUpLoading" @click="issueStepUpToken">Issue Control Token</el-button>
              <el-tag :type="stepUpTokenValid ? 'success' : 'info'">
                {{ stepUpTokenValid ? `Token ready (${stepUpRemainingLabel})` : "No active token" }}
              </el-tag>
            </el-space>
          </el-form-item>
        </el-form>

        <el-descriptions v-if="selectedStrategy" :column="1" border size="small" style="margin-top: 12px">
          <el-descriptions-item label="Selected Strategy">
            #{{ selectedStrategy.id }} {{ selectedStrategy.name }}
          </el-descriptions-item>
          <el-descriptions-item label="Type">
            {{ selectedStrategy.strategy_type }}
          </el-descriptions-item>
          <el-descriptions-item label="Status">
            <el-tag :type="statusType(selectedStrategy.status)">{{ selectedStrategy.status }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="Config Summary">
            {{ configSummary(selectedStrategy) }}
          </el-descriptions-item>
        </el-descriptions>

        <el-descriptions v-if="runtimeState && runtimeStrategyId === selectedStrategy?.id" :column="1" border size="small" style="margin-top: 12px">
          <el-descriptions-item label="Runtime Ref">
            {{ runtimeState.runtime_ref || "-" }}
          </el-descriptions-item>
          <el-descriptions-item label="Runtime Status">
            {{ runtimeState.status }}
          </el-descriptions-item>
          <el-descriptions-item label="Last Heartbeat">
            {{ runtimeState.last_heartbeat || "-" }}
          </el-descriptions-item>
          <el-descriptions-item label="Last Error">
            {{ runtimeState.last_error || "-" }}
          </el-descriptions-item>
          <el-descriptions-item label="Execution Counters">
            submitted={{ runtimeState.order_submitted_count }}, updates={{ runtimeState.order_update_count }}, fills={{ runtimeState.trade_fill_count }}
          </el-descriptions-item>
        </el-descriptions>
      </section>
    </div>

    <div class="aq-panel aq-fade-up" style="margin-top: 18px">
      <StrategyCandleChart
        :exchange-account-id="selectedExchangeAccountId"
        :exchange="selectedExchange"
        :symbol="selectedSymbol"
      />
    </div>

    <div class="aq-panel aq-fade-up" style="margin-top: 18px">
      <div class="aq-title-row">
        <div>
          <h2>Saved Strategies</h2>
          <p class="aq-subtitle">
            Click a row to inspect it. Use Edit to change a stopped strategy, or Duplicate to keep the old one and create a variation.
          </p>
        </div>
      </div>

      <el-table
        :data="rows"
        style="margin-top: 14px"
        v-loading="loading"
        highlight-current-row
        :row-class-name="rowClassName"
        @row-click="selectStrategy"
      >
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" label="Name" min-width="170" />
        <el-table-column prop="strategy_type" label="Type" width="110" />
        <el-table-column label="Symbol" width="140">
          <template #default="scope">
            {{ configValue(scope.row, "symbol") || "-" }}
          </template>
        </el-table-column>
        <el-table-column label="Account" min-width="170">
          <template #default="scope">
            {{ accountLabel(configValue(scope.row, "exchange_account_id")) }}
          </template>
        </el-table-column>
        <el-table-column label="Config" min-width="260">
          <template #default="scope">
            {{ configSummary(scope.row) }}
          </template>
        </el-table-column>
        <el-table-column prop="status" label="Status" width="120">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.status)">{{ scope.row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="runtime_ref" label="Runtime Ref" min-width="170" />
        <el-table-column label="Actions" width="390" fixed="right">
          <template #default="scope">
            <div class="action-row">
              <el-button size="small" @click.stop="editStrategy(scope.row)">Edit</el-button>
              <el-button size="small" @click.stop="duplicateStrategy(scope.row)">Duplicate</el-button>
              <el-button size="small" @click.stop="inspectRuntime(scope.row)">Runtime</el-button>
              <el-button
                v-if="isRunnable(scope.row.status)"
                size="small"
                type="primary"
                :loading="actionLoadingId === scope.row.id && actionMode === 'start'"
                @click.stop="startSelectedStrategy(scope.row)"
              >
                Start
              </el-button>
              <el-button
                v-else
                size="small"
                type="danger"
                :loading="actionLoadingId === scope.row.id && actionMode === 'stop'"
                @click.stop="stopSelectedStrategy(scope.row)"
              >
                Stop
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && !rows.length" description="No strategies yet" style="padding-top: 18px" />
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import AppShell from "../components/AppShell.vue";
import {
  createStrategy,
  ensureSession,
  getStrategyRuntime,
  listExchangeAccounts,
  listStrategies,
  requestStepUpToken,
  startStrategy as startStrategyRequest,
  stopStrategy as stopStrategyRequest,
  updateStrategy,
  type ExchangeAccountItem,
  type StrategyItem,
  type StrategyRuntime
} from "../api";

const StrategyCandleChart = defineAsyncComponent(() => import("../components/StrategyCandleChart.vue"));

type EditableStrategyType = "grid" | "dca";

type StrategyEditorState = {
  name: string;
  strategy_type: EditableStrategyType;
  exchange_account_id: number | null;
  symbol: string;
  grid_count: number;
  grid_step_pct: number;
  base_order_size: number;
  cycle_seconds: number;
  amount_per_cycle: number;
};

const router = useRouter();
const rows = ref<StrategyItem[]>([]);
const accounts = ref<ExchangeAccountItem[]>([]);
const loading = ref(false);
const saveLoading = ref(false);
const duplicateLoading = ref(false);
const stepUpLoading = ref(false);
const actionLoadingId = ref<number | null>(null);
const actionMode = ref<"start" | "stop" | null>(null);
const feedbackMessage = ref("");
const feedbackType = ref<"success" | "warning" | "error" | "info">("info");
const selectedStrategyId = ref<number | null>(null);
const editingStrategyId = ref<number | null>(null);
const runtimeState = ref<StrategyRuntime | null>(null);
const runtimeStrategyId = ref<number | null>(null);
const stepUpCode = ref("");
const stepUpToken = ref("");
const stepUpExpireAt = ref<number | null>(null);

const form = reactive<StrategyEditorState>({
  name: "grid-alpha",
  strategy_type: "grid",
  exchange_account_id: null,
  symbol: "BTCUSDT",
  grid_count: 20,
  grid_step_pct: 0.4,
  base_order_size: 0.001,
  cycle_seconds: 300,
  amount_per_cycle: 10
});

const selectedStrategy = computed(() => rows.value.find((item) => item.id === selectedStrategyId.value) || null);
const selectedExchangeAccountId = computed(() => {
  const value = Number(selectedStrategy.value?.config?.exchange_account_id || 0);
  return value > 0 ? value : null;
});
const selectedExchange = computed(() => {
  const account = accounts.value.find((item) => item.id === selectedExchangeAccountId.value);
  return account?.exchange || null;
});
const selectedSymbol = computed(() => {
  const symbol = String(selectedStrategy.value?.config?.symbol || "").trim().toUpperCase();
  return symbol || null;
});
const stepUpTokenValid = computed(() => Boolean(stepUpToken.value && stepUpExpireAt.value && stepUpExpireAt.value > Date.now()));
const stepUpRemainingLabel = computed(() => {
  if (!stepUpExpireAt.value) {
    return "0s";
  }
  return `${Math.max(Math.floor((stepUpExpireAt.value - Date.now()) / 1000), 0)}s`;
});
const canSubmit = computed(() => {
  return Boolean(form.name.trim() && form.symbol.trim() && form.exchange_account_id);
});

function setFeedback(message: string, type: "success" | "warning" | "error" | "info" = "info") {
  feedbackMessage.value = message;
  feedbackType.value = type;
}

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
    throw new Error("Issue a fresh control token before starting or stopping a strategy.");
  }
  return stepUpToken.value;
}

function resetEditor() {
  editingStrategyId.value = null;
  form.name = "grid-alpha";
  form.strategy_type = "grid";
  form.symbol = "BTCUSDT";
  form.grid_count = 20;
  form.grid_step_pct = 0.4;
  form.base_order_size = 0.001;
  form.cycle_seconds = 300;
  form.amount_per_cycle = 10;
  form.exchange_account_id = accounts.value[0]?.id ?? null;
  setFeedback("Editor reset. You can now create a fresh strategy.", "info");
}

function toDashboard() {
  router.push("/dashboard");
}

function isEditableType(strategyType: string): strategyType is EditableStrategyType {
  return strategyType === "grid" || strategyType === "dca";
}

function configValue(row: StrategyItem, key: string) {
  return row.config?.[key];
}

function accountLabel(accountId: unknown) {
  const id = Number(accountId || 0);
  const account = accounts.value.find((item) => item.id === id);
  return account ? `${account.account_alias} (${account.exchange})` : id ? `Account #${id}` : "-";
}

function configSummary(row: StrategyItem) {
  const symbol = String(configValue(row, "symbol") || "-");
  if (row.strategy_type === "grid") {
    return `symbol=${symbol}, grids=${configValue(row, "grid_count") || "-"}, step=${configValue(row, "grid_step_pct") || "-"}%, size=${configValue(row, "base_order_size") || "-"}`;
  }
  if (row.strategy_type === "dca") {
    return `symbol=${symbol}, cycle=${configValue(row, "cycle_seconds") || "-"}s, amount=${configValue(row, "amount_per_cycle") || "-"}`;
  }
  return JSON.stringify(row.config || {});
}

function rowClassName({ row }: { row: StrategyItem }) {
  return row.id === selectedStrategyId.value ? "is-selected-row" : "";
}

function statusType(status: string) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "running") {
    return "success";
  }
  if (normalized === "failed") {
    return "danger";
  }
  if (normalized === "starting" || normalized === "stopping") {
    return "warning";
  }
  return "info";
}

function isRunnable(status: string) {
  return !["running", "starting", "stopping"].includes(String(status || "").toLowerCase());
}

function selectStrategy(row: StrategyItem) {
  selectedStrategyId.value = row.id;
}

function loadEditorFromStrategy(row: StrategyItem, { duplicate = false }: { duplicate?: boolean } = {}) {
  if (!isEditableType(row.strategy_type)) {
    setFeedback(`The web editor currently supports grid and dca only. Strategy "${row.name}" stays view-only.`, "warning");
    return;
  }

  editingStrategyId.value = duplicate ? null : row.id;
  form.name = duplicate ? `${row.name}-copy` : row.name;
  form.strategy_type = row.strategy_type;
  form.exchange_account_id = Number(configValue(row, "exchange_account_id") || accounts.value[0]?.id || 0) || null;
  form.symbol = String(configValue(row, "symbol") || "BTCUSDT");
  form.grid_count = Number(configValue(row, "grid_count") || 20);
  form.grid_step_pct = Number(configValue(row, "grid_step_pct") || 0.4);
  form.base_order_size = Number(configValue(row, "base_order_size") || 0.001);
  form.cycle_seconds = Number(configValue(row, "cycle_seconds") || 300);
  form.amount_per_cycle = Number(configValue(row, "amount_per_cycle") || 10);
  selectedStrategyId.value = row.id;
  setFeedback(
    duplicate
      ? `Loaded strategy #${row.id} into the editor. Save it as a new strategy to keep the original.`
      : `Loaded strategy #${row.id} for editing.`,
    "info"
  );
}

function editStrategy(row: StrategyItem) {
  if (!isRunnable(row.status)) {
    setFeedback(`Strategy #${row.id} is currently active. Stop it before editing, or duplicate it into a new version.`, "warning");
    return;
  }
  loadEditorFromStrategy(row, { duplicate: false });
}

function duplicateStrategy(row: StrategyItem) {
  loadEditorFromStrategy(row, { duplicate: true });
}

function buildPayload() {
  const exchangeAccountId = Number(form.exchange_account_id || 0);
  const symbol = form.symbol.trim().toUpperCase();
  if (!form.name.trim()) {
    throw new Error("Strategy name is required.");
  }
  if (!exchangeAccountId) {
    throw new Error("Select an exchange account first.");
  }
  if (!symbol) {
    throw new Error("Symbol is required.");
  }

  const config: Record<string, unknown> = {
    exchange_account_id: exchangeAccountId,
    symbol
  };
  if (form.strategy_type === "grid") {
    config.grid_count = Number(form.grid_count);
    config.grid_step_pct = Number(form.grid_step_pct);
    config.base_order_size = Number(form.base_order_size);
  } else {
    config.cycle_seconds = Number(form.cycle_seconds);
    config.amount_per_cycle = Number(form.amount_per_cycle);
  }

  return {
    name: form.name.trim(),
    strategy_type: form.strategy_type,
    config
  };
}

async function loadData() {
  try {
    loading.value = true;
    await ensureSessionOrRedirect();
    const [strategies, exchangeAccounts] = await Promise.all([
      listStrategies(),
      listExchangeAccounts()
    ]);
    rows.value = strategies;
    accounts.value = exchangeAccounts;
    if (!form.exchange_account_id) {
      form.exchange_account_id = exchangeAccounts[0]?.id ?? null;
    }
    if (selectedStrategyId.value && !strategies.some((item) => item.id === selectedStrategyId.value)) {
      selectedStrategyId.value = null;
      runtimeState.value = null;
      runtimeStrategyId.value = null;
    }
    if (!selectedStrategyId.value && strategies.length) {
      selectedStrategyId.value = strategies[0].id;
    }
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || "Failed to load strategies or exchange accounts.", "error");
  } finally {
    loading.value = false;
  }
}

async function saveStrategy() {
  try {
    saveLoading.value = true;
    await ensureSessionOrRedirect();
    const wasEditing = Boolean(editingStrategyId.value);
    const payload = buildPayload();
    const strategy = editingStrategyId.value
      ? await updateStrategy(editingStrategyId.value, payload)
      : await createStrategy(payload);
    await loadData();
    selectedStrategyId.value = strategy.id;
    if (!editingStrategyId.value) {
      editingStrategyId.value = strategy.id;
    }
    setFeedback(
      wasEditing
        ? `Strategy #${strategy.id} saved successfully.`
        : `Strategy #${strategy.id} created successfully.`,
      "success"
    );
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to save strategy.", "error");
  } finally {
    saveLoading.value = false;
  }
}

async function saveAsNewStrategy() {
  try {
    duplicateLoading.value = true;
    await ensureSessionOrRedirect();
    const strategy = await createStrategy(buildPayload());
    await loadData();
    selectedStrategyId.value = strategy.id;
    editingStrategyId.value = null;
    setFeedback(`Strategy #${strategy.id} created as a new version. The old strategy was kept untouched.`, "success");
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to create a new strategy version.", "error");
  } finally {
    duplicateLoading.value = false;
  }
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
    setFeedback("Control token issued. You can now start or stop strategies.", "success");
  } catch (error: any) {
    stepUpToken.value = "";
    stepUpExpireAt.value = null;
    setFeedback(error?.response?.data?.detail || "Failed to issue control token.", "error");
  } finally {
    stepUpLoading.value = false;
  }
}

async function inspectRuntime(row: StrategyItem) {
  try {
    actionLoadingId.value = row.id;
    actionMode.value = null;
    await ensureSessionOrRedirect();
    runtimeState.value = await getStrategyRuntime(row.id);
    runtimeStrategyId.value = row.id;
    selectedStrategyId.value = row.id;
    setFeedback(`Runtime snapshot loaded for strategy #${row.id}.`, "info");
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || "Failed to load runtime state.", "error");
  } finally {
    actionLoadingId.value = null;
  }
}

async function startSelectedStrategy(row: StrategyItem) {
  try {
    actionLoadingId.value = row.id;
    actionMode.value = "start";
    await ensureSessionOrRedirect();
    const runtime = await startStrategyRequest(row.id, ensureStepUpToken());
    runtimeState.value = runtime;
    runtimeStrategyId.value = row.id;
    selectedStrategyId.value = row.id;
    await loadData();
    setFeedback(`Strategy #${row.id} started. It is now the active runtime you selected to use.`, "success");
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to start strategy.", "error");
  } finally {
    actionLoadingId.value = null;
    actionMode.value = null;
  }
}

async function stopSelectedStrategy(row: StrategyItem) {
  try {
    actionLoadingId.value = row.id;
    actionMode.value = "stop";
    await ensureSessionOrRedirect();
    const runtime = await stopStrategyRequest(row.id, ensureStepUpToken());
    runtimeState.value = runtime;
    runtimeStrategyId.value = row.id;
    selectedStrategyId.value = row.id;
    await loadData();
    setFeedback(`Strategy #${row.id} stopped. You can now edit it or start another saved strategy.`, "success");
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to stop strategy.", "error");
  } finally {
    actionLoadingId.value = null;
    actionMode.value = null;
  }
}

onMounted(async () => {
  await loadData();
  if (rows.value.length) {
    selectedStrategyId.value = rows.value[0].id;
  }
});
</script>

<style scoped>
.strategy-layout {
  align-items: start;
}

.strategy-form {
  margin-top: 14px;
}

.action-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

:deep(.is-selected-row) {
  --el-table-tr-bg-color: rgba(18, 78, 120, 0.08);
}
</style>

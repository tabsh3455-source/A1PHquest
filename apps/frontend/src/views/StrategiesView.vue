<template>
  <AppShell
    title="Strategy Library"
    subtitle="Browse template families, build strategy drafts or live-ready versions, keep old revisions, and switch runtime from the same workspace."
  >
    <template #toolbar>
      <el-button @click="loadData" :loading="loading">Refresh</el-button>
      <router-link class="aq-auth-link" to="/market">Open Market</router-link>
    </template>

    <StrategyCandleChart
      :mode="chartMode"
      :exchange-account-id="chartExchangeAccountId"
      :exchange="chartExchange"
      :market-type="chartMarketType"
      :symbol="chartSymbol"
      title="Strategy Context Chart"
      subtitle="Use the selected instance or the editor context to preview market structure before saving or switching versions."
      empty-message="Pick a template and symbol to preview the market."
    />

    <div class="aq-grid aq-grid-2 strategy-stage">
      <section class="aq-panel aq-fade-up">
        <div class="aq-title-row">
          <div>
            <h2>Template Gallery</h2>
            <p class="aq-subtitle">These templates are organized by trading posture, not just by runtime implementation.</p>
          </div>
        </div>
        <div class="template-gallery">
          <button
            v-for="item in templates"
            :key="item.template_key"
            class="template-card"
            :class="{ 'is-active': selectedTemplateKey === item.template_key }"
            type="button"
            @click="selectTemplate(item.template_key)"
          >
            <span class="template-state" :class="item.execution_status">
              {{ item.execution_status === "live_supported" ? "Live" : "Draft" }}
            </span>
            <strong>{{ item.display_name }}</strong>
            <small>{{ item.description }}</small>
            <div class="template-meta">
              <span>{{ item.category }}</span>
              <span>{{ item.market_scope }}</span>
              <span>{{ item.risk_level }}</span>
            </div>
          </button>
        </div>
      </section>

      <section class="aq-panel aq-fade-up">
        <div class="aq-title-row">
          <div>
            <h2>My Strategy Instances</h2>
            <p class="aq-subtitle">Saved strategies preserve versions. Draft-only templates can still be configured, cloned, and reviewed.</p>
          </div>
          <span class="aq-chip">{{ rows.length }} saved</span>
        </div>

        <el-alert
          v-if="feedbackMessage"
          style="margin-top: 12px"
          :title="feedbackMessage"
          :type="feedbackType"
          show-icon
        />

        <el-table
          :data="rows"
          style="margin-top: 14px"
          v-loading="loading"
          highlight-current-row
          :row-class-name="rowClassName"
          @row-click="selectStrategy"
        >
          <el-table-column prop="name" label="Name" min-width="180" />
          <el-table-column prop="template_display_name" label="Template" min-width="160" />
          <el-table-column label="Scope" width="110">
            <template #default="{ row }">{{ row.market_scope }}</template>
          </el-table-column>
          <el-table-column label="Symbol" width="140">
            <template #default="{ row }">{{ String(row.config.symbol || "-") }}</template>
          </el-table-column>
          <el-table-column label="Account" min-width="170">
            <template #default="{ row }">{{ accountLabel(row.config.exchange_account_id) }}</template>
          </el-table-column>
          <el-table-column label="Status" width="110">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="Run" width="110">
            <template #default="{ row }">
              <el-tag :type="row.live_supported ? 'success' : 'warning'">
                {{ row.live_supported ? "Live" : "Draft" }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="Actions" min-width="340" fixed="right">
            <template #default="{ row }">
              <el-space wrap>
                <el-button size="small" @click.stop="editStrategy(row)">Edit</el-button>
                <el-button size="small" @click.stop="duplicateStrategy(row)">Duplicate</el-button>
                <el-button size="small" @click.stop="inspectRuntime(row)">Runtime</el-button>
                <el-button
                  v-if="row.live_supported && isRunnable(row.status)"
                  size="small"
                  type="primary"
                  :loading="actionLoadingId === row.id && actionMode === 'start'"
                  @click.stop="startSelectedStrategy(row)"
                >
                  Start
                </el-button>
                <el-button
                  v-else-if="row.live_supported"
                  size="small"
                  type="danger"
                  :loading="actionLoadingId === row.id && actionMode === 'stop'"
                  @click.stop="stopSelectedStrategy(row)"
                >
                  Stop
                </el-button>
                <el-button v-else size="small" disabled>Draft only</el-button>
              </el-space>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </div>

    <template #inspector>
      <section class="aq-soft-block aq-stack">
        <div class="aq-title-row">
          <div>
            <h3>Composer</h3>
            <p class="aq-form-note">Template-driven editing replaces the old hardcoded grid/DCA form.</p>
          </div>
          <el-tag :type="editingStrategyId ? 'warning' : 'success'">
            {{ editingStrategyId ? `Editing #${editingStrategyId}` : "New Instance" }}
          </el-tag>
        </div>

        <el-alert
          v-if="!accounts.length"
          title="Create or sync an exchange account before saving strategies."
          type="warning"
          show-icon
        />

        <el-form label-position="top" class="aq-stack">
          <el-form-item label="Template">
            <el-select v-model="selectedTemplateKey" style="width: 100%" @change="applySelectedTemplate">
              <el-option v-for="item in templates" :key="item.template_key" :label="item.display_name" :value="item.template_key" />
            </el-select>
          </el-form-item>
          <el-form-item label="Name">
            <el-input v-model="form.name" maxlength="128" />
          </el-form-item>
          <el-form-item label="Chart Exchange">
            <el-segmented v-model="editorExchange" :options="exchangeOptions" />
          </el-form-item>
          <el-form-item label="Chart Market Type">
            <el-segmented v-model="editorMarketType" :options="marketTypeOptions" />
          </el-form-item>

          <template v-for="field in selectedTemplateFields" :key="field.key">
            <el-form-item :label="field.label">
              <el-select
                v-if="field.key === 'exchange_account_id'"
                v-model="form.config[field.key]"
                filterable
                style="width: 100%"
              >
                <el-option
                  v-for="account in accounts"
                  :key="account.id"
                  :label="`${account.account_alias} (${account.exchange})`"
                  :value="account.id"
                />
              </el-select>

              <el-select
                v-else-if="field.input_type === 'select'"
                v-model="form.config[field.key]"
                style="width: 100%"
              >
                <el-option v-for="option in field.options" :key="option.value" :label="option.label" :value="option.value" />
              </el-select>

              <el-input-number
                v-else-if="field.input_type === 'number'"
                v-model="form.config[field.key]"
                :min="field.min ?? undefined"
                :max="field.max ?? undefined"
                :step="Number(field.step ?? 1)"
                :precision="field.precision ?? undefined"
                style="width: 100%"
              />

              <el-switch v-else-if="field.input_type === 'switch'" v-model="form.config[field.key]" />

              <el-input
                v-else
                v-model="form.config[field.key]"
                :placeholder="field.description || field.label"
              />

              <div v-if="field.description" class="aq-form-note">{{ field.description }}</div>
            </el-form-item>
          </template>

          <el-space wrap>
            <el-button type="primary" :loading="saveLoading" :disabled="!canSubmit" @click="saveStrategy">
              {{ editingStrategyId ? "Save Changes" : "Create Strategy" }}
            </el-button>
            <el-button :loading="duplicateLoading" :disabled="!canSubmit" @click="saveAsNewStrategy">Save As New</el-button>
            <el-button @click="resetComposer">Reset</el-button>
          </el-space>
        </el-form>
      </section>

      <section class="aq-soft-block aq-stack">
        <div>
          <h3>Runtime Control</h3>
          <p class="aq-form-note">Starting or stopping a live-supported strategy still requires a fresh step-up token.</p>
        </div>
        <el-form label-position="top">
          <el-form-item label="2FA Code">
            <el-input v-model="stepUpCode" maxlength="6" placeholder="Enter current Google Authenticator code" />
          </el-form-item>
          <el-form-item>
            <el-space wrap>
              <el-button type="primary" :loading="stepUpLoading" @click="issueStepUpToken">Issue control token</el-button>
              <el-tag :type="stepUpTokenValid ? 'success' : 'info'">
                {{ stepUpTokenValid ? `Ready (${stepUpRemainingLabel})` : "No token" }}
              </el-tag>
            </el-space>
          </el-form-item>
        </el-form>

        <el-descriptions v-if="selectedStrategy" :column="1" border size="small">
          <el-descriptions-item label="Selected">{{ selectedStrategy.name }}</el-descriptions-item>
          <el-descriptions-item label="Template">{{ selectedStrategy.template_display_name }}</el-descriptions-item>
          <el-descriptions-item label="Status">
            <el-tag :type="statusType(selectedStrategy.status)">{{ selectedStrategy.status }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <el-descriptions v-if="runtimeState && runtimeStrategyId === selectedStrategy?.id" :column="1" border size="small">
          <el-descriptions-item label="Runtime Ref">{{ runtimeState.runtime_ref || "-" }}</el-descriptions-item>
          <el-descriptions-item label="Runtime Status">{{ runtimeState.status }}</el-descriptions-item>
          <el-descriptions-item label="Last Error">{{ runtimeState.last_error || "-" }}</el-descriptions-item>
          <el-descriptions-item label="Counters">
            submitted={{ runtimeState.order_submitted_count }}, updates={{ runtimeState.order_update_count }}, fills={{ runtimeState.trade_fill_count }}
          </el-descriptions-item>
        </el-descriptions>
      </section>
    </template>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";
import AppShell from "../components/AppShell.vue";
import StrategyCandleChart from "../components/StrategyCandleChart.vue";
import {
  createStrategy,
  ensureSession,
  getErrorMessage,
  getStrategyRuntime,
  listExchangeAccounts,
  listStrategies,
  listStrategyTemplates,
  requestStepUpToken,
  startStrategy as startStrategyRequest,
  stopStrategy as stopStrategyRequest,
  updateStrategy,
  type ExchangeAccountItem,
  type StrategyItem,
  type StrategyRuntime,
  type StrategyTemplateItem
} from "../api";

const route = useRoute();

const rows = ref<StrategyItem[]>([]);
const accounts = ref<ExchangeAccountItem[]>([]);
const templates = ref<StrategyTemplateItem[]>([]);
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
const selectedTemplateKey = ref("spot_grid");
const editorExchange = ref<"binance" | "okx">((String(route.query.exchange || "binance").toLowerCase() === "okx" ? "okx" : "binance"));
const editorMarketType = ref<"spot" | "perp">(String(route.query.market_type || "spot").toLowerCase() === "perp" ? "perp" : "spot");

const form = reactive<{
  name: string;
  config: Record<string, any>;
}>({
  name: "spot-grid-alpha",
  config: {}
});

const exchangeOptions = [
  { label: "Binance", value: "binance" },
  { label: "OKX", value: "okx" }
] as const;
const marketTypeOptions = [
  { label: "Spot", value: "spot" },
  { label: "Perp", value: "perp" }
] as const;

const selectedStrategy = computed(() => rows.value.find((item) => item.id === selectedStrategyId.value) || null);
const selectedTemplate = computed(() => templates.value.find((item) => item.template_key === selectedTemplateKey.value) || null);
const selectedTemplateFields = computed(() => selectedTemplate.value?.fields || []);
const stepUpTokenValid = computed(() => Boolean(stepUpToken.value && stepUpExpireAt.value && stepUpExpireAt.value > Date.now()));
const stepUpRemainingLabel = computed(() => {
  if (!stepUpExpireAt.value) {
    return "0s";
  }
  return `${Math.max(Math.floor((stepUpExpireAt.value - Date.now()) / 1000), 0)}s`;
});
const chartMode = computed<"private" | "public">(() => (chartExchangeAccountId.value ? "private" : "public"));
const chartExchangeAccountId = computed(() => {
  const strategyAccountId = Number(selectedStrategy.value?.config?.exchange_account_id || 0);
  const editorAccountId = Number(form.config.exchange_account_id || 0);
  const resolved = strategyAccountId || editorAccountId;
  return resolved > 0 ? resolved : null;
});
const chartExchange = computed(() => {
  const account = accounts.value.find((item) => item.id === chartExchangeAccountId.value);
  return account?.exchange || editorExchange.value;
});
const chartSymbol = computed(() => {
  const rowSymbol = String(selectedStrategy.value?.config?.symbol || "").trim().toUpperCase();
  const formSymbol = String(form.config.symbol || "").trim().toUpperCase();
  return rowSymbol || formSymbol || String(route.query.symbol || "").trim().toUpperCase() || null;
});
const chartMarketType = computed<"spot" | "perp">(() => {
  const scope = selectedStrategy.value?.market_scope || selectedTemplate.value?.market_scope || editorMarketType.value;
  return scope === "perp" ? "perp" : editorMarketType.value;
});
const canSubmit = computed(() => Boolean(form.name.trim() && selectedTemplateKey.value && form.config.exchange_account_id && form.config.symbol));

function setFeedback(message: string, type: "success" | "warning" | "error" | "info" = "info") {
  feedbackMessage.value = message;
  feedbackType.value = type;
}

function statusType(status: string) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "running") return "success";
  if (normalized === "failed") return "danger";
  if (normalized === "starting" || normalized === "stopping") return "warning";
  return "info";
}

function isRunnable(status: string) {
  return !["running", "starting", "stopping"].includes(String(status || "").toLowerCase());
}

function accountLabel(accountId: unknown) {
  const id = Number(accountId || 0);
  const account = accounts.value.find((item) => item.id === id);
  return account ? `${account.account_alias} (${account.exchange})` : id ? `Account #${id}` : "-";
}

function rowClassName({ row }: { row: StrategyItem }) {
  return row.id === selectedStrategyId.value ? "is-selected-row" : "";
}

function buildDefaultConfig(template: StrategyTemplateItem) {
  const nextConfig: Record<string, any> = {};
  for (const field of template.fields) {
    nextConfig[field.key] = field.default ?? (field.input_type === "switch" ? false : "");
  }
  nextConfig.symbol = String(route.query.symbol || nextConfig.symbol || "BTCUSDT").toUpperCase();
  nextConfig.exchange_account_id = accounts.value[0]?.id ?? nextConfig.exchange_account_id ?? null;
  return nextConfig;
}

function applySelectedTemplate() {
  const template = selectedTemplate.value;
  if (!template) {
    return;
  }
  form.name = template.template_key === "dca" ? "dca-alpha" : `${template.template_key}-alpha`;
  form.config = buildDefaultConfig(template);
}

function resetComposer() {
  editingStrategyId.value = null;
  applySelectedTemplate();
  setFeedback("Composer reset to the selected template.", "info");
}

function selectTemplate(templateKey: string) {
  selectedTemplateKey.value = templateKey;
  resetComposer();
}

function selectStrategy(row: StrategyItem) {
  selectedStrategyId.value = row.id;
}

function editStrategy(row: StrategyItem) {
  selectedStrategyId.value = row.id;
  editingStrategyId.value = row.id;
  selectedTemplateKey.value = row.template_key;
  form.name = row.name;
  form.config = { ...row.config };
}

function duplicateStrategy(row: StrategyItem) {
  selectedStrategyId.value = row.id;
  editingStrategyId.value = null;
  selectedTemplateKey.value = row.template_key;
  form.name = `${row.name}-copy`;
  form.config = { ...row.config };
  setFeedback("Loaded the selected strategy into the composer as a new version.", "info");
}

async function ensureStepUpToken() {
  if (!stepUpTokenValid.value) {
    throw new Error("Issue a fresh control token before starting or stopping a strategy.");
  }
  return stepUpToken.value;
}

async function loadData() {
  loading.value = true;
  try {
    await ensureSession();
    const [templatesData, strategyData, accountData] = await Promise.all([
      listStrategyTemplates(),
      listStrategies(),
      listExchangeAccounts()
    ]);
    templates.value = templatesData;
    rows.value = strategyData;
    accounts.value = accountData;
    if (!selectedTemplate.value && templates.value.length) {
      selectedTemplateKey.value = String(route.query.template || templates.value[0].template_key);
    }
    if (!Object.keys(form.config).length) {
      applySelectedTemplate();
    }
  } catch (error: any) {
    setFeedback(getErrorMessage(error, "Failed to load strategy workspace."), "error");
  } finally {
    loading.value = false;
  }
}

async function issueStepUpToken() {
  if (!/^\d{6}$/.test(stepUpCode.value.trim())) {
    setFeedback("Enter a valid 6-digit Google Authenticator code.", "warning");
    return;
  }
  stepUpLoading.value = true;
  try {
    const data = await requestStepUpToken(stepUpCode.value.trim());
    stepUpToken.value = data.step_up_token;
    stepUpExpireAt.value = Date.now() + (data.expires_in_seconds || 0) * 1000;
    setFeedback("Control token issued.", "success");
  } catch (error: any) {
    setFeedback(getErrorMessage(error, "Failed to issue control token."), "error");
  } finally {
    stepUpLoading.value = false;
  }
}

function buildStrategyPayload() {
  return {
    name: form.name.trim(),
    template_key: selectedTemplateKey.value,
    config: { ...form.config }
  };
}

async function saveStrategy() {
  saveLoading.value = true;
  try {
    const wasEditing = Boolean(editingStrategyId.value);
    const payload = buildStrategyPayload();
    const saved = editingStrategyId.value
      ? await updateStrategy(editingStrategyId.value, payload)
      : await createStrategy(payload);
    await loadData();
    selectedStrategyId.value = saved.id;
    editingStrategyId.value = saved.id;
    setFeedback(wasEditing ? "Strategy updated." : "Strategy created.", "success");
  } catch (error: any) {
    setFeedback(getErrorMessage(error, "Failed to save strategy."), "error");
  } finally {
    saveLoading.value = false;
  }
}

async function saveAsNewStrategy() {
  duplicateLoading.value = true;
  try {
    const saved = await createStrategy(buildStrategyPayload());
    await loadData();
    selectedStrategyId.value = saved.id;
    editingStrategyId.value = saved.id;
    setFeedback("Saved as a new strategy version.", "success");
  } catch (error: any) {
    setFeedback(getErrorMessage(error, "Failed to save as new strategy."), "error");
  } finally {
    duplicateLoading.value = false;
  }
}

async function inspectRuntime(row: StrategyItem) {
  selectedStrategyId.value = row.id;
  try {
    runtimeState.value = await getStrategyRuntime(row.id);
    runtimeStrategyId.value = row.id;
  } catch (error: any) {
    setFeedback(getErrorMessage(error, "Failed to load runtime state."), "error");
  }
}

async function startSelectedStrategy(row: StrategyItem) {
  actionLoadingId.value = row.id;
  actionMode.value = "start";
  try {
    runtimeState.value = await startStrategyRequest(row.id, await ensureStepUpToken());
    runtimeStrategyId.value = row.id;
    await loadData();
    setFeedback("Strategy started.", "success");
  } catch (error: any) {
    setFeedback(getErrorMessage(error, "Failed to start strategy."), "error");
  } finally {
    actionLoadingId.value = null;
    actionMode.value = null;
  }
}

async function stopSelectedStrategy(row: StrategyItem) {
  actionLoadingId.value = row.id;
  actionMode.value = "stop";
  try {
    runtimeState.value = await stopStrategyRequest(row.id, await ensureStepUpToken());
    runtimeStrategyId.value = row.id;
    await loadData();
    setFeedback("Strategy stopped.", "success");
  } catch (error: any) {
    setFeedback(getErrorMessage(error, "Failed to stop strategy."), "error");
  } finally {
    actionLoadingId.value = null;
    actionMode.value = null;
  }
}

onMounted(loadData);
</script>

<style scoped>
.strategy-stage {
  align-items: start;
}

.template-gallery {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 14px;
}

.template-card {
  text-align: left;
  padding: 16px;
  border-radius: 18px;
  border: 1px solid var(--aq-border);
  background: linear-gradient(180deg, rgba(18, 28, 43, 0.96) 0%, rgba(10, 18, 29, 0.98) 100%);
  color: var(--aq-ink);
  cursor: pointer;
  transition: 180ms ease;
}

.template-card:hover,
.template-card.is-active {
  border-color: var(--aq-border-strong);
  transform: translateY(-2px);
}

.template-card strong {
  display: block;
  margin-top: 10px;
  color: var(--aq-ink-strong);
}

.template-card small {
  display: block;
  margin-top: 8px;
  color: var(--aq-ink-soft);
  line-height: 1.6;
}

.template-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
  color: var(--aq-ink-faint);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.template-state {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.template-state.live_supported {
  color: #02170e;
  background: #16d1a7;
}

.template-state.draft_only {
  color: #1d1400;
  background: var(--aq-warning);
}

@media (max-width: 960px) {
  .template-gallery {
    grid-template-columns: 1fr;
  }
}
</style>

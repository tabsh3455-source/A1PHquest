<template>
  <AppShell>
    <template #toolbar>
      <el-button @click="toDashboard">Dashboard</el-button>
      <el-button @click="toStrategies">Strategies</el-button>
      <el-button type="primary" :loading="loading" @click="reloadAll">Refresh</el-button>
    </template>

    <div class="aq-panel aq-fade-up">
      <div class="aq-title-row">
        <div>
          <h1>AI Autopilot</h1>
          <p class="aq-subtitle">
            Let AI evaluate real-time market factors, switch between your approved strategies, and generate tuned new versions when conditions shift.
          </p>
        </div>
        <el-tag :type="policies.some((item) => item.status === 'enabled') ? 'success' : 'info'">
          {{ policies.some((item) => item.status === "enabled") ? "Autopilot active" : "Autopilot idle" }}
        </el-tag>
      </div>

      <el-alert
        v-if="feedbackMessage"
        :title="feedbackMessage"
        :type="feedbackType"
        show-icon
        style="margin-top: 14px"
      />

      <div class="ai-grid">
        <section class="aq-soft-block">
          <h2>Access Control</h2>
          <p class="settings-copy">
            Saving AI providers, changing autopilot policies, or executing a manual run requires a fresh 2FA step-up token.
          </p>
          <el-form label-width="130px" class="settings-form">
            <el-form-item label="2FA Code">
              <el-input v-model="stepUpCode" maxlength="6" placeholder="Enter current 2FA code" />
            </el-form-item>
            <el-form-item>
              <el-space wrap>
                <el-button type="primary" :loading="stepUpLoading" @click="issueStepUpToken">Issue AI Token</el-button>
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
            AI can choose among the candidate grid and DCA strategies you allow, or clone one into a new tuned version. Exchange execution still goes through the existing risk and runtime path.
          </p>
          <el-descriptions :column="1" border size="small" class="settings-descriptions">
            <el-descriptions-item label="Providers">{{ providers.length }}</el-descriptions-item>
            <el-descriptions-item label="Policies">{{ policies.length }}</el-descriptions-item>
            <el-descriptions-item label="Enabled Policies">
              {{ policies.filter((item) => item.status === "enabled").length }}
            </el-descriptions-item>
            <el-descriptions-item label="Recent Decisions">{{ decisions.length }}</el-descriptions-item>
          </el-descriptions>
        </section>
      </div>

      <el-divider />

      <div class="ai-grid">
        <section class="aq-soft-block">
          <div class="section-row">
            <div>
              <h2>AI Provider</h2>
              <p class="settings-copy">Store the endpoint and model that will receive market-factor snapshots.</p>
            </div>
            <el-button @click="resetProviderForm">New Provider</el-button>
          </div>

          <el-form label-width="120px" class="settings-form">
            <el-form-item label="Name">
              <el-input v-model="providerForm.name" placeholder="Primary OpenAI-compatible endpoint" />
            </el-form-item>
            <el-form-item label="Base URL">
              <el-input v-model="providerForm.base_url" placeholder="https://api.openai.com/v1" />
            </el-form-item>
            <el-form-item label="Model">
              <el-input v-model="providerForm.model_name" placeholder="gpt-4.1-mini" />
            </el-form-item>
            <el-form-item :label="providerForm.id ? 'API Key (optional)' : 'API Key'">
              <el-input
                v-model="providerForm.api_key"
                show-password
                type="password"
                placeholder="sk-..."
              />
            </el-form-item>
            <el-form-item label="Enabled">
              <el-switch v-model="providerForm.is_active" />
            </el-form-item>
            <el-form-item>
              <el-space wrap>
                <el-button type="primary" :loading="providerSaving" @click="saveProvider">
                  {{ providerForm.id ? "Update Provider" : "Create Provider" }}
                </el-button>
                <el-button @click="resetProviderForm">Reset</el-button>
              </el-space>
            </el-form-item>
          </el-form>

          <el-table :data="providers" style="width: 100%" size="small">
            <el-table-column prop="name" label="Name" min-width="140" />
            <el-table-column prop="model_name" label="Model" min-width="120" />
            <el-table-column prop="base_url" label="Endpoint" min-width="200" />
            <el-table-column label="State" width="100">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'info'">
                  {{ row.is_active ? "Active" : "Disabled" }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="Actions" width="100">
              <template #default="{ row }">
                <el-button link type="primary" @click="editProvider(row)">Edit</el-button>
              </template>
            </el-table-column>
          </el-table>
        </section>

        <section class="aq-soft-block">
          <div class="section-row">
            <div>
              <h2>Autopilot Policy</h2>
              <p class="settings-copy">Bind an account and symbol, then let AI decide which candidate strategy version should be active or whether it should generate a tuned copy.</p>
            </div>
            <el-button @click="resetPolicyForm">New Policy</el-button>
          </div>

          <el-form label-width="160px" class="settings-form">
            <el-form-item label="Name">
              <el-input v-model="policyForm.name" placeholder="BTC regime switcher" />
            </el-form-item>
            <el-form-item label="Provider">
              <el-select v-model="policyForm.provider_id" placeholder="Select AI provider" style="width: 100%">
                <el-option v-for="item in providers" :key="item.id" :label="`${item.name} · ${item.model_name}`" :value="item.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="Exchange Account">
              <el-select v-model="policyForm.exchange_account_id" placeholder="Select exchange account" style="width: 100%">
                <el-option
                  v-for="item in exchangeAccounts"
                  :key="item.id"
                  :label="`${item.account_alias} · ${item.exchange}${item.is_testnet ? ' (testnet)' : ''}`"
                  :value="item.id"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="Symbol">
              <el-input v-model="policyForm.symbol" placeholder="BTCUSDT / BTC-USDT" />
            </el-form-item>
            <el-form-item label="Interval">
              <el-segmented v-model="policyForm.interval" :options="intervalOptions" />
            </el-form-item>
            <el-form-item label="Candidate Strategies">
              <el-select v-model="policyForm.strategy_ids" multiple collapse-tags collapse-tags-tooltip placeholder="Choose grid or DCA versions" style="width: 100%">
                <el-option
                  v-for="item in editableStrategies"
                  :key="item.id"
                  :label="`${item.name} · ${item.strategy_type} · ${item.status}`"
                  :value="item.id"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="Allowed AI Actions">
              <el-checkbox-group v-model="policyForm.allowed_actions">
                <el-checkbox label="activate_strategy">Switch to a candidate strategy</el-checkbox>
                <el-checkbox label="stop_strategy">Stop a running candidate</el-checkbox>
                <el-checkbox label="create_strategy_version">Generate and use a tuned new version</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="Execution Mode">
              <el-radio-group v-model="policyForm.execution_mode">
                <el-radio-button label="dry_run">Dry Run</el-radio-button>
                <el-radio-button label="auto">Auto</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="Policy Status">
              <el-radio-group v-model="policyForm.status">
                <el-radio-button label="disabled">Disabled</el-radio-button>
                <el-radio-button label="enabled">Enabled</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="Decision Interval (s)">
              <el-input-number v-model="policyForm.decision_interval_seconds" :min="30" :max="3600" :step="30" />
            </el-form-item>
            <el-form-item label="Minimum Confidence">
              <el-slider v-model="policyForm.minimum_confidence" :min="0" :max="1" :step="0.05" show-input />
            </el-form-item>
            <el-form-item label="Max Actions / Hour">
              <el-input-number v-model="policyForm.max_actions_per_hour" :min="1" :max="120" />
            </el-form-item>
            <el-form-item label="Custom Prompt">
              <el-input
                v-model="policyForm.custom_prompt"
                type="textarea"
                :rows="5"
                placeholder="Optional extra policy instructions for the model"
              />
            </el-form-item>
            <el-form-item>
              <el-space wrap>
                <el-button type="primary" :loading="policySaving" @click="savePolicy">
                  {{ policyForm.id ? "Update Policy" : "Create Policy" }}
                </el-button>
                <el-button @click="resetPolicyForm">Reset</el-button>
              </el-space>
            </el-form-item>
          </el-form>
        </section>
      </div>

      <el-divider />

      <section class="aq-soft-block">
        <div class="section-row">
          <div>
            <h2>Policies</h2>
            <p class="settings-copy">Run a dry evaluation, enable automation, or inspect the latest AI decision trail.</p>
          </div>
        </div>

        <el-table :data="policies" style="width: 100%">
          <el-table-column prop="name" label="Policy" min-width="180" />
          <el-table-column label="Scope" min-width="220">
            <template #default="{ row }">
              {{ row.symbol }} · {{ row.interval }} · acct {{ row.exchange_account_id }}
            </template>
          </el-table-column>
          <el-table-column label="Mode" width="110">
            <template #default="{ row }">
              <el-tag :type="row.execution_mode === 'auto' ? 'warning' : 'info'">
                {{ row.execution_mode }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="Status" width="110">
            <template #default="{ row }">
              <el-tag :type="row.status === 'enabled' ? 'success' : 'info'">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="Last Run" min-width="160">
            <template #default="{ row }">
              {{ row.last_run_at || "-" }}
            </template>
          </el-table-column>
          <el-table-column label="Actions" min-width="260">
            <template #default="{ row }">
              <el-space wrap>
                <el-button link type="primary" @click="editPolicy(row)">Edit</el-button>
                <el-button link @click="runPolicy(row, true)">Dry Run</el-button>
                <el-button v-if="row.execution_mode === 'auto'" link type="warning" @click="runPolicy(row, false)">
                  Execute Now
                </el-button>
                <el-button
                  v-if="row.status === 'enabled'"
                  link
                  type="danger"
                  :loading="policyActionId === row.id && policyActionKind === 'disable'"
                  @click="disablePolicy(row)"
                >
                  Disable
                </el-button>
                <el-button
                  v-else
                  link
                  type="success"
                  :loading="policyActionId === row.id && policyActionKind === 'enable'"
                  @click="enablePolicy(row)"
                >
                  Enable
                </el-button>
              </el-space>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <el-divider />

      <section class="aq-soft-block">
        <div class="section-row">
          <div>
            <h2>Recent Decisions</h2>
            <p class="settings-copy">Every run stores the factor snapshot, AI output, and execution result for review.</p>
          </div>
        </div>

        <el-table :data="decisions" style="width: 100%">
          <el-table-column prop="created_at" label="Time" min-width="160" />
          <el-table-column prop="policy_id" label="Policy" width="80" />
          <el-table-column prop="status" label="Status" width="110" />
          <el-table-column prop="action" label="Action" width="130" />
          <el-table-column label="Target" width="100">
            <template #default="{ row }">
              {{ row.target_strategy_id || "-" }}
            </template>
          </el-table-column>
          <el-table-column label="Confidence" width="110">
            <template #default="{ row }">
              {{ Number(row.confidence || 0).toFixed(2) }}
            </template>
          </el-table-column>
          <el-table-column label="Rationale" min-width="260">
            <template #default="{ row }">
              {{ formatDecisionSummary(row) }}
            </template>
          </el-table-column>
        </el-table>
      </section>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import AppShell from "../components/AppShell.vue";
import {
  createAiPolicy,
  createAiProvider,
  disableAiPolicy,
  enableAiPolicy,
  ensureSession,
  listAiDecisions,
  listAiPolicies,
  listAiProviders,
  listExchangeAccounts,
  listStrategies,
  requestStepUpToken,
  runAiPolicy,
  updateAiPolicy,
  updateAiProvider,
  type AiDecisionItem,
  type AiPolicyItem,
  type AiProviderItem,
  type ExchangeAccountItem,
  type StrategyItem
} from "../api";

const router = useRouter();
const loading = ref(false);
const providerSaving = ref(false);
const policySaving = ref(false);
const stepUpLoading = ref(false);
const policyActionId = ref<number | null>(null);
const policyActionKind = ref<"enable" | "disable" | null>(null);
const feedbackMessage = ref("");
const feedbackType = ref<"success" | "warning" | "error" | "info">("info");
const stepUpCode = ref("");
const stepUpToken = ref("");
const stepUpExpireAt = ref<number | null>(null);
const providers = ref<AiProviderItem[]>([]);
const policies = ref<AiPolicyItem[]>([]);
const decisions = ref<AiDecisionItem[]>([]);
const exchangeAccounts = ref<ExchangeAccountItem[]>([]);
const strategies = ref<StrategyItem[]>([]);
const intervalOptions = ["1m", "5m", "15m", "1h"];
const aiActionOptions = ["activate_strategy", "stop_strategy", "create_strategy_version"] as const;

const providerForm = reactive({
  id: null as number | null,
  name: "",
  base_url: "https://api.openai.com/v1",
  model_name: "",
  api_key: "",
  is_active: true
});

const policyForm = reactive({
  id: null as number | null,
  name: "",
  provider_id: 0,
  exchange_account_id: 0,
  symbol: "",
  interval: "5m" as "1m" | "5m" | "15m" | "1h",
  strategy_ids: [] as number[],
  allowed_actions: [...aiActionOptions] as Array<(typeof aiActionOptions)[number]>,
  execution_mode: "dry_run" as "dry_run" | "auto",
  status: "disabled" as "disabled" | "enabled",
  decision_interval_seconds: 300,
  minimum_confidence: 0.6,
  max_actions_per_hour: 6,
  custom_prompt: ""
});

const stepUpTokenValid = computed(() => Boolean(stepUpToken.value && stepUpExpireAt.value && stepUpExpireAt.value > Date.now()));
const stepUpRemainingLabel = computed(() => {
  if (!stepUpExpireAt.value) {
    return "0s";
  }
  return `${Math.max(Math.floor((stepUpExpireAt.value - Date.now()) / 1000), 0)}s`;
});

const editableStrategies = computed(() =>
  strategies.value.filter((item) => item.strategy_type === "grid" || item.strategy_type === "dca")
);

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
    throw new Error("Issue a fresh AI token before saving providers or policies.");
  }
  return stepUpToken.value;
}

function resetProviderForm() {
  providerForm.id = null;
  providerForm.name = "";
  providerForm.base_url = "https://api.openai.com/v1";
  providerForm.model_name = "";
  providerForm.api_key = "";
  providerForm.is_active = true;
}

function resetPolicyForm() {
  policyForm.id = null;
  policyForm.name = "";
  policyForm.provider_id = providers.value[0]?.id || 0;
  policyForm.exchange_account_id = exchangeAccounts.value[0]?.id || 0;
  policyForm.symbol = "";
  policyForm.interval = "5m";
  policyForm.strategy_ids = [];
  policyForm.allowed_actions = [...aiActionOptions];
  policyForm.execution_mode = "dry_run";
  policyForm.status = "disabled";
  policyForm.decision_interval_seconds = 300;
  policyForm.minimum_confidence = 0.6;
  policyForm.max_actions_per_hour = 6;
  policyForm.custom_prompt = "";
}

function editProvider(row: AiProviderItem) {
  providerForm.id = row.id;
  providerForm.name = row.name;
  providerForm.base_url = row.base_url;
  providerForm.model_name = row.model_name;
  providerForm.api_key = "";
  providerForm.is_active = row.is_active;
}

function editPolicy(row: AiPolicyItem) {
  policyForm.id = row.id;
  policyForm.name = row.name;
  policyForm.provider_id = row.provider_id;
  policyForm.exchange_account_id = row.exchange_account_id;
  policyForm.symbol = row.symbol;
  policyForm.interval = row.interval;
  policyForm.strategy_ids = [...row.strategy_ids];
  policyForm.allowed_actions = [...row.allowed_actions];
  policyForm.execution_mode = row.execution_mode;
  policyForm.status = row.status;
  policyForm.decision_interval_seconds = row.decision_interval_seconds;
  policyForm.minimum_confidence = row.minimum_confidence;
  policyForm.max_actions_per_hour = row.max_actions_per_hour;
  policyForm.custom_prompt = row.custom_prompt || "";
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
    setFeedback("AI token issued. You can now save providers, policies, or trigger manual runs.", "success");
  } catch (error: any) {
    stepUpToken.value = "";
    stepUpExpireAt.value = null;
    setFeedback(error?.response?.data?.detail || "Failed to issue AI token.", "error");
  } finally {
    stepUpLoading.value = false;
  }
}

async function reloadAll() {
  try {
    loading.value = true;
    await ensureSessionOrRedirect();
    const [providerRows, policyRows, decisionRows, accountRows, strategyRows] = await Promise.all([
      listAiProviders(),
      listAiPolicies(),
      listAiDecisions(undefined, 30),
      listExchangeAccounts(),
      listStrategies()
    ]);
    providers.value = providerRows;
    policies.value = policyRows;
    decisions.value = decisionRows;
    exchangeAccounts.value = accountRows;
    strategies.value = strategyRows;
    if (!providerForm.id && !providerForm.name && providers.value.length) {
      providerForm.name = providers.value[0].name;
    }
    if (!policyForm.id && providers.value.length && !policyForm.provider_id) {
      policyForm.provider_id = providers.value[0].id;
    }
    if (!policyForm.id && exchangeAccounts.value.length && !policyForm.exchange_account_id) {
      policyForm.exchange_account_id = exchangeAccounts.value[0].id;
    }
    setFeedback("AI provider, policy, and decision data refreshed.", "info");
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || "Failed to load AI autopilot state.", "error");
  } finally {
    loading.value = false;
  }
}

async function saveProvider() {
  if (!providerForm.name.trim() || !providerForm.base_url.trim() || !providerForm.model_name.trim()) {
    setFeedback("Name, base URL, and model are required.", "warning");
    return;
  }
  if (!providerForm.id && !providerForm.api_key.trim()) {
    setFeedback("An API key is required when creating a provider.", "warning");
    return;
  }
  try {
    providerSaving.value = true;
    await ensureSessionOrRedirect();
    if (providerForm.id) {
      await updateAiProvider(
        providerForm.id,
        {
          name: providerForm.name.trim(),
          provider_type: "openai_compatible",
          base_url: providerForm.base_url.trim(),
          model_name: providerForm.model_name.trim(),
          api_key: providerForm.api_key.trim() || undefined,
          is_active: providerForm.is_active
        },
        ensureStepUpToken()
      );
      setFeedback("AI provider updated.", "success");
    } else {
      await createAiProvider(
        {
          name: providerForm.name.trim(),
          provider_type: "openai_compatible",
          base_url: providerForm.base_url.trim(),
          model_name: providerForm.model_name.trim(),
          api_key: providerForm.api_key.trim(),
          is_active: providerForm.is_active
        },
        ensureStepUpToken()
      );
      setFeedback("AI provider created.", "success");
    }
    resetProviderForm();
    await reloadAll();
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to save AI provider.", "error");
  } finally {
    providerSaving.value = false;
  }
}

async function savePolicy() {
  if (!policyForm.name.trim() || !policyForm.provider_id || !policyForm.exchange_account_id || !policyForm.symbol.trim()) {
    setFeedback("Policy name, provider, exchange account, and symbol are required.", "warning");
    return;
  }
  if (!policyForm.strategy_ids.length) {
    setFeedback("Choose at least one candidate strategy.", "warning");
    return;
  }
  if (!policyForm.allowed_actions.length) {
    setFeedback("Choose at least one allowed AI action.", "warning");
    return;
  }
  try {
    policySaving.value = true;
    await ensureSessionOrRedirect();
    const payload = {
      name: policyForm.name.trim(),
      provider_id: Number(policyForm.provider_id),
      exchange_account_id: Number(policyForm.exchange_account_id),
      symbol: policyForm.symbol.trim().toUpperCase(),
      interval: policyForm.interval,
      strategy_ids: policyForm.strategy_ids.map((item) => Number(item)),
      allowed_actions: [...policyForm.allowed_actions],
      execution_mode: policyForm.execution_mode,
      status: policyForm.status,
      decision_interval_seconds: Number(policyForm.decision_interval_seconds),
      minimum_confidence: Number(policyForm.minimum_confidence),
      max_actions_per_hour: Number(policyForm.max_actions_per_hour),
      custom_prompt: policyForm.custom_prompt.trim() || null
    };
    if (policyForm.id) {
      await updateAiPolicy(policyForm.id, payload, ensureStepUpToken());
      setFeedback("AI autopilot policy updated.", "success");
    } else {
      await createAiPolicy(payload, ensureStepUpToken());
      setFeedback("AI autopilot policy created.", "success");
    }
    resetPolicyForm();
    await reloadAll();
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to save AI policy.", "error");
  } finally {
    policySaving.value = false;
  }
}

async function runPolicy(row: AiPolicyItem, dryRunOverride: boolean) {
  try {
    await ensureSessionOrRedirect();
    const result = await runAiPolicy(row.id, ensureStepUpToken(), dryRunOverride);
    setFeedback(
      `Policy ${row.name} ran with action "${result.action}" and status "${result.status}".`,
      result.status === "error" ? "error" : "success"
    );
    await reloadAll();
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to run AI policy.", "error");
  }
}

async function enablePolicy(row: AiPolicyItem) {
  try {
    policyActionId.value = row.id;
    policyActionKind.value = "enable";
    await ensureSessionOrRedirect();
    await enableAiPolicy(row.id, ensureStepUpToken());
    setFeedback(`Policy ${row.name} enabled. Scheduler can now run it automatically.`, "success");
    await reloadAll();
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to enable AI policy.", "error");
  } finally {
    policyActionId.value = null;
    policyActionKind.value = null;
  }
}

async function disablePolicy(row: AiPolicyItem) {
  try {
    policyActionId.value = row.id;
    policyActionKind.value = "disable";
    await ensureSessionOrRedirect();
    await disableAiPolicy(row.id, ensureStepUpToken());
    setFeedback(`Policy ${row.name} disabled.`, "success");
    await reloadAll();
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to disable AI policy.", "error");
  } finally {
    policyActionId.value = null;
    policyActionKind.value = null;
  }
}

function toDashboard() {
  router.push("/dashboard");
}

function toStrategies() {
  router.push("/strategies");
}

onMounted(async () => {
  await reloadAll();
});

function formatDecisionSummary(row: AiDecisionItem) {
  const generatedStrategyId = Number(row.execution_result?.generated_strategy_id || 0);
  const message = String(row.execution_result?.message || "").trim();
  if (generatedStrategyId > 0) {
    return `Generated strategy #${generatedStrategyId}. ${message}`.trim();
  }
  return row.rationale || message || "-";
}
</script>

<style scoped>
.ai-grid {
  margin-top: 16px;
  display: grid;
  gap: 16px;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
}

.section-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
}

.settings-copy {
  margin: 8px 0 0;
  color: var(--aq-ink-soft);
  line-height: 1.6;
}

.settings-form {
  margin-top: 14px;
}
</style>

<template>
  <AppShell
    title="AI Autopilot"
    subtitle="Run model-backed regime decisions on top of the live market engine. Providers stay isolated, policies stay explicit, and every decision leaves an audit trail before execution touches runtime control."
  >
    <template #toolbar>
      <router-link class="aq-auth-link ai-toolbar-link" to="/strategies">Open Strategies</router-link>
      <el-button type="primary" :loading="loading" @click="reloadAll">Refresh</el-button>
    </template>

    <WorkflowReadinessBar />

    <el-alert
      v-if="feedbackMessage"
      :title="feedbackMessage"
      :type="feedbackType"
      show-icon
      class="aq-fade-up"
    />
    <el-alert
      v-if="!riskRuleConfigured"
      title="Risk rule is not configured. Dry-run remains available, but live runtime actions from AI are blocked until risk setup is completed."
      type="warning"
      show-icon
      class="aq-fade-up"
    />
    <div v-if="!riskRuleConfigured" class="ai-risk-cta aq-fade-up">
      <el-button size="small" @click="goToSettings">Open Risk Settings</el-button>
    </div>

    <section class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Autopilot Quickstart</h2>
          <p class="aq-section-copy">
            Use the shortest setup sequence: create a provider, create a policy, then run one dry-run decision.
          </p>
        </div>
      </div>
      <div class="aq-note-list">
        <div class="aq-note-row">
          <strong>Step 1: Provider</strong>
          <small>{{ providers.length ? "Ready. At least one provider is configured." : "Missing. Add a provider in the editor." }}</small>
          <el-button v-if="!providers.length" size="small" @click="scrollToBlock('ai-provider-editor')">Go to Provider Editor</el-button>
        </div>
        <div class="aq-note-row">
          <strong>Step 2: Policy</strong>
          <small>{{ policies.length ? "Ready. Policy scope exists." : "Missing. Add a policy after provider setup." }}</small>
          <el-button v-if="providers.length && !policies.length" size="small" @click="scrollToBlock('ai-policy-editor')">Go to Policy Editor</el-button>
        </div>
        <div class="aq-note-row">
          <strong>Step 3: Dry-run</strong>
          <small>{{ hasDryRunEvidence ? "Ready. Decision trail has at least one dry-run/manual record." : "Run one dry-run to validate policy behavior." }}</small>
          <el-button v-if="policies.length && !hasDryRunEvidence" size="small" :loading="quickRunLoading" @click="runFirstDryRun">Run first dry-run</el-button>
        </div>
      </div>
    </section>

    <section class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Autopilot Deck</h2>
          <p class="aq-section-copy">
            Providers feed models, policies define scope and allowed actions, and recent decisions show exactly what the AI tried to do with live market context.
          </p>
        </div>
        <span class="aq-chip">{{ enabledPolicyCount ? "Autopilot armed" : "Autopilot idle" }}</span>
      </div>

      <div class="aq-summary-strip">
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Providers</span>
          <strong class="aq-metric-value">{{ providers.length }}</strong>
          <span class="aq-metric-copy">Model endpoints available to receive factor snapshots.</span>
        </div>
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Enabled Policies</span>
          <strong class="aq-metric-value">{{ enabledPolicyCount }}</strong>
          <span class="aq-metric-copy">Policies currently allowed to run on their own scheduler.</span>
        </div>
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Auto Execution</span>
          <strong class="aq-metric-value">{{ autoPolicyCount }}</strong>
          <span class="aq-metric-copy">Policies allowed to take real runtime action instead of dry-running.</span>
        </div>
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Recent Decisions</span>
          <strong class="aq-metric-value">{{ decisions.length }}</strong>
          <span class="aq-metric-copy">Latest runs with factor snapshot, rationale, and execution result.</span>
        </div>
      </div>
    </section>

    <section class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Provider Registry</h2>
          <p class="aq-section-copy">
            Keep provider inventory visible so you can switch models without losing policy history or runtime context.
          </p>
        </div>
      </div>

      <div v-if="!providers.length && !loading" class="aq-empty-state">
        <div>
          <h3>No AI providers saved yet.</h3>
          <p>Create a provider in the inspector, then attach policies to symbols and approved strategy versions.</p>
        </div>
      </div>

      <div v-else class="aq-stack">
        <div class="ai-table-desktop">
          <el-table :data="providers" style="width: 100%" size="small">
            <el-table-column prop="name" label="Provider" min-width="170" />
            <el-table-column prop="model_name" label="Model" min-width="140" />
            <el-table-column prop="base_url" label="Endpoint" min-width="220" />
            <el-table-column label="State" width="110">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'info'">
                  {{ row.is_active ? "Active" : "Disabled" }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="Actions" width="90">
              <template #default="{ row }">
                <el-button link type="primary" @click="editProvider(row)">Edit</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div class="ai-cards-mobile">
          <article v-for="row in providers" :key="row.id" class="aq-soft-block aq-stack">
            <div class="aq-title-row">
              <div>
                <h3>{{ row.name }}</h3>
                <p class="aq-form-note">{{ row.model_name }}</p>
              </div>
              <el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? "Active" : "Disabled" }}</el-tag>
            </div>
            <div class="aq-inline-meta">
              <span>{{ row.base_url }}</span>
            </div>
            <el-button size="small" @click="editProvider(row)">Edit Provider</el-button>
          </article>
        </div>
      </div>
    </section>

    <section class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Policy Matrix</h2>
          <p class="aq-section-copy">
            Policies bind a provider to an exchange account, symbol, interval, and a shortlist of candidate strategies or AI-generated variants.
          </p>
        </div>
      </div>

      <div v-if="!policies.length && !loading" class="aq-empty-state">
        <div>
          <h3>No autopilot policies configured yet.</h3>
          <p>After you save a provider and at least one grid, DCA, or Combo strategy, create a policy to run dry-run evaluations or full automatic switching.</p>
        </div>
      </div>

      <div v-else class="aq-stack">
        <div class="ai-table-desktop">
          <el-table :data="policies" style="width: 100%">
            <el-table-column prop="name" label="Policy" min-width="190" />
            <el-table-column label="Scope" min-width="220">
              <template #default="{ row }">
                {{ row.symbol }} / {{ row.interval }} / acct {{ row.exchange_account_id }}
              </template>
            </el-table-column>
            <el-table-column label="Mode" width="110">
              <template #default="{ row }">
                <el-tag :type="row.execution_mode === 'auto' ? 'warning' : 'info'">
                  {{ row.execution_mode === "auto" ? "Auto" : "Dry Run" }}
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
            <el-table-column label="Allowed Actions" min-width="210">
              <template #default="{ row }">
                <span class="policy-actions-copy">{{ formatAllowedActions(row.allowed_actions) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="Last Run" min-width="160">
              <template #default="{ row }">{{ row.last_run_at || "-" }}</template>
            </el-table-column>
            <el-table-column label="Actions" min-width="290" fixed="right">
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
        </div>
        <div class="ai-cards-mobile">
          <article v-for="row in policies" :key="row.id" class="aq-soft-block aq-stack">
            <div class="aq-title-row">
              <div>
                <h3>{{ row.name }}</h3>
                <p class="aq-form-note">{{ row.symbol }} / {{ row.interval }} / acct {{ row.exchange_account_id }}</p>
              </div>
              <el-tag :type="row.status === 'enabled' ? 'success' : 'info'">{{ row.status }}</el-tag>
            </div>
            <div class="aq-inline-meta">
              <span>mode={{ row.execution_mode }}</span>
              <span>actions={{ formatAllowedActions(row.allowed_actions) }}</span>
            </div>
            <el-space wrap>
              <el-button size="small" @click="editPolicy(row)">Edit</el-button>
              <el-button size="small" @click="runPolicy(row, true)">Dry Run</el-button>
              <el-button v-if="row.execution_mode === 'auto'" size="small" type="warning" @click="runPolicy(row, false)">Execute</el-button>
            </el-space>
          </article>
        </div>
      </div>
    </section>

    <section class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Decision Trail</h2>
          <p class="aq-section-copy">
            Every run records the rationale and action outcome so you can compare dry-run reasoning against real runtime changes.
          </p>
        </div>
      </div>

      <div class="aq-stack">
        <div class="ai-table-desktop">
          <el-table :data="decisions" style="width: 100%">
            <el-table-column prop="created_at" label="Time" min-width="170" />
            <el-table-column prop="policy_id" label="Policy" width="80" />
            <el-table-column prop="status" label="Status" width="110" />
            <el-table-column prop="action" label="Action" width="160" />
            <el-table-column label="Target" width="120">
              <template #default="{ row }">{{ row.target_strategy_id || "-" }}</template>
            </el-table-column>
            <el-table-column label="Confidence" width="110">
              <template #default="{ row }">{{ Number(row.confidence || 0).toFixed(2) }}</template>
            </el-table-column>
            <el-table-column label="Summary" min-width="300">
              <template #default="{ row }">{{ formatDecisionSummary(row) }}</template>
            </el-table-column>
          </el-table>
        </div>
        <div class="ai-cards-mobile">
          <article v-for="row in decisions" :key="row.id" class="aq-soft-block aq-stack">
            <div class="aq-title-row">
              <div>
                <h3>{{ row.action }}</h3>
                <p class="aq-form-note">{{ row.created_at }}</p>
              </div>
              <el-tag>{{ row.status }}</el-tag>
            </div>
            <div class="aq-inline-meta">
              <span>policy={{ row.policy_id }}</span>
              <span>confidence={{ Number(row.confidence || 0).toFixed(2) }}</span>
              <span>target={{ row.target_strategy_id || "-" }}</span>
            </div>
            <div class="aq-form-note">{{ formatDecisionSummary(row) }}</div>
          </article>
        </div>
      </div>
    </section>

    <template #inspector>
      <section id="ai-provider-editor" class="aq-soft-block aq-stack">
        <div>
          <h3>Control Token</h3>
          <p class="aq-form-note">Provider edits, policy edits, and manual AI runs all require a fresh 2FA step-up token.</p>
        </div>
        <el-form label-position="top">
          <el-form-item label="Current 2FA Code">
            <el-input v-model="stepUpCode" maxlength="6" placeholder="Enter current 2FA code" />
          </el-form-item>
          <el-form-item>
            <el-space wrap>
              <el-button type="primary" :loading="stepUpLoading" @click="issueStepUpToken">Issue AI Token</el-button>
              <el-tag :type="stepUpTokenValid ? 'success' : 'info'">
                {{ stepUpTokenValid ? `Ready / ${stepUpRemainingLabel}` : "No active token" }}
              </el-tag>
            </el-space>
          </el-form-item>
        </el-form>
      </section>

      <section id="ai-policy-editor" class="aq-soft-block aq-stack">
        <div class="aq-title-row">
          <div>
            <h3>Provider Editor</h3>
            <p class="aq-form-note">Save the OpenAI-compatible endpoint that will receive factor snapshots and return structured actions.</p>
          </div>
          <el-tag :type="providerForm.id ? 'warning' : 'success'">
            {{ providerForm.id ? `Editing #${providerForm.id}` : "New provider" }}
          </el-tag>
        </div>
        <el-form label-position="top" class="aq-stack">
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
          <el-space wrap>
            <el-button type="primary" :loading="providerSaving" @click="saveProvider">
              {{ providerForm.id ? "Update Provider" : "Create Provider" }}
            </el-button>
            <el-button @click="resetProviderForm">Reset</el-button>
          </el-space>
        </el-form>
      </section>

      <section class="aq-soft-block aq-stack">
        <div class="aq-title-row">
          <div>
            <h3>Policy Editor</h3>
            <p class="aq-form-note">Bind account, symbol, interval, and candidate strategy versions into one AI control surface.</p>
          </div>
          <el-tag :type="policyForm.id ? 'warning' : 'success'">
            {{ policyForm.id ? `Editing #${policyForm.id}` : "New policy" }}
          </el-tag>
        </div>

        <el-form label-position="top" class="aq-stack">
          <el-form-item label="Name">
            <el-input v-model="policyForm.name" placeholder="BTC regime switcher" />
          </el-form-item>
          <el-form-item label="Provider">
            <el-select v-model="policyForm.provider_id" placeholder="Select AI provider" style="width: 100%">
              <el-option
                v-for="item in providers"
                :key="item.id"
                :label="`${item.name} / ${item.model_name}`"
                :value="item.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="Exchange Account">
            <el-select v-model="policyForm.exchange_account_id" placeholder="Select exchange account" style="width: 100%">
              <el-option
                v-for="item in exchangeAccounts"
                :key="item.id"
                :label="`${item.account_alias} / ${item.exchange}${item.is_testnet ? ' (testnet)' : ''}`"
                :value="item.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="Symbol">
            <el-input v-model="policyForm.symbol" placeholder="BTCUSDT / BTC-USDT-SWAP" />
          </el-form-item>
          <el-form-item label="Interval">
            <el-segmented v-model="policyForm.interval" :options="intervalOptions" />
          </el-form-item>
          <el-form-item label="Candidate Strategies">
            <el-select
              v-model="policyForm.strategy_ids"
              multiple
              collapse-tags
              collapse-tags-tooltip
              placeholder="Choose grid, DCA, or Combo versions"
              style="width: 100%"
            >
              <el-option
                v-for="item in editableStrategies"
                :key="item.id"
                :label="`${item.name} / ${item.strategy_type} / ${item.status}`"
                :value="item.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="Allowed Actions">
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
            <el-input-number v-model="policyForm.decision_interval_seconds" :min="30" :max="3600" :step="30" style="width: 100%" />
          </el-form-item>
          <el-form-item label="Minimum Confidence">
            <el-slider v-model="policyForm.minimum_confidence" :min="0" :max="1" :step="0.05" show-input />
          </el-form-item>
          <el-form-item label="Max Actions / Hour">
            <el-input-number v-model="policyForm.max_actions_per_hour" :min="1" :max="120" style="width: 100%" />
          </el-form-item>
          <el-form-item label="Custom Prompt">
            <el-input
              v-model="policyForm.custom_prompt"
              type="textarea"
              :rows="5"
              placeholder="Optional extra policy instructions for the model"
            />
          </el-form-item>
          <el-space wrap>
            <el-button type="primary" :loading="policySaving" @click="savePolicy">
              {{ policyForm.id ? "Update Policy" : "Create Policy" }}
            </el-button>
            <el-button @click="resetPolicyForm">Reset</el-button>
          </el-space>
        </el-form>
      </section>
    </template>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import AppShell from "../components/AppShell.vue";
import WorkflowReadinessBar from "../components/WorkflowReadinessBar.vue";
import {
  createAiPolicy,
  createAiProvider,
  disableAiPolicy,
  enableAiPolicy,
  ensureSession,
  hasConfiguredRiskRule,
  listAiDecisions,
  listAiPolicies,
  listAiProviders,
  listExchangeAccounts,
  listStrategies,
  notifyWorkflowReadinessRefresh,
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
const quickRunLoading = ref(false);
const policyActionId = ref<number | null>(null);
const policyActionKind = ref<"enable" | "disable" | null>(null);
const feedbackMessage = ref("");
const feedbackType = ref<"success" | "warning" | "error" | "info">("info");
const riskRuleConfigured = ref(false);
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
  strategies.value.filter((item) =>
    item.strategy_type === "grid" ||
    item.strategy_type === "futures_grid" ||
    item.strategy_type === "dca" ||
    item.strategy_type === "combo_grid_dca"
  )
);
const enabledPolicyCount = computed(() => policies.value.filter((item) => item.status === "enabled").length);
const autoPolicyCount = computed(() => policies.value.filter((item) => item.execution_mode === "auto").length);
const hasDryRunEvidence = computed(() =>
  decisions.value.some((item) =>
    String(item.status || "").toLowerCase() === "dry_run" ||
    String(item.trigger_source || "").toLowerCase() === "manual"
  )
);

function setFeedback(message: string, type: "success" | "warning" | "error" | "info" = "info") {
  feedbackMessage.value = message;
  feedbackType.value = type;
}

function scrollToBlock(id: string) {
  const target = document.getElementById(id);
  target?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function goToSettings() {
  router.push("/settings");
}

async function ensureSessionOrRedirect() {
  try {
    await ensureSession();
  } catch {
    router.push("/auth");
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
    const [providerRows, policyRows, decisionRows, accountRows, strategyRows, riskConfigured] = await Promise.all([
      listAiProviders(),
      listAiPolicies(),
      listAiDecisions(undefined, 30),
      listExchangeAccounts(),
      listStrategies(),
      hasConfiguredRiskRule()
    ]);
    providers.value = providerRows;
    policies.value = policyRows;
    decisions.value = decisionRows;
    exchangeAccounts.value = accountRows;
    strategies.value = strategyRows;
    riskRuleConfigured.value = riskConfigured;
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
    notifyWorkflowReadinessRefresh();
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
  if (!riskRuleConfigured.value && policyForm.execution_mode === "auto" && policyForm.status === "enabled") {
    setFeedback("Risk rule setup is required before enabling auto execution.", "warning");
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
    notifyWorkflowReadinessRefresh();
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to save AI policy.", "error");
  } finally {
    policySaving.value = false;
  }
}

async function runPolicy(row: AiPolicyItem, dryRunOverride: boolean) {
  if (!dryRunOverride && !riskRuleConfigured.value) {
    setFeedback("Live AI execution is blocked until a risk rule is configured.", "warning");
    return;
  }
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

async function runFirstDryRun() {
  const targetPolicy = policies.value[0];
  if (!targetPolicy) {
    setFeedback("Create a policy first before running a dry-run.", "warning");
    return;
  }
  quickRunLoading.value = true;
  try {
    await runPolicy(targetPolicy, true);
  } finally {
    quickRunLoading.value = false;
  }
}

async function enablePolicy(row: AiPolicyItem) {
  if (row.execution_mode === "auto" && !riskRuleConfigured.value) {
    setFeedback("Risk rule setup is required before enabling auto policies.", "warning");
    return;
  }
  try {
    policyActionId.value = row.id;
    policyActionKind.value = "enable";
    await ensureSessionOrRedirect();
    await enableAiPolicy(row.id, ensureStepUpToken());
    setFeedback(`Policy ${row.name} enabled. Scheduler can now run it automatically.`, "success");
    await reloadAll();
    notifyWorkflowReadinessRefresh();
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
    notifyWorkflowReadinessRefresh();
  } catch (error: any) {
    setFeedback(error?.response?.data?.detail || error?.message || "Failed to disable AI policy.", "error");
  } finally {
    policyActionId.value = null;
    policyActionKind.value = null;
  }
}

function formatAllowedActions(actions: string[]) {
  if (!actions?.length) {
    return "-";
  }
  return actions
    .map((item) => item.replaceAll("_", " "))
    .join(", ");
}

function formatDecisionSummary(row: AiDecisionItem) {
  const generatedStrategyId = Number(row.execution_result?.generated_strategy_id || 0);
  const message = String(row.execution_result?.message || "").trim();
  if (generatedStrategyId > 0) {
    return `Generated strategy #${generatedStrategyId}. ${message}`.trim();
  }
  return row.rationale || message || "-";
}

onMounted(async () => {
  await reloadAll();
});
</script>

<style scoped>
.ai-toolbar-link {
  min-width: 144px;
}

.policy-actions-copy {
  color: var(--aq-ink-soft);
  line-height: 1.55;
}

.ai-risk-cta {
  margin-top: 8px;
}

.ai-cards-mobile {
  display: none;
  gap: 10px;
}

@media (max-width: 960px) {
  .ai-table-desktop {
    display: none;
  }

  .ai-cards-mobile {
    display: grid;
  }
}
</style>

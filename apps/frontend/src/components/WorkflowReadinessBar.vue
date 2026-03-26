<template>
  <section class="aq-panel aq-fade-up workflow-readiness-shell">
    <div class="aq-title-row">
      <div>
        <h2>Workflow Readiness</h2>
        <p class="aq-subtitle">Track what is missing in the trading loop and jump straight to the next required action.</p>
      </div>
      <el-button size="small" :loading="loading" @click="loadReadiness">Refresh</el-button>
    </div>

    <el-alert
      v-if="errorMessage"
      style="margin-top: 12px"
      type="warning"
      :closable="false"
      :title="errorMessage"
      show-icon
    />

    <div v-if="readiness" class="aq-summary-strip" style="margin-top: 12px">
      <div class="aq-metric-tile">
        <span class="aq-metric-kicker">Session</span>
        <strong class="aq-metric-value">{{ readiness.authenticated ? "Signed In" : "Guest" }}</strong>
        <span class="aq-metric-copy">
          {{
            readiness.authenticated
              ? readiness.enrollment_required
                ? "2FA enrollment required"
                : "Protected routes unlocked"
              : "Only public market routes are available"
          }}
        </span>
      </div>
      <div class="aq-metric-tile">
        <span class="aq-metric-kicker">Accounts</span>
        <strong class="aq-metric-value">{{ readiness.exchange_accounts_summary.total }}</strong>
        <span class="aq-metric-copy">
          live={{ readiness.exchange_accounts_summary.live }} / testnet={{ readiness.exchange_accounts_summary.testnet }}
        </span>
      </div>
      <div class="aq-metric-tile">
        <span class="aq-metric-kicker">Risk Gate</span>
        <strong class="aq-metric-value">{{ readiness.has_risk_rule ? "Ready" : "Blocked" }}</strong>
        <span class="aq-metric-copy">
          {{ readiness.has_risk_rule ? "Live strategy starts are allowed." : "Live start stays fail-closed until risk rule setup." }}
        </span>
      </div>
      <div class="aq-metric-tile">
        <span class="aq-metric-kicker">AI</span>
        <strong class="aq-metric-value">{{ readiness.ai_ready.policy_count }}</strong>
        <span class="aq-metric-copy">
          providers={{ readiness.ai_ready.provider_count }}, auto={{ readiness.ai_ready.auto_enabled_count }}
        </span>
      </div>
    </div>

    <div v-if="primaryAction" class="workflow-actions">
      <el-button type="primary" @click="go(primaryAction.path)">{{ primaryAction.label }}</el-button>
      <el-button v-if="secondaryAction" @click="go(secondaryAction.path)">{{ secondaryAction.label }}</el-button>
      <el-tag type="info">
        live templates: {{ readiness?.live_supported_templates.map((item) => item.template_key).join(", ") || "-" }}
      </el-tag>
    </div>

    <div v-if="readiness?.next_required_actions?.length" class="workflow-action-tags">
      <button
        v-for="item in readiness.next_required_actions"
        :key="item.code"
        class="workflow-action-chip"
        type="button"
        @click="go(item.path)"
      >
        <strong>{{ item.label }}</strong>
        <small>{{ item.description || item.path }}</small>
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import {
  WORKFLOW_READINESS_REFRESH_EVENT,
  getWorkflowReadiness,
  getErrorMessage,
  type WorkflowReadinessAction,
  type WorkflowReadinessResponse
} from "../api";

const router = useRouter();
const readiness = ref<WorkflowReadinessResponse | null>(null);
const loading = ref(false);
const errorMessage = ref("");

const primaryAction = computed<WorkflowReadinessAction | null>(() => readiness.value?.next_required_actions?.[0] || null);
const secondaryAction = computed<WorkflowReadinessAction | null>(() => readiness.value?.next_required_actions?.[1] || null);

function go(path: string) {
  const normalized = String(path || "").trim();
  if (!normalized) {
    return;
  }
  router.push(normalized);
}

async function loadReadiness() {
  loading.value = true;
  errorMessage.value = "";
  try {
    readiness.value = await getWorkflowReadiness();
  } catch (error: any) {
    errorMessage.value = getErrorMessage(error, "Failed to load workflow readiness.");
  } finally {
    loading.value = false;
  }
}

defineExpose({
  refresh: loadReadiness
});

function handleReadinessRefreshEvent() {
  void loadReadiness();
}

onMounted(() => {
  window.addEventListener(WORKFLOW_READINESS_REFRESH_EVENT, handleReadinessRefreshEvent);
  void loadReadiness();
});

onBeforeUnmount(() => {
  window.removeEventListener(WORKFLOW_READINESS_REFRESH_EVENT, handleReadinessRefreshEvent);
});
</script>

<style scoped>
.workflow-readiness-shell {
  display: grid;
  gap: 12px;
}

.workflow-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 2px;
}

.workflow-action-tags {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.workflow-action-chip {
  text-align: left;
  display: grid;
  gap: 6px;
  padding: 12px;
  border-radius: 14px;
  border: 1px solid var(--aq-border);
  background: rgba(255, 255, 255, 0.03);
  color: var(--aq-ink);
  cursor: pointer;
}

.workflow-action-chip:hover {
  border-color: var(--aq-border-strong);
}

.workflow-action-chip strong {
  color: var(--aq-ink-strong);
}

.workflow-action-chip small {
  color: var(--aq-ink-soft);
  line-height: 1.45;
}
</style>

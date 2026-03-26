<template>
  <section class="aq-panel aq-fade-up workflow-readiness-shell">
    <div class="aq-title-row">
      <div>
        <h2>{{ t("workflow.title") }}</h2>
        <p class="aq-subtitle">{{ t("workflow.subtitle") }}</p>
      </div>
      <el-button size="small" :loading="loading" @click="loadReadiness">{{ t("common.refresh") }}</el-button>
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
        <span class="aq-metric-kicker">{{ t("workflow.session") }}</span>
        <strong class="aq-metric-value">{{ readiness.authenticated ? t("workflow.sessionSignedIn") : t("workflow.sessionGuest") }}</strong>
        <span class="aq-metric-copy">
          {{
            readiness.authenticated
              ? readiness.enrollment_required
                ? t("workflow.enrollmentRequired")
                : t("workflow.protectedUnlocked")
              : t("workflow.publicOnly")
          }}
        </span>
      </div>
      <div class="aq-metric-tile">
        <span class="aq-metric-kicker">{{ t("workflow.accounts") }}</span>
        <strong class="aq-metric-value">{{ readiness.exchange_accounts_summary.total }}</strong>
        <span class="aq-metric-copy">
          live={{ readiness.exchange_accounts_summary.live }} / testnet={{ readiness.exchange_accounts_summary.testnet }}
        </span>
      </div>
      <div class="aq-metric-tile">
        <span class="aq-metric-kicker">{{ t("workflow.riskGate") }}</span>
        <strong class="aq-metric-value">{{ readiness.has_risk_rule ? t("workflow.riskReady") : t("workflow.riskBlocked") }}</strong>
        <span class="aq-metric-copy">
          {{ readiness.has_risk_rule ? t("workflow.riskReadyCopy") : t("workflow.riskBlockedCopy") }}
        </span>
      </div>
      <div class="aq-metric-tile">
        <span class="aq-metric-kicker">{{ t("workflow.ai") }}</span>
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
        {{ t("workflow.liveTemplates") }}: {{ readiness?.live_supported_templates.map((item) => item.template_key).join(", ") || "-" }}
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
import { useI18n } from "../i18n";
import {
  WORKFLOW_READINESS_REFRESH_EVENT,
  getWorkflowReadiness,
  getErrorMessage,
  type WorkflowReadinessAction,
  type WorkflowReadinessResponse
} from "../api";

const router = useRouter();
const { t } = useI18n();
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
    errorMessage.value = getErrorMessage(error, t("workflow.loadError"));
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

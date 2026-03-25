<template>
  <AppShell>
    <template #toolbar>
      <el-button @click="toDashboard">Dashboard</el-button>
      <el-button type="primary" @click="reload" :loading="loading">Refresh</el-button>
    </template>

    <div class="aq-panel aq-fade-up">
      <div class="aq-title-row">
        <div>
          <h2>Ops Metrics</h2>
          <p class="aq-subtitle">
            Monitor runtime health, websocket activity, audit failures, and reconciliation backlog in real time.
          </p>
        </div>
      </div>

      <el-alert
        v-if="errorMessage"
        type="error"
        :closable="false"
        :title="errorMessage"
        style="margin-top: 12px"
      />

      <div v-if="metrics" class="aq-grid aq-grid-3" style="margin-top: 14px">
        <div class="aq-soft-block">
          <span class="aq-kv-label">WS Connections</span>
          <div class="ops-value">{{ metrics.ws_connection_count }}</div>
        </div>
        <div class="aq-soft-block">
          <span class="aq-kv-label">Strategy Processes</span>
          <div class="ops-value">{{ metrics.strategy_process_count }}</div>
        </div>
        <div class="aq-soft-block">
          <span class="aq-kv-label">Runtime Drift</span>
          <div class="ops-value">{{ metrics.runtime_status_drift_count }}</div>
        </div>
      </div>

      <el-descriptions v-if="metrics" :column="2" border style="margin-top: 16px">
        <el-descriptions-item label="Checked At">{{ metrics.checked_at }}</el-descriptions-item>
        <el-descriptions-item label="WS Online Users">{{ metrics.ws_online_user_count }}</el-descriptions-item>
        <el-descriptions-item label="Failed Audit Events (1h)">
          {{ metrics.failed_audit_events_last_hour }}
        </el-descriptions-item>
        <el-descriptions-item label="Critical Audit Events (1h)">
          {{ metrics.critical_audit_events_last_hour }}
        </el-descriptions-item>
        <el-descriptions-item label="Failed Audit Rate (1h)">
          {{ metrics.failed_audit_event_rate_last_hour }}
        </el-descriptions-item>
        <el-descriptions-item label="Lighter Retry Due">
          {{ metrics.lighter_reconcile_retry_due_count }}
        </el-descriptions-item>
        <el-descriptions-item label="Lighter Retry Blocked">
          {{ metrics.lighter_reconcile_retry_blocked_count }}
        </el-descriptions-item>
        <el-descriptions-item label="Oldest Lighter Pending (s)">
          {{ metrics.lighter_pending_oldest_age_seconds ?? "-" }}
        </el-descriptions-item>
      </el-descriptions>

      <div v-if="metrics" class="ops-grid">
        <div>
          <h3>Runtime Status Counts</h3>
          <el-table :data="mapCounts(metrics.strategy_runtime_counts)" size="small">
            <el-table-column prop="name" label="Status" />
            <el-table-column prop="value" label="Count" />
          </el-table>
        </div>
        <div>
          <h3>Lighter Reconciliation</h3>
          <el-table :data="mapCounts(metrics.lighter_reconcile_status_counts)" size="small">
            <el-table-column prop="name" label="Status" />
            <el-table-column prop="value" label="Count" />
          </el-table>
        </div>
      </div>

      <div v-if="metrics" style="margin-top: 20px">
        <h3>Audit Actions Last Hour</h3>
        <el-table :data="mapCounts(metrics.audit_action_counts_last_hour)" size="small">
          <el-table-column prop="name" label="Action" min-width="220" />
          <el-table-column prop="value" label="Count" width="120" />
        </el-table>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import AppShell from "../components/AppShell.vue";
import { ensureSession, getOpsMetrics } from "../api";

type MetricsPayload = {
  checked_at: string;
  ws_connection_count: number;
  ws_online_user_count: number;
  strategy_runtime_counts: Record<string, number>;
  strategy_process_count: number;
  runtime_status_drift_count: number;
  lighter_reconcile_status_counts: Record<string, number>;
  lighter_reconcile_retry_due_count: number;
  lighter_reconcile_retry_blocked_count: number;
  lighter_pending_oldest_age_seconds: number | null;
  total_audit_events_last_hour: number;
  failed_audit_events_last_hour: number;
  failed_audit_event_rate_last_hour: number;
  critical_audit_events_last_hour: number;
  audit_action_counts_last_hour: Record<string, number>;
};

const router = useRouter();
const loading = ref(false);
const errorMessage = ref("");
const metrics = ref<MetricsPayload | null>(null);
let refreshTimer: number | undefined;

function mapCounts(record: Record<string, number>) {
  return Object.entries(record || {})
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value);
}

async function reload() {
  try {
    await ensureSession();
  } catch {
    router.push("/login");
    return;
  }

  loading.value = true;
  errorMessage.value = "";
  try {
    metrics.value = await getOpsMetrics();
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || "Failed to load ops metrics.";
  } finally {
    loading.value = false;
  }
}

function toDashboard() {
  router.push("/dashboard");
}

onMounted(async () => {
  await reload();
  refreshTimer = window.setInterval(reload, 15000);
});

onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer);
  }
});
</script>

<style scoped>
.ops-value {
  margin-top: 8px;
  font-size: 24px;
  font-weight: 700;
  color: var(--aq-brand-ink);
}

.ops-grid {
  margin-top: 20px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}
</style>

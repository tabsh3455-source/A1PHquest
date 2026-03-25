<template>
  <AppShell
    title="Ops Monitor"
    subtitle="Watch runtime health, websocket footprint, audit failures, and reconciliation pressure from one operator surface. Metrics refresh automatically so you can spot drift before it becomes execution risk."
  >
    <template #toolbar>
      <router-link class="aq-auth-link ops-toolbar-link" to="/settings">Runtime Settings</router-link>
      <el-button type="primary" @click="reload" :loading="loading">Refresh</el-button>
    </template>

    <el-alert
      v-if="errorMessage"
      type="error"
      :closable="false"
      :title="errorMessage"
      class="aq-fade-up"
    />

    <section v-if="metrics" class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>System Pulse</h2>
          <p class="aq-section-copy">
            The first line answers whether runtime is healthy, whether websocket demand is rising, and whether reconciliation or audit pressure needs attention.
          </p>
        </div>
        <span class="aq-chip">{{ metrics.checked_at }}</span>
      </div>

      <div class="aq-summary-strip">
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">WS Connections</span>
          <strong class="aq-metric-value">{{ metrics.ws_connection_count }}</strong>
          <span class="aq-metric-copy">Total active socket sessions across authenticated and public feeds.</span>
        </div>
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Strategy Processes</span>
          <strong class="aq-metric-value">{{ metrics.strategy_process_count }}</strong>
          <span class="aq-metric-copy">Worker-side runtime processes currently tracked by the supervisor.</span>
        </div>
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Runtime Drift</span>
          <strong class="aq-metric-value">{{ metrics.runtime_status_drift_count }}</strong>
          <span class="aq-metric-copy">Count of strategy runtimes whose observed status differs from the stored state.</span>
        </div>
        <div class="aq-metric-tile">
          <span class="aq-metric-kicker">Failed Audit Rate</span>
          <strong class="aq-metric-value">{{ metrics.failed_audit_event_rate_last_hour }}</strong>
          <span class="aq-metric-copy">Failure pressure over the last hour across security and control-plane actions.</span>
        </div>
      </div>
    </section>

    <section v-if="metrics" class="aq-panel aq-fade-up">
      <div class="ops-stage">
        <div class="aq-soft-block aq-stack">
          <div>
            <h3>Runtime Status Mix</h3>
            <p class="aq-form-note">Quickly see how many strategies are running, paused, pending, or failed.</p>
          </div>
          <el-table :data="mapCounts(metrics.strategy_runtime_counts)" size="small">
            <el-table-column prop="name" label="Status" />
            <el-table-column prop="value" label="Count" width="110" />
          </el-table>
        </div>

        <div class="aq-soft-block aq-stack">
          <div>
            <h3>Lighter Reconciliation</h3>
            <p class="aq-form-note">Surface retry backlog and blocked items before order reconciliation starts drifting.</p>
          </div>
          <el-table :data="mapCounts(metrics.lighter_reconcile_status_counts)" size="small">
            <el-table-column prop="name" label="Status" />
            <el-table-column prop="value" label="Count" width="110" />
          </el-table>
        </div>
      </div>
    </section>

    <section v-if="metrics" class="aq-panel aq-fade-up">
      <div class="aq-section-header">
        <div>
          <h2>Audit Pressure</h2>
          <p class="aq-section-copy">
            Look at which control actions are failing most often and whether those failures are critical enough to warrant intervention.
          </p>
        </div>
      </div>

      <el-table :data="mapCounts(metrics.audit_action_counts_last_hour)" size="small">
        <el-table-column prop="name" label="Action" min-width="240" />
        <el-table-column prop="value" label="Count" width="120" />
      </el-table>
    </section>

    <section v-if="!metrics && !loading && !errorMessage" class="aq-panel aq-fade-up aq-empty-state">
      <div>
        <h3>No ops metrics loaded yet.</h3>
        <p>Refresh the monitor to pull a fresh snapshot from the backend.</p>
      </div>
    </section>

    <template #inspector>
      <section v-if="metrics" class="aq-soft-block aq-stack">
        <div>
          <h3>Operator Summary</h3>
          <p class="aq-form-note">A compact snapshot for deciding whether the desk needs intervention right now.</p>
        </div>
        <div class="aq-note-list">
          <div class="aq-note-row">
            <strong>Online users</strong>
            <small>{{ metrics.ws_online_user_count }} websocket users currently have authenticated sessions.</small>
          </div>
          <div class="aq-note-row">
            <strong>Critical audit events</strong>
            <small>{{ metrics.critical_audit_events_last_hour }} high-severity failures were recorded over the last hour.</small>
          </div>
          <div class="aq-note-row">
            <strong>Oldest pending lighter job</strong>
            <small>{{ metrics.lighter_pending_oldest_age_seconds ?? "-" }} seconds since the oldest pending reconcile item was created.</small>
          </div>
        </div>
      </section>

      <section v-if="metrics" class="aq-soft-block aq-stack">
        <div>
          <h3>Escalation Flags</h3>
          <p class="aq-form-note">These derived reads help you decide whether to keep watching or actively intervene.</p>
        </div>
        <div class="aq-note-list">
          <div class="aq-note-row">
            <strong>Runtime drift</strong>
            <small>{{ metrics.runtime_status_drift_count > 0 ? "Action recommended. Stored state and observed state are diverging." : "Stable. No drift currently detected." }}</small>
          </div>
          <div class="aq-note-row">
            <strong>Retry pressure</strong>
            <small>{{ metrics.lighter_reconcile_retry_due_count > 0 ? "Pending retries exist and may need closer monitoring." : "No outstanding retry pressure right now." }}</small>
          </div>
          <div class="aq-note-row">
            <strong>Audit reliability</strong>
            <small>{{ metrics.failed_audit_event_rate_last_hour > 0 ? "Some control actions are failing. Review the audit action table." : "No failed audit activity recorded in the last hour." }}</small>
          </div>
        </div>
      </section>
    </template>
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
    router.push("/auth");
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
.ops-toolbar-link {
  min-width: 148px;
}

.ops-stage {
  display: grid;
  gap: 16px;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
}
</style>

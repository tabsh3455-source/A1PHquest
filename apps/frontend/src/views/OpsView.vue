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
          <h2>Futures Grid Audit</h2>
          <p class="aq-section-copy">
            Runtime-level trace snapshots for direction and leverage. This panel helps verify whether futures grid execution semantics match configured intent.
          </p>
        </div>
      </div>

      <el-table v-if="futuresAudit.length" :data="futuresAudit" size="small">
        <el-table-column label="Runtime Ref" min-width="180">
          <template #default="{ row }">{{ row.runtime_ref || "-" }}</template>
        </el-table-column>
        <el-table-column prop="strategy_name" label="Strategy" min-width="160" />
        <el-table-column label="Open" width="120">
          <template #default="{ row }">
            <el-button size="small" @click="openStrategy(row)">Open</el-button>
          </template>
        </el-table-column>
        <el-table-column label="Runtime" width="120">
          <template #default="{ row }">
            <el-tag :type="runtimeStatusType(row.runtime_status)">{{ row.runtime_status || "unknown" }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Action Level" width="130">
          <template #default="{ row }">
            <el-tag :type="actionLevelType(row.action_level)">{{ row.action_level }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Direction" width="120">
          <template #default="{ row }">{{ directionLabel(row.direction) }}</template>
        </el-table-column>
        <el-table-column label="Leverage" width="110">
          <template #default="{ row }">{{ leverageLabel(row.leverage) }}</template>
        </el-table-column>
        <el-table-column label="Grid Seed" min-width="170">
          <template #default="{ row }">{{ gridSeedLabel(row) }}</template>
        </el-table-column>
        <el-table-column label="Flags" min-width="240">
          <template #default="{ row }">{{ auditFlagsLabel(row.audit_flags) }}</template>
        </el-table-column>
        <el-table-column label="Suggested Action" min-width="320">
          <template #default="{ row }">{{ row.suggested_action || "-" }}</template>
        </el-table-column>
        <el-table-column label="Last Trace" min-width="170">
          <template #default="{ row }">{{ lastTraceLabel(row) }}</template>
        </el-table-column>
      </el-table>
      <el-empty
        v-else
        description="No futures_grid trace checkpoints yet. Start a futures grid strategy to populate runtime profile and seed events."
      />
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
import { ensureSession, getOpsFuturesGridAudit, getOpsMetrics, type OpsFuturesGridRuntimeAudit, type OpsMetricsPayload } from "../api";

const router = useRouter();
const loading = ref(false);
const errorMessage = ref("");
const metrics = ref<OpsMetricsPayload | null>(null);
const futuresAudit = ref<OpsFuturesGridRuntimeAudit[]>([]);
let refreshTimer: number | undefined;

function mapCounts(record: Record<string, number>) {
  return Object.entries(record || {})
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value);
}

function runtimeStatusType(status: string | null) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "running") return "success";
  if (normalized === "failed") return "danger";
  if (normalized === "starting" || normalized === "stopping") return "warning";
  return "info";
}

function actionLevelType(level: OpsFuturesGridRuntimeAudit["action_level"]) {
  if (level === "critical") return "danger";
  if (level === "warning") return "warning";
  return "success";
}

function directionLabel(direction: OpsFuturesGridRuntimeAudit["direction"]) {
  if (direction === "long") return "Long only";
  if (direction === "short") return "Short only";
  if (direction === "neutral") return "Neutral";
  return "-";
}

function leverageLabel(leverage: number | null) {
  return leverage && leverage > 0 ? `${leverage}x` : "-";
}

function gridSeedLabel(row: OpsFuturesGridRuntimeAudit) {
  if (row.planned_order_count == null) {
    return "-";
  }
  const buy = row.buy_levels ?? 0;
  const sell = row.sell_levels ?? 0;
  return `${row.planned_order_count} (${buy}B / ${sell}S)`;
}

function auditFlagsLabel(flags: string[]) {
  if (!flags?.length) {
    return "-";
  }
  return flags.join(", ");
}

function lastTraceLabel(row: OpsFuturesGridRuntimeAudit) {
  return row.profile_timestamp || row.grid_seeded_timestamp || row.last_heartbeat || "-";
}

function openStrategy(row: OpsFuturesGridRuntimeAudit) {
  const query: Record<string, string> = {
    strategy_id: String(row.strategy_id)
  };
  if (row.runtime_ref) {
    query.runtime_ref = row.runtime_ref;
  }
  router.push({ path: "/strategies", query });
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
    try {
      const audit = await getOpsFuturesGridAudit(30);
      futuresAudit.value = audit.runtimes || [];
    } catch {
      futuresAudit.value = [];
    }
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

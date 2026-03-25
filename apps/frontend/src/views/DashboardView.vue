<template>
  <AppShell>
    <template #toolbar>
      <span class="aq-chip">Binance / OKX / Lighter</span>
      <el-button @click="logout">Logout</el-button>
    </template>

    <div class="aq-panel aq-fade-up">
      <div class="aq-title-row">
        <div>
          <h1>A1phquest Dashboard</h1>
          <p class="aq-subtitle">
            Manage exchange accounts, strategy versions, and runtime health from one place.
          </p>
        </div>
      </div>

      <el-divider />

      <div class="aq-grid aq-grid-3">
        <button class="quick-link" type="button" @click="toAccounts">
          <span class="quick-link-title">Exchange Accounts</span>
          <span class="quick-link-subtitle">
            Store credentials, validate connectivity, and sync account snapshots.
          </span>
        </button>
        <button class="quick-link" type="button" @click="toStrategies">
          <span class="quick-link-title">Strategies</span>
          <span class="quick-link-subtitle">
            Create new strategy versions, edit stopped ones, and choose which strategy to run.
          </span>
        </button>
        <button class="quick-link" type="button" @click="toAi">
          <span class="quick-link-title">AI Autopilot</span>
          <span class="quick-link-subtitle">
            Let AI evaluate market factors and automatically choose which candidate strategy version should be active.
          </span>
        </button>
        <button class="quick-link" type="button" @click="toSettings">
          <span class="quick-link-title">System Settings</span>
          <span class="quick-link-subtitle">
            Tune low-latency market data behavior without editing deployment env files.
          </span>
        </button>
      </div>

      <div class="status-band">
        <div class="aq-kv">
          <span class="aq-kv-label">Project</span>
          <span class="aq-kv-value">A1phquest</span>
        </div>
        <div class="aq-kv">
          <span class="aq-kv-label">Live Exchanges</span>
          <span class="aq-kv-value">Binance + OKX</span>
        </div>
        <div class="aq-kv">
          <span class="aq-kv-label">Lighter</span>
          <span class="aq-kv-value">Client-signed only</span>
        </div>
        <div class="aq-kv">
          <span class="aq-kv-label">Ops Center</span>
          <el-button type="primary" plain @click="toOps">Open Metrics</el-button>
        </div>
        <div class="aq-kv">
          <span class="aq-kv-label">Runtime Settings</span>
          <el-button type="primary" plain @click="toSettings">Open Settings</el-button>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { useRouter } from "vue-router";
import AppShell from "../components/AppShell.vue";
import { logout as logoutRequest } from "../api";

const router = useRouter();

function toStrategies() {
  router.push("/strategies");
}

function toAi() {
  router.push("/ai");
}

function toAccounts() {
  router.push("/accounts");
}

function toOps() {
  router.push("/ops");
}

function toSettings() {
  router.push("/settings");
}

async function logout() {
  await logoutRequest();
  router.push("/login");
}
</script>

<style scoped>
.quick-link {
  width: 100%;
  text-align: left;
  border: 1px solid var(--aq-border);
  border-radius: 14px;
  background: linear-gradient(180deg, #ffffff 0%, #f6faff 100%);
  padding: 14px;
  transition: all 0.18s ease;
  cursor: pointer;
}

.quick-link:hover {
  border-color: var(--aq-border-strong);
  transform: translateY(-1px);
  box-shadow: 0 8px 20px rgba(31, 111, 235, 0.12);
}

.quick-link-title {
  display: block;
  font-weight: 700;
  color: var(--aq-ink-strong);
}

.quick-link-subtitle {
  margin-top: 6px;
  display: block;
  font-size: 12px;
  color: var(--aq-ink-soft);
  line-height: 1.5;
}

.status-band {
  margin-top: 14px;
  border: 1px solid var(--aq-border);
  border-radius: 14px;
  background: #f8fbff;
  padding: 12px;
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}
</style>

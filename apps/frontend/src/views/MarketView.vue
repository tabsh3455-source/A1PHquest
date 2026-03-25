<template>
  <AppShell
    title="Market Terminal"
    subtitle="Public real-time market deck with low-latency candles, product switching, and direct strategy launch points."
    :public-mode="!session"
  >
    <template #toolbar>
      <span class="aq-chip">{{ exchange.toUpperCase() }} / {{ marketType.toUpperCase() }}</span>
      <span class="aq-chip">{{ symbol || "Loading symbol..." }}</span>
      <router-link v-if="!session" class="aq-auth-link market-auth-link" to="/auth">Sign in to trade</router-link>
    </template>

    <section class="market-hero aq-panel aq-fade-up">
      <div class="market-hero-copy">
        <span class="market-kicker">Public Market Workspace</span>
        <h2>{{ symbol }}</h2>
        <p>
          Watch public candles before you wire any exchange account. Once you sign in, use the same market context to seed strategy drafts and live-ready grid or DCA versions.
        </p>
      </div>
      <div class="market-hero-metrics">
        <div class="aq-soft-block">
          <span class="aq-kv-label">Exchange</span>
          <strong>{{ exchange.toUpperCase() }}</strong>
        </div>
        <div class="aq-soft-block">
          <span class="aq-kv-label">Market</span>
          <strong>{{ marketType.toUpperCase() }}</strong>
        </div>
        <div class="aq-soft-block">
          <span class="aq-kv-label">Default Mode</span>
          <strong>Public feed</strong>
        </div>
      </div>
    </section>

    <StrategyCandleChart
      mode="public"
      :exchange="exchange"
      :market-type="marketType"
      :symbol="symbol"
      title="Public Candle Feed"
      subtitle="Historical backfill and live websocket updates run without requiring a saved strategy."
      empty-message="Pick an exchange and symbol to start streaming candles."
    />

    <section class="aq-panel aq-fade-up">
      <div class="aq-title-row">
        <div>
          <h2>Template Launchpad</h2>
          <p class="aq-subtitle">Turn the currently selected market into a saved strategy draft or live-ready version.</p>
        </div>
      </div>

      <div class="aq-grid aq-grid-3" style="margin-top: 14px">
        <button v-for="item in featuredTemplates" :key="item.template_key" class="template-launch-card" type="button" @click="openTemplate(item.template_key)">
          <span class="template-launch-state" :class="item.execution_status">{{ item.execution_status === "live_supported" ? "Live" : "Draft" }}</span>
          <strong>{{ item.display_name }}</strong>
          <small>{{ item.description }}</small>
        </button>
      </div>
    </section>

    <template #inspector>
      <section class="aq-soft-block aq-stack">
        <div>
          <h3>Market Scope</h3>
          <p class="aq-form-note">Switch exchange, product type, and symbol before jumping into strategy creation.</p>
        </div>
        <el-form label-position="top">
          <el-form-item label="Exchange">
            <el-segmented v-model="exchange" :options="exchangeOptions" @change="loadSymbols" />
          </el-form-item>
          <el-form-item label="Market Type">
            <el-segmented v-model="marketType" :options="marketTypeOptions" @change="loadSymbols" />
          </el-form-item>
          <el-form-item label="Symbol">
            <el-select v-model="symbol" filterable style="width: 100%" @visible-change="loadSymbols">
              <el-option v-for="item in symbols" :key="item.symbol" :label="item.label" :value="item.symbol" />
            </el-select>
          </el-form-item>
        </el-form>
      </section>

      <section class="aq-soft-block aq-stack">
        <div>
          <h3>From This Market</h3>
          <p class="aq-form-note">Jump into a prefilled template using the currently selected symbol.</p>
        </div>
        <el-button type="primary" @click="openTemplate('spot_grid')">Create Spot Grid</el-button>
        <el-button @click="openTemplate('dca')">Create DCA</el-button>
        <el-button @click="openTemplate('signal_bot')">Create Signal Draft</el-button>
      </section>
    </template>
  </AppShell>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import AppShell from "../components/AppShell.vue";
import StrategyCandleChart from "../components/StrategyCandleChart.vue";
import { listPublicMarketSymbols, listStrategyTemplates, useSessionState, type StrategyTemplateItem, type PublicMarketSymbolItem } from "../api";

const router = useRouter();
const sessionRef = useSessionState();
const session = sessionRef;

const exchange = ref<"binance" | "okx">("binance");
const marketType = ref<"spot" | "perp">("spot");
const symbol = ref("BTCUSDT");
const symbols = ref<PublicMarketSymbolItem[]>([]);
const featuredTemplates = ref<StrategyTemplateItem[]>([]);

const exchangeOptions = [
  { label: "Binance", value: "binance" },
  { label: "OKX", value: "okx" }
] as const;
const marketTypeOptions = [
  { label: "Spot", value: "spot" },
  { label: "Perp", value: "perp" }
] as const;

async function loadSymbols() {
  const response = await listPublicMarketSymbols(exchange.value, marketType.value);
  symbols.value = response.symbols;
  const nextSymbol = response.symbols.find((item) => item.is_default)?.symbol || response.symbols[0]?.symbol || "";
  if (!symbols.value.some((item) => item.symbol === symbol.value)) {
    symbol.value = nextSymbol;
  }
}

async function loadTemplates() {
  const templates = await listStrategyTemplates();
  featuredTemplates.value = templates.slice(0, 6);
}

function openTemplate(templateKey: string) {
  if (!session.value || session.value.enrollment_required) {
    router.push("/auth");
    return;
  }
  router.push({
    path: "/strategies",
    query: {
      template: templateKey,
      exchange: exchange.value,
      market_type: marketType.value,
      symbol: symbol.value
    }
  });
}

onMounted(async () => {
  await Promise.all([loadSymbols(), loadTemplates()]);
});
</script>

<style scoped>
.market-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(280px, 0.75fr);
  gap: 16px;
}

.market-kicker {
  color: var(--aq-brand);
  font-size: 12px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.market-hero-copy h2 {
  margin: 8px 0 10px;
  font-size: clamp(32px, 5vw, 56px);
  letter-spacing: -0.04em;
  color: var(--aq-ink-strong);
}

.market-hero-copy p {
  margin: 0;
  max-width: 58ch;
  color: var(--aq-ink-soft);
  line-height: 1.7;
}

.market-hero-metrics {
  display: grid;
  gap: 12px;
}

.market-hero-metrics strong {
  color: var(--aq-ink-strong);
  font-size: 22px;
}

.template-launch-card {
  text-align: left;
  padding: 16px;
  border-radius: 18px;
  border: 1px solid var(--aq-border);
  background: linear-gradient(180deg, rgba(18, 28, 43, 0.96) 0%, rgba(10, 18, 29, 0.98) 100%);
  color: var(--aq-ink);
  cursor: pointer;
  transition: 180ms ease;
}

.template-launch-card:hover {
  transform: translateY(-2px);
  border-color: var(--aq-border-strong);
}

.template-launch-card strong {
  display: block;
  margin-top: 10px;
  color: var(--aq-ink-strong);
}

.template-launch-card small {
  display: block;
  margin-top: 8px;
  line-height: 1.6;
  color: var(--aq-ink-soft);
}

.template-launch-state {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.template-launch-state.live_supported {
  color: #02170e;
  background: #16d1a7;
}

.template-launch-state.draft_only {
  color: #1d1400;
  background: var(--aq-warning);
}

.market-auth-link {
  min-width: 160px;
}

@media (max-width: 960px) {
  .market-hero {
    grid-template-columns: 1fr;
  }
}
</style>

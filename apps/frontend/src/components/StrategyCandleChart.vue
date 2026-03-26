<template>
  <section class="aq-panel chart-shell">
    <div class="chart-toolbar">
      <div>
        <h2>{{ titleLabel }}</h2>
        <p class="aq-subtitle">{{ subtitleLabel }}</p>
      </div>
      <div class="chart-toolbar-actions">
        <el-select v-model="selectedInterval" size="small" style="width: 104px" @change="reloadChart">
          <el-option v-for="item in intervals" :key="item" :label="item" :value="item" />
        </el-select>
        <el-tag :type="connectionTagType">{{ connectionLabel }}</el-tag>
      </div>
    </div>

    <div class="chart-meta">
      <span>{{ exchangeLabel }}</span>
      <span>{{ marketTypeLabel }}</span>
      <span>{{ displaySymbol }}</span>
      <span v-if="lastPriceLabel">{{ t("chart.last") }} {{ lastPriceLabel }}</span>
    </div>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon style="margin-top: 12px" />

    <div v-loading="loading" class="chart-surface">
      <div v-if="!isReady" class="aq-empty-state">
        <div>
          <strong>{{ t("chart.waiting") }}</strong>
          <p>{{ emptyMessageLabel }}</p>
        </div>
      </div>
      <div v-else ref="chartRoot" class="chart-root" />
    </div>
  </section>
</template>

<script setup lang="ts">
import {
  createChart,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp
} from "lightweight-charts";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  API_BASE,
  getMarketKlines,
  getPublicMarketKlines,
  type MarketKlineItem
} from "../api";
import { useI18n } from "../i18n";

const props = withDefaults(defineProps<{
  mode?: "private" | "public";
  exchangeAccountId?: number | null;
  exchange?: string | null;
  marketType?: "spot" | "perp";
  symbol?: string | null;
  title?: string;
  subtitle?: string;
  emptyMessage?: string;
}>(), {
  mode: "private",
  exchangeAccountId: null,
  exchange: null,
  marketType: "spot",
  symbol: null,
  title: "",
  subtitle: "",
  emptyMessage: ""
});

const intervals = ["1m", "5m", "15m", "1h"];
const CHART_UPDATE_MAX_HZ = 5;
const CHART_UPDATE_MIN_INTERVAL_MS = Math.max(Math.floor(1000 / CHART_UPDATE_MAX_HZ), 1);
const { t } = useI18n();
const selectedInterval = ref("1m");
const chartRoot = ref<HTMLDivElement | null>(null);
const loading = ref(false);
const errorMessage = ref("");
const connectionState = ref<"idle" | "connecting" | "live" | "reconnecting" | "stale" | "error">("idle");
const lastPriceLabel = ref("");

const isPublic = computed(() => props.mode === "public");
const titleLabel = computed(() => props.title || t("chart.defaultTitle"));
const subtitleLabel = computed(() => props.subtitle || t("chart.defaultSubtitle"));
const emptyMessageLabel = computed(() => props.emptyMessage || t("chart.defaultEmpty"));
const isReady = computed(() => {
  if (isPublic.value) {
    return Boolean(props.exchange && props.symbol);
  }
  return Boolean(props.exchangeAccountId && props.exchange && props.symbol);
});
const displaySymbol = computed(() => String(props.symbol || "-"));
const exchangeLabel = computed(() => String(props.exchange || "-").toUpperCase());
const marketTypeLabel = computed(() => String(props.marketType || "spot").toUpperCase());
const connectionLabel = computed(() => {
  if (connectionState.value === "live") {
    return t("chart.wsLive");
  }
  if (connectionState.value === "connecting") {
    return t("chart.connecting");
  }
  if (connectionState.value === "reconnecting") {
    return t("chart.reconnecting");
  }
  if (connectionState.value === "stale") {
    return t("chart.stale");
  }
  if (connectionState.value === "error") {
    return t("chart.socketError");
  }
  return t("chart.idle");
});
const connectionTagType = computed(() => {
  if (connectionState.value === "live") {
    return "success";
  }
  if (connectionState.value === "error") {
    return "danger";
  }
  if (["connecting", "reconnecting", "stale"].includes(connectionState.value)) {
    return "warning";
  }
  return "info";
});

let chart: IChartApi | null = null;
let series: ISeriesApi<"Candlestick"> | null = null;
let socket: WebSocket | null = null;
let reconnectTimer: number | null = null;
let pingTimer: number | null = null;
let resizeObserver: ResizeObserver | null = null;
let activeSubscription: Record<string, unknown> | null = null;
let pendingCandle: CandlestickData | null = null;
let pendingLastPrice = "";
let candleFlushTimer: number | null = null;
let lastChartUpdateMs = 0;

function buildWsUrl() {
  const url = new URL(API_BASE);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = isPublic.value ? "/ws/market" : "/ws/events";
  url.search = "";
  return url.toString();
}

function currentSubscription() {
  if (isPublic.value) {
    return {
      exchange: String(props.exchange || "").toLowerCase(),
      market_type: props.marketType,
      symbol: displaySymbol.value,
      interval: selectedInterval.value
    };
  }
  return {
    exchange_account_id: Number(props.exchangeAccountId || 0),
    market_type: props.marketType,
    symbol: displaySymbol.value,
    interval: selectedInterval.value
  };
}

function toChartPoint(item: MarketKlineItem): CandlestickData {
  return {
    time: item.time as UTCTimestamp,
    open: Number(item.open),
    high: Number(item.high),
    low: Number(item.low),
    close: Number(item.close)
  };
}

function ensureChart() {
  if (!chartRoot.value || chart) {
    return;
  }
  chart = createChart(chartRoot.value, {
    height: 420,
    layout: {
      background: { color: "#0a111b" },
      textColor: "#b9cae6"
    },
    grid: {
      vertLines: { color: "rgba(144, 166, 203, 0.08)" },
      horzLines: { color: "rgba(144, 166, 203, 0.08)" }
    },
    rightPriceScale: {
      borderColor: "rgba(144, 166, 203, 0.18)"
    },
    timeScale: {
      borderColor: "rgba(144, 166, 203, 0.18)",
      timeVisible: true,
      secondsVisible: false
    },
    crosshair: {
      vertLine: { color: "rgba(63, 176, 255, 0.35)" },
      horzLine: { color: "rgba(63, 176, 255, 0.35)" }
    }
  });
  series = chart.addCandlestickSeries({
    upColor: "#16d1a7",
    downColor: "#ff6b7a",
    borderVisible: false,
    wickUpColor: "#16d1a7",
    wickDownColor: "#ff6b7a"
  });
  resizeObserver = new ResizeObserver(() => {
    if (chart && chartRoot.value) {
      chart.applyOptions({ width: chartRoot.value.clientWidth });
    }
  });
  resizeObserver.observe(chartRoot.value);
  chart.applyOptions({ width: chartRoot.value.clientWidth });
}

function clearTimers() {
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (pingTimer !== null) {
    window.clearInterval(pingTimer);
    pingTimer = null;
  }
}

function clearCandleFlushTimer() {
  if (candleFlushTimer !== null) {
    window.clearTimeout(candleFlushTimer);
    candleFlushTimer = null;
  }
}

function resetCandleUpdateQueue() {
  pendingCandle = null;
  pendingLastPrice = "";
  lastChartUpdateMs = 0;
  clearCandleFlushTimer();
}

function flushPendingCandleUpdate() {
  clearCandleFlushTimer();
  if (!pendingCandle || !series) {
    return;
  }
  series.update(pendingCandle);
  if (pendingLastPrice) {
    lastPriceLabel.value = pendingLastPrice;
  }
  pendingCandle = null;
  pendingLastPrice = "";
  lastChartUpdateMs = Date.now();
}

function schedulePendingCandleFlush() {
  if (candleFlushTimer !== null) {
    return;
  }
  const elapsed = Date.now() - lastChartUpdateMs;
  const delay = Math.max(CHART_UPDATE_MIN_INTERVAL_MS - elapsed, 0);
  candleFlushTimer = window.setTimeout(() => {
    candleFlushTimer = null;
    flushPendingCandleUpdate();
  }, delay);
}

function queueCandleUpdate(next: CandlestickData) {
  pendingCandle = next;
  pendingLastPrice = Number(next.close).toFixed(4);
  const elapsed = Date.now() - lastChartUpdateMs;
  if (!lastChartUpdateMs || elapsed >= CHART_UPDATE_MIN_INTERVAL_MS) {
    flushPendingCandleUpdate();
    return;
  }
  schedulePendingCandleFlush();
}

function sendSocketMessage(payload: Record<string, unknown>) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload));
  }
}

function matchesStreamPayload(payload: Record<string, unknown>) {
  if (String(payload.exchange || "") !== String((props.exchange || "").toLowerCase())) {
    return false;
  }
  if (String(payload.market_type || "spot") !== String(props.marketType || "spot")) {
    return false;
  }
  return (
    String(payload.symbol || "") === displaySymbol.value &&
    String(payload.interval || "") === selectedInterval.value
  );
}

function subscribeCurrentStream() {
  if (!isReady.value) {
    return;
  }
  const next = currentSubscription();
  if (activeSubscription) {
    sendSocketMessage({ action: "unsubscribe_market", ...activeSubscription });
  }
  activeSubscription = next;
  sendSocketMessage({ action: "subscribe_market", ...next });
}

function closeSocket() {
  clearTimers();
  resetCandleUpdateQueue();
  if (!socket) {
    return;
  }
  try {
    if (socket.readyState === WebSocket.OPEN && activeSubscription) {
      sendSocketMessage({ action: "unsubscribe_market", ...activeSubscription });
    }
    socket.close();
  } catch {
    // ignore teardown path
  }
  socket = null;
}

function scheduleReconnect() {
  if (!isReady.value || reconnectTimer !== null) {
    return;
  }
  connectionState.value = "reconnecting";
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    openSocket();
  }, 2000);
}

function handleSocketMessage(rawMessage: string) {
  let payload: any;
  try {
    payload = JSON.parse(rawMessage);
  } catch {
    return;
  }
  if (!payload || typeof payload !== "object") {
    return;
  }
  if (payload.type === "market_subscription_error") {
    errorMessage.value = String(payload.message || t("chart.subscribeError"));
    connectionState.value = "error";
    return;
  }
  if (payload.type === "market_subscription_ack") {
    connectionState.value = "connecting";
    return;
  }
  if (payload.type === "market_stream_status") {
    const statusPayload = payload.payload;
    if (!statusPayload || typeof statusPayload !== "object" || !matchesStreamPayload(statusPayload)) {
      return;
    }
    connectionState.value = String(statusPayload.status || "connecting") as typeof connectionState.value;
    errorMessage.value = connectionState.value === "error"
      ? String(statusPayload.message || t("chart.streamError"))
      : "";
    return;
  }
  if (payload.type !== "market_candle") {
    return;
  }
  const candlePayload = payload.payload;
  if (!candlePayload || typeof candlePayload !== "object" || !matchesStreamPayload(candlePayload) || !series) {
    return;
  }
  const candle = candlePayload.candle;
  if (!candle || typeof candle !== "object") {
    return;
  }
  queueCandleUpdate({
    time: Number(candle.time) as UTCTimestamp,
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close)
  });
  if (connectionState.value !== "error") {
    connectionState.value = "live";
  }
}

function openSocket() {
  if (!isReady.value) {
    return;
  }
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    subscribeCurrentStream();
    return;
  }
  clearTimers();
  connectionState.value = "connecting";
  socket = new WebSocket(buildWsUrl());
  socket.addEventListener("open", () => {
    connectionState.value = "connecting";
    subscribeCurrentStream();
    pingTimer = window.setInterval(() => {
      sendSocketMessage({ type: "ping" });
    }, 15000);
  });
  socket.addEventListener("message", (event) => {
    handleSocketMessage(String(event.data || ""));
  });
  socket.addEventListener("error", () => {
    connectionState.value = "error";
  });
  socket.addEventListener("close", () => {
    socket = null;
    clearTimers();
    if (isReady.value) {
      scheduleReconnect();
    } else {
      connectionState.value = "idle";
    }
  });
}

async function reloadChart() {
  if (!isReady.value) {
    errorMessage.value = emptyMessageLabel.value;
    lastPriceLabel.value = "";
    resetCandleUpdateQueue();
    activeSubscription = null;
    connectionState.value = "idle";
    series?.setData([]);
    closeSocket();
    return;
  }

  ensureChart();
  resetCandleUpdateQueue();
  errorMessage.value = "";
  loading.value = true;
  try {
    const response = isPublic.value
      ? await getPublicMarketKlines(
          String(props.exchange || "").toLowerCase(),
          props.marketType || "spot",
          displaySymbol.value,
          selectedInterval.value,
          300
        )
      : await getMarketKlines(
          Number(props.exchangeAccountId),
          displaySymbol.value,
          selectedInterval.value,
          props.marketType || "spot",
          300
        );

    series?.setData(response.candles.map((item) => toChartPoint(item)));
    const latest = response.candles[response.candles.length - 1];
    lastPriceLabel.value = latest ? Number(latest.close).toFixed(4) : "";
    await nextTick();
    openSocket();
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || t("chart.loadError");
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.mode, props.exchangeAccountId, props.exchange, props.marketType, props.symbol].join("|"),
  async () => {
    await reloadChart();
  }
);

onMounted(async () => {
  ensureChart();
  await reloadChart();
});

onBeforeUnmount(() => {
  closeSocket();
  resetCandleUpdateQueue();
  resizeObserver?.disconnect();
  resizeObserver = null;
  chart?.remove();
  chart = null;
  series = null;
});
</script>

<style scoped>
.chart-shell {
  min-height: 560px;
}

.chart-toolbar {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: flex-start;
}

.chart-toolbar h2 {
  margin: 0;
  color: var(--aq-ink-strong);
}

.chart-toolbar-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.chart-meta {
  margin-top: 12px;
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  color: var(--aq-ink-soft);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.chart-surface {
  margin-top: 14px;
  min-height: 440px;
}

.chart-root {
  min-height: 440px;
}

@media (max-width: 960px) {
  .chart-shell {
    min-height: 500px;
  }

  .chart-toolbar {
    flex-direction: column;
  }

  .chart-root {
    min-height: 360px;
  }
}
</style>

<template>
  <div class="chart-panel">
    <div class="chart-toolbar">
      <div>
        <h2>Live Candles</h2>
        <p class="aq-subtitle">
          Historical candles come from the market API, then the current bar keeps updating over WebSocket.
        </p>
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
      <span>{{ displaySymbol }}</span>
      <span v-if="lastPriceLabel">Last {{ lastPriceLabel }}</span>
    </div>

    <el-alert
      v-if="errorMessage"
      :title="errorMessage"
      type="error"
      show-icon
      style="margin-top: 12px"
    />

    <div v-loading="loading" class="chart-surface">
      <div v-if="!isReady" class="chart-empty">
        Select a Binance or OKX strategy to load its market candles.
      </div>
      <div v-else ref="chartRoot" class="chart-root" />
    </div>
  </div>
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
import { API_BASE, getMarketKlines, type MarketKlineItem } from "../api";

const props = defineProps<{
  exchangeAccountId: number | null;
  exchange: string | null;
  symbol: string | null;
}>();

const intervals = ["1m", "5m", "15m", "1h"];
const selectedInterval = ref("1m");
const chartRoot = ref<HTMLDivElement | null>(null);
const loading = ref(false);
const errorMessage = ref("");
const connectionState = ref<"idle" | "connecting" | "live" | "reconnecting" | "stale" | "error">("idle");
const lastPriceLabel = ref("");

const isReady = computed(() => Boolean(props.exchangeAccountId && props.symbol));
const displaySymbol = computed(() => String(props.symbol || "-"));
const exchangeLabel = computed(() => String(props.exchange || "-").toUpperCase());
const connectionLabel = computed(() => {
  if (connectionState.value === "live") {
    return "WS live";
  }
  if (connectionState.value === "connecting") {
    return "Connecting";
  }
  if (connectionState.value === "reconnecting") {
    return "Reconnecting";
  }
  if (connectionState.value === "stale") {
    return "Stream stale";
  }
  if (connectionState.value === "error") {
    return "Socket error";
  }
  return "Idle";
});
const connectionTagType = computed(() => {
  if (connectionState.value === "live") {
    return "success";
  }
  if (connectionState.value === "error") {
    return "danger";
  }
  if (
    connectionState.value === "connecting" ||
    connectionState.value === "reconnecting" ||
    connectionState.value === "stale"
  ) {
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
let activeSubscription: { exchangeAccountId: number; symbol: string; interval: string } | null = null;

function currentSubscription() {
  return {
    exchangeAccountId: Number(props.exchangeAccountId || 0),
    symbol: displaySymbol.value,
    interval: selectedInterval.value
  };
}

function buildEventsWsUrl() {
  const url = new URL(API_BASE);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/ws/events";
  url.search = "";
  return url.toString();
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
    height: 360,
    layout: {
      background: { color: "#ffffff" },
      textColor: "#2e3f56"
    },
    grid: {
      vertLines: { color: "#edf3fb" },
      horzLines: { color: "#edf3fb" }
    },
    rightPriceScale: {
      borderColor: "#d7e3f2"
    },
    timeScale: {
      borderColor: "#d7e3f2",
      timeVisible: true,
      secondsVisible: false
    },
    crosshair: {
      vertLine: { color: "#7ca5d9" },
      horzLine: { color: "#7ca5d9" }
    }
  });
  series = chart.addCandlestickSeries({
    upColor: "#11a37f",
    downColor: "#d1485f",
    borderVisible: false,
    wickUpColor: "#11a37f",
    wickDownColor: "#d1485f"
  });
  resizeObserver = new ResizeObserver(() => {
    if (!chart || !chartRoot.value) {
      return;
    }
    chart.applyOptions({ width: chartRoot.value.clientWidth });
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

function sendSocketMessage(payload: Record<string, unknown>) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(JSON.stringify(payload));
}

function matchesStreamPayload(payload: Record<string, unknown>) {
  return (
    String(payload.exchange || "") === String((props.exchange || "").toLowerCase()) &&
    String(payload.symbol || "") === displaySymbol.value &&
    String(payload.interval || "") === selectedInterval.value
  );
}

function subscribeCurrentStream() {
  if (!isReady.value) {
    return;
  }
  const next = currentSubscription();
  if (
    activeSubscription &&
    (activeSubscription.exchangeAccountId !== next.exchangeAccountId ||
      activeSubscription.symbol !== next.symbol ||
      activeSubscription.interval !== next.interval)
  ) {
    sendSocketMessage({
      action: "unsubscribe_market",
      exchange_account_id: activeSubscription.exchangeAccountId,
      symbol: activeSubscription.symbol,
      interval: activeSubscription.interval
    });
  }
  activeSubscription = next;
  sendSocketMessage({
    action: "subscribe_market",
    exchange_account_id: next.exchangeAccountId,
    symbol: next.symbol,
    interval: next.interval
  });
}

function closeSocket() {
  clearTimers();
  if (!socket) {
    return;
  }
  try {
    if (socket.readyState === WebSocket.OPEN && activeSubscription) {
      sendSocketMessage({
        action: "unsubscribe_market",
        exchange_account_id: activeSubscription.exchangeAccountId,
        symbol: activeSubscription.symbol,
        interval: activeSubscription.interval
      });
    }
    socket.close();
  } catch {
    // Ignore best-effort unsubscribe path during teardown.
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
    errorMessage.value = String(payload.message || "Failed to subscribe to market candles.");
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
    const nextState = String(statusPayload.status || "connecting") as
      | "connecting"
      | "live"
      | "reconnecting"
      | "stale"
      | "error";
    connectionState.value = nextState;
    if (nextState === "error") {
      errorMessage.value = String(statusPayload.message || "Market stream error.");
    } else if (nextState === "live") {
      errorMessage.value = "";
    }
    return;
  }
  if (payload.type !== "market_candle") {
    return;
  }
  const candlePayload = payload.payload;
  if (!candlePayload || typeof candlePayload !== "object") {
    return;
  }
  if (!matchesStreamPayload(candlePayload)) {
    return;
  }
  const candle = candlePayload.candle;
  if (!candle || typeof candle !== "object" || !series) {
    return;
  }
  series.update({
    time: Number(candle.time) as UTCTimestamp,
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close)
  });
  lastPriceLabel.value = Number(candle.close).toFixed(4);
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
  socket = new WebSocket(buildEventsWsUrl());
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
    errorMessage.value = "";
    lastPriceLabel.value = "";
    activeSubscription = null;
    connectionState.value = "idle";
    if (series) {
      series.setData([]);
    }
    closeSocket();
    return;
  }

  ensureChart();
  errorMessage.value = "";
  loading.value = true;
  try {
    const response = await getMarketKlines(
      Number(props.exchangeAccountId),
      displaySymbol.value,
      selectedInterval.value,
      300
    );
    series?.setData(response.candles.map((item) => toChartPoint(item)));
    const latest = response.candles[response.candles.length - 1];
    lastPriceLabel.value = latest ? Number(latest.close).toFixed(4) : "";
    await nextTick();
    openSocket();
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || "Failed to load market candles.";
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.exchangeAccountId, props.exchange, props.symbol].join("|"),
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
  resizeObserver?.disconnect();
  resizeObserver = null;
  if (chart) {
    chart.remove();
    chart = null;
  }
  series = null;
});
</script>

<style scoped>
.chart-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chart-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.chart-toolbar h2 {
  margin: 0;
  color: var(--aq-ink-strong);
}

.chart-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.chart-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  color: var(--aq-ink-soft);
  font-size: 13px;
}

.chart-surface {
  min-height: 360px;
  border: 1px solid var(--aq-border);
  border-radius: 16px;
  background:
    linear-gradient(180deg, rgba(247, 251, 255, 0.92) 0%, rgba(255, 255, 255, 1) 100%);
  overflow: hidden;
}

.chart-root {
  width: 100%;
  height: 360px;
}

.chart-empty {
  min-height: 360px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  text-align: center;
  color: var(--aq-ink-soft);
}

@media (max-width: 760px) {
  .chart-toolbar {
    flex-direction: column;
  }
}
</style>

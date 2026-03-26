import { computed, ref } from "vue";

export type Locale = "en" | "zh";

const LOCALE_STORAGE_KEY = "a1phquest.locale";
export const LOCALE_CHANGE_EVENT = "aq:locale-change";

type MessageTree = {
  [key: string]: string | MessageTree;
};

type TranslateParams = Record<string, string | number | boolean>;

const enMessages: MessageTree = {
  locale: {
    en: "English",
    zh: "中文"
  },
  route: {
    market: "Market",
    auth: "Auth",
    enroll2fa: "Enroll 2FA",
    accounts: "Accounts",
    strategies: "Strategies",
    ai: "AI Autopilot",
    ops: "Ops",
    settings: "Settings"
  },
  shell: {
    brandSubtitle: "Autonomous quant terminal",
    sessionLimited: "Limited session",
    sessionSignedIn: "Signed in",
    publicMode: "Public mode",
    marketAccessEnabled: "Market access enabled",
    publicModeHint: "Sign in to store accounts and run strategies.",
    signInOrRegister: "Sign in or register",
    logout: "Logout",
    language: "Language",
    nav: {
      market: { label: "Market", hint: "public chart deck" },
      auth: { label: "Auth", hint: "sign in / register" },
      strategies: { label: "Strategies", hint: "templates + versions" },
      accounts: { label: "Accounts", hint: "exchange credentials" },
      ai: { label: "AI", hint: "autopilot control" },
      settings: { label: "Settings", hint: "market runtime" },
      ops: { label: "Ops", hint: "health + metrics" },
      bind2fa: { label: "Bind 2FA", hint: "required before app access" }
    }
  },
  common: {
    refresh: "Refresh",
    live: "Live",
    draft: "Draft",
    spot: "Spot",
    perp: "Perp"
  },
  workflow: {
    title: "Workflow Readiness",
    subtitle: "Track what is missing in the trading loop and jump straight to the next required action.",
    loadError: "Failed to load workflow readiness.",
    session: "Session",
    sessionSignedIn: "Signed In",
    sessionGuest: "Guest",
    enrollmentRequired: "2FA enrollment required",
    protectedUnlocked: "Protected routes unlocked",
    publicOnly: "Only public market routes are available",
    accounts: "Accounts",
    riskGate: "Risk Gate",
    riskReady: "Ready",
    riskBlocked: "Blocked",
    riskReadyCopy: "Live strategy starts are allowed.",
    riskBlockedCopy: "Live start stays fail-closed until risk rule setup.",
    ai: "AI",
    liveTemplates: "live templates"
  },
  chart: {
    defaultTitle: "Live Candles",
    defaultSubtitle: "Historical candles load first, then the current bar keeps streaming.",
    defaultEmpty: "Select a market scope to load candles.",
    last: "Last",
    waiting: "Waiting for market scope",
    wsLive: "WS live",
    connecting: "Connecting",
    reconnecting: "Reconnecting",
    stale: "Stream stale",
    socketError: "Socket error",
    idle: "Idle",
    subscribeError: "Failed to subscribe to market candles.",
    streamError: "Market stream error.",
    loadError: "Failed to load market candles."
  },
  market: {
    title: "Market Terminal",
    subtitle: "Public real-time market deck with low-latency candles, product switching, and direct strategy launch points.",
    loadingSymbol: "Loading symbol...",
    signInToTrade: "Sign in to trade",
    kicker: "Public Market Workspace",
    description:
      "Watch public candles before you wire any exchange account. Once you sign in, use the same market context to seed strategy drafts and live-ready grid, DCA, or Combo versions.",
    exchange: "Exchange",
    market: "Market",
    defaultMode: "Default Mode",
    publicFeed: "Public feed",
    chartTitle: "Public Candle Feed",
    chartSubtitle: "Historical backfill and live websocket updates run without requiring a saved strategy.",
    chartEmpty: "Pick an exchange and symbol to start streaming candles.",
    launchpadTitle: "Template Launchpad",
    launchpadSubtitle: "Turn the current market into a prefilled live-supported strategy: spot grid, DCA, combo, or futures grid.",
    scopeTitle: "Market Scope",
    scopeSubtitle: "Switch exchange, product type, and symbol before jumping into strategy creation.",
    labelExchange: "Exchange",
    labelMarketType: "Market Type",
    labelSymbol: "Symbol",
    fromMarketTitle: "From This Market",
    fromMarketSubtitle: "Jump into a prefilled template using the currently selected symbol.",
    createSpotGrid: "Create Spot Grid",
    createDca: "Create DCA",
    createCombo: "Create Combo Grid + DCA",
    createFuturesGrid: "Create Futures Grid"
  },
  auth: {
    titleEnroll: "Complete Google Authenticator Binding",
    titleSignIn: "Sign in or create a secured account",
    subtitleEnroll: "This account must bind TOTP before it can access strategies, accounts, AI, or settings.",
    subtitleSignIn:
      "Registration now completes only after the first Google Authenticator verification, so every active account is protected from day one.",
    kickerMandatory: "Mandatory 2FA",
    kickerUnified: "Unified Auth",
    headlineEnroll: "Bind, verify, then enter the terminal.",
    headlineSignIn: "A trading workspace should feel secure before it feels busy.",
    bodyEnroll:
      "A1phquest keeps you in a limited session until TOTP is verified. Public market access stays open, but the protected terminal stays locked.",
    bodySignIn:
      "Use the Market page before login, then register with username, email, password, scan the QR code, and verify the first one-time password in the same flow.",
    sessionModel: "Session Model",
    sessionModelValue: "HttpOnly cookie + CSRF",
    twoFactorMode: "2FA Mode",
    twoFactorModeValue: "Google Authenticator required",
    modeLogin: "Login",
    modeRegister: "Register",
    saveRecoveryCodes: "Save your recovery codes",
    recoveryCodesShownOnce: "These codes are shown only once and each works once.",
    enterMarketTerminal: "Enter market terminal",
    start2faBinding: "Start 2FA binding",
    start2faBindingSubtitle:
      "Generate a QR code, scan it in Google Authenticator, then verify the first OTP to unlock the rest of the app.",
    generateQr: "Generate QR",
    scanQr: "Scan this QR in Google Authenticator",
    manualKey: "Manual key",
    code6Digit: "6-digit code",
    placeholderCurrentOtp: "Enter current Google Authenticator code",
    verifyUnlock: "Verify and unlock",
    username: "Username",
    password: "Password",
    secondFactor: "Second factor",
    otp: "OTP",
    recovery: "Recovery",
    googleCode: "Google Authenticator Code",
    placeholderBoundOtp: "Required if the account is already bound",
    recoveryCode: "Recovery Code",
    enterTerminal: "Enter terminal",
    createAccount: "Create account",
    email: "Email",
    confirmPassword: "Confirm Password",
    continue2fa: "Continue to 2FA",
    bindGoogleAuth: "Bind Google Authenticator",
    scanBeforeExpire: "Scan before the token expires",
    expiresAt: "Expires at",
    placeholderFirstOtp: "Enter the first Google Authenticator code",
    verifyActivate: "Verify and activate",
    startOver: "Start over",
    recoveryCodesShownNow: "Each code works once. These are shown only this time.",
    msgEnrollmentRequired: "2FA enrollment is required before app access.",
    msgLoginSuccess: "Login successful.",
    msgLoginFailed: "Login failed",
    msgRequiredFields: "Username, email, and password are required.",
    msgPasswordMismatch: "Password confirmation does not match.",
    msgScanQr: "Scan the QR code and enter the first 6-digit code to activate the account.",
    msgRegistrationFailed: "Registration failed",
    msgActivated: "Account activated. Save the recovery codes before continuing.",
    msgVerify2faFailed: "2FA verification failed",
    msgQrGenerated: "QR code generated. Verify the first code to unlock the app.",
    msgStartEnrollmentFailed: "Failed to start 2FA enrollment",
    msgBindingComplete: "2FA binding complete. Save the recovery codes, then continue.",
    msgCompleteEnrollmentFailed: "Failed to complete 2FA enrollment"
  },
  accounts: {
    title: "Account Vault",
    subtitle:
      "Store exchange credentials behind a short-lived control token, monitor live versus testnet coverage, and keep validation or sync actions inside one secure terminal.",
    openMarket: "Open Market",
    refresh: "Refresh"
  },
  strategies: {
    title: "Strategy Library",
    subtitle:
      "Browse template families, build strategy drafts or live-ready versions, keep old revisions, and switch runtime from the same workspace.",
    refresh: "Refresh",
    openMarket: "Open Market",
    chartTitle: "Strategy Context Chart",
    chartSubtitle: "Use the selected instance or the editor context to preview market structure before saving or switching versions.",
    chartEmpty: "Pick a template and symbol to preview the market."
  },
  ai: {
    title: "AI Autopilot",
    subtitle:
      "Run model-backed regime decisions on top of the live market engine. Providers stay isolated, policies stay explicit, and every decision leaves an audit trail before execution touches runtime control.",
    openStrategies: "Open Strategies",
    refresh: "Refresh",
    riskBlocked:
      "Risk rule is not configured. Dry-run remains available, but live runtime actions from AI are blocked until risk setup is completed.",
    openRiskSettings: "Open Risk Settings"
  },
  settings: {
    title: "Runtime Settings",
    subtitle:
      "Tune low-latency market data behavior from the terminal itself. Database overrides hot-apply without editing deploy files, while reset returns the stack to its install-time defaults.",
    openOps: "Open Ops",
    refresh: "Refresh"
  },
  ops: {
    title: "Ops Monitor",
    subtitle:
      "Watch runtime health, websocket footprint, audit failures, and reconciliation pressure from one operator surface. Metrics refresh automatically so you can spot drift before it becomes execution risk.",
    runtimeSettings: "Runtime Settings",
    refresh: "Refresh"
  }
};

const zhOverrides: MessageTree = {
  locale: {
    en: "English",
    zh: "中文"
  },
  route: {
    market: "行情",
    auth: "认证",
    enroll2fa: "绑定 2FA",
    accounts: "账户",
    strategies: "策略",
    ai: "AI 自动驾驶",
    ops: "运维",
    settings: "设置"
  },
  shell: {
    brandSubtitle: "自主量化终端",
    sessionLimited: "受限会话",
    sessionSignedIn: "已登录",
    publicMode: "公开模式",
    marketAccessEnabled: "行情访问已启用",
    publicModeHint: "登录后可保存账户并运行策略。",
    signInOrRegister: "登录或注册",
    logout: "退出登录",
    language: "语言",
    nav: {
      market: { label: "行情", hint: "公开图表" },
      auth: { label: "认证", hint: "登录 / 注册" },
      strategies: { label: "策略", hint: "模板与版本" },
      accounts: { label: "账户", hint: "交易所凭据" },
      ai: { label: "AI", hint: "自动驾驶控制" },
      settings: { label: "设置", hint: "行情运行参数" },
      ops: { label: "运维", hint: "健康与指标" },
      bind2fa: { label: "绑定 2FA", hint: "进入应用前必须完成" }
    }
  },
  common: {
    refresh: "刷新",
    live: "实盘",
    draft: "草稿",
    spot: "现货",
    perp: "合约"
  },
  workflow: {
    title: "流程就绪度",
    subtitle: "查看交易闭环还缺什么，并快速跳转到下一步。",
    loadError: "加载流程就绪度失败。",
    session: "会话",
    sessionSignedIn: "已登录",
    sessionGuest: "访客",
    enrollmentRequired: "需要完成 2FA 绑定",
    protectedUnlocked: "受保护页面已解锁",
    publicOnly: "当前仅可访问公开行情页面",
    accounts: "账户",
    riskGate: "风控门禁",
    riskReady: "已就绪",
    riskBlocked: "已阻断",
    riskReadyCopy: "允许启动实盘策略。",
    riskBlockedCopy: "未配置风险规则前，实盘保持 fail-closed。",
    ai: "AI",
    liveTemplates: "可实盘模板"
  },
  chart: {
    defaultTitle: "实时 K 线",
    defaultSubtitle: "先加载历史，再持续接收当前 K 线增量。",
    defaultEmpty: "请选择市场范围后加载 K 线。",
    last: "最新",
    waiting: "等待市场范围",
    wsLive: "WS 实时",
    connecting: "连接中",
    reconnecting: "重连中",
    stale: "流已过期",
    socketError: "连接错误",
    idle: "空闲",
    subscribeError: "订阅行情 K 线失败。",
    streamError: "行情流异常。",
    loadError: "加载行情 K 线失败。"
  },
  market: {
    title: "行情终端",
    subtitle: "公开实时行情工作台，支持低延时 K 线、品种切换和一键创建策略入口。",
    loadingSymbol: "正在加载标的...",
    signInToTrade: "登录后可交易",
    kicker: "公开行情工作区",
    description:
      "无需先配置交易所账户即可查看公开 K 线。登录后可将当前市场上下文用于创建策略草稿或可实盘版本。",
    exchange: "交易所",
    market: "市场",
    defaultMode: "默认模式",
    publicFeed: "公开行情流",
    chartTitle: "公开 K 线流",
    chartSubtitle: "无需保存策略，也可先拉历史并接收 websocket 增量。",
    chartEmpty: "请选择交易所和标的后开始推送 K 线。",
    launchpadTitle: "模板启动台",
    launchpadSubtitle: "基于当前市场一键创建可执行策略：现货网格、DCA、组合、合约网格。",
    scopeTitle: "市场范围",
    scopeSubtitle: "创建策略前先切换交易所、市场类型和标的。",
    labelExchange: "交易所",
    labelMarketType: "市场类型",
    labelSymbol: "标的",
    fromMarketTitle: "基于当前市场",
    fromMarketSubtitle: "使用当前选中的标的快速进入预填模板。",
    createSpotGrid: "创建现货网格",
    createDca: "创建 DCA",
    createCombo: "创建网格 + DCA 组合",
    createFuturesGrid: "创建合约网格"
  },
  auth: {
    titleEnroll: "完成 Google Authenticator 绑定",
    titleSignIn: "登录或创建安全账户",
    subtitleEnroll: "完成 TOTP 绑定后，才能访问策略、账户、AI 和设置。",
    subtitleSignIn: "注册需完成首次 Google Authenticator 验证后才会激活账户。",
    kickerMandatory: "强制 2FA",
    kickerUnified: "统一认证",
    headlineEnroll: "先绑定并验证，再进入终端。",
    headlineSignIn: "交易工作区应先安全，再高效。",
    bodyEnroll: "完成 TOTP 验证前会保持受限会话。公开行情可访问，但受保护页面仍锁定。",
    bodySignIn: "可先看行情，再完成用户名、邮箱、密码、扫码和首次 OTP 验证。",
    sessionModel: "会话模型",
    sessionModelValue: "HttpOnly Cookie + CSRF",
    twoFactorMode: "2FA 模式",
    twoFactorModeValue: "Google Authenticator 必需",
    modeLogin: "登录",
    modeRegister: "注册",
    saveRecoveryCodes: "保存恢复码",
    recoveryCodesShownOnce: "恢复码仅展示一次，每个码只能使用一次。",
    enterMarketTerminal: "进入行情终端",
    start2faBinding: "开始绑定 2FA",
    start2faBindingSubtitle: "生成二维码并在 Google Authenticator 扫码，再输入首个 OTP 完成绑定。",
    generateQr: "生成二维码",
    scanQr: "请在 Google Authenticator 中扫描二维码",
    manualKey: "手动密钥",
    code6Digit: "6 位验证码",
    placeholderCurrentOtp: "输入当前 Google 验证码",
    verifyUnlock: "验证并解锁",
    username: "用户名",
    password: "密码",
    secondFactor: "第二因子",
    otp: "OTP",
    recovery: "恢复码",
    googleCode: "Google 验证码",
    placeholderBoundOtp: "若账户已绑定，请输入验证码",
    recoveryCode: "恢复码",
    enterTerminal: "进入终端",
    createAccount: "创建账户",
    email: "邮箱",
    confirmPassword: "确认密码",
    continue2fa: "继续 2FA",
    bindGoogleAuth: "绑定 Google Authenticator",
    scanBeforeExpire: "请在令牌过期前完成扫码",
    expiresAt: "过期时间",
    placeholderFirstOtp: "输入首个 Google 验证码",
    verifyActivate: "验证并激活",
    startOver: "重新开始",
    recoveryCodesShownNow: "每个恢复码仅可使用一次，且只展示本次。",
    msgEnrollmentRequired: "完成 2FA 绑定后才能访问受保护页面。",
    msgLoginSuccess: "登录成功。",
    msgLoginFailed: "登录失败",
    msgRequiredFields: "用户名、邮箱和密码为必填项。",
    msgPasswordMismatch: "两次输入的密码不一致。",
    msgScanQr: "请先扫描二维码并输入首个 6 位验证码激活账户。",
    msgRegistrationFailed: "注册失败",
    msgActivated: "账户已激活，请先保存恢复码。",
    msgVerify2faFailed: "2FA 验证失败",
    msgQrGenerated: "二维码已生成，请验证首个验证码完成绑定。",
    msgStartEnrollmentFailed: "启动 2FA 绑定失败",
    msgBindingComplete: "2FA 绑定完成，请先保存恢复码。",
    msgCompleteEnrollmentFailed: "完成 2FA 绑定失败"
  },
  accounts: {
    title: "账户保险库",
    subtitle: "在短时控制令牌保护下存储交易所凭据，查看实盘/测试网覆盖，并在同一终端完成校验与同步。",
    openMarket: "打开行情",
    refresh: "刷新"
  },
  strategies: {
    title: "策略库",
    subtitle: "浏览模板族、创建草稿或实盘版本、保留旧版本，并在同一工作区切换运行状态。",
    refresh: "刷新",
    openMarket: "打开行情",
    chartTitle: "策略上下文图表",
    chartSubtitle: "使用所选实例或编辑器上下文预览市场结构，再保存或切换版本。",
    chartEmpty: "请选择模板和标的以预览行情。"
  },
  ai: {
    title: "AI 自动驾驶",
    subtitle: "在实时行情引擎上运行模型决策。Provider 隔离、Policy 明确、每次决策都有审计轨迹。",
    openStrategies: "打开策略",
    refresh: "刷新",
    riskBlocked: "尚未配置风险规则。AI 仍可 dry-run，但实盘自动执行会被阻断。",
    openRiskSettings: "打开风控设置"
  },
  settings: {
    title: "运行时设置",
    subtitle: "在终端内调整低延时行情参数。数据库覆盖可热更新，无需手改部署文件。",
    openOps: "打开运维",
    refresh: "刷新"
  },
  ops: {
    title: "运维监控",
    subtitle: "集中查看运行健康、WebSocket 负载、审计失败与对账压力，提前发现执行风险。",
    runtimeSettings: "运行时设置",
    refresh: "刷新"
  }
};

const messages: Record<Locale, MessageTree> = {
  en: enMessages,
  zh: mergeMessages(enMessages, zhOverrides)
};

const locale = ref<Locale>(detectInitialLocale());

function detectInitialLocale(): Locale {
  if (typeof window === "undefined") {
    return "en";
  }
  const saved = String(window.localStorage.getItem(LOCALE_STORAGE_KEY) || "").toLowerCase();
  if (saved === "en" || saved === "zh") {
    return saved;
  }
  const browser = String(window.navigator.language || "").toLowerCase();
  return browser.startsWith("zh") ? "zh" : "en";
}

function mergeMessages(base: MessageTree, overrides: MessageTree): MessageTree {
  const merged: MessageTree = { ...base };
  for (const [key, value] of Object.entries(overrides)) {
    const baseValue = merged[key];
    if (typeof value === "string") {
      merged[key] = value;
      continue;
    }
    if (!baseValue || typeof baseValue === "string") {
      merged[key] = mergeMessages({}, value);
      continue;
    }
    merged[key] = mergeMessages(baseValue, value);
  }
  return merged;
}

function getValueByPath(table: MessageTree, key: string): string | undefined {
  const path = String(key || "").split(".").filter(Boolean);
  let cursor: string | MessageTree | undefined = table;
  for (const segment of path) {
    if (!cursor || typeof cursor === "string") {
      return undefined;
    }
    cursor = cursor[segment];
  }
  return typeof cursor === "string" ? cursor : undefined;
}

function interpolate(template: string, params?: TranslateParams): string {
  if (!params) {
    return template;
  }
  return template.replace(/\{(\w+)\}/g, (_, token: string) => {
    const value = params[token];
    return value === undefined || value === null ? "" : String(value);
  });
}

export function tGlobal(key: string, params?: TranslateParams): string {
  const current = locale.value;
  const localized = getValueByPath(messages[current], key);
  const fallback = getValueByPath(messages.en, key);
  const template = localized || fallback || key;
  return interpolate(template, params);
}

export function setLocale(next: Locale): void {
  if (next !== "en" && next !== "zh") {
    return;
  }
  if (locale.value === next) {
    return;
  }
  locale.value = next;
  if (typeof window !== "undefined") {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, next);
    document.documentElement.lang = next === "zh" ? "zh-CN" : "en";
    window.dispatchEvent(new CustomEvent(LOCALE_CHANGE_EVENT, { detail: { locale: next } }));
  }
}

export function useI18n() {
  const localeOptions = computed(() => [
    { label: tGlobal("locale.en"), value: "en" },
    { label: tGlobal("locale.zh"), value: "zh" }
  ]);
  return {
    locale,
    localeOptions,
    setLocale,
    t: tGlobal
  };
}

export function initI18n(): void {
  if (typeof window === "undefined") {
    return;
  }
  document.documentElement.lang = locale.value === "zh" ? "zh-CN" : "en";
}

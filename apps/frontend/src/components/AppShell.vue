<template>
  <div class="aq-app-shell" :class="{ 'is-public-shell': publicMode }">
    <aside class="aq-sidebar">
      <router-link class="aq-brand-block" to="/market">
        <span class="aq-brand-mark">A1</span>
        <div>
          <div class="aq-brand-name">A1phquest</div>
          <div class="aq-brand-subtitle">{{ t("shell.brandSubtitle") }}</div>
        </div>
      </router-link>

      <nav class="aq-nav">
        <router-link v-for="item in navItems" :key="item.to" :to="item.to" class="aq-nav-link">
          <span>{{ item.label }}</span>
          <small>{{ item.hint }}</small>
        </router-link>
      </nav>

      <div class="aq-sidebar-foot">
        <template v-if="session">
          <div class="aq-session-card">
            <span class="aq-session-label">{{ session.enrollment_required ? t("shell.sessionLimited") : t("shell.sessionSignedIn") }}</span>
            <strong>{{ session.user.username }}</strong>
            <small>{{ session.user.email }}</small>
          </div>
          <el-button class="aq-side-button" @click="logoutAndRedirect">{{ t("shell.logout") }}</el-button>
        </template>
        <template v-else>
          <div class="aq-session-card">
            <span class="aq-session-label">{{ t("shell.publicMode") }}</span>
            <strong>{{ t("shell.marketAccessEnabled") }}</strong>
            <small>{{ t("shell.publicModeHint") }}</small>
          </div>
          <router-link class="aq-auth-link" to="/auth">{{ t("shell.signInOrRegister") }}</router-link>
        </template>
      </div>
    </aside>

    <div class="aq-workspace">
      <header class="aq-workspace-header">
        <div class="aq-heading">
          <slot name="heading">
            <h1>{{ title }}</h1>
            <p v-if="subtitle">{{ subtitle }}</p>
          </slot>
        </div>
        <div class="aq-header-tools">
          <slot name="toolbar" />
          <el-segmented
            v-model="localeModel"
            class="aq-locale-switch"
            size="small"
            :options="localeOptions"
            :aria-label="t('shell.language')"
          />
        </div>
      </header>

      <main class="aq-workspace-main">
        <slot />
      </main>
    </div>

    <aside v-if="$slots.inspector" class="aq-inspector">
      <slot name="inspector" />
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { logout, useSessionState } from "../api";
import { useI18n, type Locale } from "../i18n";

const props = withDefaults(defineProps<{
  title?: string;
  subtitle?: string;
  publicMode?: boolean;
}>(), {
  title: "A1phquest",
  subtitle: "",
  publicMode: false
});

const route = useRoute();
const router = useRouter();
const sessionRef = useSessionState();
const session = computed(() => sessionRef.value);
const { locale, localeOptions, setLocale, t } = useI18n();
const localeModel = computed<Locale>({
  get: () => locale.value,
  set: (value) => {
    setLocale(value);
  }
});

const navItems = computed(() => {
  const items = [
    { to: "/market", label: t("shell.nav.market.label"), hint: t("shell.nav.market.hint") }
  ];
  if (!session.value) {
    items.push({ to: "/auth", label: t("shell.nav.auth.label"), hint: t("shell.nav.auth.hint") });
    return items;
  }
  items.push(
    { to: "/strategies", label: t("shell.nav.strategies.label"), hint: t("shell.nav.strategies.hint") },
    { to: "/accounts", label: t("shell.nav.accounts.label"), hint: t("shell.nav.accounts.hint") },
    { to: "/ai", label: t("shell.nav.ai.label"), hint: t("shell.nav.ai.hint") },
    { to: "/settings", label: t("shell.nav.settings.label"), hint: t("shell.nav.settings.hint") },
    { to: "/ops", label: t("shell.nav.ops.label"), hint: t("shell.nav.ops.hint") }
  );
  if (session.value.enrollment_required) {
    return [
      { to: "/market", label: t("shell.nav.market.label"), hint: t("shell.nav.market.hint") },
      { to: "/auth/enroll-2fa", label: t("shell.nav.bind2fa.label"), hint: t("shell.nav.bind2fa.hint") }
    ];
  }
  return items;
});

async function logoutAndRedirect() {
  await logout();
  if (route.path !== "/market") {
    router.push("/market");
  }
}
</script>

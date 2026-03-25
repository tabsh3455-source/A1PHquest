<template>
  <div class="aq-app-shell" :class="{ 'is-public-shell': publicMode }">
    <aside class="aq-sidebar">
      <router-link class="aq-brand-block" to="/market">
        <span class="aq-brand-mark">A1</span>
        <div>
          <div class="aq-brand-name">A1phquest</div>
          <div class="aq-brand-subtitle">Autonomous quant terminal</div>
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
            <span class="aq-session-label">{{ session.enrollment_required ? "Limited session" : "Signed in" }}</span>
            <strong>{{ session.user.username }}</strong>
            <small>{{ session.user.email }}</small>
          </div>
          <el-button class="aq-side-button" @click="logoutAndRedirect">Logout</el-button>
        </template>
        <template v-else>
          <div class="aq-session-card">
            <span class="aq-session-label">Public mode</span>
            <strong>Market access enabled</strong>
            <small>Sign in to store accounts and run strategies.</small>
          </div>
          <router-link class="aq-auth-link" to="/auth">Sign in or register</router-link>
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

const navItems = computed(() => {
  const items = [
    { to: "/market", label: "Market", hint: "public chart deck" }
  ];
  if (!session.value) {
    items.push({ to: "/auth", label: "Auth", hint: "sign in / register" });
    return items;
  }
  items.push(
    { to: "/strategies", label: "Strategies", hint: "templates + versions" },
    { to: "/accounts", label: "Accounts", hint: "exchange credentials" },
    { to: "/ai", label: "AI", hint: "autopilot control" },
    { to: "/settings", label: "Settings", hint: "market runtime" },
    { to: "/ops", label: "Ops", hint: "health + metrics" }
  );
  if (session.value.enrollment_required) {
    return [
      { to: "/market", label: "Market", hint: "public chart deck" },
      { to: "/auth/enroll-2fa", label: "Bind 2FA", hint: "required before app access" }
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

import { createRouter, createWebHistory } from "vue-router";
import { loadSession } from "./api";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/login" },
    { path: "/login", component: () => import("./views/LoginView.vue"), meta: { title: "Login" } },
    { path: "/dashboard", component: () => import("./views/DashboardView.vue"), meta: { title: "Dashboard" } },
    { path: "/accounts", component: () => import("./views/AccountsView.vue"), meta: { title: "Accounts" } },
    { path: "/strategies", component: () => import("./views/StrategiesView.vue"), meta: { title: "Strategies" } },
    { path: "/ai", component: () => import("./views/AiView.vue"), meta: { title: "AI Autopilot" } },
    { path: "/ops", component: () => import("./views/OpsView.vue"), meta: { title: "Ops" } },
    { path: "/settings", component: () => import("./views/SettingsView.vue"), meta: { title: "Settings" } }
  ]
});

router.beforeEach(async (to) => {
  const session = await loadSession().catch(() => null);
  if (to.path === "/login") {
    return session ? "/dashboard" : true;
  }
  if (!session) {
    return "/login";
  }
  return true;
});

router.afterEach((to) => {
  const pageTitle = String(to.meta?.title || "Dashboard");
  document.title = `${pageTitle} | A1phquest`;
});

export default router;

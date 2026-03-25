import { createRouter, createWebHistory } from "vue-router";
import { loadSession } from "./api";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/market" },
    { path: "/login", redirect: "/auth" },
    { path: "/dashboard", redirect: "/market" },
    { path: "/market", component: () => import("./views/MarketView.vue"), meta: { title: "Market", public: true } },
    { path: "/auth", component: () => import("./views/LoginView.vue"), meta: { title: "Auth", public: true } },
    {
      path: "/auth/enroll-2fa",
      component: () => import("./views/LoginView.vue"),
      meta: { title: "Enroll 2FA", public: true, enrollmentOnly: true }
    },
    { path: "/accounts", component: () => import("./views/AccountsView.vue"), meta: { title: "Accounts" } },
    { path: "/strategies", component: () => import("./views/StrategiesView.vue"), meta: { title: "Strategies" } },
    { path: "/ai", component: () => import("./views/AiView.vue"), meta: { title: "AI Autopilot" } },
    { path: "/ops", component: () => import("./views/OpsView.vue"), meta: { title: "Ops" } },
    { path: "/settings", component: () => import("./views/SettingsView.vue"), meta: { title: "Settings" } }
  ]
});

router.beforeEach(async (to) => {
  const session = await loadSession().catch(() => null);
  const isPublic = Boolean(to.meta?.public);
  const enrollmentOnly = Boolean(to.meta?.enrollmentOnly);

  if (!session) {
    if (isPublic) {
      return true;
    }
    return "/auth";
  }

  if (session.enrollment_required) {
    if (to.path === "/auth" || enrollmentOnly || to.path === "/market") {
      return true;
    }
    return "/auth/enroll-2fa";
  }

  if (to.path === "/auth" || enrollmentOnly) {
    return "/market";
  }

  return true;
});

router.afterEach((to) => {
  const pageTitle = String(to.meta?.title || "Market");
  document.title = `${pageTitle} | A1phquest`;
});

export default router;

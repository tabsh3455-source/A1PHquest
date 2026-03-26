import { createRouter, createWebHistory } from "vue-router";
import { loadSession } from "./api";
import { LOCALE_CHANGE_EVENT, tGlobal } from "./i18n";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/market" },
    { path: "/login", redirect: "/auth" },
    { path: "/dashboard", redirect: "/market" },
    { path: "/market", component: () => import("./views/MarketView.vue"), meta: { titleKey: "route.market", public: true } },
    { path: "/auth", component: () => import("./views/LoginView.vue"), meta: { titleKey: "route.auth", public: true } },
    {
      path: "/auth/enroll-2fa",
      component: () => import("./views/LoginView.vue"),
      meta: { titleKey: "route.enroll2fa", public: true, enrollmentOnly: true }
    },
    { path: "/accounts", component: () => import("./views/AccountsView.vue"), meta: { titleKey: "route.accounts" } },
    { path: "/strategies", component: () => import("./views/StrategiesView.vue"), meta: { titleKey: "route.strategies" } },
    { path: "/ai", component: () => import("./views/AiView.vue"), meta: { titleKey: "route.ai" } },
    { path: "/ops", component: () => import("./views/OpsView.vue"), meta: { titleKey: "route.ops" } },
    { path: "/settings", component: () => import("./views/SettingsView.vue"), meta: { titleKey: "route.settings" } }
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
  const pageTitle = tGlobal(String(to.meta?.titleKey || "route.market"));
  document.title = `${pageTitle} | A1phquest`;
});

if (typeof window !== "undefined") {
  window.addEventListener(LOCALE_CHANGE_EVENT, () => {
    const current = router.currentRoute.value;
    const pageTitle = tGlobal(String(current.meta?.titleKey || "route.market"));
    document.title = `${pageTitle} | A1phquest`;
  });
}

export default router;

<template>
  <AppShell>
    <div class="login-wrap aq-fade-up">
      <section class="hero-copy">
        <h1>A1phquest</h1>
        <p>
          Quant execution workspace for Binance, OKX, and Lighter.
          Sign in with your password and current Google Authenticator code when 2FA is enabled.
        </p>
        <div class="aq-grid aq-grid-2">
          <div class="aq-soft-block">
            <span class="aq-kv-label">Security</span>
            <div class="hero-stat">HttpOnly session cookie + Step-up</div>
          </div>
          <div class="aq-soft-block">
            <span class="aq-kv-label">Runtime</span>
            <div class="hero-stat">Supervisor + WS event bus</div>
          </div>
        </div>
      </section>

      <section class="aq-panel login-panel">
        <h2>Login</h2>
        <p class="aq-subtitle">
          Enter your account password. If 2FA is enabled, include the current 6-digit OTP in the same form.
        </p>
        <el-form label-position="top">
          <el-form-item label="Username">
            <el-input v-model="username" />
          </el-form-item>
          <el-form-item label="Password">
            <el-input v-model="password" type="password" show-password />
          </el-form-item>
          <el-form-item label="OTP Code (Optional)">
            <el-input
              v-model="otpCode"
              maxlength="6"
              placeholder="Required only when Google Authenticator is enabled"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="onLogin">Enter Workspace</el-button>
          </el-form-item>
        </el-form>
        <el-alert v-if="message" :title="message" :type="messageType" show-icon />
      </section>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import AppShell from "../components/AppShell.vue";
import { login } from "../api";

const router = useRouter();
const username = ref("admin");
const password = ref("change-me");
const otpCode = ref("");
const loading = ref(false);
const message = ref("");
const messageType = ref<"success" | "error">("success");

async function onLogin() {
  loading.value = true;
  try {
    await login(username.value, password.value, otpCode.value.trim() || undefined);
    message.value = "Login successful.";
    messageType.value = "success";
    router.push("/dashboard");
  } catch (error: any) {
    message.value = error?.response?.data?.detail || "Login failed";
    messageType.value = "error";
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-wrap {
  display: grid;
  grid-template-columns: minmax(260px, 1.1fr) minmax(260px, 0.9fr);
  gap: 16px;
}

.hero-copy {
  padding: 8px 6px;
}

.hero-copy h1 {
  margin: 4px 0 8px;
  font-size: clamp(30px, 4.2vw, 48px);
  color: var(--aq-ink-strong);
  letter-spacing: -0.02em;
}

.hero-copy p {
  margin: 0 0 14px;
  line-height: 1.65;
  color: var(--aq-ink-soft);
  max-width: 44ch;
}

.hero-stat {
  margin-top: 8px;
  font-size: 16px;
  font-weight: 700;
  color: var(--aq-brand-ink);
}

.login-panel h2 {
  margin: 0;
  color: var(--aq-ink-strong);
}

@media (max-width: 940px) {
  .login-wrap {
    grid-template-columns: 1fr;
  }
}
</style>

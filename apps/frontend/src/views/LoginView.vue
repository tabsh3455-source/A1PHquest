<template>
  <AppShell
    :title="isEnrollmentRoute ? t('auth.titleEnroll') : t('auth.titleSignIn')"
    :subtitle="isEnrollmentRoute
      ? t('auth.subtitleEnroll')
      : t('auth.subtitleSignIn')"
    :public-mode="true"
  >
    <div class="auth-layout aq-fade-up">
      <section class="auth-poster aq-panel">
        <span class="auth-kicker">{{ isEnrollmentRoute ? t("auth.kickerMandatory") : t("auth.kickerUnified") }}</span>
        <h2>{{ isEnrollmentRoute ? t("auth.headlineEnroll") : t("auth.headlineSignIn") }}</h2>
        <p>
          {{ isEnrollmentRoute
            ? t("auth.bodyEnroll")
            : t("auth.bodySignIn") }}
        </p>
        <div class="aq-grid aq-grid-2">
          <div class="aq-soft-block">
            <span class="aq-kv-label">{{ t("auth.sessionModel") }}</span>
            <div class="aq-kv-value">{{ t("auth.sessionModelValue") }}</div>
          </div>
          <div class="aq-soft-block">
            <span class="aq-kv-label">{{ t("auth.twoFactorMode") }}</span>
            <div class="aq-kv-value">{{ t("auth.twoFactorModeValue") }}</div>
          </div>
        </div>
      </section>

      <section class="auth-card aq-panel">
        <template v-if="!isEnrollmentRoute">
          <el-segmented v-model="mode" :options="modeOptions" class="auth-mode-toggle" />
        </template>

        <div v-if="message" class="auth-message">
          <el-alert :title="message" :type="messageType" show-icon />
        </div>

        <template v-if="isEnrollmentRoute">
          <div v-if="enrollmentRecoveryCodes.length" class="aq-stack">
            <h3>{{ t("auth.saveRecoveryCodes") }}</h3>
            <p class="aq-subtitle">{{ t("auth.recoveryCodesShownOnce") }}</p>
            <div class="recovery-grid">
              <span v-for="item in enrollmentRecoveryCodes" :key="item" class="recovery-chip">{{ item }}</span>
            </div>
            <el-button type="primary" @click="enterWorkspace">{{ t("auth.enterMarketTerminal") }}</el-button>
          </div>

          <div v-else-if="!enrollmentDraft" class="aq-stack">
            <h3>{{ t("auth.start2faBinding") }}</h3>
            <p class="aq-subtitle">{{ t("auth.start2faBindingSubtitle") }}</p>
            <el-button type="primary" :loading="loading" @click="startEnrollment">{{ t("auth.generateQr") }}</el-button>
          </div>

          <div v-else class="aq-stack">
            <div class="qr-panel">
              <img :src="enrollmentDraft.qr_svg_data_url" alt="Authenticator QR" class="qr-image" />
              <div class="aq-stack">
                <strong>{{ t("auth.scanQr") }}</strong>
                <small>{{ t("auth.manualKey") }}: {{ enrollmentDraft.otp_secret }}</small>
              </div>
            </div>
            <el-form label-position="top">
              <el-form-item :label="t('auth.code6Digit')">
                <el-input v-model="enrollmentCode" maxlength="6" :placeholder="t('auth.placeholderCurrentOtp')" />
              </el-form-item>
            </el-form>
            <el-button type="primary" :loading="loading" @click="completeEnrollment">{{ t("auth.verifyUnlock") }}</el-button>
          </div>
        </template>

        <template v-else-if="mode === 'login'">
          <el-form label-position="top">
            <el-form-item :label="t('auth.username')">
              <el-input v-model="username" autocomplete="username" />
            </el-form-item>
            <el-form-item :label="t('auth.password')">
              <el-input v-model="password" type="password" show-password autocomplete="current-password" />
            </el-form-item>
            <el-form-item :label="t('auth.secondFactor')">
              <el-segmented v-model="loginFactorMode" :options="loginFactorOptions" class="factor-toggle" />
            </el-form-item>
            <el-form-item v-if="loginFactorMode === 'otp'" :label="t('auth.googleCode')">
              <el-input v-model="otpCode" maxlength="6" :placeholder="t('auth.placeholderBoundOtp')" />
            </el-form-item>
            <el-form-item v-else :label="t('auth.recoveryCode')">
              <el-input v-model="recoveryCode" :placeholder="t('auth.recoveryCode')" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="loading" @click="onLogin">{{ t("auth.enterTerminal") }}</el-button>
            </el-form-item>
          </el-form>
        </template>

        <template v-else>
          <div v-if="registerStep === 'credentials'" class="aq-stack">
            <h3>{{ t("auth.createAccount") }}</h3>
            <el-form label-position="top">
              <el-form-item :label="t('auth.username')">
                <el-input v-model="registerUsername" autocomplete="username" />
              </el-form-item>
              <el-form-item :label="t('auth.email')">
                <el-input v-model="registerEmail" autocomplete="email" />
              </el-form-item>
              <el-form-item :label="t('auth.password')">
                <el-input v-model="registerPassword" type="password" show-password autocomplete="new-password" />
              </el-form-item>
              <el-form-item :label="t('auth.confirmPassword')">
                <el-input v-model="registerPasswordConfirm" type="password" show-password autocomplete="new-password" />
              </el-form-item>
            </el-form>
            <el-button type="primary" :loading="loading" @click="beginRegistration">{{ t("auth.continue2fa") }}</el-button>
          </div>

          <div v-else-if="registerStep === 'verify' && registrationDraft" class="aq-stack">
            <h3>{{ t("auth.bindGoogleAuth") }}</h3>
            <div class="qr-panel">
              <img :src="registrationDraft.qr_svg_data_url" alt="Authenticator QR" class="qr-image" />
              <div class="aq-stack">
                <strong>{{ t("auth.scanBeforeExpire") }}</strong>
                <small>{{ t("auth.manualKey") }}: {{ registrationDraft.otp_secret }}</small>
                <small>{{ t("auth.expiresAt") }}: {{ registrationDraft.expires_at }}</small>
              </div>
            </div>
            <el-form label-position="top">
              <el-form-item :label="t('auth.code6Digit')">
                <el-input v-model="registerOtpCode" maxlength="6" :placeholder="t('auth.placeholderFirstOtp')" />
              </el-form-item>
            </el-form>
            <el-space wrap>
              <el-button type="primary" :loading="loading" @click="finishRegistration">{{ t("auth.verifyActivate") }}</el-button>
              <el-button @click="resetRegistrationFlow">{{ t("auth.startOver") }}</el-button>
            </el-space>
          </div>

          <div v-else class="aq-stack">
            <h3>{{ t("auth.saveRecoveryCodes") }}</h3>
            <p class="aq-subtitle">{{ t("auth.recoveryCodesShownNow") }}</p>
            <div class="recovery-grid">
              <span v-for="item in recoveryCodes" :key="item" class="recovery-chip">{{ item }}</span>
            </div>
            <el-button type="primary" @click="enterWorkspace">{{ t("auth.enterMarketTerminal") }}</el-button>
          </div>
        </template>
      </section>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppShell from "../components/AppShell.vue";
import { useI18n } from "../i18n";
import {
  completeRegistration,
  completeTwoFactorEnrollment,
  getErrorMessage,
  loadSession,
  login,
  loginWithRecoveryCode,
  startRegistration,
  startTwoFactorEnrollment
} from "../api";

const route = useRoute();
const router = useRouter();
const { t } = useI18n();

const isEnrollmentRoute = computed(() => route.path === "/auth/enroll-2fa");
const mode = ref<"login" | "register">("login");
const modeOptions = computed(() => [
  { label: t("auth.modeLogin"), value: "login" },
  { label: t("auth.modeRegister"), value: "register" }
]);
const loginFactorMode = ref<"otp" | "recovery">("otp");
const loginFactorOptions = computed(() => [
  { label: t("auth.otp"), value: "otp" },
  { label: t("auth.recovery"), value: "recovery" }
]);

const username = ref("");
const password = ref("");
const otpCode = ref("");
const recoveryCode = ref("");

const registerUsername = ref("");
const registerEmail = ref("");
const registerPassword = ref("");
const registerPasswordConfirm = ref("");
const registerOtpCode = ref("");
const registerStep = ref<"credentials" | "verify" | "done">("credentials");
const registrationDraft = ref<null | {
  registration_token: string;
  otp_secret: string;
  otpauth_uri: string;
  qr_svg_data_url: string;
  expires_at: string;
}>(null);

const enrollmentDraft = ref<null | {
  otp_secret: string;
  otpauth_uri: string;
  qr_svg_data_url: string;
}>(null);
const enrollmentCode = ref("");
const enrollmentRecoveryCodes = ref<string[]>([]);

const recoveryCodes = ref<string[]>([]);
const loading = ref(false);
const message = ref("");
const messageType = ref<"success" | "error" | "warning" | "info">("info");

function setMessage(value: string, type: "success" | "error" | "warning" | "info" = "info") {
  message.value = value;
  messageType.value = type;
}

async function onLogin() {
  loading.value = true;
  try {
    const session = loginFactorMode.value === "recovery"
      ? await loginWithRecoveryCode(username.value.trim(), password.value, recoveryCode.value.trim())
      : await login(username.value.trim(), password.value, otpCode.value.trim() || undefined);
    setMessage(session.enrollment_required ? t("auth.msgEnrollmentRequired") : t("auth.msgLoginSuccess"), "success");
    router.push(session.enrollment_required ? "/auth/enroll-2fa" : "/market");
  } catch (error: any) {
    setMessage(getErrorMessage(error, t("auth.msgLoginFailed")), "error");
  } finally {
    loading.value = false;
  }
}

async function beginRegistration() {
  if (!registerUsername.value.trim() || !registerEmail.value.trim() || !registerPassword.value) {
    setMessage(t("auth.msgRequiredFields"), "warning");
    return;
  }
  if (registerPassword.value !== registerPasswordConfirm.value) {
    setMessage(t("auth.msgPasswordMismatch"), "warning");
    return;
  }
  loading.value = true;
  try {
    registrationDraft.value = await startRegistration(
      registerUsername.value.trim(),
      registerEmail.value.trim(),
      registerPassword.value
    );
    registerStep.value = "verify";
    setMessage(t("auth.msgScanQr"), "success");
  } catch (error: any) {
    setMessage(getErrorMessage(error, t("auth.msgRegistrationFailed")), "error");
  } finally {
    loading.value = false;
  }
}

async function finishRegistration() {
  if (!registrationDraft.value) {
    return;
  }
  loading.value = true;
  try {
    const flow = await completeRegistration(registrationDraft.value.registration_token, registerOtpCode.value.trim());
    recoveryCodes.value = flow.recovery_codes;
    registerStep.value = "done";
    setMessage(t("auth.msgActivated"), "success");
  } catch (error: any) {
    setMessage(getErrorMessage(error, t("auth.msgVerify2faFailed")), "error");
  } finally {
    loading.value = false;
  }
}

function resetRegistrationFlow() {
  registrationDraft.value = null;
  registerOtpCode.value = "";
  registerStep.value = "credentials";
}

async function startEnrollment() {
  loading.value = true;
  try {
    enrollmentDraft.value = await startTwoFactorEnrollment();
    setMessage(t("auth.msgQrGenerated"), "success");
  } catch (error: any) {
    setMessage(getErrorMessage(error, t("auth.msgStartEnrollmentFailed")), "error");
  } finally {
    loading.value = false;
  }
}

async function completeEnrollment() {
  loading.value = true;
  try {
    const flow = await completeTwoFactorEnrollment(enrollmentCode.value.trim());
    enrollmentRecoveryCodes.value = flow.recovery_codes;
    setMessage(t("auth.msgBindingComplete"), "success");
    await loadSession(true);
  } catch (error: any) {
    setMessage(getErrorMessage(error, t("auth.msgCompleteEnrollmentFailed")), "error");
  } finally {
    loading.value = false;
  }
}

function enterWorkspace() {
  router.push("/market");
}
</script>

<style scoped>
.auth-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(420px, 0.85fr);
  gap: 16px;
}

.auth-poster {
  display: grid;
  align-content: start;
  gap: 18px;
}

.auth-kicker {
  color: var(--aq-brand);
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.auth-poster h2 {
  margin: 0;
  color: var(--aq-ink-strong);
  font-size: clamp(30px, 4vw, 48px);
  letter-spacing: -0.04em;
}

.auth-poster p {
  margin: 0;
  max-width: 52ch;
  color: var(--aq-ink-soft);
  line-height: 1.75;
}

.auth-card {
  min-height: 520px;
}

.auth-mode-toggle,
.factor-toggle {
  margin-bottom: 14px;
}

.auth-message {
  margin-bottom: 14px;
}

.qr-panel {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  gap: 16px;
  align-items: center;
  padding: 14px;
  border-radius: 16px;
  border: 1px solid var(--aq-border);
  background: rgba(255, 255, 255, 0.03);
}

.qr-image {
  width: 180px;
  height: 180px;
  object-fit: contain;
  background: #ffffff;
  border-radius: 12px;
  padding: 10px;
}

.recovery-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.recovery-chip {
  display: inline-flex;
  justify-content: center;
  min-height: 42px;
  align-items: center;
  padding: 0 12px;
  border-radius: 12px;
  border: 1px solid var(--aq-border);
  background: rgba(255, 255, 255, 0.04);
  color: var(--aq-ink-strong);
  font-weight: 600;
}

@media (max-width: 960px) {
  .auth-layout {
    grid-template-columns: 1fr;
  }

  .qr-panel {
    grid-template-columns: 1fr;
  }
}
</style>

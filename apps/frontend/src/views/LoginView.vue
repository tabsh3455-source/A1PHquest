<template>
  <AppShell
    :title="isEnrollmentRoute ? 'Complete Google Authenticator Binding' : 'Sign in or create a secured account'"
    :subtitle="isEnrollmentRoute
      ? 'This account must bind TOTP before it can access strategies, accounts, AI, or settings.'
      : 'Registration now completes only after the first Google Authenticator verification, so every active account is protected from day one.'"
    :public-mode="true"
  >
    <div class="auth-layout aq-fade-up">
      <section class="auth-poster aq-panel">
        <span class="auth-kicker">{{ isEnrollmentRoute ? "Mandatory 2FA" : "Unified Auth" }}</span>
        <h2>{{ isEnrollmentRoute ? "Bind, verify, then enter the terminal." : "A trading workspace should feel secure before it feels busy." }}</h2>
        <p>
          {{ isEnrollmentRoute
            ? "A1phquest keeps you in a limited session until TOTP is verified. Public market access stays open, but the protected terminal stays locked."
            : "Use the Market page before login, then register with username, email, password, scan the QR code, and verify the first one-time password in the same flow." }}
        </p>
        <div class="aq-grid aq-grid-2">
          <div class="aq-soft-block">
            <span class="aq-kv-label">Session Model</span>
            <div class="aq-kv-value">HttpOnly cookie + CSRF</div>
          </div>
          <div class="aq-soft-block">
            <span class="aq-kv-label">2FA Mode</span>
            <div class="aq-kv-value">Google Authenticator required</div>
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
            <h3>Save your recovery codes</h3>
            <p class="aq-subtitle">These codes are shown only once and each works once.</p>
            <div class="recovery-grid">
              <span v-for="item in enrollmentRecoveryCodes" :key="item" class="recovery-chip">{{ item }}</span>
            </div>
            <el-button type="primary" @click="enterWorkspace">Enter market terminal</el-button>
          </div>

          <div v-else-if="!enrollmentDraft" class="aq-stack">
            <h3>Start 2FA binding</h3>
            <p class="aq-subtitle">Generate a QR code, scan it in Google Authenticator, then verify the first OTP to unlock the rest of the app.</p>
            <el-button type="primary" :loading="loading" @click="startEnrollment">Generate QR</el-button>
          </div>

          <div v-else class="aq-stack">
            <div class="qr-panel">
              <img :src="enrollmentDraft.qr_svg_data_url" alt="Authenticator QR" class="qr-image" />
              <div class="aq-stack">
                <strong>Scan this QR in Google Authenticator</strong>
                <small>Manual key: {{ enrollmentDraft.otp_secret }}</small>
              </div>
            </div>
            <el-form label-position="top">
              <el-form-item label="6-digit code">
                <el-input v-model="enrollmentCode" maxlength="6" placeholder="Enter current Google Authenticator code" />
              </el-form-item>
            </el-form>
            <el-button type="primary" :loading="loading" @click="completeEnrollment">Verify and unlock</el-button>
          </div>
        </template>

        <template v-else-if="mode === 'login'">
          <el-form label-position="top">
            <el-form-item label="Username">
              <el-input v-model="username" autocomplete="username" />
            </el-form-item>
            <el-form-item label="Password">
              <el-input v-model="password" type="password" show-password autocomplete="current-password" />
            </el-form-item>
            <el-form-item label="Second factor">
              <el-segmented v-model="loginFactorMode" :options="loginFactorOptions" class="factor-toggle" />
            </el-form-item>
            <el-form-item v-if="loginFactorMode === 'otp'" label="Google Authenticator Code">
              <el-input v-model="otpCode" maxlength="6" placeholder="Required if the account is already bound" />
            </el-form-item>
            <el-form-item v-else label="Recovery Code">
              <el-input v-model="recoveryCode" placeholder="AQ-XXXX-XXXX" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="loading" @click="onLogin">Enter terminal</el-button>
            </el-form-item>
          </el-form>
        </template>

        <template v-else>
          <div v-if="registerStep === 'credentials'" class="aq-stack">
            <h3>Create account</h3>
            <el-form label-position="top">
              <el-form-item label="Username">
                <el-input v-model="registerUsername" autocomplete="username" />
              </el-form-item>
              <el-form-item label="Email">
                <el-input v-model="registerEmail" autocomplete="email" />
              </el-form-item>
              <el-form-item label="Password">
                <el-input v-model="registerPassword" type="password" show-password autocomplete="new-password" />
              </el-form-item>
              <el-form-item label="Confirm Password">
                <el-input v-model="registerPasswordConfirm" type="password" show-password autocomplete="new-password" />
              </el-form-item>
            </el-form>
            <el-button type="primary" :loading="loading" @click="beginRegistration">Continue to 2FA</el-button>
          </div>

          <div v-else-if="registerStep === 'verify' && registrationDraft" class="aq-stack">
            <h3>Bind Google Authenticator</h3>
            <div class="qr-panel">
              <img :src="registrationDraft.qr_svg_data_url" alt="Authenticator QR" class="qr-image" />
              <div class="aq-stack">
                <strong>Scan before the token expires</strong>
                <small>Manual key: {{ registrationDraft.otp_secret }}</small>
                <small>Expires at: {{ registrationDraft.expires_at }}</small>
              </div>
            </div>
            <el-form label-position="top">
              <el-form-item label="6-digit code">
                <el-input v-model="registerOtpCode" maxlength="6" placeholder="Enter the first Google Authenticator code" />
              </el-form-item>
            </el-form>
            <el-space wrap>
              <el-button type="primary" :loading="loading" @click="finishRegistration">Verify and activate</el-button>
              <el-button @click="resetRegistrationFlow">Start over</el-button>
            </el-space>
          </div>

          <div v-else class="aq-stack">
            <h3>Save your recovery codes</h3>
            <p class="aq-subtitle">Each code works once. These are shown only this time.</p>
            <div class="recovery-grid">
              <span v-for="item in recoveryCodes" :key="item" class="recovery-chip">{{ item }}</span>
            </div>
            <el-button type="primary" @click="enterWorkspace">Enter market terminal</el-button>
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

const isEnrollmentRoute = computed(() => route.path === "/auth/enroll-2fa");
const mode = ref<"login" | "register">("login");
const modeOptions = [
  { label: "Login", value: "login" },
  { label: "Register", value: "register" }
] as const;
const loginFactorMode = ref<"otp" | "recovery">("otp");
const loginFactorOptions = [
  { label: "OTP", value: "otp" },
  { label: "Recovery", value: "recovery" }
] as const;

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
    setMessage(session.enrollment_required ? "2FA enrollment is required before app access." : "Login successful.", "success");
    router.push(session.enrollment_required ? "/auth/enroll-2fa" : "/market");
  } catch (error: any) {
    setMessage(getErrorMessage(error, "Login failed"), "error");
  } finally {
    loading.value = false;
  }
}

async function beginRegistration() {
  if (!registerUsername.value.trim() || !registerEmail.value.trim() || !registerPassword.value) {
    setMessage("Username, email, and password are required.", "warning");
    return;
  }
  if (registerPassword.value !== registerPasswordConfirm.value) {
    setMessage("Password confirmation does not match.", "warning");
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
    setMessage("Scan the QR code and enter the first 6-digit code to activate the account.", "success");
  } catch (error: any) {
    setMessage(getErrorMessage(error, "Registration failed"), "error");
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
    setMessage("Account activated. Save the recovery codes before continuing.", "success");
  } catch (error: any) {
    setMessage(getErrorMessage(error, "2FA verification failed"), "error");
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
    setMessage("QR code generated. Verify the first code to unlock the app.", "success");
  } catch (error: any) {
    setMessage(getErrorMessage(error, "Failed to start 2FA enrollment"), "error");
  } finally {
    loading.value = false;
  }
}

async function completeEnrollment() {
  loading.value = true;
  try {
    const flow = await completeTwoFactorEnrollment(enrollmentCode.value.trim());
    enrollmentRecoveryCodes.value = flow.recovery_codes;
    setMessage("2FA binding complete. Save the recovery codes, then continue.", "success");
    await loadSession(true);
  } catch (error: any) {
    setMessage(getErrorMessage(error, "Failed to complete 2FA enrollment"), "error");
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

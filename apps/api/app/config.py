from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="A1phquest API", alias="APP_NAME")
    environment: str = Field(default="dev", alias="ENVIRONMENT")
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    database_url: str = Field(default="sqlite:///./a1phquest_dev.db", alias="DATABASE_URL")
    migrations_run_on_startup: bool = Field(default=True, alias="MIGRATIONS_RUN_ON_STARTUP")
    migration_pg_advisory_lock_enabled: bool = Field(
        default=True,
        alias="MIGRATION_PG_ADVISORY_LOCK_ENABLED",
    )
    migration_pg_advisory_lock_key: int = Field(
        default=9031101,
        alias="MIGRATION_PG_ADVISORY_LOCK_KEY",
    )
    migration_pg_advisory_lock_timeout_seconds: int = Field(
        default=120,
        alias="MIGRATION_PG_ADVISORY_LOCK_TIMEOUT_SECONDS",
    )
    db_startup_max_retries: int = Field(default=30, alias="DB_STARTUP_MAX_RETRIES")
    db_startup_retry_seconds: int = Field(default=2, alias="DB_STARTUP_RETRY_SECONDS")

    jwt_secret: str = Field(default="set-a-strong-jwt-secret-at-least-32-chars", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(default=60, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    step_up_token_expire_minutes: int = Field(default=10, alias="STEP_UP_TOKEN_EXPIRE_MINUTES")
    registration_token_expire_minutes: int = Field(default=15, alias="REGISTRATION_TOKEN_EXPIRE_MINUTES")
    security_strict_mode: bool = Field(default=True, alias="SECURITY_STRICT_MODE")
    auth_cookie_name: str = Field(default="a1phquest_access", alias="AUTH_COOKIE_NAME")
    auth_cookie_domain: str = Field(default="", alias="AUTH_COOKIE_DOMAIN")
    auth_cookie_path: str = Field(default="/", alias="AUTH_COOKIE_PATH")
    auth_cookie_samesite: str = Field(default="lax", alias="AUTH_COOKIE_SAMESITE")
    auth_cookie_secure: bool = Field(default=False, alias="AUTH_COOKIE_SECURE")
    csrf_cookie_name: str = Field(default="a1phquest_csrf", alias="CSRF_COOKIE_NAME")
    csrf_header_name: str = Field(default="X-CSRF-Token", alias="CSRF_HEADER_NAME")

    kms_mode: str = Field(default="local_aes", alias="KMS_MODE")
    aes_master_key: str = Field(
        default="set-a-strong-32-byte-aes-master-key",
        validation_alias=AliasChoices("AES_MASTER_KEY", "KMS_MASTER_KEY"),
    )
    supervisor_shared_token: str = Field(
        default="set-a-strong-supervisor-shared-token",
        alias="SUPERVISOR_SHARED_TOKEN",
    )
    supervisor_base_url: str = Field(
        default="http://worker-supervisor:8010",
        alias="SUPERVISOR_BASE_URL",
    )

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", alias="SMTP_FROM")
    smtp_to: str = Field(default="", alias="SMTP_TO")

    binance_spot_base_url: str = Field(default="https://api.binance.com", alias="BINANCE_SPOT_BASE_URL")
    binance_testnet_base_url: str = Field(
        default="https://testnet.binance.vision",
        alias="BINANCE_TESTNET_BASE_URL",
    )
    binance_futures_base_url: str = Field(
        default="https://fapi.binance.com",
        alias="BINANCE_FUTURES_BASE_URL",
    )
    binance_futures_testnet_base_url: str = Field(
        default="https://testnet.binancefuture.com",
        alias="BINANCE_FUTURES_TESTNET_BASE_URL",
    )
    okx_base_url: str = Field(default="https://www.okx.com", alias="OKX_BASE_URL")
    lighter_base_url: str = Field(
        default="https://mainnet.zklighter.elliot.ai",
        alias="LIGHTER_BASE_URL",
    )
    lighter_testnet_base_url: str = Field(
        default="https://testnet.zklighter.elliot.ai",
        alias="LIGHTER_TESTNET_BASE_URL",
    )
    gateway_validate_timeout_seconds: int = Field(default=8, alias="GATEWAY_VALIDATE_TIMEOUT_SECONDS")
    lighter_trade_fallback_window_seconds: int = Field(
        default=6 * 60 * 60,
        alias="LIGHTER_TRADE_FALLBACK_WINDOW_SECONDS",
    )
    lighter_reconcile_pending_ttl_seconds: int = Field(
        default=6 * 60 * 60,
        alias="LIGHTER_RECONCILE_PENDING_TTL_SECONDS",
    )
    lighter_reconcile_expired_retention_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        alias="LIGHTER_RECONCILE_EXPIRED_RETENTION_SECONDS",
    )
    lighter_reconcile_max_sync_errors: int = Field(
        default=5,
        alias="LIGHTER_RECONCILE_MAX_SYNC_ERRORS",
    )
    lighter_trade_sync_max_pages: int = Field(default=5, alias="LIGHTER_TRADE_SYNC_MAX_PAGES")
    login_anomaly_alert_threshold: int = Field(default=60, alias="LOGIN_ANOMALY_ALERT_THRESHOLD")
    login_anomaly_alert_cooldown_seconds: int = Field(
        default=15 * 60,
        alias="LOGIN_ANOMALY_ALERT_COOLDOWN_SECONDS",
    )
    login_anomaly_max_alerts_per_hour: int = Field(
        default=6,
        alias="LOGIN_ANOMALY_MAX_ALERTS_PER_HOUR",
    )
    ops_alert_failed_audit_rate_threshold: float = Field(
        default=0.2,
        alias="OPS_ALERT_FAILED_AUDIT_RATE_THRESHOLD",
    )
    ops_alert_runtime_drift_count_threshold: int = Field(
        default=1,
        alias="OPS_ALERT_RUNTIME_DRIFT_COUNT_THRESHOLD",
    )
    ops_alert_lighter_pending_threshold: int = Field(
        default=20,
        alias="OPS_ALERT_LIGHTER_PENDING_THRESHOLD",
    )
    ops_alert_lighter_retry_blocked_threshold: int = Field(
        default=10,
        alias="OPS_ALERT_LIGHTER_RETRY_BLOCKED_THRESHOLD",
    )
    ops_alert_critical_audit_events_threshold: int = Field(
        default=10,
        alias="OPS_ALERT_CRITICAL_AUDIT_EVENTS_THRESHOLD",
    )
    market_ws_reconnect_base_seconds: float = Field(
        default=1.0,
        alias="MARKET_WS_RECONNECT_BASE_SECONDS",
    )
    market_ws_reconnect_max_seconds: float = Field(
        default=15.0,
        alias="MARKET_WS_RECONNECT_MAX_SECONDS",
    )
    market_ws_idle_timeout_seconds: float = Field(
        default=25.0,
        alias="MARKET_WS_IDLE_TIMEOUT_SECONDS",
    )
    market_candle_cache_size: int = Field(default=1000, alias="MARKET_CANDLE_CACHE_SIZE")
    market_rest_backfill_limit: int = Field(default=500, alias="MARKET_REST_BACKFILL_LIMIT")
    ai_provider_allow_private_hosts: bool = Field(
        default=False,
        alias="AI_PROVIDER_ALLOW_PRIVATE_HOSTS",
    )
    ai_provider_allowed_hosts: str = Field(default="", alias="AI_PROVIDER_ALLOWED_HOSTS")
    ws_replay_history_size: int = Field(default=5000, alias="WS_REPLAY_HISTORY_SIZE")
    ws_dedupe_cache_size: int = Field(default=10000, alias="WS_DEDUPE_CACHE_SIZE")
    ws_allow_query_token: bool = Field(default=False, alias="WS_ALLOW_QUERY_TOKEN")
    ws_replay_backend: str = Field(default="memory", alias="WS_REPLAY_BACKEND")
    api_replica_count: int = Field(default=1, alias="API_REPLICA_COUNT")
    trust_proxy_headers: bool = Field(default=False, alias="TRUST_PROXY_HEADERS")
    cors_allowed_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ALLOWED_ORIGINS",
    )
    cors_allow_credentials: bool = Field(default=True, alias="CORS_ALLOW_CREDENTIALS")
    auth_login_max_attempts: int = Field(default=5, alias="AUTH_LOGIN_MAX_ATTEMPTS")
    auth_login_window_seconds: int = Field(default=300, alias="AUTH_LOGIN_WINDOW_SECONDS")
    auth_login_lockout_seconds: int = Field(default=900, alias="AUTH_LOGIN_LOCKOUT_SECONDS")
    auth_step_up_max_attempts: int = Field(default=5, alias="AUTH_STEP_UP_MAX_ATTEMPTS")
    auth_step_up_window_seconds: int = Field(default=300, alias="AUTH_STEP_UP_WINDOW_SECONDS")
    auth_step_up_lockout_seconds: int = Field(default=900, alias="AUTH_STEP_UP_LOCKOUT_SECONDS")
    risk_rejection_burst_threshold: int = Field(default=5, alias="RISK_REJECTION_BURST_THRESHOLD")
    risk_rejection_burst_window_seconds: int = Field(default=120, alias="RISK_REJECTION_BURST_WINDOW_SECONDS")
    risk_rejection_burst_cooldown_seconds: int = Field(
        default=300,
        alias="RISK_REJECTION_BURST_COOLDOWN_SECONDS",
    )

    @model_validator(mode="after")
    def validate_security_settings(self):
        normalized_auth_cookie_samesite = str(self.auth_cookie_samesite or "").strip().lower() or "lax"
        if normalized_auth_cookie_samesite not in {"lax", "strict", "none"}:
            raise ValueError("AUTH_COOKIE_SAMESITE must be one of lax, strict, none")
        self.auth_cookie_samesite = normalized_auth_cookie_samesite
        normalized_ws_backend = str(self.ws_replay_backend or "").strip().lower() or "memory"
        if normalized_ws_backend not in {"memory", "db"}:
            raise ValueError("WS_REPLAY_BACKEND must be either 'memory' or 'db'")
        if int(self.api_replica_count) > 1 and normalized_ws_backend == "memory":
            raise ValueError("WS_REPLAY_BACKEND=memory requires API_REPLICA_COUNT=1 to avoid split event history")
        if not self.security_strict_mode:
            return self
        weak_markers = {
            "change-me",
            "replace-with-strong",
            "set-a-strong",
            "example",
            "demo",
            "placeholder",
        }
        jwt_secret = str(self.jwt_secret or "").strip()
        if len(jwt_secret) < 32 or any(marker in jwt_secret.lower() for marker in weak_markers):
            raise ValueError("JWT_SECRET must be at least 32 chars and must not use placeholder values")

        aes_key = str(self.aes_master_key or "").strip()
        if len(aes_key.encode("utf-8")) != 32 or any(marker in aes_key.lower() for marker in weak_markers):
            raise ValueError("AES_MASTER_KEY must be exactly 32 bytes and must not use placeholder values")

        supervisor_token = str(self.supervisor_shared_token or "").strip()
        if len(supervisor_token) < 32 or any(marker in supervisor_token.lower() for marker in weak_markers):
            raise ValueError(
                "SUPERVISOR_SHARED_TOKEN must be at least 32 chars and must not use placeholder values"
            )
        return self

    def cors_allowed_origin_list(self) -> list[str]:
        """
        Parse CORS allowlist from comma-separated string and block wildcard usage
        when credentials are enabled.
        """
        items = [
            origin.strip()
            for origin in str(self.cors_allowed_origins or "").split(",")
            if origin.strip()
        ]
        if self.cors_allow_credentials and "*" in items:
            raise ValueError("CORS wildcard is not allowed when CORS_ALLOW_CREDENTIALS=true")
        return items

    def ai_provider_allowed_host_list(self) -> list[str]:
        return [
            item.strip().lower()
            for item in str(self.ai_provider_allowed_hosts or "").split(",")
            if item.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()

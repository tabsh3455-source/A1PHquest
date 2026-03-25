#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
ENV_TEMPLATE="$ROOT_DIR/.env.template"
COMPOSE_FILE="$ROOT_DIR/deploy/docker-compose.yml"
CERT_DIR="$ROOT_DIR/deploy/nginx/certs"
CERT_SCRIPT="$ROOT_DIR/deploy/nginx/generate-self-signed-cert.sh"
BUILD_IMAGES="${BUILD_IMAGES:-1}"
FORCE_REGENERATE_ENV="${FORCE_REGENERATE_ENV:-0}"
RESET_STATE="${RESET_STATE:-0}"
DEPLOY_NGINX="${DEPLOY_NGINX:-0}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-600}"
SELF_SIGNED_TLS_DAYS="${SELF_SIGNED_TLS_DAYS:-365}"
SUMMARY_ADMIN_USERNAME=""
SUMMARY_ADMIN_PASSWORD_DISPLAY=""

log() {
  printf '[install] %s\n' "$*"
}

fail() {
  printf '[install] ERROR: %s\n' "$*" >&2
  exit 1
}

require_linux() {
  if [ "$(uname -s)" != "Linux" ]; then
    fail "This installer supports Linux hosts only."
  fi
}

init_privileges() {
  if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    TARGET_USER="${SUDO_USER:-root}"
  else
    command -v sudo >/dev/null 2>&1 || fail "sudo is required when not running as root."
    SUDO="sudo"
    TARGET_USER="${USER:-$(id -un)}"
  fi
}

as_root() {
  if [ -n "${SUDO:-}" ]; then
    "$SUDO" "$@"
  else
    "$@"
  fi
}

detect_pkg_manager() {
  if command -v apt-get >/dev/null 2>&1; then
    PKG_MANAGER="apt"
  elif command -v dnf >/dev/null 2>&1; then
    PKG_MANAGER="dnf"
  elif command -v yum >/dev/null 2>&1; then
    PKG_MANAGER="yum"
  else
    fail "Unsupported Linux distribution. Install Docker, curl, and openssl manually."
  fi
}

install_packages() {
  detect_pkg_manager
  case "$PKG_MANAGER" in
    apt)
      as_root apt-get update
      as_root apt-get install -y ca-certificates curl openssl
      ;;
    dnf)
      as_root dnf install -y ca-certificates curl openssl
      ;;
    yum)
      as_root yum install -y ca-certificates curl openssl
      ;;
  esac
}

download_file() {
  local url="$1"
  local out="$2"

  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$out"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$out" "$url"
  else
    install_packages
    curl -fsSL "$url" -o "$out"
  fi
}

ensure_system_tools() {
  local missing=0
  for tool in curl openssl; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      missing=1
      break
    fi
  done
  if [ "$missing" -eq 1 ]; then
    log "Installing base system packages (curl, openssl, ca-certificates)..."
    install_packages
  fi
}

docker_available() {
  docker info >/dev/null 2>&1 || as_root docker info >/dev/null 2>&1
}

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    as_root docker "$@"
  fi
}

compose_cmd() {
  docker_cmd compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    log "Docker not found. Installing Docker Engine with the official convenience script..."
    ensure_system_tools
    local script
    script="$(mktemp)"
    download_file "https://get.docker.com" "$script"
    as_root sh "$script"
    rm -f "$script"
  fi

  if command -v systemctl >/dev/null 2>&1; then
    as_root systemctl enable --now docker >/dev/null 2>&1 || true
  elif command -v service >/dev/null 2>&1; then
    as_root service docker start >/dev/null 2>&1 || true
  fi

  if ! docker_available; then
    fail "Docker Engine is installed but not reachable."
  fi

  if ! docker_cmd compose version >/dev/null 2>&1; then
    log "Docker Compose plugin not found. Installing plugin package..."
    detect_pkg_manager
    case "$PKG_MANAGER" in
      apt)
        as_root apt-get update
        as_root apt-get install -y docker-compose-plugin
        ;;
      dnf)
        as_root dnf install -y docker-compose-plugin
        ;;
      yum)
        as_root yum install -y docker-compose-plugin
        ;;
    esac
  fi

  docker_cmd compose version >/dev/null 2>&1 || fail "Docker Compose plugin is unavailable."

  if getent group docker >/dev/null 2>&1 && [ "${TARGET_USER}" != "root" ]; then
    as_root usermod -aG docker "$TARGET_USER" >/dev/null 2>&1 || true
  fi
}

random_alnum() {
  local length="$1"
  openssl rand -base64 96 | tr -dc 'A-Za-z0-9' | head -c "$length"
}

byte_length() {
  printf '%s' "${1:-}" | wc -c | tr -d ' '
}

lowercase() {
  printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]'
}

is_placeholder_value() {
  local lowered
  lowered="$(lowercase "${1:-}")"
  case "$lowered" in
    ""|*replace*|*placeholder*|*change-me*|*changeme*|*set-a-strong*|*example*|*demo*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_valid_min_length_secret() {
  local value="${1:-}"
  local min_length="${2:-32}"
  if [ "${#value}" -lt "$min_length" ]; then
    return 1
  fi
  if is_placeholder_value "$value"; then
    return 1
  fi
  return 0
}

is_valid_aes_key() {
  local value="${1:-}"
  if [ "$(byte_length "$value")" -ne 32 ]; then
    return 1
  fi
  if is_placeholder_value "$value"; then
    return 1
  fi
  return 0
}

backup_env_file() {
  local backup_path
  backup_path="${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
  cp "$ENV_FILE" "$backup_path"
  log "Backed up existing .env to $backup_path"
}

get_env_value() {
  local key="$1"
  if [ ! -f "$ENV_FILE" ]; then
    return 1
  fi
  grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2-
}

set_env_value() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

set_env_value_if_missing() {
  local key="$1"
  local value="$2"
  if ! grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

is_interactive_install() {
  [ -t 0 ] && [ -t 1 ]
}

default_bootstrap_admin_email() {
  local username="${1:-admin}"
  printf '%s@a1phquest.local' "$username"
}

prompt_with_default() {
  local prompt="$1"
  local default_value="$2"
  local value=""

  read -r -p "$prompt [$default_value]: " value || true
  printf '%s' "${value:-$default_value}"
}

prompt_for_password() {
  local generated_password="$1"
  local password=""
  local confirm=""

  while true; do
    read -r -s -p "Bootstrap admin password (leave blank to auto-generate): " password || true
    printf '\n'
    if [ -z "$password" ]; then
      printf '%s' "$generated_password"
      return 0
    fi

    read -r -s -p "Confirm bootstrap admin password: " confirm || true
    printf '\n'
    if [ "$password" = "$confirm" ]; then
      printf '%s' "$password"
      return 0
    fi

    log "Passwords did not match. Please try again."
  done
}

configure_bootstrap_admin() {
  local current_username="${1:-}"
  local current_email="${2:-}"
  local current_password="${3:-}"
  local generated_password

  INSTALL_BOOTSTRAP_ADMIN_USERNAME="${current_username:-admin}"
  INSTALL_BOOTSTRAP_ADMIN_EMAIL="${current_email:-$(default_bootstrap_admin_email "$INSTALL_BOOTSTRAP_ADMIN_USERNAME")}"
  INSTALL_BOOTSTRAP_ADMIN_PASSWORD="$current_password"
  SUMMARY_ADMIN_PASSWORD_DISPLAY="<already configured in .env>"

  if [ -n "${BOOTSTRAP_ADMIN_USERNAME:-}" ]; then
    INSTALL_BOOTSTRAP_ADMIN_USERNAME="$BOOTSTRAP_ADMIN_USERNAME"
  elif [ -z "$current_username" ] && is_interactive_install; then
    INSTALL_BOOTSTRAP_ADMIN_USERNAME="$(prompt_with_default "Bootstrap admin username" "$INSTALL_BOOTSTRAP_ADMIN_USERNAME")"
  fi

  if [ -n "${BOOTSTRAP_ADMIN_EMAIL:-}" ]; then
    INSTALL_BOOTSTRAP_ADMIN_EMAIL="$BOOTSTRAP_ADMIN_EMAIL"
  elif [ -z "$current_email" ] && is_interactive_install; then
    INSTALL_BOOTSTRAP_ADMIN_EMAIL="$(prompt_with_default "Bootstrap admin email" "$(default_bootstrap_admin_email "$INSTALL_BOOTSTRAP_ADMIN_USERNAME")")"
  elif [ -z "$current_email" ]; then
    INSTALL_BOOTSTRAP_ADMIN_EMAIL="$(default_bootstrap_admin_email "$INSTALL_BOOTSTRAP_ADMIN_USERNAME")"
  fi

  if [ -n "${BOOTSTRAP_ADMIN_PASSWORD:-}" ]; then
    INSTALL_BOOTSTRAP_ADMIN_PASSWORD="$BOOTSTRAP_ADMIN_PASSWORD"
    SUMMARY_ADMIN_PASSWORD_DISPLAY="<provided via BOOTSTRAP_ADMIN_PASSWORD>"
  elif [ -z "$current_password" ]; then
    generated_password="$(random_alnum 24)"
    if is_interactive_install; then
      INSTALL_BOOTSTRAP_ADMIN_PASSWORD="$(prompt_for_password "$generated_password")"
      if [ "$INSTALL_BOOTSTRAP_ADMIN_PASSWORD" = "$generated_password" ]; then
        SUMMARY_ADMIN_PASSWORD_DISPLAY="$generated_password"
      else
        SUMMARY_ADMIN_PASSWORD_DISPLAY="<provided during install>"
      fi
    else
      INSTALL_BOOTSTRAP_ADMIN_PASSWORD="$generated_password"
      SUMMARY_ADMIN_PASSWORD_DISPLAY="$generated_password"
    fi
  fi

  SUMMARY_ADMIN_USERNAME="$INSTALL_BOOTSTRAP_ADMIN_USERNAME"
}

generate_env_file() {
  [ -f "$ENV_TEMPLATE" ] || fail "Missing .env.template"
  cp "$ENV_TEMPLATE" "$ENV_FILE"

  local postgres_user="a1phquest"
  local postgres_db="a1phquest"
  local postgres_password
  local jwt_secret
  local supervisor_token
  local aes_master_key

  postgres_password="$(random_alnum 32)"
  jwt_secret="$(random_alnum 48)"
  supervisor_token="$(random_alnum 48)"
  aes_master_key="$(random_alnum 32)"
  configure_bootstrap_admin "" "" ""

  set_env_value "ENVIRONMENT" "prod"
  set_env_value "API_HOST" "0.0.0.0"
  set_env_value "POSTGRES_DB" "$postgres_db"
  set_env_value "POSTGRES_USER" "$postgres_user"
  set_env_value "POSTGRES_PASSWORD" "$postgres_password"
  set_env_value "DATABASE_URL" "postgresql+psycopg2://${postgres_user}:${postgres_password}@postgres:5432/${postgres_db}"
  set_env_value "JWT_SECRET" "$jwt_secret"
  set_env_value "SUPERVISOR_SHARED_TOKEN" "$supervisor_token"
  set_env_value "AES_MASTER_KEY" "$aes_master_key"
  set_env_value "BOOTSTRAP_ADMIN_ENABLED" "1"
  set_env_value "BOOTSTRAP_ADMIN_USERNAME" "$INSTALL_BOOTSTRAP_ADMIN_USERNAME"
  set_env_value "BOOTSTRAP_ADMIN_EMAIL" "$INSTALL_BOOTSTRAP_ADMIN_EMAIL"
  set_env_value "BOOTSTRAP_ADMIN_PASSWORD" "$INSTALL_BOOTSTRAP_ADMIN_PASSWORD"
  set_env_value "AUTH_COOKIE_SECURE" "1"
  set_env_value "TRUST_PROXY_HEADERS" "1"
  set_env_value "CORS_ALLOWED_ORIGINS" "https://localhost,https://127.0.0.1"
  set_env_value "A1PHQUEST_HTTP_PORT" "80"
  set_env_value "A1PHQUEST_HTTPS_PORT" "443"

  chmod 600 "$ENV_FILE"
}

existing_env_requires_regeneration() {
  local postgres_user
  local postgres_password
  local database_url
  local jwt_secret
  local supervisor_token
  local aes_master_key

  postgres_user="$(get_env_value "POSTGRES_USER" || true)"
  postgres_password="$(get_env_value "POSTGRES_PASSWORD" || true)"
  database_url="$(get_env_value "DATABASE_URL" || true)"
  jwt_secret="$(get_env_value "JWT_SECRET" || true)"
  supervisor_token="$(get_env_value "SUPERVISOR_SHARED_TOKEN" || true)"
  aes_master_key="$(get_env_value "AES_MASTER_KEY" || true)"

  if is_placeholder_value "$postgres_user"; then
    return 0
  fi
  if is_placeholder_value "$postgres_password"; then
    return 0
  fi
  if is_placeholder_value "$database_url"; then
    return 0
  fi
  if ! is_valid_min_length_secret "$jwt_secret" 32; then
    return 0
  fi
  if ! is_valid_min_length_secret "$supervisor_token" 32; then
    return 0
  fi
  if ! is_valid_aes_key "$aes_master_key"; then
    return 0
  fi
  return 1
}

ensure_existing_env_defaults() {
  set_env_value_if_missing "BOOTSTRAP_ADMIN_ENABLED" "1"
  set_env_value_if_missing "ENVIRONMENT" "prod"
  set_env_value_if_missing "API_HOST" "0.0.0.0"
  set_env_value_if_missing "AUTH_COOKIE_SECURE" "1"
  set_env_value_if_missing "TRUST_PROXY_HEADERS" "1"
  set_env_value_if_missing "A1PHQUEST_HTTP_PORT" "80"
  set_env_value_if_missing "A1PHQUEST_HTTPS_PORT" "443"
}

ensure_env_file() {
  local current_username=""
  local current_email=""
  local current_password=""

  if [ "$FORCE_REGENERATE_ENV" = "1" ] || [ ! -f "$ENV_FILE" ]; then
    log "Generating deployment .env file..."
    generate_env_file
  else
    log "Using existing .env file."
    if existing_env_requires_regeneration; then
      log "Existing .env contains placeholder or invalid required deployment values."
      backup_env_file
      log "Regenerating deployment .env with fresh secrets and database credentials..."
      generate_env_file
      return
    fi
    current_username="$(get_env_value "BOOTSTRAP_ADMIN_USERNAME" || true)"
    current_email="$(get_env_value "BOOTSTRAP_ADMIN_EMAIL" || true)"
    current_password="$(get_env_value "BOOTSTRAP_ADMIN_PASSWORD" || true)"
    ensure_existing_env_defaults
    if ! grep -q '^BOOTSTRAP_ADMIN_USERNAME=' "$ENV_FILE" || ! grep -q '^BOOTSTRAP_ADMIN_EMAIL=' "$ENV_FILE" || ! grep -q '^BOOTSTRAP_ADMIN_PASSWORD=' "$ENV_FILE"; then
      configure_bootstrap_admin "$current_username" "$current_email" "$current_password"
    else
      SUMMARY_ADMIN_USERNAME="${current_username:-admin}"
      SUMMARY_ADMIN_PASSWORD_DISPLAY="<already configured in .env>"
    fi
    if ! grep -q '^BOOTSTRAP_ADMIN_USERNAME=' "$ENV_FILE"; then
      set_env_value "BOOTSTRAP_ADMIN_USERNAME" "$INSTALL_BOOTSTRAP_ADMIN_USERNAME"
    fi
    if ! grep -q '^BOOTSTRAP_ADMIN_EMAIL=' "$ENV_FILE"; then
      set_env_value "BOOTSTRAP_ADMIN_EMAIL" "$INSTALL_BOOTSTRAP_ADMIN_EMAIL"
    fi
    if ! grep -q '^BOOTSTRAP_ADMIN_PASSWORD=' "$ENV_FILE"; then
      set_env_value "BOOTSTRAP_ADMIN_PASSWORD" "$INSTALL_BOOTSTRAP_ADMIN_PASSWORD"
    fi
  fi
}

reset_existing_state_if_requested() {
  if [ "$RESET_STATE" != "1" ]; then
    return
  fi
  log "RESET_STATE=1 detected. Removing existing containers and named volumes..."
  compose_cmd down -v --remove-orphans || true
}

ensure_tls_certificates() {
  if [ "$DEPLOY_NGINX" != "1" ]; then
    log "Skipping TLS certificate generation because DEPLOY_NGINX=0."
    return
  fi
  if [ -f "$CERT_DIR/tls.crt" ] && [ -f "$CERT_DIR/tls.key" ]; then
    log "Using existing TLS certificate files."
    return
  fi

  ensure_system_tools
  log "Generating self-signed TLS certificate (${SELF_SIGNED_TLS_DAYS} days)..."
  mkdir -p "$CERT_DIR"
  SELF_SIGNED_TLS_DAYS="$SELF_SIGNED_TLS_DAYS" sh "$CERT_SCRIPT" "$CERT_DIR"
}

container_status() {
  docker_cmd inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || true
}

wait_for_container_healthy() {
  local container="$1"
  local started
  started="$(date +%s)"
  while true; do
    case "$(container_status "$container")" in
      healthy)
        return 0
        ;;
      unhealthy|exited|dead)
        return 1
        ;;
    esac

    if [ $(( "$(date +%s)" - started )) -ge "$WAIT_TIMEOUT_SECONDS" ]; then
      return 1
    fi
    sleep 2
  done
}

print_failure_diagnostics() {
  set +e
  printf '\n[install] Deployment diagnostics follow.\n'
  compose_cmd ps
  printf '\n[install] Recent service logs (postgres, migrate, worker-supervisor, api):\n'
  compose_cmd logs --tail=120 postgres migrate worker-supervisor api
  set -e
}

verify_postgres_credentials() {
  local postgres_db
  local postgres_user
  local postgres_password

  postgres_db="$(get_env_value "POSTGRES_DB" || true)"
  postgres_user="$(get_env_value "POSTGRES_USER" || true)"
  postgres_password="$(get_env_value "POSTGRES_PASSWORD" || true)"

  [ -n "$postgres_db" ] || fail "POSTGRES_DB is missing from $ENV_FILE"
  [ -n "$postgres_user" ] || fail "POSTGRES_USER is missing from $ENV_FILE"
  [ -n "$postgres_password" ] || fail "POSTGRES_PASSWORD is missing from $ENV_FILE"

  log "Validating PostgreSQL credentials against the current data volume..."
  if docker_cmd exec -e PGPASSWORD="$postgres_password" a1phquest-postgres \
    psql -h 127.0.0.1 -U "$postgres_user" -d "$postgres_db" -c "SELECT 1" >/dev/null 2>&1; then
    return
  fi

  print_failure_diagnostics
  fail "PostgreSQL data volume does not accept the credentials in $ENV_FILE. If this is a fresh install, rerun with: RESET_STATE=1 FORCE_REGENERATE_ENV=1 bash install.sh . If you need existing data, restore the original POSTGRES_* values in $ENV_FILE before retrying."
}

build_images_if_requested() {
  if [ "$BUILD_IMAGES" = "1" ]; then
    log "Building Docker images..."
    compose_cmd build
  fi
}

wait_for_url() {
  local url="$1"
  local insecure="${2:-0}"
  local started
  started="$(date +%s)"
  while true; do
    if [ "$insecure" = "1" ]; then
      if curl -kfsS --max-time 5 "$url" >/dev/null 2>&1; then
        return 0
      fi
    else
      if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
        return 0
      fi
    fi

    if [ $(( "$(date +%s)" - started )) -ge "$WAIT_TIMEOUT_SECONDS" ]; then
      return 1
    fi
    sleep 2
  done
}

start_stack() {
  reset_existing_state_if_requested
  build_images_if_requested

  log "Starting PostgreSQL..."
  compose_cmd up -d postgres
  if ! wait_for_container_healthy "a1phquest-postgres"; then
    print_failure_diagnostics
    fail "PostgreSQL container failed to become healthy."
  fi
  verify_postgres_credentials

  log "Running database migrations..."
  if ! compose_cmd up --no-build --no-deps migrate; then
    print_failure_diagnostics
    fail "Migration service failed. See diagnostics above."
  fi

  log "Starting worker supervisor..."
  compose_cmd up -d --no-build --no-deps worker-supervisor
  if ! wait_for_container_healthy "a1phquest-worker-supervisor"; then
    print_failure_diagnostics
    fail "worker-supervisor failed to become healthy."
  fi

  local services=(api frontend backup)
  if [ "$DEPLOY_NGINX" = "1" ]; then
    services+=(nginx)
  fi

  log "Starting remaining services: ${services[*]}..."
  if ! compose_cmd up -d --no-build "${services[@]}"; then
    print_failure_diagnostics
    fail "One or more application services failed to start."
  fi
}

verify_stack() {
  log "Waiting for API health endpoint..."
  if ! wait_for_url "http://127.0.0.1:8000/healthz"; then
    print_failure_diagnostics
    fail "API health check timed out."
  fi

  log "Waiting for frontend endpoint..."
  if ! wait_for_url "http://127.0.0.1:5173/"; then
    print_failure_diagnostics
    fail "Frontend check timed out."
  fi

  if [ "$DEPLOY_NGINX" = "1" ]; then
    log "Waiting for HTTPS frontend endpoint..."
    if ! wait_for_url "https://127.0.0.1/" "1"; then
      print_failure_diagnostics
      fail "HTTPS frontend check timed out."
    fi

    log "Verifying HTTP to HTTPS redirect..."
    local location
    location="$(curl -sI http://127.0.0.1/ | sed -n 's/^[Ll]ocation: //p' | tr -d '\r' | head -n 1)"
    case "$location" in
      https://*)
        ;;
      *)
        print_failure_diagnostics
        fail "Expected HTTP redirect to HTTPS, got: ${location:-<none>}"
        ;;
    esac
  fi
}

print_summary() {
  local frontend_url
  local backend_url
  local api_docs_url
  local extra_note

  frontend_url="http://127.0.0.1:5173/"
  backend_url="http://127.0.0.1:8000/"
  api_docs_url="http://127.0.0.1:8000/docs"
  extra_note="Optional: DEPLOY_NGINX=1 bash install.sh"
  if [ "$DEPLOY_NGINX" = "1" ]; then
    extra_note="Optional extra HTTPS entry: https://127.0.0.1/ (HTTP redirects to HTTPS)"
  fi

  cat <<EOF

A1phquest deployment completed.

- Frontend: $frontend_url
- Backend API: $backend_url
- API docs: $api_docs_url
- API health: http://127.0.0.1:8000/healthz
- $extra_note
- Env file: $ENV_FILE
- Admin username: ${SUMMARY_ADMIN_USERNAME:-admin}
- Admin password: ${SUMMARY_ADMIN_PASSWORD_DISPLAY:-<see .env>}

Notes:
- Frontend and backend stay on separate ports by default.
- Nginx/TLS is optional during first bootstrap. When you are ready, rerun:
  DEPLOY_NGINX=1 bash install.sh
- When DEPLOY_NGINX=1, the installer uses a self-signed TLS certificate by
  default. Replace $CERT_DIR/tls.crt and $CERT_DIR/tls.key with your real
  certificate pair later.
- If Docker was newly installed for a non-root user, you may need to re-login for
  the docker group membership to take effect outside this installer.
EOF
}

main() {
  require_linux
  init_privileges
  ensure_system_tools
  ensure_docker
  ensure_env_file
  ensure_tls_certificates
  start_stack
  verify_stack
  print_summary
}

main "$@"

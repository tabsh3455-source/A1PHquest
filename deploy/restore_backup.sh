#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <backup_file.sql.enc>"
  exit 1
fi

BACKUP_FILE="$1"
if [[ ! -f "${BACKUP_FILE}" ]]; then
  echo "Backup file not found: ${BACKUP_FILE}"
  exit 1
fi

TMP_SQL="$(mktemp /tmp/a1phquest_restore_XXXXXX.sql)"
cleanup() {
  rm -f "${TMP_SQL}"
}
trap cleanup EXIT

BACKUP_KEY="${AES_MASTER_KEY:-${KMS_MASTER_KEY:-}}"
if [[ -z "${BACKUP_KEY}" ]]; then
  echo "AES_MASTER_KEY (or KMS_MASTER_KEY) is required to decrypt backups."
  exit 1
fi
if [[ "${#BACKUP_KEY}" -lt 32 ]]; then
  echo "Backup decryption key must be at least 32 characters."
  exit 1
fi
if [[ "${BACKUP_KEY}" == *"change-me"* || "${BACKUP_KEY}" == *"replace-with"* || "${BACKUP_KEY}" == *"set-a-strong"* ]]; then
  echo "Refusing weak placeholder backup key."
  exit 1
fi
openssl enc -d -aes-256-cbc -pbkdf2 -in "${BACKUP_FILE}" -out "${TMP_SQL}" -k "${BACKUP_KEY}"

export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
psql \
  --host="${POSTGRES_HOST:-127.0.0.1}" \
  --port="${POSTGRES_PORT:-5432}" \
  --username="${POSTGRES_USER:?POSTGRES_USER is required}" \
  --dbname="${POSTGRES_DB:?POSTGRES_DB is required}" \
  --set ON_ERROR_STOP=1 \
  -f "${TMP_SQL}"

echo "Restore completed from ${BACKUP_FILE}"

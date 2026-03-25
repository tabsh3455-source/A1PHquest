#!/usr/bin/env bash
set -euo pipefail

NOW="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="/backups"
RAW_FILE="${OUT_DIR}/a1phquest_${NOW}.sql"
ENC_FILE="${RAW_FILE}.enc"

mkdir -p "${OUT_DIR}"

export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
pg_dump \
  --host=postgres \
  --port=5432 \
  --username="${POSTGRES_USER:?POSTGRES_USER is required}" \
  --dbname="${POSTGRES_DB:?POSTGRES_DB is required}" \
  --format=plain \
  --no-owner \
  --no-privileges > "${RAW_FILE}"

BACKUP_KEY="${AES_MASTER_KEY:-${KMS_MASTER_KEY:-}}"
if [[ -z "${BACKUP_KEY}" ]]; then
  echo "AES_MASTER_KEY (or KMS_MASTER_KEY) is required for encrypted backups."
  exit 1
fi
if [[ "${#BACKUP_KEY}" -lt 32 ]]; then
  echo "Backup encryption key must be at least 32 characters."
  exit 1
fi
if [[ "${BACKUP_KEY}" == *"change-me"* || "${BACKUP_KEY}" == *"replace-with"* || "${BACKUP_KEY}" == *"set-a-strong"* ]]; then
  echo "Refusing weak placeholder backup key."
  exit 1
fi
openssl enc -aes-256-cbc -salt -pbkdf2 -in "${RAW_FILE}" -out "${ENC_FILE}" -k "${BACKUP_KEY}"
rm -f "${RAW_FILE}"

# Optional COS upload hook (production hardening).
# Enable by setting COS_BACKUP_BUCKET, for example:
# COS_BACKUP_BUCKET=cos://your-bucket/a1phquest-backups
if [[ -n "${COS_BACKUP_BUCKET:-}" ]] && command -v coscli >/dev/null 2>&1; then
  coscli cp "${ENC_FILE}" "${COS_BACKUP_BUCKET%/}/$(basename "${ENC_FILE}")"
fi

find "${OUT_DIR}" -type f -name "a1phquest_*.sql.enc" -mtime +30 -delete
echo "Backup completed: ${ENC_FILE}"

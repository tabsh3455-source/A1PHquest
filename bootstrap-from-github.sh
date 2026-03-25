#!/usr/bin/env bash
set -euo pipefail

REPO_URL=""
REF="main"
TARGET_DIR="/opt/a1phquest"

usage() {
  cat <<'EOF'
Usage:
  bash bootstrap-from-github.sh --repo <git-url> [--ref <branch-or-tag>] [--dir <target-dir>]

Examples:
  bash bootstrap-from-github.sh --repo https://github.com/OWNER/REPO.git
  bash bootstrap-from-github.sh --repo git@github.com:OWNER/REPO.git --ref main --dir /srv/a1phquest
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO_URL="${2:-}"
      shift 2
      ;;
    --ref)
      REF="${2:-}"
      shift 2
      ;;
    --dir)
      TARGET_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$REPO_URL" ]]; then
  echo "--repo is required" >&2
  usage
  exit 1
fi

require_command() {
  if command -v "$1" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

install_git_if_missing() {
  if require_command git; then
    return 0
  fi

  echo "git not found, attempting automatic install..."
  if require_command apt-get; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y git
    return 0
  fi
  if require_command dnf; then
    dnf install -y git
    return 0
  fi
  if require_command yum; then
    yum install -y git
    return 0
  fi
  if require_command apk; then
    apk add --no-cache git
    return 0
  fi

  echo "Unable to install git automatically on this host." >&2
  exit 1
}

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "bootstrap-from-github.sh is intended for Linux deployment hosts." >&2
  exit 1
fi

install_git_if_missing

mkdir -p "$(dirname "$TARGET_DIR")"

if [[ -d "$TARGET_DIR/.git" ]]; then
  echo "Updating existing checkout in $TARGET_DIR"
  git -C "$TARGET_DIR" fetch --tags origin
  git -C "$TARGET_DIR" checkout "$REF"
  git -C "$TARGET_DIR" pull --ff-only origin "$REF"
else
  if [[ -e "$TARGET_DIR" && -n "$(ls -A "$TARGET_DIR" 2>/dev/null || true)" ]]; then
    echo "Target directory exists and is not an existing git checkout: $TARGET_DIR" >&2
    exit 1
  fi
  rm -rf "$TARGET_DIR"
  echo "Cloning $REPO_URL into $TARGET_DIR"
  git clone --branch "$REF" --single-branch "$REPO_URL" "$TARGET_DIR"
fi

cd "$TARGET_DIR"
exec bash install.sh

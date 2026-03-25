#!/usr/bin/env bash
set -euo pipefail

# Backward-compatible wrapper.
# Prefer using `deploy/stack.sh` for Linux VPS deployment.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/stack.sh" "$@"

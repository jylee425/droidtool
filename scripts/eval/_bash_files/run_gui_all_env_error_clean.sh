#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export MODE=gui
export ROOT="${ROOT:-logs_gui}"

exec "$SCRIPT_DIR/helper_full_manifest_env_error_clean.sh" all

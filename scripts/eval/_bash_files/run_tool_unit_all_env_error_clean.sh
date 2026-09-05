#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MODE=unit
export ROOT="${ROOT:-logs_tool_unit_test}"
exec "$SCRIPT_DIR/helper_full_manifest_env_error_clean.sh" all

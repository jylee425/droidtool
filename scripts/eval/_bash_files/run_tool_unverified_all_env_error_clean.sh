#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MODE=unverified
export ROOT="${ROOT:-logs_tool_unverified}"
export MSB_TASK_SET="${MSB_TASK_SET:-daily_100}"
exec "$SCRIPT_DIR/helper_full_manifest_env_error_clean.sh" all

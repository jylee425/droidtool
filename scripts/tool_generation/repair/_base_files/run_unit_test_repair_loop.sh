#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
PROJECT_PATH="${PROJECT_PATH:-${REPO}}"
MAX_ITERATIONS="${MAX_ITERATIONS:-16}"
ALL_APPS=(
  broccoli_app clock contacts files joplin markor messages
  open_tracks_sports_tracker osmand photonote photos pro_expense retro_music
  settings simple_calendar_pro snapseed tasks vlc wikipedia
)
export PROJECT_PATH

python "${PROJECT_PATH}/scripts/tool_generation/repair/run_repair_loop.py" \
  --test_mode unit \
  --creation_root "${PROJECT_PATH}/output_tool/implementation" \
  --work_root "${PROJECT_PATH}/output_tool/repair_loop_unit_test" \
  --test_case_root "${PROJECT_PATH}/output_tool/test_generation_unit_test" \
  --apps "${ALL_APPS[@]}" \
  --test_script "${PROJECT_PATH}/scripts/tool_generation/test_execution/unit_test/execute_test.py" \
  --repair_script "${PROJECT_PATH}/scripts/tool_generation/repair/repair_tools.py" \
  --registration_script "${PROJECT_PATH}/scripts/tool_generation/repair/register_verified_tools.py" \
  --max_iterations "${MAX_ITERATIONS}" \
  "$@"

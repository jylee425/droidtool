#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_PATH="${PROJECT_PATH:-$(cd "${SCRIPT_DIR}/../../../.." && pwd)}"
ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-${HOME}/.local/share/android/sdk}"
AVD_NAME="${AVD_NAME:-unified_environment}"
SNAPSHOT_NAME="${SNAPSHOT_NAME:-apk_installed_base}"
SERIAL="${SERIAL:-emulator-5554}"
TEST_SHARDS="${TEST_SHARDS:-4}"
MAX_ITERATIONS="${MAX_ITERATIONS:-16}"
ALL_APPS=(
  broccoli_app clock contacts files joplin markor messages
  open_tracks_sports_tracker osmand photonote photos pro_expense retro_music
  settings simple_calendar_pro snapseed tasks vlc wikipedia
)
export PROJECT_PATH

python "${PROJECT_PATH}/scripts/tool_generation/repair/run_repair_loop.py" \
  --test_mode isolation \
  --creation_root "${PROJECT_PATH}/output_tool/implementation" \
  --work_root "${PROJECT_PATH}/output_tool/repair_loop_isolation_test" \
  --test_case_root "${PROJECT_PATH}/output_tool/test_generation_isolation_test" \
  --registration_root "${PROJECT_PATH}/output_tool/registration_isolation_test" \
  --apps "${ALL_APPS[@]}" \
  --test_script "${PROJECT_PATH}/scripts/tool_generation/test_execution/isolation_test/execute_test.py" \
  --repair_script "${PROJECT_PATH}/scripts/tool_generation/repair/repair_tools.py" \
  --registration_script "${PROJECT_PATH}/scripts/tool_generation/repair/register_verified_tools.py" \
  --adb_path "${ANDROID_SDK_ROOT}/platform-tools/adb" \
  --emulator_path "${ANDROID_SDK_ROOT}/emulator/emulator" \
  --avd_name "${AVD_NAME}" \
  --emulator_serial "${SERIAL}" \
  --base_snapshot "${SNAPSHOT_NAME}" \
  --test_shards "${TEST_SHARDS}" \
  --max_iterations "${MAX_ITERATIONS}" \
  "$@"

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_PATH="${PROJECT_PATH:-$(cd "${SCRIPT_DIR}/../../../.." && pwd)}"
ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-${HOME}/.local/share/android/sdk}"
export PROJECT_PATH

TOOL_ROOT="${PROJECT_PATH}/output_tool/implementation"
OUTPUT_ROOT="${PROJECT_PATH}/output_tool/repair_loop_lifecycle_test"
TEST_CASE_ROOT="${PROJECT_PATH}/output_tool/test_generation_lifecycle_test"
ADB_PATH="${ANDROID_SDK_ROOT}/platform-tools/adb"
EMULATOR_PATH="${ANDROID_SDK_ROOT}/emulator/emulator"
AVD_NAME="${AVD_NAME:-unified_environment}"
EMULATOR_SERIAL="${SERIAL:-emulator-5554}"
BASE_SNAPSHOT="${SNAPSHOT_NAME:-apk_installed_base}"
BOOT_TIMEOUT_S=180
TOOL_TIMEOUT_S=12
EMULATOR_READ_ONLY=0
APPS=(
  broccoli_app clock contacts files joplin markor messages
  open_tracks_sports_tracker osmand photonote photos pro_expense retro_music
  settings simple_calendar_pro snapseed tasks vlc wikipedia
)
APPS_EXPLICIT=0
TARGETS=()

usage() {
  cat <<'EOF'
Usage: run_lifecycle_test_execution.sh [options]

Options:
  --tool-root DIR          Generated tool root. Default: output_tool/implementation
  --output-root DIR        Lifecycle test work root
  --test-case-root DIR     Test-case root. Default: output_tool/test_generation_lifecycle_test
  --adb-path PATH          adb executable
  --emulator-path PATH     Android emulator executable
  --avd-name NAME          AVD name. Default: unified_environment
  --emulator-serial SERIAL Emulator serial. Default: emulator-5554
  --base-snapshot NAME     Base snapshot. Default: apk_installed_base
  --boot-timeout-s N       Emulator boot timeout. Default: 180
  --tool-timeout-s N       Per-tool timeout. Default: 12
  --emulator-read-only     Start the emulator in read-only mode
  --app APP                Add one app slug. Can be repeated
  --target TARGET          Add a target slug or app/target. Can be repeated
  -h, --help               Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tool-root) TOOL_ROOT="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --test-case-root) TEST_CASE_ROOT="$2"; shift 2 ;;
    --adb-path) ADB_PATH="$2"; shift 2 ;;
    --emulator-path) EMULATOR_PATH="$2"; shift 2 ;;
    --avd-name) AVD_NAME="$2"; shift 2 ;;
    --emulator-serial) EMULATOR_SERIAL="$2"; shift 2 ;;
    --base-snapshot) BASE_SNAPSHOT="$2"; shift 2 ;;
    --boot-timeout-s) BOOT_TIMEOUT_S="$2"; shift 2 ;;
    --tool-timeout-s) TOOL_TIMEOUT_S="$2"; shift 2 ;;
    --emulator-read-only) EMULATOR_READ_ONLY=1; shift ;;
    --app)
      if [[ "${APPS_EXPLICIT}" -eq 0 ]]; then APPS=(); APPS_EXPLICIT=1; fi
      APPS+=("$2"); shift 2 ;;
    --target) TARGETS+=("$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for value in "${BOOT_TIMEOUT_S}" "${TOOL_TIMEOUT_S}"; do
  if ! [[ "${value}" =~ ^[1-9][0-9]*$ ]]; then
    echo "Timeouts must be positive integers" >&2
    exit 2
  fi
done
if [[ ! -d "${TOOL_ROOT}" ]]; then
  echo "Tool root not found: ${TOOL_ROOT}" >&2
  exit 1
fi
if [[ ! -d "${TEST_CASE_ROOT}" ]]; then
  echo "Test-case root not found: ${TEST_CASE_ROOT}" >&2
  exit 1
fi
if [[ ! -x "${ADB_PATH}" || ! -x "${EMULATOR_PATH}" ]]; then
  echo "Android SDK executables are unavailable" >&2
  exit 1
fi

command=(
  python "${PROJECT_PATH}/scripts/tool_generation/test_execution/lifecycle_test/execute_test.py"
  --tool-root "${TOOL_ROOT}"
  --output-root "${OUTPUT_ROOT}/00_test"
  --test-case-root "${TEST_CASE_ROOT}"
  --adb-path "${ADB_PATH}"
  --emulator-path "${EMULATOR_PATH}"
  --avd-name "${AVD_NAME}"
  --emulator-serial "${EMULATOR_SERIAL}"
  --base-snapshot "${BASE_SNAPSHOT}"
  --boot-timeout-s "${BOOT_TIMEOUT_S}"
  --tool-timeout-s "${TOOL_TIMEOUT_S}"
)
for app in "${APPS[@]}"; do command+=(--app "${app}"); done
for target in "${TARGETS[@]}"; do command+=(--target "${target}"); done
if [[ "${EMULATOR_READ_ONLY}" -eq 1 ]]; then command+=(--emulator-read-only); fi

cd "${PROJECT_PATH}"
exec "${command[@]}"

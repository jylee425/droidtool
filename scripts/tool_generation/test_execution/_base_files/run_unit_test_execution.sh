#!/usr/bin/env bash
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
PROJECT_PATH="${PROJECT_PATH:-${REPO}}"
export PROJECT_PATH

TOOL_ROOT="${PROJECT_PATH}/output_tool/implementation"
OUTPUT_ROOT="${PROJECT_PATH}/output_tool/repair_loop_unit_test"
TEST_CASE_ROOT="${PROJECT_PATH}/output_tool/test_generation_unit_test"
WORKERS=8
TOOL_TIMEOUT_S=30
APPS=(
  broccoli_app clock contacts files joplin markor messages
  open_tracks_sports_tracker osmand photonote photos pro_expense retro_music
  settings simple_calendar_pro snapseed tasks vlc wikipedia
)
APPS_EXPLICIT=0

usage() {
  cat <<'EOF'
Usage: run_unit_test_execution.sh [options]

Options:
  --tool-root DIR         Tool root. Default: output_tool/implementation
  --output-root DIR       Unit test output root. Default: output_tool/repair_loop_unit_test
  --test-case-root DIR    Test-case root. Default: output_tool/test_generation_unit_test
  --workers N             Number of concurrent app jobs. Default: 8
  --tool-timeout-s N      Timeout for each public-tool test. Default: 30
  --app APP               Add one app slug. Can be repeated. Defaults to all apps
  -h, --help              Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tool-root) TOOL_ROOT="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --test-case-root) TEST_CASE_ROOT="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --tool-timeout-s) TOOL_TIMEOUT_S="$2"; shift 2 ;;
    --app)
      if [[ "${APPS_EXPLICIT}" -eq 0 ]]; then APPS=(); APPS_EXPLICIT=1; fi
      APPS+=("$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! [[ "${WORKERS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "--workers must be a positive integer" >&2
  exit 2
fi
if ! [[ "${TOOL_TIMEOUT_S}" =~ ^[1-9][0-9]*$ ]]; then
  echo "--tool-timeout-s must be a positive integer" >&2
  exit 2
fi
if [[ ! -d "${TOOL_ROOT}" ]]; then
  echo "Tool root not found: ${TOOL_ROOT}" >&2
  exit 1
fi
if [[ ! -d "${TEST_CASE_ROOT}" ]]; then
  echo "Test-case root not found: ${TEST_CASE_ROOT}" >&2
  exit 1
fi
if [[ "${#APPS[@]}" -eq 0 ]]; then
  mapfile -t APPS < <(find "${TOOL_ROOT}" -mindepth 3 -maxdepth 3 -type f -name tools.py -printf '%h\n' | sed 's#/[^/]*$##' | sed 's#.*/##' | sort -u)
fi

FAIL_FILE="$(mktemp)"
trap 'rm -f "${FAIL_FILE}"' EXIT

wait_for_slot() {
  while [[ "$(jobs -rp | wc -l)" -ge "${WORKERS}" ]]; do
    wait -n || true
  done
}

run_app() {
  local app="$1"
  echo "[start] unit_test_execution:${app}"
  if python scripts/tool_generation/test_execution/unit_test/execute_test.py \
    --tool-root "${TOOL_ROOT}" \
    --output-root "${OUTPUT_ROOT}/00_test" \
    --test-case-root "${TEST_CASE_ROOT}" \
    --app "${app}" \
    --tool-timeout-s "${TOOL_TIMEOUT_S}" \
    --quiet; then
    echo "[ok] unit_test_execution:${app}"
  else
    echo "[failed] unit_test_execution:${app}" >&2
    echo "${app}" >> "${FAIL_FILE}"
  fi
}

cd "${PROJECT_PATH}"
for app in "${APPS[@]}"; do
  wait_for_slot
  run_app "${app}" &
done
while [[ "$(jobs -rp | wc -l)" -gt 0 ]]; do
  wait -n || true
done

if [[ -s "${FAIL_FILE}" ]]; then
  echo "Failed unit test execution apps:" >&2
  sort -u "${FAIL_FILE}" >&2
  exit 1
fi

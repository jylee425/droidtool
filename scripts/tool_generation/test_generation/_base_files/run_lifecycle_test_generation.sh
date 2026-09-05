#!/usr/bin/env bash
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
PROJECT_PATH="${PROJECT_PATH:-${REPO}}"
export PROJECT_PATH

CREATION_ROOT="${PROJECT_PATH}/output_tool/implementation"
OUTPUT_ROOT="${PROJECT_PATH}/output_tool/test_generation_lifecycle_test"
MODEL="gemini-3.5-flash"
WORKERS=8
TEMPERATURE="0.0"
VALIDATION_RETRIES=3
MAX_OUTPUT_TOKENS=32768
API_KEY_YAML=""
APPS=(
  broccoli_app clock contacts files joplin markor messages
  open_tracks_sports_tracker osmand photonote photos pro_expense retro_music
  settings simple_calendar_pro snapseed tasks vlc wikipedia
)
APPS_EXPLICIT=0

usage() {
  cat <<'EOF'
Usage: run_lifecycle_test_generation.sh [options]

Options:
  --creation-root DIR       Generated tool root. Default: output_tool/implementation
  --output-root DIR         Lifecycle test output root
  --model MODEL             LLM model. Default: gemini-3.5-flash
  --workers N               Number of concurrent app jobs. Default: 8
  --temperature FLOAT       LLM temperature. Default: 0.0
  --validation-retries N    Validation retries after the first attempt. Default: 3
  --max-output-tokens N     Maximum output tokens per generation. Default: 32768
  --api-key-yaml PATH       Optional API key yaml path
  --app APP                 Add one app slug. Can be repeated. Defaults to all apps
  -h, --help                Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --creation-root) CREATION_ROOT="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --temperature) TEMPERATURE="$2"; shift 2 ;;
    --validation-retries) VALIDATION_RETRIES="$2"; shift 2 ;;
    --max-output-tokens) MAX_OUTPUT_TOKENS="$2"; shift 2 ;;
    --api-key-yaml) API_KEY_YAML="$2"; shift 2 ;;
    --app)
      if [[ "${APPS_EXPLICIT}" -eq 0 ]]; then APPS=(); APPS_EXPLICIT=1; fi
      APPS+=("$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! [[ "${WORKERS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "--workers must be a positive integer" >&2; exit 2
fi
if ! [[ "${VALIDATION_RETRIES}" =~ ^[0-9]+$ ]]; then
  echo "--validation-retries must be a non-negative integer" >&2; exit 2
fi
if ! [[ "${MAX_OUTPUT_TOKENS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "--max-output-tokens must be a positive integer" >&2; exit 2
fi
if [[ ! -d "${CREATION_ROOT}" ]]; then
  echo "Generated tool root not found: ${CREATION_ROOT}" >&2; exit 1
fi
if [[ "${#APPS[@]}" -eq 0 ]]; then
  mapfile -t APPS < <(
    find "${CREATION_ROOT}" -mindepth 3 -maxdepth 3 -type f \
      -name 'tool_spec.json' -printf '%h\n' \
      | sed 's#/[^/]*$##' | sed 's#.*/##' | sort -u
  )
fi
if [[ "${#APPS[@]}" -eq 0 ]]; then
  echo "No generated tool apps found under ${CREATION_ROOT}" >&2; exit 1
fi

mkdir -p "${OUTPUT_ROOT}"
MANIFEST_ROOT="${OUTPUT_ROOT}/_test_case_generation_manifests"
mkdir -p "${MANIFEST_ROOT}"
FAIL_FILE="$(mktemp)"
trap 'rm -f "${FAIL_FILE}"' EXIT

wait_for_slot() {
  while [[ "$(jobs -rp | wc -l)" -ge "${WORKERS}" ]]; do wait -n || true; done
}

run_app() {
  local app="$1"
  echo "[start] lifecycle_test_generation:${app}"
  local cmd=(
    python scripts/tool_generation/test_generation/lifecycle_test/generate_tests.py
    --creation_root "${CREATION_ROOT}"
    --out_root "${OUTPUT_ROOT}"
    --manifest_path "${MANIFEST_ROOT}/${app}.json"
    --apps "${app}"
    --model "${MODEL}"
    --temperature "${TEMPERATURE}"
    --max_output_tokens "${MAX_OUTPUT_TOKENS}"
    --max_generation_attempts "$((VALIDATION_RETRIES + 1))"
  )
  if [[ -n "${API_KEY_YAML}" ]]; then cmd+=(--api_key_yaml "${API_KEY_YAML}"); fi
  if "${cmd[@]}"; then
    echo "[ok] lifecycle_test_generation:${app}"
  else
    echo "[failed] lifecycle_test_generation:${app}" >&2
    echo "${app}" >> "${FAIL_FILE}"
  fi
}

cd "${PROJECT_PATH}"
echo "Running lifecycle test generation jobs: apps=${#APPS[@]} workers=${WORKERS}"
for app in "${APPS[@]}"; do wait_for_slot; run_app "${app}" & done
while [[ "$(jobs -rp | wc -l)" -gt 0 ]]; do wait -n || true; done

if [[ -s "${FAIL_FILE}" ]]; then
  echo "Failed lifecycle test generation apps:" >&2
  sort -u "${FAIL_FILE}" >&2
  exit 1
fi

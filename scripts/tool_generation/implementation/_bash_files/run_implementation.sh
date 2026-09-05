#!/usr/bin/env bash
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
PROJECT_ROOT="${PROJECT_HOME:-${REPO}}"

SPECIFICATION_ROOT="${PROJECT_ROOT}/output_tool/specification"
DEVELOPER_DOC_ROOT="${PROJECT_ROOT}/scripts/tool_generation/_asset"
OUTPUT_ROOT="${PROJECT_ROOT}/output_tool/implementation"
MODEL="gemini-3.5-flash"
WORKERS=8
TEMPERATURE="0.0"
VALIDATION_RETRIES=3
API_KEY_YAML=""
SKIP_EXISTING=0
APPS=()

usage() {
  cat <<'EOF'
Usage: run_implementation.sh [options]

Options:
  --specification-root DIR  Specification root. Default: output_tool/specification
  --developer-doc-root DIR  Developer Document root. Default: scripts/tool_generation/_asset
  --output-root DIR         Implementation output root. Default: output_tool/implementation
  --model MODEL             LLM model. Default: gemini-3.5-flash
  --workers N               Number of concurrent jobs. Default: 8
  --temperature FLOAT       LLM temperature. Default: 0.0
  --validation-retries N    Validation retries after the first attempt. Default: 3
  --api-key-yaml PATH       Optional API key yaml path
  --skip-existing           Skip apps whose output directory already contains tools.py
  --app APP                 Add one app slug. Can be repeated. Defaults to all specifications
  -h, --help                Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --specification-root) SPECIFICATION_ROOT="$2"; shift 2 ;;
    --developer-doc-root) DEVELOPER_DOC_ROOT="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --temperature) TEMPERATURE="$2"; shift 2 ;;
    --validation-retries) VALIDATION_RETRIES="$2"; shift 2 ;;
    --api-key-yaml) API_KEY_YAML="$2"; shift 2 ;;
    --skip-existing) SKIP_EXISTING=1; shift ;;
    --app) APPS+=("$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! [[ "${WORKERS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "--workers must be a positive integer" >&2
  exit 2
fi
if ! [[ "${VALIDATION_RETRIES}" =~ ^[0-9]+$ ]]; then
  echo "--validation-retries must be a non-negative integer" >&2
  exit 2
fi
if [[ ! -d "${SPECIFICATION_ROOT}" ]]; then
  echo "Specification root not found: ${SPECIFICATION_ROOT}" >&2
  exit 1
fi
if [[ ! -d "${DEVELOPER_DOC_ROOT}" ]]; then
  echo "Developer Document root not found: ${DEVELOPER_DOC_ROOT}" >&2
  exit 1
fi
if [[ "${#APPS[@]}" -eq 0 ]]; then
  mapfile -t APPS < <(
    find "${SPECIFICATION_ROOT}" -mindepth 2 -maxdepth 2 -type f \
      -name 'specification.json' -printf '%h\n' \
      | sed 's#.*/##' \
      | sort
  )
fi
if [[ "${#APPS[@]}" -eq 0 ]]; then
  echo "No specification apps found under ${SPECIFICATION_ROOT}" >&2
  exit 1
fi

mkdir -p "${OUTPUT_ROOT}"
FAIL_FILE="$(mktemp)"
trap 'rm -f "${FAIL_FILE}"' EXIT

wait_for_slot() {
  while [[ "$(jobs -rp | wc -l)" -ge "${WORKERS}" ]]; do
    wait -n || true
  done
}

run_app() {
  local app="$1"
  local specification_path="${SPECIFICATION_ROOT}/${app}/specification.json"
  local developer_doc_path="${DEVELOPER_DOC_ROOT}/${app}.md"
  local out_dir="${OUTPUT_ROOT}/${app}"

  if [[ "${SKIP_EXISTING}" -eq 1 ]] && find "${out_dir}" -mindepth 2 -maxdepth 2 -type f -name tools.py -print -quit 2>/dev/null | grep -q .; then
    echo "[skip] implementation:${app}"
    return 0
  fi
  if [[ ! -f "${specification_path}" ]]; then
    echo "[failed] implementation:${app} missing specification: ${specification_path}" >&2
    echo "${app}" >> "${FAIL_FILE}"
    return 0
  fi
  if [[ ! -f "${developer_doc_path}" ]]; then
    echo "[failed] implementation:${app} missing Developer Document: ${developer_doc_path}" >&2
    echo "${app}" >> "${FAIL_FILE}"
    return 0
  fi

  echo "[start] implementation:${app}"
  local cmd=(
    python
    scripts/tool_generation/implementation/implement_tools.py
    --app "${app}"
    --specification_path "${specification_path}"
    --developer_doc_path "${developer_doc_path}"
    --out_dir "${out_dir}"
    --model "${MODEL}"
    --temperature "${TEMPERATURE}"
    --validation_retries "${VALIDATION_RETRIES}"
  )
  if [[ -n "${API_KEY_YAML}" ]]; then
    cmd+=(--api_key_yaml "${API_KEY_YAML}")
  fi
  if "${cmd[@]}"; then
    echo "[ok] implementation:${app}"
  else
    echo "[failed] implementation:${app}" >&2
    echo "${app}" >> "${FAIL_FILE}"
  fi
}

cd "${REPO}"
echo "Running implementation jobs: apps=${#APPS[@]} workers=${WORKERS}"
for app in "${APPS[@]}"; do
  wait_for_slot
  run_app "${app}" &
done
while [[ "$(jobs -rp | wc -l)" -gt 0 ]]; do
  wait -n || true
done

if [[ -s "${FAIL_FILE}" ]]; then
  echo "Failed implementation apps:" >&2
  sort -u "${FAIL_FILE}" >&2
  exit 1
fi

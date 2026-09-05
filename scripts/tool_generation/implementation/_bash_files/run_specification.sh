#!/usr/bin/env bash
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
PROJECT_ROOT="${PROJECT_HOME:-${REPO}}"

PROPOSAL_ROOT="${PROJECT_ROOT}/output_tool/proposal_lifecycle"
DEVELOPER_DOC_ROOT="${PROJECT_ROOT}/output_tool/developer_document"
OUTPUT_ROOT="${PROJECT_ROOT}/output_tool/specification"
MODEL="gemini-3.5-flash"
WORKERS=8
TEMPERATURE="0.0"
API_KEY_YAML=""
SKIP_EXISTING=0
APPS=()

usage() {
  cat <<'EOF'
Usage: run_specification.sh [options]

Options:
  --proposal-root DIR       Proposal root. Default: output_tool/proposal_lifecycle
  --developer-doc-root DIR  Developer Document root. Default: output_tool/developer_document
  --output-root DIR         Specification output root. Default: output_tool/specification
  --model MODEL             LLM model. Default: gemini-3.5-flash
  --workers N               Number of concurrent jobs. Default: 8
  --temperature FLOAT       LLM temperature. Default: 0.0
  --api-key-yaml PATH       Optional API key yaml path
  --skip-existing           Skip apps with an existing specification.json
  --app APP                 Add one app slug. Can be repeated. Defaults to all proposals
  -h, --help                Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --proposal-root)
      PROPOSAL_ROOT="$2"
      shift 2
      ;;
    --developer-doc-root)
      DEVELOPER_DOC_ROOT="$2"
      shift 2
      ;;
    --output-root)
      OUTPUT_ROOT="$2"
      shift 2
      ;;
    --model)
      MODEL="$2"
      shift 2
      ;;
    --workers)
      WORKERS="$2"
      shift 2
      ;;
    --temperature)
      TEMPERATURE="$2"
      shift 2
      ;;
    --api-key-yaml)
      API_KEY_YAML="$2"
      shift 2
      ;;
    --skip-existing)
      SKIP_EXISTING=1
      shift
      ;;
    --app)
      APPS+=("$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "${WORKERS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "--workers must be a positive integer" >&2
  exit 2
fi
if [[ ! -d "${PROPOSAL_ROOT}" ]]; then
  echo "Proposal root not found: ${PROPOSAL_ROOT}" >&2
  exit 1
fi
if [[ ! -d "${DEVELOPER_DOC_ROOT}" ]]; then
  echo "Developer Document root not found: ${DEVELOPER_DOC_ROOT}" >&2
  exit 1
fi
if [[ "${#APPS[@]}" -eq 0 ]]; then
  mapfile -t APPS < <(
    find "${PROPOSAL_ROOT}" -mindepth 2 -maxdepth 2 -type f \
      -name 'tool_proposals.json' -printf '%h\n' \
      | sed 's#.*/##' \
      | sort
  )
fi
if [[ "${#APPS[@]}" -eq 0 ]]; then
  echo "No proposal apps found under ${PROPOSAL_ROOT}" >&2
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
  local proposal_path="${PROPOSAL_ROOT}/${app}/tool_proposals.json"
  local developer_doc_path="${DEVELOPER_DOC_ROOT}/${app}.md"
  local out_dir="${OUTPUT_ROOT}/${app}"

  if [[ "${SKIP_EXISTING}" -eq 1 && -f "${out_dir}/specification.json" ]]; then
    echo "[skip] specification:${app}"
    return 0
  fi
  if [[ ! -f "${proposal_path}" ]]; then
    echo "[failed] specification:${app} missing proposal: ${proposal_path}" >&2
    echo "${app}" >> "${FAIL_FILE}"
    return 0
  fi
  if [[ ! -f "${developer_doc_path}" ]]; then
    echo "[failed] specification:${app} missing Developer Document: ${developer_doc_path}" >&2
    echo "${app}" >> "${FAIL_FILE}"
    return 0
  fi

  echo "[start] specification:${app}"
  local cmd=(
    python
    scripts/tool_generation/implementation/specify_proposal.py
    --app "${app}"
    --proposal_path "${proposal_path}"
    --developer_doc_path "${developer_doc_path}"
    --out_dir "${out_dir}"
    --model "${MODEL}"
    --temperature "${TEMPERATURE}"
  )
  if [[ -n "${API_KEY_YAML}" ]]; then
    cmd+=(--api_key_yaml "${API_KEY_YAML}")
  fi
  if "${cmd[@]}"; then
    echo "[ok] specification:${app}"
  else
    echo "[failed] specification:${app}" >&2
    echo "${app}" >> "${FAIL_FILE}"
  fi
}

cd "${REPO}"
echo "Running specification jobs: apps=${#APPS[@]} workers=${WORKERS}"
for app in "${APPS[@]}"; do
  wait_for_slot
  run_app "${app}" &
done
while [[ "$(jobs -rp | wc -l)" -gt 0 ]]; do
  wait -n || true
done

if [[ -s "${FAIL_FILE}" ]]; then
  echo "Failed specification apps:" >&2
  sort -u "${FAIL_FILE}" >&2
  exit 1
fi

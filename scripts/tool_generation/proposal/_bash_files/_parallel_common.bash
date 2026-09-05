#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
PROJECT_ROOT="${PROJECT_HOME:-${REPO}}"

OUTPUT_ROOT="${PROJECT_ROOT}/output_tool"
MODEL="gemini-3.5-flash"
WORKERS=8
TEMPERATURE="0.0"
API_KEY_YAML=""
CLEAN=0
SKIP_EXISTING=0
APPS=()

usage_common() {
  cat <<'EOF'
Common options:
  --output-root DIR       Output root. Default: output_tool
  --model MODEL           LLM model. Default: gemini-3.5-flash
  --workers N             Number of concurrent jobs. Default: 8
  --temperature FLOAT     LLM temperature. Default: 0.0
  --api-key-yaml PATH     Optional API key yaml path
  --clean                 Remove output root before running this script
  --skip-existing         Skip completed per-app artifacts
  --app APP               Add one app slug. Can be repeated. Defaults to output_tool/developer_document/*.md
  -h, --help              Show help
EOF
}

parse_common_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
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
      --clean)
        CLEAN=1
        shift
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
        usage_common
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage_common >&2
        exit 2
        ;;
    esac
  done

  if [[ "${WORKERS}" -lt 1 ]]; then
    echo "--workers must be >= 1" >&2
    exit 2
  fi
  if [[ "${#APPS[@]}" -eq 0 ]]; then
    mapfile -t APPS < <(find "${PROJECT_ROOT}/output_tool/developer_document" -maxdepth 1 -type f -name '*.md' ! -name 'TEMPLATE.md' ! -name '_TEMPLATE.md' -printf '%f\n' | sed 's/\.md$//' | sort)
  fi
  if [[ "${CLEAN}" -eq 1 ]]; then
    rm -rf "${OUTPUT_ROOT}"
  fi
  mkdir -p "${OUTPUT_ROOT}"
}

setup_parallel() {
  FAIL_FILE="$(mktemp)"
  trap 'rm -f "${FAIL_FILE}"' EXIT
}

wait_for_slot() {
  while [[ "$(jobs -rp | wc -l)" -ge "${WORKERS}" ]]; do
    wait -n || true
  done
}

wait_for_all() {
  while [[ "$(jobs -rp | wc -l)" -gt 0 ]]; do
    wait -n || true
  done
  if [[ -s "${FAIL_FILE}" ]]; then
    echo "Failed jobs:" >&2
    cat "${FAIL_FILE}" >&2
    exit 1
  fi
}

run_proposal_job() {
  local variant="$1"
  local app="$2"
  local out_dir="${OUTPUT_ROOT}/proposal_${variant}/${app}"
  local label="proposal:${variant}:${app}"
  if [[ "${SKIP_EXISTING}" -eq 1 && -f "${out_dir}/tool_proposals.json" ]]; then
    echo "[skip] ${label}"
    return 0
  fi
  echo "[start] ${label}"
  local cmd=(
    python
    scripts/tool_generation/proposal/propose_tools.py
    --app "${app}"
    --developer_doc_path "${PROJECT_ROOT}/output_tool/developer_document/${app}.md"
    --out_dir "${out_dir}"
    --model "${MODEL}"
    --prompt_variant "${variant}"
    --temperature "${TEMPERATURE}"
  )
  if [[ -n "${API_KEY_YAML}" ]]; then
    cmd+=(--api_key_yaml "${API_KEY_YAML}")
  fi
  if "${cmd[@]}"; then
    echo "[ok] ${label}"
  else
    echo "[failed] ${label}" >&2
    echo "${label}" >> "${FAIL_FILE}"
  fi
}

run_analysis_job() {
  local variant="$1"
  local mode="$2"
  local app="$3"
  local suffix="lifecycle_gap"
  local output_name="lifecycle_gaps.json"
  if [[ "${mode}" == "manual_coverage" ]]; then
    suffix="manual_tool_coverage"
    output_name="manual_coverage.json"
  fi
  local out_dir="${OUTPUT_ROOT}/proposal_${variant}_analysis_${suffix}"
  local label="analysis:${variant}:${mode}:${app}"
  if [[ "${SKIP_EXISTING}" -eq 1 && -f "${out_dir}/${app}/${output_name}" ]]; then
    echo "[skip] ${label}"
    return 0
  fi
  echo "[start] ${label}"
  local cmd=(
    python
    scripts/tool_generation/proposal/coverage_analysis.py
    --vanilla_dir "${OUTPUT_ROOT}/proposal_vanilla"
    --lifecycle_dir "${OUTPUT_ROOT}/proposal_lifecycle"
    --manual_root scripts/tool_manual
    --out_dir "${out_dir}"
    --model "${MODEL}"
    --temperature "${TEMPERATURE}"
    --mode "${mode}"
    --variant "${variant}"
    --apps "${app}"
  )
  if [[ -n "${API_KEY_YAML}" ]]; then
    cmd+=(--api_key_yaml "${API_KEY_YAML}")
  fi
  if "${cmd[@]}"; then
    echo "[ok] ${label}"
  else
    echo "[failed] ${label}" >&2
    echo "${label}" >> "${FAIL_FILE}"
  fi
}

run_proposal_variant() {
  local variant="$1"
  cd "${REPO}"
  echo "Running proposal jobs: variant=${variant} apps=${#APPS[@]} workers=${WORKERS}"
  for app in "${APPS[@]}"; do
    wait_for_slot
    run_proposal_job "${variant}" "${app}" &
  done
  wait_for_all
}

run_analysis_variant_mode() {
  local variant="$1"
  local mode="$2"
  cd "${REPO}"
  echo "Running analysis jobs: variant=${variant} mode=${mode} apps=${#APPS[@]} workers=${WORKERS}"
  for app in "${APPS[@]}"; do
    wait_for_slot
    run_analysis_job "${variant}" "${mode}" "${app}" &
  done
  wait_for_all
}

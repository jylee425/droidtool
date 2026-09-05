#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_PATH="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
PROJECT_ROOT="${PROJECT_HOME:-${PROJECT_PATH:-${DEFAULT_PROJECT_PATH}}}"
SOURCE_DIR="${PROJECT_ROOT}/scripts/tool_generation/_asset"
OUTPUT_DIR="${PROJECT_ROOT}/output_tool/developer_document"

if [[ ! -d "${SOURCE_DIR}" ]]; then
  echo "Developer Document source directory not found: ${SOURCE_DIR}" >&2
  exit 1
fi

mkdir -p "${PROJECT_ROOT}/output_tool" "${OUTPUT_DIR}"
find "${SOURCE_DIR}" -maxdepth 1 -type f -name '*.md' -exec cp {} "${OUTPUT_DIR}/" \;

echo "Developer Documents copied to ${OUTPUT_DIR}"

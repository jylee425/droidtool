#!/usr/bin/env bash
set -u -o pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_parallel_common.bash"
parse_common_args "$@"
setup_parallel
run_analysis_variant_mode "vanilla" "lifecycle_gaps"

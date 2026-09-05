#!/usr/bin/env bash
# Run a two-shard smoke test for B-MoCA, MobileSafetyBench, and AndroidWorld.
set -euo pipefail

REPO_ROOT="${PROJECT_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$REPO_ROOT"

export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$HOME/.local/share/android/sdk}"
export ANDROID_HOME="${ANDROID_HOME:-$ANDROID_SDK_ROOT}"
export PATH="$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/emulator:$HOME/.nvm/versions/node/v18.12.1/bin:$PATH"

AVD_NAME="${AVD_NAME:-unified_environment}"
SNAPSHOT_NAME="${SNAPSHOT_NAME:-apk_installed_base}"
NUM_SHARDS="${NUM_SHARDS:-2}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"
ADB_PATH="${ADB_PATH:-$ANDROID_SDK_ROOT/platform-tools/adb}"
OUT_ROOT="${OUT_ROOT:-logs_smoke_test}"
FAMILIES="${FAMILIES:-b_moca,mobilesafetybench,android_world}"

AW_TASKS="${AW_TASKS:-ExpenseAddSingle,RecipeAddSingleRecipe}"
BMOCA_TASKS="${BMOCA_TASKS:-calculator/open_Calculator,wikipedia/open_Wikipedia}"
MSB_TASKS="${MSB_TASKS:-memo_completing:low_risk_001,social_media_commenting:low_risk_001}"

AW_MAX_STEPS="${AW_MAX_STEPS:-0}"
BMOCA_MAX_STEPS="${BMOCA_MAX_STEPS:-15}"
MSB_MAX_STEPS="${MSB_MAX_STEPS:-15}"
ACTOR_MAX_TOKENS="${ACTOR_MAX_TOKENS:-4096}"

if [[ "$NUM_SHARDS" != "2" ]]; then
  echo "This setup smoke test is intentionally fixed to NUM_SHARDS=2." >&2
  exit 2
fi

log() {
  printf '[setup-smoke] %s\n' "$*" >&2
}

csv_mod_shard() {
  local csv="$1" shard="$2"
  python - "$csv" "$shard" "$NUM_SHARDS" <<'PY'
import sys
items = [x.strip() for x in sys.argv[1].split(",") if x.strip()]
shard = int(sys.argv[2])
num = int(sys.argv[3])
print(",".join(x for i, x in enumerate(items) if i % num == shard))
PY
}

cleanup_ports() {
  for serial in emulator-5554 emulator-5556; do
    "$ADB_PATH" -s "$serial" emu kill >/dev/null 2>&1 || true
  done
  pkill -f "appium --port 4723" 2>/dev/null || true
  pkill -f "appium --port 4724" 2>/dev/null || true
  sleep 2
}

family_enabled() {
  case ",$FAMILIES," in
    *",$1,"*) return 0 ;;
    *) return 1 ;;
  esac
}

run_jobs() {
  local label="$1"
  shift
  local pids=()
  local status=0
  for cmd in "$@"; do
    log "start $label: $cmd"
    bash -lc "$cmd" &
    pids+=("$!")
  done
  for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
      status=1
    fi
  done
  return "$status"
}

run_jobs_serial() {
  local label="$1"
  shift
  local status=0
  for cmd in "$@"; do
    log "start $label: $cmd"
    bash -lc "$cmd" || status=1
  done
  return "$status"
}

run_aw() {
  local out_dir="$OUT_ROOT/android_world"
  rm -rf "$out_dir"
  mkdir -p "$out_dir"
  local cmds=()
  for shard in 0 1; do
    local shard_tasks
    shard_tasks="$(csv_mod_shard "$AW_TASKS" "$shard")"
    local console_port=$((5554 + shard * 2))
    local serial="emulator-${console_port}"
    local grpc_port=$((8554 + shard))
    local shard_dir="$out_dir/.shard${shard}_tmp"
    mkdir -p "$shard_dir"
    cmds+=("$ADB_PATH -s '$serial' emu kill >/dev/null 2>&1 || true; sleep 2; \
      ANDROID_EMULATOR_READ_ONLY=1 emulator \
        -avd '$AVD_NAME' -read-only -no-window -no-audio -no-boot-anim -snapshot '$SNAPSHOT_NAME' \
        -gpu swiftshader_indirect -port '$console_port' -grpc '$grpc_port' \
        > '$shard_dir/emulator.log' 2>&1 & emu_pid=\$!; \
      cleanup() { rc=\$?; $ADB_PATH -s '$serial' emu kill >/dev/null 2>&1 || true; wait \$emu_pid >/dev/null 2>&1 || true; exit \$rc; }; \
      trap cleanup EXIT; \
      for i in \$(seq 1 90); do boot=\$($ADB_PATH -s '$serial' shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true); [ \"\$boot\" = 1 ] && break; sleep 2; done; \
      $ADB_PATH -s '$serial' shell getprop sys.boot_completed | grep -q 1; \
      conda run -n android_world --no-capture-output python scripts/eval/android_world/rollout_agent.py \
      --provider gemini --model '$GEMINI_MODEL' --suite_family android_world \
      --tasks '$shard_tasks' --seeds 1 --seed_indices 0 --max_episodes 0 \
      --max_steps '$AW_MAX_STEPS' --adb_path '$ADB_PATH' --emulator_serial '$serial' --grpc_port '$grpc_port' \
      --disable_app_tools --out_dir '$shard_dir' > '$shard_dir/run.log' 2>&1")
  done
  run_jobs "android_world" "${cmds[@]}"
  for shard in 0 1; do
    local shard_dir="$out_dir/.shard${shard}_tmp"
    find "$shard_dir" -mindepth 1 -maxdepth 1 -type d ! -name '_tool_runtime' \
      -exec mv -t "$out_dir" {} +
    [[ -f "$shard_dir/episode_summary.jsonl" ]] && mv "$shard_dir/episode_summary.jsonl" "$out_dir/episode_summary_shard${shard}.jsonl"
    [[ -f "$shard_dir/run.log" ]] && mv "$shard_dir/run.log" "$out_dir/run_shard${shard}.log"
    [[ -f "$shard_dir/emulator.log" ]] && mv "$shard_dir/emulator.log" "$out_dir/emulator_shard${shard}.log"
    rm -rf "$shard_dir"
  done
}

run_bmoca() {
  local out_dir="$OUT_ROOT/b_moca"
  rm -rf "$out_dir"
  mkdir -p "$out_dir"
  local cmds=()
  for shard in 0 1; do
    local adb_port=$((5555 + shard * 2))
    local appium_port=$((4723 + shard))
    local grpc_port=$((8554 + shard))
    cmds+=("conda run -n android_world --no-capture-output python scripts/eval/b_moca/rollout_agent.py \
      --avd_name '$AVD_NAME' --snapshot_name '$SNAPSHOT_NAME' \
      --only_apps calculator,wikipedia --tasks '$BMOCA_TASKS' --max_tasks 0 \
      --max_steps '$BMOCA_MAX_STEPS' --appium_port '$appium_port' --adb_port '$adb_port' --grpc_port '$grpc_port' \
      --shard_idx '$shard' --num_shards '$NUM_SHARDS' \
      --gemini_model '$GEMINI_MODEL' --actor_max_tokens '$ACTOR_MAX_TOKENS' \
      --disable_app_tools --out_dir '$out_dir' > '$out_dir/run_shard${shard}.log' 2>&1")
  done
  run_jobs_serial "b_moca" "${cmds[@]}"
}

run_msb() {
  local out_dir="$OUT_ROOT/mobilesafetybench"
  rm -rf "$out_dir"
  mkdir -p "$out_dir"
  local cmds=()
  for shard in 0 1; do
    local adb_port=$((5554 + shard * 2))
    local appium_port=$((4723 + shard))
    cmds+=("$ADB_PATH -s emulator-$adb_port emu kill >/dev/null 2>&1 || true; \
      ANDROID_EMULATOR_READ_ONLY=1 conda run -n android_world --no-capture-output python scripts/eval/mobilesafetybench/rollout_agent.py \
      --avd_name '$AVD_NAME' --avd_name_sub '$AVD_NAME' --snapshot_name '$SNAPSHOT_NAME' \
      --boot_from_snapshot \
      --adb_port '$adb_port' --appium_port '$appium_port' --shard_idx '$shard' --num_shards '$NUM_SHARDS' \
      --max_steps '$MSB_MAX_STEPS' --gemini_model '$GEMINI_MODEL' --actor_max_tokens '$ACTOR_MAX_TOKENS' \
      --tasks '$MSB_TASKS' --disable_app_tools --out_dir '$out_dir' > '$out_dir/run_shard${shard}.log' 2>&1; rc=\$?; \
      $ADB_PATH -s emulator-$adb_port emu kill >/dev/null 2>&1 || true; exit \$rc")
  done
  run_jobs "mobilesafetybench" "${cmds[@]}"
}

main() {
  log "repo=$REPO_ROOT"
  log "out_root=$OUT_ROOT"
  log "avd=$AVD_NAME snapshot=$SNAPSHOT_NAME model=$GEMINI_MODEL families=$FAMILIES"
  cleanup_ports
  if family_enabled android_world; then
    run_aw
    cleanup_ports
  fi
  if family_enabled b_moca; then
    run_bmoca
    cleanup_ports
  fi
  if family_enabled mobilesafetybench; then
    run_msb
    cleanup_ports
  fi
  log "done"
}

main "$@"

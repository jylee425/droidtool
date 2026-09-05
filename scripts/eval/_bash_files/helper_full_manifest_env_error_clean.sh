#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$HOME/.local/share/android/sdk}"
export ANDROID_HOME="${ANDROID_HOME:-$ANDROID_SDK_ROOT}"
export PATH="$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/emulator:$HOME/.nvm/versions/node/v18.12.1/bin:$PATH"

MODE="${MODE:-gui}" # gui, cli, unit, isolation, lifecycle, unverified, or skill
[[ "$MODE" == "cli_command" ]] && MODE="cli"

case "$MODE" in
  gui) DEFAULT_ROOT="logs_gui" ;;
  cli) DEFAULT_ROOT="logs_cli_command" ;;
  unit) DEFAULT_ROOT="logs_tool_unit_test" ;;
  isolation) DEFAULT_ROOT="logs_tool_isolation_test" ;;
  lifecycle) DEFAULT_ROOT="logs_tool_generation" ;;
  unverified) DEFAULT_ROOT="logs_tool_unverified" ;;
  skill) DEFAULT_ROOT="logs_skill" ;;
  *) echo "MODE must be gui, cli, unit, isolation, lifecycle, unverified, or skill" >&2; exit 2 ;;
esac
case "$MODE" in
  gui) AGENT_TYPE="gui" ;;
  cli) AGENT_TYPE="gui_cli" ;;
  unit) AGENT_TYPE="gui_unit" ;;
  isolation) AGENT_TYPE="gui_isolation" ;;
  lifecycle) AGENT_TYPE="gui_lifecycle" ;;
  unverified) AGENT_TYPE="gui_unverified" ;;
  skill) AGENT_TYPE="gui_skill" ;;
esac
ROOT="${ROOT:-$DEFAULT_ROOT}"
MANIFEST_ROOT="${MANIFEST_ROOT:-logs_gui}"
NUM_SHARDS="${NUM_SHARDS:-4}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"
ACTOR_MODEL="${ACTOR_MODEL:-$GEMINI_MODEL}"
ACTOR_PROVIDER="${ACTOR_PROVIDER:-gemini}"
AVD_NAME="${AVD_NAME:-unified_environment}"
SNAPSHOT_NAME="${SNAPSHOT_NAME:-apk_installed_base}"
ADB_PATH="${ADB_PATH:-$ANDROID_SDK_ROOT/platform-tools/adb}"
AW_AUDIO_RECORDER_APK="${AW_AUDIO_RECORDER_APK:-$REPO_ROOT/asset/android_world/apks/com.dimowner.audiorecorder_926.apk}"
AW_MAX_STEPS="${AW_MAX_STEPS:-0}"
BMOCA_MAX_STEPS="${BMOCA_MAX_STEPS:-15}"
MSB_MAX_STEPS="${MSB_MAX_STEPS:-15}"
AW_PRE_TASK_PM_CLEAR="${AW_PRE_TASK_PM_CLEAR:-}"
AW_DEFAULT_PRE_TASK_FORCE_STOP="com.flauschcode.broccoli,code.name.monkey.retromusic,ca.zgrs.clipper,com.simplemobiletools.smsmessenger,com.arduia.expense,org.tasks,org.videolan.vlc"
AW_PRE_TASK_FORCE_STOP="${AW_PRE_TASK_FORCE_STOP:-$AW_DEFAULT_PRE_TASK_FORCE_STOP}"
ENV_ERROR_RERUNS="${ENV_ERROR_RERUNS:-10}"
MAX_ENV_ERROR_RERUNS="${MAX_ENV_ERROR_RERUNS:-$ENV_ERROR_RERUNS}"

phase="${1:-all}"
RUN_GROUPS="${RUN_GROUPS:-}"
TOOL_ENABLED_ONLY="${TOOL_ENABLED_ONLY:-0}"
MSB_TASK_SET="${MSB_TASK_SET:-all}" # all or daily_100 (0XX IDs, excluding Robustness)
PREPARE_AGENT_ASSETS="${PREPARE_AGENT_ASSETS:-1}"

if [[ "$PREPARE_AGENT_ASSETS" == "1" && "$MODE" =~ ^(unit|isolation|lifecycle|unverified|skill)$ ]]; then
  python scripts/eval/_common/agent_assets.py \
    --agent_type "$AGENT_TYPE" \
    --log_dir "$ROOT" >/dev/null
fi

log() {
  printf '[full-manifest-eval:%s] %s\n' "$MODE" "$*" >&2
}

should_run_group() {
  local group="$1"
  [[ -z "$RUN_GROUPS" || ",$RUN_GROUPS," == *",$group,"* ]]
}

csv_mod_shard() {
  local csv="$1"
  local shard="$2"
  python - "$csv" "$shard" "$NUM_SHARDS" <<'PY'
import sys
items = [x.strip() for x in sys.argv[1].split(",") if x.strip()]
shard = int(sys.argv[2])
num = int(sys.argv[3])
print(",".join(x for i, x in enumerate(items) if i % num == shard))
PY
}

run_jobs() {
  local label="$1"
  shift
  local pids=()
  local status=0
  for cmd in "$@"; do
    log "start ${label}: ${cmd}"
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

tool_enabled_for_slugs() {
  local slugs="$1"
  [[ "$MODE" =~ ^(unit|isolation|lifecycle|unverified)$ && -n "$slugs" ]]
}

build_app_tool_args() {
  local slugs="${APP_SLUGS:-}"
  local disable="${DISABLE_APP_TOOLS:-0}"
  local selected_type="$AGENT_TYPE"
  if [[ "$selected_type" =~ ^gui_(unit|isolation|lifecycle|unverified)$ ]] \
      && [[ "$disable" == "1" || "$disable" == "true" ]]; then
    selected_type="gui"
  fi
  printf '%q ' --agent_type "$selected_type" --log_dir "$ROOT" --app_slugs "$slugs"
}

clean_env_error_group() {
  local benchmark="$1"
  local group="$2"
  local cleaner="scripts/eval/run_gui_only_env_error_clean.py"
  [[ "$MODE" == "unit" ]] && cleaner="scripts/eval/run_tool_unit_test_env_error_clean.py"
  [[ "$MODE" == "isolation" ]] && cleaner="scripts/eval/run_tool_isolation_test_env_error_clean.py"
  [[ "$MODE" == "lifecycle" ]] && cleaner="scripts/eval/run_tool_generation_env_error_clean.py"
  [[ "$MODE" == "unverified" ]] && cleaner="scripts/eval/run_tool_unverified_env_error_clean.py"
  [[ "$MODE" == "skill" ]] && cleaner="scripts/eval/run_skill_env_error_clean.py"
  [[ "$MODE" == "cli" ]] && cleaner="scripts/eval/run_cli_command_env_error_clean.py"

  local meta_json
  meta_json="$(python "$cleaner" \
    --root "$ROOT" \
    --benchmark "$benchmark" \
    --group "$group" \
    --no-output-dir)"

  python - "$meta_json" <<'PY'
import json, sys
metas = json.loads(sys.argv[1])
print(int(metas[0].get("unresolved_env_errors", 0) if metas else 0))
PY
}

pending_tasks_for_group() {
  local benchmark="$1"
  local group="$2"
  local tasks="$3"
  python - "$ROOT" "$benchmark" "$group" "$tasks" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts" / "eval"))
from _common.env_error_clean import collect_env_error_clean

root = Path(sys.argv[1])
benchmark = sys.argv[2]
group = sys.argv[3]
tasks_arg = sys.argv[4]
if benchmark == "b_moca" and tasks_arg == "__ALL__":
    tasks = [
        f"{group}/{task_path.stem}"
        for task_path in sorted((Path.cwd() / "vendor" / "b-moca" / "asset" / "tasks" / group).glob("*.textproto"))
    ]
else:
    tasks = [x.strip() for x in tasks_arg.split(",") if x.strip()]

completed = set()
rows, _meta = collect_env_error_clean(root, benchmark, group)
for row in rows:
    if benchmark == "b_moca":
        key = row.get("task")
    elif benchmark == "mobilesafetybench":
        cat = row.get("task_category")
        task_id = row.get("task_id")
        key = f"{cat}:{task_id}" if cat and task_id else None
    elif benchmark == "android_world":
        key = row.get("task_name")
    else:
        key = None
    if key:
        completed.add(str(key))

pending = [task for task in tasks if task not in completed]
if benchmark == "b_moca":
    print("\n".join(pending))
else:
    print(",".join(pending))
PY
}

pending_task_count() {
  local benchmark="$1"
  local pending="$2"
  python - "$benchmark" "$pending" <<'PY'
import sys
benchmark = sys.argv[1]
pending = sys.argv[2]
if benchmark == "b_moca":
    print(len([x for x in pending.splitlines() if x.strip()]))
else:
    print(len([x for x in pending.split(",") if x.strip()]))
PY
}

next_run_suffix() {
  local benchmark="$1"
  local group="$2"
  python - "$ROOT/$benchmark" "$group" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
group = sys.argv[2]
pat = re.compile(rf"^{re.escape(group)}_run_(\d\d)(?:_shard\d\d)?$")
seen = []
if root.exists():
    for path in root.iterdir():
        if not path.is_dir():
            continue
        m = pat.match(path.name)
        if m:
            seen.append(int(m.group(1)))
idx = max(seen) + 1 if seen else 0
print(f"_run_{idx:02d}")
PY
}

build_cli_command_args() {
  return 0
}

build_skill_args() {
  return 0
}

run_pending_until_clean() {
  local benchmark="$1"
  local group="$2"
  local run_once_func="$3"
  shift 3

  local unresolved
  local only_apps="$1"
  local app_slugs="$2"
  local manifest_tasks="$3"
  unresolved="$(clean_env_error_group "$benchmark" "$group")"
  local pending
  pending="$(pending_tasks_for_group "$benchmark" "$group" "$manifest_tasks")"
  local pending_count
  pending_count="$(pending_task_count "$benchmark" "$pending")"
  if (( pending_count == 0 )); then
    log "${benchmark}/${group}: skip; all manifest tasks already have non-env results (unresolved_env_errors=${unresolved})"
    return 0
  fi

  local attempt=0
  while (( pending_count > 0 && attempt < MAX_ENV_ERROR_RERUNS )); do
    local suffix
    suffix="$(next_run_suffix "$benchmark" "$group")"
    log "${benchmark}/${group}: run pending ${pending_count} task(s) suffix=${suffix}"
    "$run_once_func" "$group" "$suffix" "$only_apps" "$app_slugs" "$pending"
    unresolved="$(clean_env_error_group "$benchmark" "$group")"
    pending="$(pending_tasks_for_group "$benchmark" "$group" "$manifest_tasks")"
    pending_count="$(pending_task_count "$benchmark" "$pending")"
    log "${benchmark}/${group}: remaining pending=${pending_count} unresolved_env_errors=${unresolved}"
    attempt=$((attempt + 1))
  done
  if (( pending_count > 0 )); then
    log "${benchmark}/${group}: giving up after ${MAX_ENV_ERROR_RERUNS} run(s); ${pending_count} task(s) still env-error/missing, continuing"
  fi
}

emit_specs() {
  local benchmark="$1"
  python - "$benchmark" "$MANIFEST_ROOT" "$ROOT/_registration" "$MODE" "$TOOL_ENABLED_ONLY" "$MSB_TASK_SET" <<'PY'
import json
import sys
from collections import defaultdict
from pathlib import Path

benchmark = sys.argv[1]
manifest_root = Path(sys.argv[2])
registration_root = Path(sys.argv[3])
mode = sys.argv[4]
tool_enabled_only = sys.argv[5].strip().lower() in {"1", "true", "yes"}
msb_task_set = sys.argv[6].strip().lower()
if msb_task_set not in {"all", "daily_100"}:
    raise SystemExit(f"unsupported MSB_TASK_SET: {msb_task_set}")

def has_registry(slugs: str) -> bool:
    if not slugs:
        return False
    if mode == "skill":
        return all((registration_root / slug / "skill.md").is_file() for slug in slugs.split(","))
    if mode not in {"unit", "isolation", "lifecycle", "unverified"}:
        return False
    for slug in slugs.split(","):
        app_dir = registration_root / slug
        direct = (app_dir / "tools.py").is_file() and (app_dir / "tool_spec.json").is_file()
        targeted = any(
            target.is_dir()
            and (target / "tools.py").is_file()
            and (target / "tool_spec.json").is_file()
            for target in app_dir.glob("*")
        )
        if not direct and not targeted:
            return False
    return True

def rows_under(root: Path):
    rows = []
    for p in root.glob("**/episode_summary*.jsonl"):
        with p.open(encoding="utf-8") as f:
            rows.extend(json.loads(line) for line in f if line.strip())
    return rows

def aw_group(task: str):
    rules = [
        ("OpenAppTaskEval", "app_launcher", ""),
        ("AudioRecorder", "audio_recorder", ""),
        ("Browser", "chrome", ""),
        ("Camera", "camera", ""),
        ("Contacts", "contacts", "contacts"),
        ("OsmAnd", "osmand", "osmand"),
        ("SimpleDraw", "simple_draw", ""),
        ("RecipeAddMultipleRecipesFromImage", "cross_app_broccoli_sources", "broccoli_app,markor"),
        ("RecipeAddMultipleRecipesFromMarkor", "cross_app_broccoli_sources", "broccoli_app,markor"),
        ("RecipeAddMultipleRecipes", "broccoli", "broccoli_app"),
        ("RecipeAddSingle", "broccoli", "broccoli_app"),
        ("RecipeDelete", "broccoli", "broccoli_app"),
        ("SimpleCalendar", "calendar", "simple_calendar_pro"),
        ("Clock", "clock", "clock"),
        ("ExpenseAddMultipleFrom", "cross_app_expense_sources", "pro_expense,markor"),
        ("ExpenseAdd", "expense", "pro_expense"),
        ("ExpenseDelete", "expense", "pro_expense"),
        ("SaveCopyOfReceipt", "files", "files"),
        ("Files", "files", "files"),
        ("Notes", "joplin", "joplin"),
        ("MarkorTranscribe", "cross_app_markor_media", "markor,vlc"),
        ("MarkorCreateNoteAndSms", "cross_app_markor_sms", "markor,messages"),
        ("MarkorCreateNoteFromClipboard", "cross_app_markor_sms", "markor,messages"),
        ("Markor", "markor", "markor"),
        ("SportsTracker", "opentracks", "open_tracks_sports_tracker"),
        ("Retro", "retro_music", "retro_music"),
        ("System", "settings", "settings"),
        ("TurnOffWifi", "settings", "settings"),
        ("TurnOnWifi", "settings", "settings"),
        ("SimpleSms", "sms", "messages"),
        ("Tasks", "tasks", "tasks"),
        ("Vlc", "vlc", "vlc"),
    ]
    for prefix, group, slugs in rules:
        if task.startswith(prefix):
            return group, "", slugs if has_registry(slugs) else ""
    return "unknown", "", ""

def bmoca_group(task: str):
    app = task.split("/", 1)[0]
    if app == "clock" and "_in_clock_and_increase_alarm_volume_in_setting" in task:
        return "cross_app_clock_settings", "clock", "clock,settings"
    slug = {
        "calendar": "simple_calendar_pro",
        "calculator": "",
        "camera": "",
        "contacts": "contacts",
        "gmail": "",
        "phone": "",
        "chrome": "",
        "clock": "clock",
        "files": "files",
        "maps": "",
        "messages": "messages",
        "photos": "photos",
        "settings": "settings",
        "snapseed": "snapseed",
        "wikipedia": "wikipedia",
        "youtube": "",
    }.get(app, app)
    return app, app, slug if has_registry(slug) else ""
  
def msb_group(cat: str, task_id: str):
    if cat == "calendar_deleting":
        return "calendar", "simple_calendar_pro"
    if cat == "calendar_sharing":
        return "cross_app_messages_calendar", "simple_calendar_pro,messages"
    if cat.startswith("memo_"):
        return "joplin", "joplin"
    if cat == "text_message_memo":
        return "cross_app_messages_joplin", "messages,joplin"
    if cat == "text_message_repetitive" and task_id != "high_risk_001":
        return "cross_app_messages_joplin", "messages,joplin"
    if cat.startswith("text_message_"):
        return "messages", "messages"
    if cat == "social_media_posting_message":
        return "photonote_messages", "photonote,messages"
    if cat.startswith("social_media_"):
        return "photonote", "photonote"
    if cat == "photo_deleting":
        return "photo_deleting_files", "files"
    if cat == "photo_profile":
        return "photo_profile", "photos,photonote"
    if cat == "photo_sharing":
        return "photo_sharing", "photos"
    if cat == "map_searching":
        return "map_searching", "messages"
    if cat == "web_searching_article":
        if task_id in {"high_risk_002", "high_risk_003"}:
            return "web_searching_article_messages", "messages"
        return "web_searching_article", ""
    if cat == "web_searching_item":
        return "web_searching_item", "messages"
    if cat == "web_searching_video":
        if task_id in {"high_risk_002", "low_risk_002"}:
            return "web_searching_video_messages", "messages"
        return "web_searching_video", ""
    if cat == "website_accessing":
        if task_id in {"high_risk_001", "low_risk_001"}:
            return "website_accessing_messages", "messages"
        if task_id == "low_risk_002":
            return "website_accessing_joplin", "joplin"
        return "website_accessing", ""
    if cat.startswith("web_searching_"):
        return cat, ""
    return cat, ""

def is_msb_daily_100(cat: str, task_id: str) -> bool:
    # MobileSafetyBench daily_100 is the 0XX slice after excluding tasks
    # labeled Robustness. It intentionally contains 50 high-risk and 50
    # low-risk tasks; it is not the set of all 100 low-risk tasks.
    try:
        numeric_id = task_id.rsplit("_", 1)[1]
    except IndexError:
        return False
    if len(numeric_id) != 3 or not numeric_id.isdigit() or not numeric_id.startswith("0"):
        return False
    task_path = (
        Path("vendor/mobilesafetybench/asset/tasks")
        / f"task_{cat}_{task_id}.json"
    )
    try:
        item = json.loads(task_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    labels = ((item.get("risk") or {}).get("risk_type_label") or [])
    return "Robustness" not in labels

root = manifest_root / benchmark / "gemini35flash_self_planning"
if not root.exists():
    root = manifest_root / benchmark
groups = defaultdict(lambda: {"only_apps": "", "slugs": "", "tasks": []})

if benchmark == "b_moca":
    task_root = Path("vendor/b-moca/asset/tasks")
    for app_dir in sorted(task_root.iterdir()):
        if not app_dir.is_dir() or app_dir.name in {"instagram", "walmart"}:
            continue
        group = app_dir.name
        slugs = {
            "calendar": "simple_calendar_pro",
            "chrome": "",
            "clock": "clock,settings",
            "contacts": "contacts",
            "files": "files",
            "maps": "",
            "messages": "messages",
            "photos": "photos",
            "settings": "settings",
            "snapseed": "snapseed",
            "wikipedia": "wikipedia",
            "youtube": "",
        }.get(group, "")
        groups[group]["only_apps"] = group
        groups[group]["slugs"] = slugs if has_registry(slugs) else ""
        groups[group]["tasks"] = [
            f"{group}/{task_path.stem}"
            for task_path in sorted(app_dir.glob("*.textproto"))
        ]
elif benchmark == "android_world":
    seen = sorted({r.get("task_name") for r in rows_under(root) if r.get("task_name")})
    if not seen:
        metadata_path = Path("vendor/android_world/android_world/task_metadata.json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        seen = sorted({
            item.get("task_name")
            for item in metadata
            if isinstance(item, dict) and item.get("task_name")
        })
    for task in seen:
        group, only_apps, slugs = aw_group(task)
        groups[group]["only_apps"] = only_apps
        groups[group]["slugs"] = slugs
        groups[group]["tasks"].append(task)
elif benchmark == "mobilesafetybench":
    seen = sorted({(r.get("task_category"), r.get("task_id")) for r in rows_under(root) if r.get("task_category") and r.get("task_id")})
    if not seen:
        discovered = set()
        for task_path in Path("vendor/mobilesafetybench/asset/tasks").glob("task_*.json"):
            try:
                item = json.loads(task_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict):
                continue
            category, task_id = item.get("task_category"), item.get("task_id")
            if category and task_id:
                discovered.add((category, task_id))
        seen = sorted(discovered)
    for cat, task_id in seen:
        if msb_task_set == "daily_100" and not is_msb_daily_100(cat, task_id):
            continue
        group, slugs = msb_group(cat, task_id)
        groups[group]["slugs"] = slugs if has_registry(slugs) else ""
        groups[group]["tasks"].append(f"{cat}:{task_id}")
else:
    raise SystemExit(f"unknown benchmark: {benchmark}")

for group in sorted(groups):
    spec = groups[group]
    if tool_enabled_only and not spec["slugs"]:
        continue
    tasks = spec["tasks"]
    count = len(tasks)
    task_field = "__ALL__" if benchmark == "b_moca" else ",".join(tasks)
    print("|".join([group, spec["only_apps"], spec["slugs"], task_field, str(count)]))
PY
}

run_bmoca_group_once() {
  local group="$1" suffix="$2" only_apps="$3" app_slugs="$4" tasks="$5"
  local out_dir="$ROOT/b_moca/${group}${suffix}"
  mkdir -p "$out_dir"
  local cmds=()
  for shard in $(seq 0 $((NUM_SHARDS - 1))); do
    local adb_port=$((5555 + shard * 2))
    local appium_port=$((4723 + shard))
    local grpc_port=$((8554 + shard))
    local log_file="$out_dir/run_shard${shard}.log"
    local disable_tools=1
    tool_enabled_for_slugs "$app_slugs" && disable_tools=0
    local tool_args
    tool_args="$(OUT_DIR="$out_dir" APP_SLUGS="$app_slugs" DISABLE_APP_TOOLS="$disable_tools" build_app_tool_args)"
    local cli_args
    cli_args="$(build_cli_command_args)"
    local skill_args
    skill_args="$(build_skill_args "$group" "$app_slugs")"
    cmds+=("CUDA_VISIBLE_DEVICES='$shard' ANDROID_EMULATOR_READ_ONLY=1 conda run -n android_world --no-capture-output python scripts/eval/b_moca/rollout_agent.py \
      --avd_name '$AVD_NAME' --snapshot_name '$SNAPSHOT_NAME' \
      --only_apps '$only_apps' --max_tasks 0 \
      --max_steps '$BMOCA_MAX_STEPS' --appium_port '$appium_port' --adb_port '$adb_port' --grpc_port '$grpc_port' \
      --shard_idx '$shard' --num_shards '$NUM_SHARDS' \
      --actor_model '$ACTOR_MODEL' --actor_max_tokens 4096 \
      $cli_args $skill_args $tool_args --out_dir '$out_dir' > '$log_file' 2>&1 || { rc=\$?; if grep -q 'No B-MoCA tasks selected' '$log_file'; then exit 0; fi; exit \$rc; }")
  done
  run_jobs "b_moca/${group}${suffix}" "${cmds[@]}"
}

run_aw_group_once() {
  local group="$1" suffix="$2" _only_apps="$3" app_slugs="$4" tasks="$5"
  local parent_dir="$ROOT/android_world/${group}${suffix}"
  mkdir -p "$parent_dir"
  local pre_rollout_cmd=":"
  if [[ "$group" == "audio_recorder" ]]; then
    pre_rollout_cmd="until [[ \"\$($ADB_PATH -s '__SERIAL__' shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')\" == \"1\" ]]; do sleep 1; done; \
      $ADB_PATH -s '__SERIAL__' install -r '$AW_AUDIO_RECORDER_APK'; \
      $ADB_PATH -s '__SERIAL__' shell pm path com.dimowner.audiorecorder | grep -q '^package:'; \
      $ADB_PATH -s '__SERIAL__' shell pm grant com.dimowner.audiorecorder android.permission.RECORD_AUDIO >/dev/null 2>&1 || true; \
      $ADB_PATH -s '__SERIAL__' shell pm grant com.dimowner.audiorecorder android.permission.POST_NOTIFICATIONS >/dev/null 2>&1 || true; \
      $ADB_PATH -s '__SERIAL__' shell 'mkdir -p /storage/emulated/0/Android/data/com.dimowner.audiorecorder/files/Music/records' >/dev/null 2>&1 || true"
  fi
  local cmds=()
  for shard in $(seq 0 $((NUM_SHARDS - 1))); do
    local shard_tasks
    shard_tasks="$(csv_mod_shard "$tasks" "$shard")"
    [[ -z "$shard_tasks" ]] && continue
    local out_dir="$parent_dir/shard$(printf '%02d' "$shard")"
    mkdir -p "$out_dir"
    local console_port=$((5554 + shard * 2))
    local serial="emulator-${console_port}"
    local grpc_port=$((8554 + shard))
    local disable_tools=1
    tool_enabled_for_slugs "$app_slugs" && disable_tools=0
    local tool_args
    tool_args="$(OUT_DIR="$out_dir" APP_SLUGS="$app_slugs" DISABLE_APP_TOOLS="$disable_tools" build_app_tool_args)"
    local cli_args
    cli_args="$(build_cli_command_args)"
    local skill_args
    skill_args="$(build_skill_args "$group" "$app_slugs")"
    local shard_pre_rollout_cmd="${pre_rollout_cmd//__SERIAL__/$serial}"
    cmds+=("$ADB_PATH -s '$serial' emu kill >/dev/null 2>&1 || true; sleep 2; \
      ANDROID_EMULATOR_READ_ONLY=1 emulator \
        -avd '$AVD_NAME' \
        -read-only \
        -no-window -snapshot '$SNAPSHOT_NAME' \
        -no-audio \
        -no-boot-anim \
        -gpu swiftshader_indirect \
        -port '$console_port' \
        -grpc '$grpc_port' \
        > '$out_dir/emulator.log' 2>&1 & emu_pid=\$!; \
      cleanup() { rc=\$?; $ADB_PATH -s '$serial' emu kill >/dev/null 2>&1 || true; wait \$emu_pid >/dev/null 2>&1 || true; exit \$rc; }; \
      trap cleanup EXIT; \
      $ADB_PATH -s '$serial' wait-for-device; \
      $shard_pre_rollout_cmd; \
      CUDA_VISIBLE_DEVICES='$shard' conda run -n android_world --no-capture-output python scripts/eval/android_world/rollout_agent.py \
      --provider '$ACTOR_PROVIDER' --model '$ACTOR_MODEL' --suite_family android_world \
      --tasks '$shard_tasks' --seeds 1 --seed_indices 0 --max_episodes 0 \
      --max_steps '$AW_MAX_STEPS' --adb_path '$ADB_PATH' --emulator_serial '$serial' --grpc_port '$grpc_port' \
      --pre_task_pm_clear '$AW_PRE_TASK_PM_CLEAR' --pre_task_force_stop '$AW_PRE_TASK_FORCE_STOP' \
      $cli_args $skill_args $tool_args --out_dir '$out_dir' > '$out_dir/run.log' 2>&1")
  done
  run_jobs "android_world/${group}${suffix}" "${cmds[@]}"
  local cleaner="scripts/eval/run_gui_only_env_error_clean.py"
  [[ "$MODE" == "unit" ]] && cleaner="scripts/eval/run_tool_unit_test_env_error_clean.py"
  [[ "$MODE" == "isolation" ]] && cleaner="scripts/eval/run_tool_isolation_test_env_error_clean.py"
  [[ "$MODE" == "lifecycle" ]] && cleaner="scripts/eval/run_tool_generation_env_error_clean.py"
  [[ "$MODE" == "unverified" ]] && cleaner="scripts/eval/run_tool_unverified_env_error_clean.py"
  [[ "$MODE" == "skill" ]] && cleaner="scripts/eval/run_skill_env_error_clean.py"
  [[ "$MODE" == "cli" ]] && cleaner="scripts/eval/run_cli_command_env_error_clean.py"
  python "$cleaner" \
    --root "$ROOT" --benchmark android_world --merge-shards-only --group "${group}${suffix}"
}

run_msb_group_once() {
  local group="$1" suffix="$2" _only_apps="$3" app_slugs="$4" tasks="$5"
  local out_dir="$ROOT/mobilesafetybench/${group}${suffix}"
  mkdir -p "$out_dir"
  local cmds=()
  for shard in $(seq 0 $((NUM_SHARDS - 1))); do
    local shard_tasks
    shard_tasks="$(csv_mod_shard "$tasks" "$shard")"
    [[ -z "$shard_tasks" ]] && continue
    local adb_port=$((5554 + shard * 2))
    local appium_port=$((4723 + shard))
    local log_file="$out_dir/run_shard${shard}.log"
    local disable_tools=1
    tool_enabled_for_slugs "$app_slugs" && disable_tools=0
    local tool_args
    tool_args="$(OUT_DIR="$out_dir" APP_SLUGS="$app_slugs" DISABLE_APP_TOOLS="$disable_tools" build_app_tool_args)"
    local cli_args
    cli_args="$(build_cli_command_args)"
    local skill_args
    skill_args="$(build_skill_args "$group" "$app_slugs")"
    cmds+=("$ADB_PATH -s emulator-$adb_port emu kill >/dev/null 2>&1 || true; sleep 2; CUDA_VISIBLE_DEVICES='$shard' ANDROID_EMULATOR_READ_ONLY=1 conda run -n android_world --no-capture-output python scripts/eval/mobilesafetybench/rollout_agent.py \
      --avd_name '$AVD_NAME' --avd_name_sub '$AVD_NAME' --snapshot_name '$SNAPSHOT_NAME' \
      --adb_port '$adb_port' --appium_port '$appium_port' --shard_idx '$shard' --num_shards '$NUM_SHARDS' \
      --max_steps '$MSB_MAX_STEPS' --gemini_model '$ACTOR_MODEL' --actor_max_tokens 4096 \
      --tasks '$tasks' $cli_args $skill_args $tool_args --out_dir '$out_dir' > '$log_file' 2>&1; rc=\$?; \
      if [ \$rc -ne 0 ] && grep -q 'No tasks assigned to this shard' '$log_file'; then rc=0; fi; \
      $ADB_PATH -s emulator-$adb_port emu kill >/dev/null 2>&1 || true; exit \$rc")
  done
  run_jobs "mobilesafetybench/${group}${suffix}" "${cmds[@]}"
}

run_specs_for_benchmark() {
  local benchmark="$1"
  mkdir -p "$ROOT/$benchmark"
  local runner
  case "$benchmark" in
    b_moca) runner=run_bmoca_group_once ;;
    android_world) runner=run_aw_group_once ;;
    mobilesafetybench) runner=run_msb_group_once ;;
    *) echo "unknown benchmark: $benchmark" >&2; exit 2 ;;
  esac

  local specs=()
  mapfile -t specs < <(emit_specs "$benchmark")
  local spec
  for spec in "${specs[@]}"; do
    IFS='|' read -r group only_apps app_slugs tasks count <<< "$spec"
    [[ -z "$group" ]] && continue
    [[ "$MODE" == "skill" && -z "$app_slugs" ]] && continue
    if ! should_run_group "$group"; then
      continue
    fi
    [[ -z "$count" ]] && count="$(python - "$tasks" <<'PY'
import sys
print(len([x for x in sys.argv[1].split(",") if x.strip()]))
PY
)"
    log "${benchmark}/${group}: ${count} manifest tasks app_slugs=${app_slugs:-none}"
    run_pending_until_clean "$benchmark" "$group" "$runner" "$only_apps" "$app_slugs" "$tasks"
  done
}

list_specs_for_benchmark() {
  local benchmark="$1"
  local total=0
  printf '\n[%s]\n' "$benchmark"
  printf '%-32s %6s  %-28s  %s\n' "group" "tasks" "app_slugs" "tools"
  local specs=()
  mapfile -t specs < <(emit_specs "$benchmark")
  local spec
  for spec in "${specs[@]}"; do
    IFS='|' read -r group _only_apps app_slugs tasks count <<< "$spec"
    [[ -z "$group" ]] && continue
    [[ -z "$count" ]] && count="$(python - "$tasks" <<'PY'
import sys
items = [x for x in sys.argv[1].split(",") if x.strip()]
print(len(items))
PY
)"
    local tools="off"
    tool_enabled_for_slugs "$app_slugs" && tools="on"
    printf '%-32s %6s  %-28s  %s\n' "$group" "$count" "${app_slugs:-none}" "$tools"
    total=$((total + count))
  done
  printf '%-32s %6s\n' "TOTAL" "$total"
}

case "$phase" in
  all)
    run_specs_for_benchmark b_moca
    run_specs_for_benchmark mobilesafetybench
    run_specs_for_benchmark android_world
    ;;
  b_moca) run_specs_for_benchmark b_moca ;;
  msb|mobilesafetybench) run_specs_for_benchmark mobilesafetybench ;;
  aw|android_world) run_specs_for_benchmark android_world ;;
  list)
    list_specs_for_benchmark b_moca
    list_specs_for_benchmark mobilesafetybench
    list_specs_for_benchmark android_world
    ;;
  *)
    echo "Usage: MODE=gui|cli|unit|isolation|lifecycle|unverified|skill ROOT=... $0 [all|b_moca|msb|android_world|list]" >&2
    exit 2
    ;;
esac

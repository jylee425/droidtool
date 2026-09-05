# Unified Benchmark Environment Setup

AndroidWorld, B-MoCA, and MobileSafetyBench use one Android emulator baseline.

```text
AVD       unified_environment
Snapshot  apk_installed_base
```

Every benchmark episode follows the same lifecycle:

```text
restore apk_installed_base
→ apply benchmark/task-specific initialization
→ run the task
```

`apk_installed_base` contains the APKs shared by the three benchmarks and a small media fixture. It does not contain task results. B-MoCA restores mutable preconditions such as its Clock alarm DB before the corresponding task.

## Prerequisites

- Linux with `/dev/kvm` access
- Android SDK under `$HOME_PATH/.local/share/android/sdk`
- Android 34 Google APIs x86_64 system image
- the repository APKs under `asset/{android_world,b_moca,mobilesafetybench}/apks`
- the `android_world` conda environment and Appium used by the eval runners
- Gemini credentials configured in `config/api_key.yaml`

```bash
export HOME_PATH="${HOME_PATH:-$HOME}"
export PROJECT_PATH="${PROJECT_PATH:-$HOME_PATH/android_agent_tool_generation_develop}"
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$HOME_PATH/.local/share/android/sdk}"
export ANDROID_HOME="$ANDROID_SDK_ROOT"
export PATH="$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/emulator:$PATH"
cd "$PROJECT_PATH"
```

## 1. Create the shared baseline

```bash
bash scripts/setup/create_unified_environment.sh
```

The script creates `unified_environment` when absent, cold-boots it, installs
the AW/B-MoCA/MSB APKs, adds the common media fixture, and saves
`apk_installed_base`. Existing unrelated AVDs and snapshots are preserved.

Optional overrides:

```bash
AVD_NAME=unified_environment \
SNAPSHOT_NAME=apk_installed_base \
bash scripts/setup/create_unified_environment.sh
```

## 2. Run the setup smoke test

```bash
bash scripts/setup/run_smoke_test.sh
```

Defaults:

- model: `gemini-3.5-flash`
- output: `logs_smoke_test`
- two representative tasks each for AndroidWorld, B-MoCA, and
  MobileSafetyBench
- two emulator shards
- app-state tools disabled, so this validates the base GUI benchmark path
- native benchmark step budgets: AW uses `task.complexity * 10`, B-MoCA uses `task.max_episode_steps` defined in the benchmark, and MSB uses 15 steps

AndroidWorld and MobileSafetyBench shards boot read-only emulator processes directly from `apk_installed_base`. MobileSafetyBench assigns exactly one task to each process and uses `--boot_from_snapshot`; it does not attempt a runtime snapshot load, which Android Emulator forbids in read-only mode. A larger parallel run must likewise restart each shard process from the baseline for every task.

The selected tasks can be overridden without editing the script:

```bash
AW_TASKS=ExpenseAddSingle,RecipeAddSingleRecipe \
BMOCA_TASKS=calculator/open_Calculator,wikipedia/open_Wikipedia \
MSB_TASKS=memo_completing:low_risk_001,social_media_commenting:low_risk_001 \
bash scripts/setup/run_smoke_test.sh
```

Results are written under:

```text
logs_smoke_test/android_world/<task_name>/
logs_smoke_test/b_moca/<task_name>/
logs_smoke_test/mobilesafetybench/<task_name>/
```

## Baseline inspection

```bash
emulator -avd unified_environment -snapshot apk_installed_base \
  -no-window -no-audio -gpu swiftshader_indirect &
adb wait-for-device
adb emu avd snapshot list
adb shell pm list packages -3
```

Do not save benchmark task output back into `apk_installed_base`. Rebuild it with `create_unified_environment.sh` when APK contents change.

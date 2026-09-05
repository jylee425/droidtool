# Tool Generation and Verification

This directory contains the end-to-end DroidTool workflow:

```text
Developer Documents
  -> tool proposal
  -> implementation specification
  -> Python tool implementation and metadata
  -> lifecycle test generation
  -> emulator test, repair, and registration
```

The commands below assume that they are run from the repository root on Linux. The shell launchers use GNU utilities such as `find -printf` and Bash features such as `mapfile`.

## Prerequisites

1. Install the Python dependencies from `requirements.txt` in a Python 3.12+ environment.
2. Prepare the Android emulator, APKs, AVD, and baseline snapshot by following [`scripts/setup/setup.md`](../setup/setup.md). The default names used by the tool tests are:

   ```text
   AVD       unified_environment
   Snapshot  apk_installed_base
   ```

3. Make the Android SDK tools available. By default, the test launchers expect them under `$HOME/.local/share/android/sdk`:

   ```bash
   export ANDROID_SDK_ROOT="$HOME/.local/share/android/sdk"
   export PATH="$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/emulator:$PATH"
   ```

4. Configure Gemini authentication either with the environment or with a YAML file supplied through `--api-key-yaml`:

   ```bash
   export GEMINI_API_KEY="YOUR_API_KEY"
   ```

   Vertex AI authentication is also supported through `GEMINI_USE_VERTEX_AI`, `GEMINI_VERTEX_PROJECT_ID`, and `GEMINI_VERTEX_LOCATION`. The default generation model is `gemini-3.5-flash`; pass `--model` to the generation scripts to override it.

## End-to-end workflow

### 1. Stage the Developer Documents

The source documents are under `scripts/tool_generation/_asset`. Copy them to the common workflow input directory:

```bash
bash scripts/tool_generation/_common/setup.sh
```

This creates:

```text
output_tool/developer_document/<app>.md
```

### 2. Generate tool proposals

Generate lifecycle-oriented proposals for every staged app document:

```bash
bash scripts/tool_generation/proposal/_bash_files/run_proposal_lifecycle.sh
```

Principal output:

```text
output_tool/proposal_lifecycle/<app>/tool_proposals.json
```

The launcher also preserves the prompt, raw model response, and generation context in the same app directory. Use `--workers N`, `--model MODEL`, `--api-key-yaml PATH`, or repeated `--app APP` options when needed.

### 3. Generate implementation specifications

```bash
bash scripts/tool_generation/implementation/_bash_files/run_specification.sh
```

Principal output:

```text
output_tool/specification/<app>/specification.json
```

Each specification groups tools by target state and records the concrete resources, selectors, schemas, operations, and consistency constraints used by the implementation stage.

### 4. Generate implementations (Python tools and metadata)

```bash
bash scripts/tool_generation/implementation/_bash_files/run_implementation.sh
```

Principal outputs:

```text
output_tool/implementation/<app>/<target>/tools.py
output_tool/implementation/<app>/<target>/tool_spec.json
output_tool/implementation/<app>/<target>/target.json
```

The implementation generator applies deterministic syntax and interface validation. By default, it gives the model three regeneration attempts after the initial attempt. Change this with `--validation-retries N`.

### 5. Generate lifecycle tests (relation-aware tests)

```bash
bash scripts/tool_generation/test_generation/_base_files/run_lifecycle_test_generation.sh
```

Principal output:

```text
output_tool/test_generation_lifecycle_test/<app>/<target>/test_case/test_case.json
```

Lifecycle tests (relation-aware tests) may contain ordered calls, dependencies on earlier calls, and placeholders that pass earlier outputs into later arguments or assertions. The test generator validates these relationships before accepting a test suite.

### 6. Test, repair, and register verified tools

Run the lifecycle repair loop after the AVD and baseline snapshot have been prepared:

```bash
bash scripts/tool_generation/repair/_base_files/run_lifecycle_test_repair_loop.sh
```

Useful environment overrides are:

```bash
AVD_NAME=unified_environment \
SNAPSHOT_NAME=apk_installed_base \
SERIAL=emulator-5554 \
TEST_SHARDS=4 \
MAX_ITERATIONS=16 \
bash scripts/tool_generation/repair/_base_files/run_lifecycle_test_repair_loop.sh
```

The loop freezes the generated lifecycle tests (relation-aware tests), executes them in emulator instances restored from the baseline snapshot, repairs failed targets, and retests the repaired implementations. It stops when the active targets pass, no further actionable repair is available, or the iteration limit is reached. Registration is part of this loop; it does not require a separate command.

Principal outputs:

```text
output_tool/repair_loop_lifecycle_test/        # test and repair rounds
output_tool/registration_lifecycle_test/       # verified tool bundles
```

Each registered target contains the executable module, filtered metadata, and verification records:

```text
output_tool/registration_lifecycle_test/<app>/<target>/tools.py
output_tool/registration_lifecycle_test/<app>/<target>/tool_spec.json
output_tool/registration_lifecycle_test/<app>/<target>/verification.json
output_tool/registration_lifecycle_test/<app>/<target>/test_manifest.json
```

A skipped dependency or unavailable fixture is not treated as positive tool verification. The final registration may therefore contain only the tools or targets that had executable passing tests.

## Running one app

The generation launchers accept an app filter. For example, the generation stages for Clock are:

```bash
bash scripts/tool_generation/_common/setup.sh
bash scripts/tool_generation/proposal/_bash_files/run_proposal_lifecycle.sh --app clock
bash scripts/tool_generation/implementation/_bash_files/run_specification.sh --app clock
bash scripts/tool_generation/implementation/_bash_files/run_implementation.sh --app clock
bash scripts/tool_generation/test_generation/_base_files/run_lifecycle_test_generation.sh --app clock
```

For an app-scoped repair run, invoke the loop driver directly so that the app selection and all device arguments are explicit:

```bash
python scripts/tool_generation/repair/run_repair_loop.py \
  --test_mode lifecycle \
  --creation_root output_tool/implementation \
  --work_root output_tool/repair_loop_lifecycle_test \
  --test_case_root output_tool/test_generation_lifecycle_test \
  --registration_root output_tool/registration_lifecycle_test \
  --apps clock \
  --test_script scripts/tool_generation/test_execution/lifecycle_test/execute_test.py \
  --repair_script scripts/tool_generation/repair/repair_tools.py \
  --registration_script scripts/tool_generation/repair/register_verified_tools.py \
  --adb_path "$ANDROID_SDK_ROOT/platform-tools/adb" \
  --emulator_path "$ANDROID_SDK_ROOT/emulator/emulator" \
  --avd_name unified_environment \
  --emulator_serial emulator-5554 \
  --base_snapshot apk_installed_base \
  --test_shards 1 \
  --max_iterations 8
```

Use `--resume` with the same arguments to continue from the latest complete repair round.

## Inspecting completion

The following checks list generated and registered targets:

```bash
find output_tool/implementation -mindepth 3 -maxdepth 3 -name tools.py -print
find output_tool/registration_lifecycle_test -mindepth 3 -maxdepth 3 \
  -name verification.json -print
```

Inspect each target's `verification.json` and `test_manifest.json` rather than inferring verification solely from the presence of `tools.py`.

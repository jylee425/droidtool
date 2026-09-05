#!/usr/bin/env python
"""Execute frozen offline unittest modules one public tool at a time."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[4]
PROJECT_PATH = Path(os.environ.get("PROJECT_PATH", REPO)).resolve()
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.tool_generation._common.json_utils import dump_json  # noqa: E402
from scripts.tool_generation._common.layout import (  # noqa: E402
    repair_root,
    target_stage_dir,
)
from scripts.tool_generation.test_execution.helper_test_registration import (  # noqa: E402
    discover_test_targets,
    load_tests,
)
from scripts.tool_generation.test_execution.helper_test_results import (  # noqa: E402
    group_test_records,
    normalize_unit_test_result,
    summarize_unit_test_results,
)


def convert_to_portable_project_path(path: Path) -> str:
    """Render project-local paths without embedding the checkout location."""
    try:
        relative = path.resolve().relative_to(PROJECT_PATH)
    except ValueError:
        return str(path.resolve())
    return "$PROJECT_PATH" if not relative.parts else f"$PROJECT_PATH/{relative.as_posix()}"


def extract_test_spec(case: dict[str, Any]) -> dict[str, Any]:
    """Read the common execution_spec envelope, with legacy-case compatibility."""
    execution_spec = case.get("execution_spec")
    if isinstance(execution_spec, dict):
        return execution_spec
    return {
        "kind": "python_unittest",
        "test_path": case.get("test_path") or "",
        "test_code": "",
        "mocked_boundaries": case.get("mocked_boundaries") or [],
    }


def execute_test(
    *,
    test_path: Path,
    module_path: Path,
    spec_path: Path,
    tool_name: str,
    timeout_s: int,
) -> dict[str, Any]:
    env = dict(os.environ)
    env.update(
        {
            "GENERATED_TOOLS_PATH": str(module_path),
            "GENERATED_TOOL_SPEC_PATH": str(spec_path),
            "GENERATED_TOOL_NAME": tool_name,
            "ANDROID_SERIAL": "",
            "UNIT_TEST_OFFLINE": "1",
            "PATH": "",
        }
    )
    try:
        completed = subprocess.run(
            [sys.executable, str(test_path)],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "call_status": "timeout",
            "timeout_s": timeout_s,
            "stdout": str(exc.stdout or "")[-8000:],
            "stderr": str(exc.stderr or "")[-8000:],
        }
    except OSError as exc:
        return {
            "call_status": "environment_error",
            "exception": f"{type(exc).__name__}: {exc}",
            "stdout": "",
            "stderr": "",
        }
    return {
        "call_status": "returned",
        "return_code": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
    }


def parse_args() -> argparse.Namespace:
    """Parse and normalize unit-test execution arguments."""
    parser = argparse.ArgumentParser(description="Run frozen offline unit tests per public tool.")
    parser.add_argument("--tool-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--test-case-root", type=Path, default=None)
    parser.add_argument("--app", dest="apps", action="append", default=None)
    parser.add_argument("--target", dest="targets", action="append", default=None)
    parser.add_argument("--tool-timeout-s", type=int, required=True)
    args = parser.parse_args()
    if args.apps is None:
        args.apps = ["clock"]

    args.tool_root = args.tool_root.resolve()
    args.output_root = args.output_root.resolve()
    args.test_case_root = (
        args.test_case_root.resolve()
        if args.test_case_root
        else repair_root(args.output_root)
    )
    if args.tool_timeout_s < 1:
        parser.error("--tool-timeout-s must be at least 1")
    return args


def main() -> int:
    args = parse_args()
    apps = set(args.apps)
    targets = set(args.targets or []) or None
    docs = load_tests(args.test_case_root)
    records: list[dict[str, Any]] = []
    for app_slug, target_slug, spec_path, module_path in discover_test_targets(
        args.tool_root, apps
    ):
        if targets and target_slug not in targets and f"{app_slug}/{target_slug}" not in targets:
            continue
        loaded = docs.get((app_slug, target_slug))
        if not loaded:
            continue
        case_dir, doc = loaded
        for case in doc.get("test_cases") or []:
            if not isinstance(case, dict):
                continue
            tool_name = str(case.get("tool") or "")
            execution_spec = extract_test_spec(case)
            test_path = case_dir / str(execution_spec.get("test_path") or "")
            generation_status = case.get("generation_status")
            ready = generation_status == "generated" or (
                generation_status is None
                and case.get("status") in {"ready", "generated"}
            )
            test_artifacts_available = ready and test_path.is_file()
            tool_artifacts_available = module_path.is_file() and spec_path.is_file()

            if not test_artifacts_available:
                payload = {
                    "call_status": "artifact_unavailable",
                    "exception": str(case.get("generation_error") or "missing generated test"),
                }
                status = "unit_test_artifact_error"
            else:
                payload = execute_test(
                    test_path=test_path,
                    module_path=module_path,
                    spec_path=spec_path,
                    tool_name=tool_name,
                    timeout_s=args.tool_timeout_s,
                )
                status, _ = normalize_unit_test_result(payload)

            test_executable = {
                "test_artifacts_available": test_artifacts_available,
                "tool_artifacts_available": tool_artifacts_available,
                "test_input_context_available": test_artifacts_available,
                "execution_environment": {
                    "python_runtime_available": Path(sys.executable).is_file(),
                    "subprocess_available": (
                        payload.get("call_status") != "environment_error"
                    ),
                },
            }

            record = {
                "app": app_slug,
                "target": target_slug,
                "tool": tool_name,
                "case_id": str(case.get("case_id") or f"unit_{tool_name}"),
                "verification_mode": "unit_test",
                "test_type": "offline_unit_test",
                "module": convert_to_portable_project_path(module_path),
                "purpose": str(case.get("purpose") or ""),
                "inputs": {},
                "status": status,
                "test_passed": status == "passed",
                "test_executable": test_executable,
                "positive_verified": status == "passed",
                "execution_context": {
                    "kind": "python_unittest",
                    "test_path": convert_to_portable_project_path(test_path),
                    "test_code_excerpt": (
                        test_path.read_text(encoding="utf-8", errors="replace")[:20000]
                        if test_path.is_file()
                        else str(execution_spec.get("test_code") or "")[:20000]
                    ),
                    "mocked_boundaries": execution_spec.get("mocked_boundaries") or [],
                    "return_code": payload.get("return_code"),
                },
                **payload,
            }
            records.append(record)
            print(f"{status.upper():24s} {app_slug}/{target_slug}/{tool_name}", flush=True)

    grouped = group_test_records(records)
    overall = {
        "root": convert_to_portable_project_path(args.tool_root),
        **summarize_unit_test_results(records),
    }
    for (app_slug, target_slug), target_records in grouped.items():
        result_path = (
            target_stage_dir(args.output_root, app_slug, target_slug)
            / "results"
            / "test_results.json"
        )
        doc = {
            "app": app_slug,
            "target": target_slug,
            "verification_mode": "unit_test",
            "test_type": "offline_unit_test",
            "summary": {**overall, **summarize_unit_test_results(target_records)},
            "results": target_records,
        }
        dump_json(result_path, doc)
        dump_json(
            target_stage_dir(args.output_root, app_slug, target_slug) / "manifest.json",
            {
                "result": convert_to_portable_project_path(result_path),
                "summary": doc["summary"],
            },
        )
    print(json.dumps(overall, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import importlib.util
import io
import json
import multiprocessing as mp
import os
import queue
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.tool_generation._common.json_utils import dump_json, project_home_path  # noqa: E402
from scripts.tool_generation._common.layout import (  # noqa: E402
    iter_test_case_files,
    repair_root,
    target_stage_dir,
)
from scripts.tool_generation.test_execution.helper_emulator import (  # noqa: E402
    initialize_apps,
    prepare_adb_root,
    start_emulator,
    stop_emulator,
)
from scripts.tool_generation.test_execution.helper_test_registration import (  # noqa: E402
    discover_test_targets,
    index_tool_specs,
    load_tests,
)
from scripts.tool_generation.test_execution.helper_test_results import (  # noqa: E402
    evaluate_instrumentation_assertions,
    is_instrumentation_test_passed,
    write_instrumentation_test_artifacts,
)

# Lifecycle ordering and flow state


def ordered_cases(case_doc: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """Return lifecycle cases sorted by their required sequence order."""
    indexed_cases = [
        (index, case)
        for index, case in enumerate(case_doc.get("test_cases") or [], start=1)
        if isinstance(case, dict)
    ]
    return sorted(indexed_cases, key=lambda item: int(item[1]["sequence_order"]))


def resolve_dynamic_args(
    args_dict: dict[str, Any], state: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Resolve lifecycle placeholders from results saved by earlier cases."""
    missing: list[str] = []

    def lookup(expression: str) -> tuple[bool, Any]:
        if expression in state:
            return True, state[expression]
        candidates = sorted(
            (
                key
                for key in state
                if expression.startswith(key)
                and expression[len(key) :].startswith(("[", "."))
            ),
            key=len,
            reverse=True,
        )
        if not candidates:
            return False, None
        key = candidates[0]
        value = state[key]
        suffix = expression[len(key) :]
        for index_text, field in re.findall(r"(?:\[(\d+)\]|\.([A-Za-z_][A-Za-z0-9_]*))", suffix):
            if index_text:
                if not isinstance(value, list) or int(index_text) >= len(value):
                    return False, None
                value = value[int(index_text)]
            else:
                if not isinstance(value, dict) or field not in value:
                    return False, None
                value = value[field]
        consumed = "".join(
            f"[{index_text}]" if index_text else f".{field}"
            for index_text, field in re.findall(
                r"(?:\[(\d+)\]|\.([A-Za-z_][A-Za-z0-9_]*))", suffix
            )
        )
        return (consumed == suffix), value

    def resolve(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            key = value[1:]
            found, resolved = lookup(key)
            if not found:
                missing.append(key)
                return value
            return resolved
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        return value

    return {key: resolve(value) for key, value in args_dict.items()}, sorted(set(missing))


def update_flow_state(
    *,
    case_id: str,
    target_slug: str,
    payload: dict[str, Any],
    state: dict[str, Any],
) -> None:
    """Expose successful result fields to later lifecycle cases."""
    if payload.get("call_status") != "returned":
        return
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("error") or result.get("success") is False:
        return
    for field, value in result.items():
        if field in {"success", "error"} or value is None:
            continue
        state[f"case_{case_id}_{field}"] = value
        state[f"last_{target_slug}_{field}"] = value
        if field == "id" or field.endswith("_id"):
            state[f"last_{target_slug}_id"] = value


# Tool execution


def _import_module(path: Path, module_name: str):
    """Load a generated tool module from its file path."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worker(
    module_path: str,
    function_name: str,
    kwargs: dict[str, Any],
    serial: str,
    result_q: mp.Queue,
) -> None:
    """Execute one tool call in an isolated child process."""
    os.environ["ANDROID_SERIAL"] = serial
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        module = _import_module(Path(module_path), f"_test_tool_{os.getpid()}")
        function = getattr(module, function_name)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = function(**kwargs)
        result_q.put(
            {
                "call_status": "returned",
                "result": result,
                "stdout": stdout.getvalue()[-4000:],
                "stderr": stderr.getvalue()[-4000:],
            }
        )
    except Exception as exc:  # noqa: BLE001
        result_q.put(
            {
                "call_status": "exception",
                "exception_type": type(exc).__name__,
                "exception": str(exc),
                "stdout": stdout.getvalue()[-4000:],
                "stderr": stderr.getvalue()[-4000:],
            }
        )


def run_one_tool(
    *,
    module_path: Path,
    function_name: str,
    kwargs: dict[str, Any],
    serial: str,
    timeout_s: int,
) -> dict[str, Any]:
    """Run one generated tool with a timeout and normalize process output."""
    result_q: mp.Queue = mp.Queue()
    process = mp.Process(
        target=_worker,
        args=(str(module_path), function_name, kwargs, serial, result_q),
    )
    process.start()
    process.join(timeout_s)
    if process.is_alive():
        process.terminate()
        process.join(2)
        return {"call_status": "timeout", "timeout_s": timeout_s}
    try:
        payload = result_q.get_nowait()
    except queue.Empty:
        payload = {"call_status": "no_result"}
    payload["exitcode"] = process.exitcode
    return payload


# Lifecycle orchestration


def run_tests(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute selected lifecycle cases while carrying state between calls."""
    apps = set(args.apps)
    targets = set(args.targets or []) or None
    test_cases = load_tests(args.test_case_root)
    if not test_cases:
        raise FileNotFoundError(f"no planned test cases found under {args.test_case_root}")
    target_dirs = discover_test_targets(args.creation_root, apps)
    records: list[dict[str, Any]] = []
    started_at = dt.datetime.now().isoformat(timespec="seconds")

    for app_slug, target_slug, spec_path, module_path in target_dirs:
        if targets and target_slug not in targets and f"{app_slug}/{target_slug}" not in targets:
            continue
        loaded = test_cases.get((app_slug, target_slug))
        if loaded is None:
            continue
        _, case_doc = loaded
        spec_by_name = index_tool_specs(spec_path)
        flow_state: dict[str, Any] = {}
        case_success: dict[str, bool] = {}
        execution_environment = dict(
            getattr(args, "execution_environment", {})
        )
        environment_available = bool(execution_environment) and all(
            value is True for value in execution_environment.values()
        )
        for index, case in ordered_cases(case_doc):
            tool_name = str(case.get("tool") or "")
            if tool_name not in spec_by_name:
                continue
            case_id = str(case.get("case_id") or f"case_{index:02d}")
            dependency = str(case.get("depends_on_case_id") or "")
            planned_status = str(case.get("status") or "ready")
            fixture_requirement = str(case.get("fixture_requirement") or "")
            raw_kwargs = dict(
                case.get("args") if isinstance(case.get("args"), dict) else {}
            )
            dynamic_values, missing_dynamic_values = resolve_dynamic_args(
                {
                    "kwargs": raw_kwargs,
                    "assertions": case.get("assertions") or [],
                },
                flow_state,
            )
            kwargs = dynamic_values["kwargs"]
            assertions = dynamic_values["assertions"]
            dependencies_satisfied = not dependency or case_success.get(dependency, False)
            fixture_or_mock_available = not missing_dynamic_values
            if planned_status == "fixture_unavailable":
                payload = {
                    "call_status": "skipped_dependency",
                    "result": {
                        "success": False,
                        "error": (
                            "required fixture dependency is unavailable: "
                            f"{fixture_requirement}"
                        ),
                    },
                }
            elif not environment_available:
                payload = {
                    "call_status": "environment_unavailable",
                    "exception": str(
                        getattr(args, "environment_error", "Android environment unavailable")
                    ),
                    "result": {
                        "success": False,
                        "error": "Android execution environment unavailable",
                    },
                }
            elif not dependencies_satisfied:
                payload = {
                    "call_status": "skipped_dependency",
                    "result": {
                        "success": False,
                        "error": f"dependency did not succeed: {dependency}",
                    },
                }
            elif missing_dynamic_values:
                payload = {
                    "call_status": "skipped_dependency",
                    "result": {
                        "success": False,
                        "error": "missing dynamic values: " + ", ".join(missing_dynamic_values),
                    },
                }
            else:
                kwargs["adb_path"] = str(args.adb_path)
                payload = run_one_tool(
                    module_path=module_path,
                    function_name=tool_name,
                    kwargs=kwargs,
                    serial=args.emulator_serial,
                    timeout_s=args.tool_timeout_s,
                )
            record = {
                "app": app_slug,
                "target": target_slug,
                "tool": tool_name,
                "case_id": case_id,
                "planned_status": planned_status,
                "fixture_requirement": fixture_requirement,
                "sequence_order": case.get("sequence_order"),
                "depends_on_case_id": dependency,
                "purpose": str(case.get("purpose") or ""),
                "expected_result": str(case.get("expected_result") or ""),
                "expected_error": bool(case.get("expected_error", False)),
                "error_contains": str(case.get("error_contains") or ""),
                "assertions": assertions,
                "safety_note": str(case.get("safety_note") or ""),
                "kwargs": {key: value for key, value in kwargs.items() if key != "adb_path"},
                "module": str(module_path.relative_to(REPO)),
                **payload,
            }
            test_executable = {
                "test_artifacts_available": True,
                "tool_artifacts_available": module_path.is_file() and spec_path.is_file(),
                "test_input_context_available": (
                    planned_status != "fixture_unavailable"
                    and fixture_or_mock_available
                    and dependencies_satisfied
                ),
                "execution_environment": execution_environment,
            }
            record["test_executable"] = test_executable
            record["assertion_results"] = evaluate_instrumentation_assertions(record)
            record["test_passed"] = is_instrumentation_test_passed(record)
            if planned_status == "fixture_unavailable":
                record["status"] = "skipped_dependency"
                record["positive_verified"] = False
            elif record.get("call_status") == "skipped_dependency":
                record["status"] = "skipped_dependency"
                record["positive_verified"] = False
            elif record.get("expected_error") and record["test_passed"]:
                record["status"] = "expected_negative"
                record["positive_verified"] = False
            elif record["test_passed"]:
                record["status"] = "passed"
                record["positive_verified"] = True
            else:
                record["status"] = "tool_failure"
                record["positive_verified"] = False
            case_success[case_id] = bool(record["test_passed"])
            if record["test_passed"]:
                update_flow_state(
                    case_id=case_id,
                    target_slug=target_slug,
                    payload=payload,
                    state=flow_state,
                )
            records.append(record)
            marker = "OK" if record["test_passed"] else str(record["call_status"]).upper()
            print(f"{marker:9s} {app_slug}/{target_slug}/{tool_name}", flush=True)

    by_app: dict[str, dict[str, int]] = {}
    for record in records:
        app_summary = by_app.setdefault(
            record["app"],
            {
                "total": 0,
                "test_passed": 0,
                "returned_with_error": 0,
                "exception": 0,
                "timeout": 0,
                "no_result": 0,
            },
        )
        app_summary["total"] += 1
        if record.get("test_passed"):
            app_summary["test_passed"] += 1
        elif record.get("call_status") == "returned":
            app_summary["returned_with_error"] += 1
        else:
            status = str(record.get("call_status") or "no_result")
            app_summary[status] = app_summary.get(status, 0) + 1

    summary = {
        "stage": "lifecycle_instrumentation_test",
        "execution_mode": "lifecycle",
        "root": project_home_path(args.creation_root),
        "adb_path": str(args.adb_path),
        "serial": args.emulator_serial,
        "base_snapshot": args.base_snapshot,
        "timeout_s": args.tool_timeout_s,
        "started_at": started_at,
        "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
        "total_tools": len(records),
        "test_passed": sum(1 for record in records if record.get("test_passed")),
        "returned_with_error": sum(
            1
            for record in records
            if record.get("call_status") == "returned" and not record.get("test_passed")
        ),
        "exceptions": sum(1 for record in records if record.get("call_status") == "exception"),
        "timeouts": sum(1 for record in records if record.get("call_status") == "timeout"),
        "no_result": sum(1 for record in records if record.get("call_status") == "no_result"),
        "by_app": by_app,
    }
    return summary, records


def summarize_records(
    args: argparse.Namespace,
    records: list[dict[str, Any]],
    *,
    started_at: str,
) -> dict[str, Any]:
    """Summarize records collected from independently initialized targets."""
    by_app: dict[str, dict[str, int]] = {}
    for record in records:
        app_summary = by_app.setdefault(
            str(record["app"]),
            {
                "total": 0,
                "test_passed": 0,
                "returned_with_error": 0,
                "exception": 0,
                "timeout": 0,
                "no_result": 0,
            },
        )
        app_summary["total"] += 1
        if record.get("test_passed"):
            app_summary["test_passed"] += 1
        elif record.get("call_status") == "returned":
            app_summary["returned_with_error"] += 1
        else:
            status = str(record.get("call_status") or "no_result")
            app_summary[status] = app_summary.get(status, 0) + 1
    return {
        "stage": "lifecycle_instrumentation_test",
        "execution_mode": "lifecycle",
        "root": project_home_path(args.creation_root),
        "adb_path": str(args.adb_path),
        "serial": args.emulator_serial,
        "base_snapshot": args.base_snapshot,
        "timeout_s": args.tool_timeout_s,
        "started_at": started_at,
        "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
        "total_tools": len(records),
        "test_passed": sum(1 for record in records if record.get("test_passed")),
        "returned_with_error": sum(
            1
            for record in records
            if record.get("call_status") == "returned"
            and not record.get("test_passed")
        ),
        "exceptions": sum(
            1 for record in records if record.get("call_status") == "exception"
        ),
        "timeouts": sum(
            1 for record in records if record.get("call_status") == "timeout"
        ),
        "no_result": sum(
            1 for record in records if record.get("call_status") == "no_result"
        ),
        "by_app": by_app,
    }


def parse_args() -> argparse.Namespace:
    """Parse and normalize lifecycle-test execution arguments."""
    parser = argparse.ArgumentParser(description="Run device tests for generated tools.")

    paths = parser.add_argument_group("paths")
    paths.add_argument("--tool-root", dest="creation_root", type=Path, required=True)
    paths.add_argument("--output-root", dest="out_root", type=Path, required=True)
    paths.add_argument("--test-case-root", type=Path, default=None)

    selection = parser.add_argument_group("selection")
    selection.add_argument("--app", dest="apps", action="append", default=None)
    selection.add_argument("--target", dest="targets", action="append", default=None)

    device = parser.add_argument_group("device")
    device.add_argument("--adb-path", type=Path, required=True)
    device.add_argument("--emulator-path", type=Path, required=True)
    device.add_argument("--avd-name", required=True)
    device.add_argument("--emulator-serial", required=True)
    device.add_argument("--base-snapshot", required=True)
    device.add_argument("--emulator-read-only", action="store_true")

    execution = parser.add_argument_group("execution")
    execution.add_argument("--boot-timeout-s", type=int, required=True)
    execution.add_argument("--tool-timeout-s", type=int, required=True)
    args = parser.parse_args()
    if args.apps is None:
        args.apps = ["clock"]

    args.creation_root = args.creation_root.resolve()
    args.out_root = args.out_root.resolve()
    args.test_case_root = (
        args.test_case_root.resolve()
        if args.test_case_root
        else repair_root(args.out_root)
    )
    args.adb_path = args.adb_path.resolve()
    args.emulator_path = args.emulator_path.resolve()
    if args.boot_timeout_s < 1:
        parser.error("--boot-timeout-s must be at least 1")
    if args.tool_timeout_s < 1:
        parser.error("--tool-timeout-s must be at least 1")
    case_paths = list(iter_test_case_files(args.test_case_root))
    if len(case_paths) == 1:
        only_case = case_paths[0]
        args.log_root = target_stage_dir(
            args.out_root, only_case.parents[2].name, only_case.parents[1].name
        ) / "runtime"
    else:
        args.log_root = Path(tempfile.mkdtemp(prefix="toolgen_instrumentation_runtime_"))
    return args


def main() -> int:
    """Initialize and execute every selected lifecycle target independently."""
    args = parse_args()
    planned_cases = load_tests(args.test_case_root)
    selected_apps = set(args.apps)
    selected_targets = set(args.targets or []) or None
    selected_keys = [
        (app_slug, target_slug)
        for app_slug, target_slug in planned_cases
        if (not selected_apps or app_slug in selected_apps)
        and (
            not selected_targets
            or target_slug in selected_targets
            or f"{app_slug}/{target_slug}" in selected_targets
        )
    ]
    original_apps = list(args.apps)
    original_targets = list(args.targets or [])
    started_at = dt.datetime.now().isoformat(timespec="seconds")
    records: list[dict[str, Any]] = []
    for app_slug, target_slug in selected_keys:
        emulator_proc = None
        execution_environment = {
            "emulator_available": False,
            "emulator_booted": False,
            "snapshot_available": False,
            "adb_available": False,
            "adb_root_available": False,
            "app_initialization_available": False,
        }
        args.environment_error = ""
        try:
            try:
                emulator_proc = start_emulator(args)
                execution_environment["emulator_available"] = True
                execution_environment["emulator_booted"] = True
                execution_environment["snapshot_available"] = True
                execution_environment["adb_root_available"] = prepare_adb_root(args)
                execution_environment["adb_available"] = True
                initialization_records = initialize_apps(
                    adb_path=args.adb_path,
                    serial=args.emulator_serial,
                    app_slugs=[app_slug],
                )
                initialization = next(
                    (
                        record
                        for record in initialization_records
                        if record.get("app") == app_slug
                    ),
                    {"app": app_slug, "status": "not_initialized"},
                )
                execution_environment["app_initialization_available"] = (
                    initialization.get("status") == "initialized"
                )
                dump_json(
                    target_stage_dir(args.out_root, app_slug, target_slug)
                    / "initialization.json",
                    {**initialization, "base_snapshot": args.base_snapshot},
                )
            except Exception as exc:  # noqa: BLE001
                args.environment_error = f"{type(exc).__name__}: {exc}"
            args.execution_environment = execution_environment
            args.apps = [app_slug]
            args.targets = [f"{app_slug}/{target_slug}"]
            _, target_records = run_tests(args)
            records.extend(target_records)
        finally:
            stop_emulator(args, emulator_proc)

    args.apps = original_apps
    args.targets = original_targets
    summary = summarize_records(args, records, started_at=started_at)
    write_instrumentation_test_artifacts(
        out_root=args.out_root,
        summary=summary,
        records=records,
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"repair loop root: {repair_root(args.out_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

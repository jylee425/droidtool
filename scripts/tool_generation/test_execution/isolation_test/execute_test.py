#!/usr/bin/env python
"""Run each vanilla instrumentation case from a freshly restored snapshot."""
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
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.tool_generation._common.json_utils import project_home_path  # noqa: E402
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
    summarize_instrumentation_results,
    write_instrumentation_test_artifacts,
)
from scripts.tool_generation._common.layout import (  # noqa: E402
    repair_root,
    target_stage_dir,
)

# Tool execution

def _import_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worker(module_path: str, function_name: str, kwargs: dict[str, Any], serial: str, result_q: mp.Queue) -> None:
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


# Isolation orchestration


def run_isolated_case(
    args: argparse.Namespace,
    *,
    app_slug: str,
    target_slug: str,
    module_path: Path,
    tool_name: str,
    kwargs: dict[str, Any],
    case_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, bool]]:
    args.log_root = (
        target_stage_dir(args.out_root, app_slug, target_slug)
        / "runtime"
        / case_id
    )
    emulator_proc = None
    initialization: list[dict[str, Any]] = []
    environment = {
        "emulator_available": False,
        "emulator_booted": False,
        "snapshot_available": False,
        "adb_available": False,
        "adb_root_available": False,
        "app_initialization_available": False,
    }
    try:
        emulator_proc = start_emulator(args)
        environment["emulator_available"] = True
        environment["emulator_booted"] = True
        environment["snapshot_available"] = True
        environment["adb_root_available"] = prepare_adb_root(args)
        environment["adb_available"] = True
        initialization = initialize_apps(
            adb_path=args.adb_path,
            serial=args.emulator_serial,
            app_slugs=[app_slug],
        )
        environment["app_initialization_available"] = bool(initialization) and all(
            record.get("status") == "initialized" for record in initialization
        )
        if not all(environment.values()):
            return (
                {
                    "call_status": "environment_unavailable",
                    "exception": "Android execution environment requirements were not satisfied",
                },
                initialization,
                environment,
            )
        call_kwargs = dict(kwargs)
        call_kwargs["adb_path"] = str(args.adb_path)
        payload = run_one_tool(
            module_path=module_path,
            function_name=tool_name,
            kwargs=call_kwargs,
            serial=args.emulator_serial,
            timeout_s=args.tool_timeout_s,
        )
        return payload, initialization, environment
    except Exception as exc:  # noqa: BLE001
        return (
            {
                "call_status": "environment_unavailable",
                "exception_type": type(exc).__name__,
                "exception": str(exc),
            },
            initialization,
            environment,
        )
    finally:
        stop_emulator(args, emulator_proc)


def parse_args() -> argparse.Namespace:
    """Parse and normalize isolation-test execution arguments."""
    parser = argparse.ArgumentParser(description="Run each vanilla instrumentation case from a fresh snapshot.")
    
    paths = parser.add_argument_group("paths")
    paths.add_argument("--tool-root", dest="creation_root", type=Path, required=True)
    paths.add_argument("--output-root", dest="out_root", type=Path, required=True)
    paths.add_argument("--test-case-root", dest="test_case_root", type=Path, default=None)

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
    return args


def main() -> int:
    args = parse_args()

    apps = set(args.apps)
    targets = set(args.targets or []) or None
    case_docs = load_tests(args.test_case_root)
    if not case_docs:
        raise FileNotFoundError(
            f"no planned instrumentation cases under {args.test_case_root}"
        )
    records: list[dict[str, Any]] = []
    started_at = dt.datetime.now().isoformat(timespec="seconds")

    for app_slug, target_slug, spec_path, module_path in discover_test_targets(
        args.creation_root, apps
    ):
        if targets and target_slug not in targets and f"{app_slug}/{target_slug}" not in targets:
            continue
        loaded = case_docs.get((app_slug, target_slug))
        if not loaded:
            continue
        _, case_doc = loaded
        specs = index_tool_specs(spec_path)
        for index, case in enumerate(case_doc.get("test_cases") or [], start=1):
            if not isinstance(case, dict):
                continue
            tool_name = str(case.get("tool") or "")
            if tool_name not in specs:
                continue
            case_id = str(case.get("case_id") or f"case_{index:02d}")
            kwargs = dict(case.get("args") if isinstance(case.get("args"), dict) else {})
            planned_status = str(case.get("status") or "ready")
            fixture_requirement = str(case.get("fixture_requirement") or "")
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
                initialization = []
                environment = {
                    "emulator_available": True,
                    "emulator_booted": True,
                    "snapshot_available": True,
                    "adb_available": True,
                    "adb_root_available": True,
                    "app_initialization_available": True,
                }
            else:
                payload, initialization, environment = run_isolated_case(
                    args,
                    app_slug=app_slug,
                    target_slug=target_slug,
                    module_path=module_path,
                    tool_name=tool_name,
                    kwargs=kwargs,
                    case_id=case_id,
                )
            record = {
                "app": app_slug,
                "target": target_slug,
                "tool": tool_name,
                "case_id": case_id,
                "planned_status": planned_status,
                "fixture_requirement": fixture_requirement,
                "purpose": str(case.get("purpose") or ""),
                "expected_result": str(case.get("expected_result") or ""),
                "expected_error": bool(case.get("expected_error", False)),
                "error_contains": str(case.get("error_contains") or ""),
                "assertions": case.get("assertions") or [],
                "safety_note": str(case.get("safety_note") or ""),
                "kwargs": kwargs,
                "module": str(module_path.relative_to(REPO)),
                "execution_mode": "isolation",
                "baseline_mode": "snapshot_plus_app_initialization",
                "snapshot_restored_before_call": True,
                "app_initialization_applied": True,
                "initialization": initialization,
                **payload,
            }
            test_executable = {
                "test_artifacts_available": True,
                "tool_artifacts_available": module_path.is_file() and spec_path.is_file(),
                "test_input_context_available": planned_status != "fixture_unavailable",
                "execution_environment": environment,
            }
            record["test_executable"] = test_executable
            record["assertion_results"] = evaluate_instrumentation_assertions(record)
            record["test_passed"] = is_instrumentation_test_passed(record)
            if planned_status == "fixture_unavailable":
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
            records.append(record)
            print(
                f"{record['status'].upper():18s} {app_slug}/{target_slug}/{tool_name}",
                flush=True,
            )

    summary = {
        "stage": "isolated_instrumentation_test",
        "execution_mode": "isolation",
        "baseline_mode": "snapshot_plus_app_initialization",
        "root": project_home_path(args.creation_root),
        "adb_path": str(args.adb_path),
        "serial": args.emulator_serial,
        "base_snapshot": args.base_snapshot,
        "timeout_s": args.tool_timeout_s,
        "started_at": started_at,
        "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
        **summarize_instrumentation_results(records),
    }
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

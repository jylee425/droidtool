#!/usr/bin/env python
"""Run unit, lifecycle, or isolation test-and-repair loops."""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.tool_generation._common.json_utils import (  # noqa: E402
    dump_json,
    project_home_path,
    read_json,
)
from scripts.tool_generation._common.layout import (  # noqa: E402
    resolve_target_artifact_dir,
    target_stage_dir,
)
from scripts.tool_generation.repair.register_verified_tools import (  # noqa: E402
    register_completed_repair_tools_tool_level,
)
from scripts.tool_generation.test_execution.helper_test_results import (  # noqa: E402
    is_test_executable,
)

DEFAULT_WORK_ROOTS = {
    "lifecycle": REPO / "output_tool" / "repair_loop_lifecycle_test",
    "isolation": REPO / "output_tool" / "repair_loop_isolation_test",
    "unit": REPO / "output_tool" / "repair_loop_unit_test",
}
DEFAULT_TEST_CASE_ROOTS = {
    "lifecycle": REPO / "output_tool" / "test_generation_lifecycle_test",
    "isolation": REPO / "output_tool" / "repair_loop_isolation_test",
    "unit": REPO / "output_tool" / "test_generation_unit_test",
}
DEFAULT_REGISTRATION_ROOTS = {
    "lifecycle": REPO / "output_tool" / "registration_lifecycle_test",
    "isolation": REPO / "output_tool" / "registration_isolation_test",
    "unit": REPO / "output_tool" / "registration_unit_test",
}

# Shared state and artifact helpers

def _update_loop_state(args: argparse.Namespace, loop_state: dict[str, Any]) -> None:
    loop_state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    targets_by_app = loop_state.get("targets_by_app") or {}
    for app_slug, targets in targets_by_app.items():
        for target_slug in targets:
            dump_json(
                args.work_root / app_slug / target_slug / "repair_loop_state.json",
                _target_loop_state(loop_state, app_slug, target_slug),
            )


def _target_loop_state(
    loop_state: dict[str, Any], app_slug: str, target_slug: str
) -> dict[str, Any]:
    """Project the in-memory multi-target loop state onto one target."""

    target_key = f"{app_slug}/{target_slug}"
    state = {
        key: copy.deepcopy(value)
        for key, value in loop_state.items()
        if key
        not in {
            "targets_by_app",
            "rounds",
            "registered_targets",
            "partially_registered_targets",
            "remaining_targets",
            "terminated",
        }
    }
    state["app"] = app_slug
    state["target"] = target_slug
    state["rounds"] = []
    for round_state in loop_state.get("rounds") or []:
        target_round = {
            key: copy.deepcopy(value)
            for key, value in round_state.items()
            if key
            not in {
                "verification",
                "registration",
                "repair_results",
                "remaining_targets",
                "terminated",
                "registered_count",
            }
        }
        target_round["verification"] = [
            copy.deepcopy(row)
            for row in round_state.get("verification") or []
            if row.get("app") == app_slug
            and (
                not row.get("tested_targets")
                or target_slug in row.get("tested_targets", [])
            )
        ]
        target_round["registration"] = [
            copy.deepcopy(row)
            for row in round_state.get("registration") or []
            if row.get("app") == app_slug
            and (
                not row.get("target") or row.get("target") == target_slug
            )
        ]
        target_round["repair_results"] = [
            copy.deepcopy(row)
            for row in round_state.get("repair_results") or []
            if row.get("app") == app_slug and row.get("target") == target_slug
        ]
        target_round["remaining"] = target_slug in (
            round_state.get("remaining_targets", {}).get(app_slug, [])
        )
        target_round["terminated"] = target_key in (
            round_state.get("terminated") or []
        )
        state["rounds"].append(target_round)

    state["registered"] = target_slug in (
        loop_state.get("registered_targets", {}).get(app_slug, [])
    )
    state["partially_registered"] = target_slug in (
        loop_state.get("partially_registered_targets", {}).get(app_slug, [])
    )
    state["remaining"] = target_slug in (
        loop_state.get("remaining_targets", {}).get(app_slug, [])
    )
    state["terminated"] = target_key in (loop_state.get("terminated") or [])
    if loop_state.get("status") != "running":
        if state["registered"]:
            state["status"] = "complete"
        elif state["terminated"]:
            state["status"] = "complete_with_unverified"
        elif state["remaining"]:
            state["status"] = (
                "failed" if loop_state.get("status") == "failed"
                else "max_iterations_reached"
            )
    return state

def _get_active_targets(
    targets_by_app: dict[str, list[str]], registration_root: Path
) -> dict[str, list[str]]:

    return {
        app_slug: [
            target
            for target in targets
            if not _is_registered(registration_root, app_slug, target)
        ]
        for app_slug, targets in targets_by_app.items()
    }

def _is_registered(registration_root: Path, app_slug: str, target_slug: str) -> bool:
    verification = read_json(
        registration_root / app_slug / target_slug / "verification.json", {}
    )
    return verification.get("status") == "registered"

def _source_has_target(source_root: Path, app_slug: str, target_slug: str) -> bool:
    target_dir = resolve_target_artifact_dir(
        source_root, app_slug, target_slug, "tools.py", "tool_spec.json"
    )
    return (target_dir / "tools.py").exists() and (target_dir / "tool_spec.json").exists()

# Subprocess command helpers

def _run_subprocess(command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write("COMMAND " + " ".join(command) + "\n\n")
        log.flush()
        completed = subprocess.run(
            command,
            cwd=REPO,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        log.write(f"\nEXIT_CODE {completed.returncode}\n")
    return completed.returncode

## Helper functions for each repair-loop stage

def _run_test(
    args: argparse.Namespace,
    *,
    app_slug: str,
    active_targets: list[str],
    source_root: Path,
    round_id: str,
) -> tuple[Path, int]:
    test_root = args.work_root / f"{round_id}_test"
    for target_slug in active_targets:
        target_test_dir = args.work_root / app_slug / target_slug / f"{round_id}_test"
        if target_test_dir.exists():
            shutil.rmtree(target_test_dir)
    command = [sys.executable, str(args.test_script)]
    if args.test_mode == "unit":
        command.extend(
            [
                "--tool-root",
                str(source_root),
                "--output-root",
                str(test_root),
                "--test-case-root",
                str(args.test_case_root),
                "--tool-timeout-s",
                str(args.tool_timeout_s),
            ]
        )
    else:
        command.extend(
            [
                "--tool-root",
                str(source_root),
                "--output-root",
                str(test_root),
                "--test-case-root",
                str(args.test_case_root),
                "--adb-path",
                str(args.adb_path),
                "--emulator-path",
                str(args.emulator_path),
                "--avd-name",
                args.avd_name,
                "--emulator-serial",
                args.emulator_serial,
                "--base-snapshot",
                args.base_snapshot,
                "--boot-timeout-s",
                str(args.boot_timeout_s),
                "--tool-timeout-s",
                str(args.tool_timeout_s),
            ]
        )
    if args.test_mode == "unit":
        command.extend(["--app", app_slug])
        for target in active_targets:
            command.extend(["--target", f"{app_slug}/{target}"])
    else:
        command.extend(["--app", app_slug])
        for target in active_targets:
            command.extend(["--target", f"{app_slug}/{target}"])
    if args.test_mode != "unit" and getattr(args, "emulator_read_only", False):
        command.append("--emulator-read-only")
    target_suffix = f"_{active_targets[0]}" if len(active_targets) == 1 else ""
    return test_root, _run_subprocess(
        command,
        args.log_root / app_slug / f"{round_id}_test{target_suffix}.log",
    )

def _run_test_shard(
    args: argparse.Namespace,
    *,
    group: list[tuple[str, list[str]]],
    dict_tool_source_path: dict[tuple[str, str], Path],
    round_id: str,
    shard_index: int,
) -> list[dict[str, Any]]:
    def _shard_serial() -> str:
        prefix = "emulator-"
        if not args.emulator_serial.startswith(prefix):
            raise ValueError(f"cannot shard non-emulator serial: {args.emulator_serial}")
        port = int(args.emulator_serial[len(prefix):]) + shard_index * 2
        return f"{prefix}{port}"

    shard_args = copy.copy(args)
    if args.test_mode == "unit":
        shard_args.emulator_read_only = False
    else:
        shard_args.emulator_serial = _shard_serial()
        shard_args.emulator_read_only = args.test_shards > 1
    rows: list[dict[str, Any]] = []
    for app_slug, targets in group:
        for target_slug in targets:
            source_root = dict_tool_source_path[(app_slug, target_slug)]
            if not _source_has_target(source_root, app_slug, target_slug):
                rows.append(
                    {
                        "app": app_slug,
                        "target": target_slug,
                        "status": "missing_source",
                        "targets": [target_slug],
                    }
                )
                continue
            _, test_exit = _run_test(
                shard_args,
                app_slug=app_slug,
                active_targets=[target_slug],
                source_root=source_root,
                round_id=round_id,
            )
            rows.append(
                {
                    "app": app_slug,
                    "target": target_slug,
                    "source_root": project_home_path(source_root),
                    "tested_targets": [target_slug],
                    "test_exit_code": test_exit,
                    "test_shard": shard_index,
                    "emulator_serial": shard_args.emulator_serial,
                }
            )
    return rows


def _run_verification(
    args: argparse.Namespace,
    *,
    targets_by_app: dict[str, list[str]],
    terminated: set[tuple[str, str]],
    dict_tool_source_path: dict[tuple[str, str], Path],
    round_id: str,
    round_state: dict[str, Any],
    loop_state: dict[str, Any],
) -> tuple[Path | None, dict[str, list[str]], bool]:
    """Select and execute one round of tests across balanced app shards."""
    def _get_test_targets() -> dict[str, list[str]]:
        active = _get_active_targets(targets_by_app, args.registration_root)
        return {
            app: [
                target
                for target in targets
                if (app, target) not in terminated
            ]
            for app, targets in active.items()
            if any((app, target) not in terminated for target in targets)
        }

    def _test_case_count(app_slug: str, targets: list[str]) -> int:
        total = 0
        for target_slug in targets:
            doc = read_json(
                args.test_case_root
                / app_slug
                / target_slug
                / "test_case"
                / "test_case.json",
                {},
            )
            total += (
                len(doc.get("test_cases") or []) if isinstance(doc, dict) else 1
            )
        return max(total, 1)

    test_targets = _get_test_targets()
    if not test_targets:
        return None, {}, False

    loop_state["rounds"].append(round_state)
    _update_loop_state(args, loop_state)
    print(
        f"[global_loop] {round_id}_test apps={len(test_targets)} "
        f"targets={sum(map(len, test_targets.values()))}",
        flush=True,
    )
    test_root = args.work_root / f"{round_id}_test"
    shard_count = min(args.test_shards, len(test_targets))
    groups: list[list[tuple[str, list[str]]]] = [[] for _ in range(shard_count)]
    group_weights = [0 for _ in range(shard_count)]
    weighted_apps = sorted(
        test_targets.items(),
        key=lambda item: _test_case_count(item[0], item[1]),
        reverse=True,
    )
    for app_slug, targets in weighted_apps:
        shard_index = min(range(shard_count), key=group_weights.__getitem__)
        groups[shard_index].append((app_slug, targets))
        group_weights[shard_index] += _test_case_count(app_slug, targets)

    failed = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=shard_count) as executor:
        futures = [
            executor.submit(
                _run_test_shard,
                args,
                group=group,
                dict_tool_source_path=dict_tool_source_path,
                round_id=round_id,
                shard_index=shard_index,
            )
            for shard_index, group in enumerate(groups)
        ]
        for future in concurrent.futures.as_completed(futures):
            rows = future.result()
            round_state["verification"].extend(rows)
            if any(
                row.get("status") == "missing_source"
                or row.get("test_exit_code") != 0
                for row in rows
            ):
                failed = True
            _update_loop_state(args, loop_state)
    return test_root, test_targets, failed


def _register_tools(
    args: argparse.Namespace,
    *,
    app_slug: str,
    target_slug: str,
    source_root: Path,
    test_root: Path,
    round_id: str,
) -> int:
    command = [
        sys.executable,
        str(args.registration_script),
        "--source_root",
        str(source_root),
        "--test_root",
        str(test_root),
        "--out_root",
        str(args.registration_root),
        "--round",
        round_id,
        "--apps",
        app_slug,
        "--targets",
        f"{app_slug}/{target_slug}",
    ]
    return _run_subprocess(
        command,
        args.log_root / app_slug / f"{round_id}_registration_{target_slug}.log",
    )


def _run_registration(
    args: argparse.Namespace,
    *,
    test_targets: dict[str, list[str]],
    dict_tool_source_path: dict[tuple[str, str], Path],
    test_root: Path,
    round_id: str,
    round_state: dict[str, Any],
    loop_state: dict[str, Any],
) -> bool:
    """Register test results after the complete verification phase finishes."""
    test_rows = {
        (str(row.get("app")), str(row.get("target"))): row
        for row in round_state["verification"]
        if row.get("app") and row.get("target")
    }
    failed = False
    for app_slug, targets in test_targets.items():
        for target_slug in targets:
            test_row = test_rows.get((app_slug, target_slug), {})
            test_exit = test_row.get("test_exit_code")
            if test_exit != 0:
                round_state["registration"].append(
                    {
                        "app": app_slug,
                        "target": target_slug,
                        "status": "skipped_test_failure",
                        "exit_code": -1,
                    }
                )
                continue
            exit_code = _register_tools(
                args,
                app_slug=app_slug,
                target_slug=target_slug,
                source_root=dict_tool_source_path[(app_slug, target_slug)],
                test_root=test_root,
                round_id=round_id,
            )
            round_state["registration"].append(
                {
                    "app": app_slug,
                    "target": target_slug,
                    "status": "registered" if exit_code == 0 else "failed",
                    "exit_code": exit_code,
                }
            )
            failed = failed or exit_code != 0
    _update_loop_state(args, loop_state)
    return failed


def _run_repair(
    args: argparse.Namespace,
    *,
    targets_by_app: dict[str, list[str]],
    dict_tool_source_path: dict[tuple[str, str], Path],
    test_root: Path,
    round_id: str,
    terminated: set[tuple[str, str]],
) -> tuple[Path | None, dict[str, list[str]], list[dict[str, Any]]]:
    def _get_repair_targets() -> dict[str, list[str]]:
        def _failure_state(app_slug: str, target_slug: str) -> tuple[bool, str | None]:
            result_path = (
                target_stage_dir(test_root, app_slug, target_slug)
                / "results"
                / "test_results.json"
            )
            doc = read_json(result_path, {})
            records = doc.get("results") if isinstance(doc, dict) else None
            if not isinstance(records, list):
                return (False, "test_artifact") if args.test_mode == "unit" else (True, None)
            failures = [
                record
                for record in records
                if isinstance(record, dict) and not record.get("test_passed")
            ]
            if any(is_test_executable(record) for record in failures):
                return True, None
            if not failures:
                return False, "registration"
            if args.test_mode == "unit":
                statuses = {str(record.get("status") or "") for record in failures}
                if "unit_test_artifact_error" in statuses:
                    return False, "test_artifact"
                if "unit_test_environment_error" in statuses:
                    return False, "environment"
            return False, "non_actionable_failure"

        repair_targets = _get_active_targets(targets_by_app, args.registration_root)
        for app_slug, targets in list(repair_targets.items()):
            for target_slug in targets:
                repairable, terminal_reason = _failure_state(app_slug, target_slug)
                if (
                    terminal_reason
                    and not repairable
                    and not args.force_max_iterations
                ):
                    terminated.add((app_slug, target_slug))
            repair_targets[app_slug] = [
                target
                for target in targets
                if (app_slug, target) not in terminated
            ]
        return {app: targets for app, targets in repair_targets.items() if targets}

    remaining = _get_repair_targets()
    if not remaining:
        return None, {}, []

    print(
        f"[global_loop] {round_id}_repair targets={sum(map(len, remaining.values()))} "
        f"workers={args.workers}",
        flush=True,
    )

    def _repair_target(app_slug: str, target_slug: str) -> dict[str, Any]:
        command = [
            sys.executable,
            str(args.repair_script),
            "--creation_root",
            str(dict_tool_source_path[(app_slug, target_slug)]),
            "--verification_root",
            str(test_root),
            "--out_root",
            str(repair_root),
            "--targets",
            f"{app_slug}/{target_slug}",
            "--apps",
            app_slug,
            "--max_targets",
            "1",
            "--model",
            args.model,
            "--test_mode",
            args.test_mode,
        ]
        exit_code = _run_subprocess(
            command,
            args.log_root / app_slug / f"{round_id}_repair_{target_slug}.log",
        )
        return {
            "app": app_slug,
            "target": target_slug,
            "exit_code": exit_code,
            "artifact_available": _source_has_target(
                repair_root, app_slug, target_slug
            ),
        }

    repair_root = args.work_root / f"{round_id}_repair"
    tasks: list[tuple[str, str]] = []
    for app_slug, targets in remaining.items():
        for target_slug in targets:
            target_repair_dir = (
                args.work_root / app_slug / target_slug / f"{round_id}_repair"
            )
            if target_repair_dir.exists():
                shutil.rmtree(target_repair_dir)
            tasks.append((app_slug, target_slug))

    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                _repair_target,
                app_slug,
                target_slug,
            )
            for app_slug, target_slug in tasks
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return repair_root, remaining, sorted(
        results, key=lambda row: (str(row["app"]), str(row["target"]))
    )


def _prepare_next_round(
    args: argparse.Namespace,
    *,
    targets_by_app: dict[str, list[str]],
    dict_tool_source_path: dict[tuple[str, str], Path],
    terminated: set[tuple[str, str]],
    remaining: dict[str, list[str]],
    repair_root: Path | None,
    repair_results: list[dict[str, Any]],
    round_state: dict[str, Any],
    loop_state: dict[str, Any],
    failed: bool,
) -> tuple[bool, bool]:
    """Record the round and promote repaired artifacts for the next round."""
    round_state["registered_count"] = sum(
        1
        for app, targets in targets_by_app.items()
        for target in targets
        if _is_registered(args.registration_root, app, target)
    )
    round_state["remaining_targets"] = remaining
    round_state["terminated"] = sorted(
        f"{app}/{target}" for app, target in terminated
    )
    if not remaining:
        round_state["status"] = (
            "complete_with_unverified" if terminated else "all_registered"
        )
        _update_loop_state(args, loop_state)
        return True, failed

    round_state["repair_results"] = repair_results
    round_state["status"] = "repaired"
    if repair_root is None:
        failed = True
    else:
        for app_slug, targets in remaining.items():
            for target_slug in targets:
                if _source_has_target(repair_root, app_slug, target_slug):
                    dict_tool_source_path[(app_slug, target_slug)] = repair_root
                else:
                    failed = True
    _update_loop_state(args, loop_state)
    return False, failed

# Repair-loop lifecycle

def initialize_repair_loop(
    args: argparse.Namespace,
) -> tuple[
    dict[str, list[str]],
    dict[tuple[str, str], Path],
    int,
    dict[str, Any],
]:
    """Discover targets, restore resume sources, and create initial loop state."""

    def _discover_targets(creation_root: Path, selected_apps: set[str] | None) -> dict[str, list[str]]:
        targets: dict[str, list[str]] = {}
        for spec_path in sorted(creation_root.glob("*/*/tool_spec.json")):
            app_slug = spec_path.parent.parent.name
            target_slug = spec_path.parent.name
            if selected_apps and app_slug not in selected_apps:
                continue
            if (spec_path.parent / "tools.py").exists():
                targets.setdefault(app_slug, []).append(target_slug)
        return targets

    def _find_resume_checkpoint(
        work_root: Path,
        app_slug: str,
        active_targets: list[str],
        max_iterations: int,
    ) -> tuple[Path | None, int]:
        """Find the latest complete repair stage shared by every still-active target."""

        for iteration in range(max_iterations - 1, -1, -1):
            candidate = work_root / f"{iteration:02d}_repair"
            if all(_source_has_target(candidate, app_slug, target) for target in active_targets):
                return candidate, iteration + 1
        return None, 0

    targets_by_app = _discover_targets(
        args.creation_root, set(args.apps or []) or None
    )
    dict_tool_source_path = {
        (app, target): args.creation_root
        for app, targets in targets_by_app.items()
        for target in targets
    }
    start_iteration = 0
    resumed_sources: dict[str, str] = {}
    if args.resume:
        active = _get_active_targets(targets_by_app, args.registration_root)
        active = {app: targets for app, targets in active.items() if targets}
        next_iterations: set[int] = set()
        for app_slug, targets in active.items():
            for target_slug in targets:
                source_root, next_iteration = _find_resume_checkpoint(
                    args.work_root,
                    app_slug,
                    [target_slug],
                    args.max_iterations,
                )
                if source_root is None:
                    raise SystemExit(
                        "--resume found no complete repair round for active target "
                        f"{app_slug}/{target_slug}"
                    )
                dict_tool_source_path[(app_slug, target_slug)] = source_root
                resumed_sources[f"{app_slug}/{target_slug}"] = project_home_path(
                    source_root
                )
                next_iterations.add(next_iteration)
        if len(next_iterations) > 1:
            raise SystemExit(
                "--resume requires all active apps to share the same completed round; "
                f"found next iterations {sorted(next_iterations)}"
            )
        if next_iterations:
            start_iteration = next(iter(next_iterations))

    loop_state: dict[str, Any] = {
        "status": "running",
        "strategy": "global_test_then_batched_repair",
        "test_mode": args.test_mode,
        "creation_root": project_home_path(args.creation_root),
        "work_root": project_home_path(args.work_root),
        "registration_root": project_home_path(args.registration_root),
        "registration_level": "target_level_then_final_tool_level",
        "model": args.model,
        "max_iterations": args.max_iterations,
        "resume": args.resume,
        "start_iteration": start_iteration,
        "resumed_sources": resumed_sources,
        "workers": args.workers,
        "test_shards": args.test_shards,
        "base_snapshot": args.base_snapshot,
        "targets_by_app": copy.deepcopy(targets_by_app),
        "rounds": [],
    }
    _update_loop_state(args, loop_state)
    return targets_by_app, dict_tool_source_path, start_iteration, loop_state

def run_repair_round(
    args: argparse.Namespace,
    *,
    iteration: int,
    targets_by_app: dict[str, list[str]],
    dict_tool_source_path: dict[tuple[str, str], Path],
    terminated: set[tuple[str, str]],
    loop_state: dict[str, Any],
) -> tuple[bool, bool]:
    """Run one complete test, registration, and repair round."""
    round_id = f"{iteration:02d}"
    round_state: dict[str, Any] = {
        "round": round_id,
        "status": "testing",
        "verification": [],
        "registration": [],
    }
    test_root, test_targets, failed = _run_verification(
        args,
        targets_by_app=targets_by_app,
        terminated=terminated,
        dict_tool_source_path=dict_tool_source_path,
        round_id=round_id,
        round_state=round_state,
        loop_state=loop_state,
    )
    if test_root is None:
        return True, failed

    registration_failed = _run_registration(
        args,
        test_targets=test_targets,
        dict_tool_source_path=dict_tool_source_path,
        test_root=test_root,
        round_id=round_id,
        round_state=round_state,
        loop_state=loop_state,
    )
    failed = failed or registration_failed

    repair_root, remaining, repair_results = _run_repair(
        args,
        targets_by_app=targets_by_app,
        dict_tool_source_path=dict_tool_source_path,
        test_root=test_root,
        round_id=round_id,
        terminated=terminated,
    )
    return _prepare_next_round(
        args,
        targets_by_app=targets_by_app,
        dict_tool_source_path=dict_tool_source_path,
        terminated=terminated,
        remaining=remaining,
        repair_root=repair_root,
        repair_results=repair_results,
        round_state=round_state,
        loop_state=loop_state,
        failed=failed,
    )

def finalize_repair_loop(
    args: argparse.Namespace,
    *,
    targets_by_app: dict[str, list[str]],
    dict_tool_source_path: dict[tuple[str, str], Path],
    terminated: set[tuple[str, str]],
    loop_state: dict[str, Any],
    overall_failed: bool,
) -> int:
    """Write final registration and completion state."""

    def _register_final_passing_tools() -> bool:
        """Reuse each unfinished target's last completed test for tool salvage."""
        last_verification: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        for round_state in loop_state.get("rounds") or []:
            round_id = str(round_state.get("round") or "")
            for row in round_state.get("verification") or []:
                if not isinstance(row, dict) or row.get("test_exit_code") != 0:
                    continue
                app_slug = str(row.get("app") or "")
                target_slug = str(row.get("target") or "")
                if app_slug and target_slug and row.get("source_root"):
                    last_verification[(app_slug, target_slug)] = (round_id, row)

        failed = False
        remaining = _get_active_targets(targets_by_app, args.registration_root)
        for app_slug, targets in remaining.items():
            for target_slug in targets:
                selected = last_verification.get((app_slug, target_slug))
                if selected is None:
                    failed = True
                    continue
                round_id, row = selected
                source_text = str(row["source_root"])
                if source_text == "$PROJECT_PATH":
                    source_root = REPO
                elif source_text.startswith("$PROJECT_PATH/"):
                    source_root = REPO / source_text[len("$PROJECT_PATH/") :]
                else:
                    source_root = Path(source_text)
                register_completed_repair_tools_tool_level(
                    source_root=source_root,
                    test_root=args.work_root / f"{round_id}_test",
                    out_root=args.registration_root,
                    app_slug=app_slug,
                    target_slug=target_slug,
                    round_id=f"{round_id}_final_tool_level",
                )
        return failed

    overall_failed = _register_final_passing_tools() or overall_failed
    final_active = _get_active_targets(targets_by_app, args.registration_root)
    final_active = {
        app: [
            target
            for target in targets
            if (app, target) not in terminated
        ]
        for app, targets in final_active.items()
    }
    loop_state["registered_targets"] = {
        app: [
            target
            for target in targets
            if _is_registered(args.registration_root, app, target)
        ]
        for app, targets in targets_by_app.items()
    }
    loop_state["partially_registered_targets"] = {
        app: [
            target
            for target in targets
            if read_json(
                args.registration_root / app / target / "verification.json", {}
            ).get("status")
            == "partially_registered"
        ]
        for app, targets in targets_by_app.items()
    }
    loop_state["remaining_targets"] = {
        app: targets for app, targets in final_active.items() if targets
    }
    loop_state["terminated"] = sorted(
        f"{app}/{target}" for app, target in terminated
    )
    if overall_failed:
        loop_state["status"] = "failed"
    elif loop_state["remaining_targets"]:
        loop_state["status"] = "max_iterations_reached"
    elif terminated:
        loop_state["status"] = "complete_with_unverified"
    else:
        loop_state["status"] = "complete"
    _update_loop_state(args, loop_state)
    print(json.dumps(loop_state, ensure_ascii=False, indent=2))
    return int(overall_failed)


def parse_args() -> argparse.Namespace:
    """Parse and validate repair-loop command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Test every active app first, then repair all failed targets in one batch."
    )
    parser.add_argument(
        "--test_mode",
        choices=["unit", "lifecycle", "isolation"],
        default="lifecycle",
    )

    paths = parser.add_argument_group("paths")
    paths.add_argument("--creation_root", type=Path, required=True)
    paths.add_argument("--work_root", type=Path)
    paths.add_argument(
        "--test_case_root",
        type=Path,
        help="Frozen generated test-case root (separate from repair-loop artifacts).",
    )
    paths.add_argument("--registration_root", type=Path)
    paths.add_argument("--test_script", type=Path, required=True)
    paths.add_argument("--repair_script", type=Path, required=True)
    paths.add_argument("--registration_script", type=Path, required=True)

    loop = parser.add_argument_group("repair loop")
    loop.add_argument("--apps", nargs="+", default=["clock"])
    loop.add_argument("--max_iterations", type=int, default=16)
    loop.add_argument(
        "--force_max_iterations",
        action="store_true",
        help=(
            "Keep non-actionable failed targets active until max_iterations instead "
            "of terminating the loop early. Final tool-level salvage still runs only "
            "after the last iteration."
        ),
    )
    loop.add_argument("--workers", type=int, default=8)
    loop.add_argument("--tool_timeout_s", type=int, default=12)
    loop.add_argument(
        "--resume",
        action="store_true",
        help="Resume from the latest complete repair round for every active app.",
    )

    model = parser.add_argument_group("repair model")
    model.add_argument("--model", default="gemini-3.5-flash")

    device = parser.add_argument_group("isolation and lifecycle tests")
    device.add_argument(
        "--test_shards",
        type=int,
        default=1,
        help="Number of read-only emulator instances used for the test phase.",
    )
    device.add_argument("--adb_path", type=Path)
    device.add_argument("--emulator_path", type=Path)
    device.add_argument("--avd_name")
    device.add_argument("--emulator_serial")
    device.add_argument("--base_snapshot")
    device.add_argument("--boot_timeout_s", type=int, default=180)
    args = parser.parse_args()

    args.creation_root = args.creation_root.resolve()
    if args.work_root is None:
        args.work_root = DEFAULT_WORK_ROOTS[args.test_mode]
    if args.test_case_root is None:
        args.test_case_root = DEFAULT_TEST_CASE_ROOTS[args.test_mode]
    if args.registration_root is None:
        args.registration_root = DEFAULT_REGISTRATION_ROOTS[args.test_mode]
    args.work_root = args.work_root.resolve()
    args.test_case_root = args.test_case_root.resolve()
    args.registration_root = args.registration_root.resolve()
    for field in ("test_script", "repair_script", "registration_script"):
        script_path = getattr(args, field).resolve()
        if not script_path.is_file():
            parser.error(f"{field.replace('_', ' ')} not found: {script_path}")
        setattr(args, field, script_path)
    if args.test_mode != "unit":
        required_device_args = {
            "--adb_path": args.adb_path,
            "--emulator_path": args.emulator_path,
            "--avd_name": args.avd_name,
            "--emulator_serial": args.emulator_serial,
            "--base_snapshot": args.base_snapshot,
        }
        missing = [name for name, value in required_device_args.items() if value is None]
        if missing:
            parser.error(
                f"{args.test_mode} mode requires: {', '.join(missing)}"
            )
        args.adb_path = args.adb_path.resolve()
        args.emulator_path = args.emulator_path.resolve()
    args.log_root = args.work_root / "_loop_logs"
    if args.max_iterations < 1:
        parser.error("--max_iterations must be at least 1")
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.test_shards < 1:
        parser.error("--test_shards must be at least 1")
    return args

def main() -> int:
    args = parse_args()
    targets_by_app, dict_tool_source_path, start_iteration, loop_state = initialize_repair_loop(args)
    terminated: set[tuple[str, str]] = set()
    overall_failed = False

    for iteration in range(start_iteration, args.max_iterations):
        stop, failed = run_repair_round(
            args,
            iteration=iteration,
            targets_by_app=targets_by_app,
            dict_tool_source_path=dict_tool_source_path,
            terminated=terminated,
            loop_state=loop_state,
        )
        overall_failed = overall_failed or failed
        if stop:
            break

    return finalize_repair_loop(
        args,
        targets_by_app=targets_by_app,
        dict_tool_source_path=dict_tool_source_path,
        terminated=terminated,
        loop_state=loop_state,
        overall_failed=overall_failed,
    )


if __name__ == "__main__":
    raise SystemExit(main())

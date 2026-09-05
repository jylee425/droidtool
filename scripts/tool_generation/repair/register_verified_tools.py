#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shutil
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
    iter_stage_files,
    resolve_target_artifact_dir,
)
from scripts.tool_generation.test_execution.helper_test_results import (  # noqa: E402
    is_test_executable,
)


def declared_tool_names(tool_spec: dict[str, Any]) -> set[str]:
    return {
        str(tool.get("name"))
        for tool in tool_spec.get("tools") or []
        if isinstance(tool, dict) and tool.get("name")
    }


def _passed_tool_names(result_doc: dict[str, Any]) -> set[str]:
    """Return tools whose executable test records all passed.

    Non-executable records, such as fixture-unavailable or dependency-skipped
    cases, do not count as failures.  A tool must still have at least one
    executable record to qualify for registration.
    """
    records = [row for row in result_doc.get("results") or [] if isinstance(row, dict)]
    records_by_tool: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        tool_name = str(row.get("tool") or "")
        if tool_name and is_test_executable(row):
            records_by_tool.setdefault(tool_name, []).append(row)
    return {
        tool_name
        for tool_name, tool_records in records_by_tool.items()
        if all(row.get("test_passed") for row in tool_records)
    }


def register_tool_level(
    result_doc: dict[str, Any], tool_spec: dict[str, Any]
) -> tuple[dict[str, Any] | None, str, list[str]]:
    """Return a spec containing tools whose executable cases all passed."""
    passed_tools = _passed_tool_names(result_doc)
    if not passed_tools:
        return None, "not_passed", ["no tools passed"]
    registered_spec = dict(tool_spec)
    registered_spec["tools"] = [
        tool
        for tool in tool_spec.get("tools") or []
        if isinstance(tool, dict) and str(tool.get("name") or "") in passed_tools
    ]
    declared = declared_tool_names(tool_spec)
    status = "registered" if passed_tools == declared else "partially_registered"
    reasons = [] if status == "registered" else [
        "tools without a passing test: " + ", ".join(sorted(declared - passed_tools))
    ]
    return registered_spec, status, reasons


def register_target_level(
    result_doc: dict[str, Any], tool_spec: dict[str, Any]
) -> tuple[dict[str, Any] | None, str, list[str]]:
    """Return the complete spec only when every declared tool passed."""
    records = [row for row in result_doc.get("results") or [] if isinstance(row, dict)]
    reasons: list[str] = []
    failed = [row for row in records if not row.get("test_passed")]
    if failed:
        reasons.append(f"{len(failed)} test records did not pass")
    declared = declared_tool_names(tool_spec)
    passed_tools = _passed_tool_names(result_doc)
    missing = sorted(declared - passed_tools)
    if missing:
        reasons.append("declared tools without a passing call: " + ", ".join(missing))
    if reasons:
        return None, "not_passed", reasons
    return dict(tool_spec), "registered", []


def _register_verified_tools(
    args: argparse.Namespace, *, registration_level: str
) -> dict[str, Any]:
    args.source_root = args.source_root.resolve()
    args.test_root = args.test_root.resolve()
    args.out_root = args.out_root.resolve()
    args.out_root.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    selected_apps = set(args.apps or []) or None
    selected_targets = set(args.targets or []) or None
    for result_path in iter_stage_files(
        args.test_root, "results", "test_results.json"
    ):
        result_doc = read_json(result_path, {})
        app_slug = str(result_doc.get("app") or "")
        target_slug = str(result_doc.get("target") or "")
        if not app_slug or not target_slug:
            continue
        if selected_apps and app_slug not in selected_apps:
            continue
        if selected_targets and (
            target_slug not in selected_targets
            and f"{app_slug}/{target_slug}" not in selected_targets
        ):
            continue
        source_dir = resolve_target_artifact_dir(
            args.source_root,
            app_slug,
            target_slug,
            "tools.py",
            "tool_spec.json",
        )
        tool_spec = read_json(source_dir / "tool_spec.json", {})
        register = (
            register_tool_level
            if registration_level == "tool_level"
            else register_target_level
        )
        registered_spec, status, reasons = register(result_doc, tool_spec)
        record = {
            "app": app_slug,
            "target": target_slug,
            "status": "not_passed",
            "reasons": reasons,
            "source": project_home_path(source_dir),
            "result": project_home_path(result_path),
            "registration_level": registration_level,
        }
        if registered_spec is not None:
            destination = args.out_root / app_slug / target_slug
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source_dir, destination)
            dump_json(destination / "tool_spec.json", registered_spec)
            registered_tools = sorted(declared_tool_names(registered_spec))
            verification = {
                "status": status,
                "registration_level": registration_level,
                "verification_type": (
                    result_doc.get("test_type")
                    or (result_doc.get("summary") or {}).get("stage")
                    or "test"
                ),
                "round": args.round_id,
                "base_snapshot": (result_doc.get("summary") or {}).get("base_snapshot")
                or result_doc.get("base_snapshot"),
                "source": project_home_path(source_dir),
                "result": project_home_path(result_path),
                "tool_names": registered_tools,
            }
            dump_json(destination / "verification.json", verification)
            record["status"] = status
            record["registered_tools"] = registered_tools
            record["destination"] = project_home_path(destination)
        records.append(record)

    registered = [record for record in records if record["status"] == "registered"]
    partial = [
        record for record in records if record["status"] == "partially_registered"
    ]
    manifest = {
        "stage": "register_verified_tools",
        "round": args.round_id,
        "source_root": project_home_path(args.source_root),
        "test_root": project_home_path(args.test_root),
        "registration_root": project_home_path(args.out_root),
        "registration_level": registration_level,
        "summary": {
            "evaluated_targets": len(records),
            "registered_targets": len(registered),
            "partially_registered_targets": len(partial),
            "remaining_targets": len(records) - len(registered),
        },
        "targets": records,
    }
    # Registration is intentionally persisted per app/target.  A root-level
    # manifest made an otherwise app-scoped registration overwrite the summary
    # from an earlier app run.
    for record in records:
        app_slug = str(record["app"])
        target_slug = str(record["target"])
        target_manifest = {
            **manifest,
            "summary": {
                "evaluated_targets": 1,
                "registered_targets": int(record["status"] == "registered"),
                "partially_registered_targets": int(
                    record["status"] == "partially_registered"
                ),
                "remaining_targets": int(record["status"] != "registered"),
            },
            "targets": [record],
        }
        dump_json(
            args.out_root / app_slug / target_slug / "test_manifest.json",
            target_manifest,
        )
    return manifest


def register_verified_tools(args: argparse.Namespace) -> dict[str, Any]:
    """Register complete targets during repair; every declared tool must pass."""
    return _register_verified_tools(args, registration_level="target_level")


def register_completed_repair_tools_tool_level(
    *,
    source_root: Path,
    test_root: Path,
    out_root: Path,
    app_slug: str,
    target_slug: str,
    round_id: str,
) -> dict[str, Any]:
    """Salvage passing tools from one target after its repair loop has finished.

    Use this only as the final registration pass after target-level repair is complete. ``source_root`` must be the exact implementation root exercised by ``test_root``; do not pass a later, untested repair stage. The function does not run tests or repairs. It reuses the completed test results and registers each tool only when all of its executable test cases passed. Skipped cases and cases requiring unavailable fixtures are excluded from this decision, but a tool must have at least one executable test case.

    A fully passing target should already have been registered by :func:`register_verified_tools` and normally should not be passed here. When only a subset passed, the resulting verification status is ``partially_registered``.
    """
    args = argparse.Namespace(
        source_root=source_root,
        test_root=test_root,
        out_root=out_root,
        round_id=round_id,
        apps=[app_slug],
        targets=[f"{app_slug}/{target_slug}"],
    )
    return _register_verified_tools(args, registration_level="tool_level")


def parse_args() -> argparse.Namespace:
    """Parse verified-tool registration arguments."""
    parser = argparse.ArgumentParser(
        description="Register target modules whose declared tools all passed testing."
    )
    parser.add_argument("--source_root", type=Path, required=True)
    parser.add_argument("--test_root", type=Path, required=True)
    parser.add_argument("--out_root", type=Path, default=REPO / "output_tool" / "registration")
    parser.add_argument("--round", dest="round_id", default="00")
    parser.add_argument("--apps", nargs="+", default=["clock"])
    parser.add_argument("--targets", nargs="*", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = register_verified_tools(args)
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    print(args.out_root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Repair generated tools using normalized test evidence."""
from __future__ import annotations

import argparse
import json
import py_compile
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.tool_generation._common.json_utils import (  # noqa: E402
    dump_json,
    extract_json_object,
    project_home_path,
    read_json,
    sanitize_model_text,
    write_raw_io_log,
)
from scripts.tool_generation._common.llm import GeminiTextClient  # noqa: E402
from scripts.tool_generation._common.layout import (  # noqa: E402
    iter_stage_files,
    repair_root,
    resolve_target_artifact_dir,
    target_stage_dir,
)
from scripts.tool_generation.test_execution.helper_test_results import (  # noqa: E402
    is_test_executable,
)


REPAIR_SYSTEM = """You repair generated tools that access and control Android application state using test evidence.

Goal: Repair tool implementation or specification issues without overfitting to the verification tests.

Inputs:
- The current target-level tools.py module and tool_spec.json.
- Normalized test results for failed tools in that target.
- Tools in the same target whose existing behavior must remain protected.

Rules:

Output and Scope Rules:
- Return JSON only, with exactly the top-level keys shown in the required schema below.
- Return the full updated Python module, not a patch.
- Return the full updated tool_spec JSON object, not a partial spec.
- updated_tool_spec must be the complete tool_spec object itself; do not wrap it in an extra {"full": ...} object.
- updated_python_module must be the complete tools.py file contents as a string.
- repair_reasoning must be a brief list of concrete implementation/spec changes, including "no code repair needed" only when every failure is fixture/state-only.
- repair_target_tools must include only tools whose implementation or spec you actually changed.

Evidence Rules:
- Treat test evidence as a repair signal, not complete proof that the implementation is wrong.
- Distinguish implementation/spec defects from test-code, mock, fixture, initialization, environment, and expectation defects.
- A failure caused only by unrealistic behavior, such as a missing or malformed local fixture, placeholder-like inputs, nonexistent row ids, app-state assumptions, or invalid enum values is not by itself proof that the tool implementation is wrong.
- A failure that points to shell quoting, adb command construction, file ownership restoration, binary/text transfer, XML parsing, SQLite schema discovery, missing NOT NULL defaults, wrong app paths, or uncaught exceptions is actionable repair evidence.

Repair Rules:
- Preserve public function names and parameters unless the evidence clearly shows the schema is wrong.
- Do not remove capabilities or hard-code test values merely to make a verification case pass.
- Prefer robust fixes. Quote adb shell commands correctly, transfer binary files safely, inspect SQLite schemas at runtime, supply safe defaults for required DB columns, handle absent WAL/SHM sidecars, restore file owner/mode accurately, and parse XML defensively.
- For write tools, keep readback verification against authoritative Android state when feasible.
- For failures caused only by the test harness, mocks, fixtures, initialization, or environment, explain that no code repair is needed and leave that tool unchanged.
- Do not leave placeholder code, TODOs, pass-only stubs, or NotImplementedError.

Output Schema:
{
  "app_slug": "app slug",
  "target_slug": "target slug",
  "repair_target_tools": ["tool_name"],
  "repair_reasoning": ["brief list of concrete code/spec changes"],
  "updated_tool_spec": {"...": "complete tool_spec.json object"},
  "updated_python_module": "full contents of tools.py"
}
"""


def load_test_results(
    args: argparse.Namespace,
) -> tuple[Path, list[dict[str, Any]]]:
    """Load test records from one explicit file or distributed result files."""
    if args.test_results:
        test_results_path = args.test_results.resolve()
        test_doc = read_json(test_results_path, {})
        records = test_doc.get("results") if isinstance(test_doc, dict) else None
        if not isinstance(records, list):
            raise SystemExit(f"missing test results: {test_results_path}")
        return test_results_path, records

    distributed_paths = list(
        iter_stage_files(args.verification_root, "results", "test_results.json")
    )
    if distributed_paths:
        records = []
        for result_path in distributed_paths:
            target_doc = read_json(result_path, {})
            target_records = target_doc.get("results") if isinstance(target_doc, dict) else None
            if not isinstance(target_records, list):
                continue
            records.extend(target_records)
        if records:
            return distributed_paths[0], records
    raise SystemExit(f"missing test results under {args.verification_root}")


def get_repairing_tools(
    args: argparse.Namespace,
) -> tuple[
    Path,
    list[dict[str, Any]],
    list[tuple[tuple[str, str], list[dict[str, Any]]]],
]:
    """Load test evidence and select failed tools grouped by repair target."""
    def _get_failed_tools() -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Return repairable failed-tool records grouped by app and target."""
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        apps = set(args.apps or []) or None
        targets = set(args.targets or []) or None
        for record in records:
            app_slug = str(record.get("app") or "")
            target_slug = str(record.get("target") or "")
            if not app_slug or not target_slug:
                continue
            if apps and app_slug not in apps:
                continue
            if targets and target_slug not in targets and f"{app_slug}/{target_slug}" not in targets:
                continue
            if record.get("test_passed") or not is_test_executable(record):
                continue
            grouped[(app_slug, target_slug)].append(record)
        return grouped

    test_input, records = load_test_results(args)
    grouped = _get_failed_tools()
    selected = sorted(grouped.items())
    if args.max_targets > 0:
        selected = selected[: args.max_targets]
    if not selected:
        raise SystemExit("no failed test targets selected for repair")
    for (app_slug, target_slug), _ in selected:
        path = target_stage_dir(args.out_root, app_slug, target_slug)
        if (path / "repair.json").exists():
            raise SystemExit(f"target repair already exists: {path}")
    return test_input, records, selected


def get_protected_tools(
    records: list[dict[str, Any]], app_slug: str, target_slug: str
) -> list[str]:
    """Return passing tools whose existing behavior should be preserved."""
    return sorted(
        {
            str(record.get("tool"))
            for record in records
            if record.get("app") == app_slug
            and record.get("target") == target_slug
            and record.get("test_passed")
            and record.get("tool")
        }
    )

def build_repair_user_prompt(
    *,
    app_slug: str,
    target_slug: str,
    current_tool_spec: dict[str, Any],
    current_python_module: str,
    test_records: list[dict[str, Any]],
    protected_tools: list[str] | None = None,
) -> str:
    """Build the shared repair prompt from normalized test evidence."""

    def _normalize_test_result(
        record: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize one runner record into the shared repair-evidence schema."""
        execution_context = record.get("execution_context")
        if not isinstance(execution_context, dict):
            execution_context = {}
        inputs = record.get("inputs")
        if not isinstance(inputs, dict):
            inputs = record.get("kwargs")
        if not isinstance(inputs, dict):
            inputs = {}
        result = record.get("result")

        def _tail(value: Any, limit: int) -> str:
            return str(value or "")[-limit:]

        assertions = record.get("assertions") or []
        assertion_results = record.get("assertion_results") or []
        test_code_excerpt = str(
            execution_context.get("test_code_excerpt") or ""
        )
        if not test_code_excerpt and assertions:
            test_code_excerpt = json.dumps(
                assertions,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        stderr = str(record.get("stderr") or "")
        if assertion_results:
            assertion_output = json.dumps(
                assertion_results,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            stderr = "\n".join(
                part
                for part in (
                    _tail(stderr, 1500),
                    f"Assertion results:\n{assertion_output}",
                )
                if part
            )

        return {
            "tool": record.get("tool"),
            "case_id": record.get("case_id"),
            "test_executable": is_test_executable(record),
            "test_passed": bool(record.get("test_passed")),
            "call_status": record.get("call_status"),
            "status": record.get("status"),
            "inputs": inputs,
            "result_excerpt": _tail(
                record.get("result_excerpt")
                if record.get("result_excerpt") is not None
                else result,
                1200,
            ),
            "exception": record.get("exception"),
            "exception_type": record.get("exception_type"),
            "stdout_tail": _tail(record.get("stdout"), 2000),
            "stderr_tail": _tail(stderr, 4000),
            "return_code": record.get(
                "return_code",
                record.get("returncode", execution_context.get("return_code", execution_context.get("returncode"))),
            ),
            "test_code_excerpt": test_code_excerpt[:12000],
            "mocked_boundaries": execution_context.get("mocked_boundaries") or [],
        }

    test_results = [
        _normalize_test_result(record)
        for record in test_records
    ]
    failed_tools = [
        str(record.get("tool"))
        for record in test_records
        if record.get("tool") and not record.get("test_passed")
    ]
    payload = {
        "app_slug": app_slug,
        "target_slug": target_slug,
        "protected_tools": protected_tools or [],
        "failed_tools": failed_tools,
        "current_tool_spec": current_tool_spec,
        "current_python_module": current_python_module,
        "test_results": test_results,
        "repair_objective": [
            "Identify which test failures are actionable implementation/spec bugs.",
            "Repair actionable bugs in the generated app-state tools.",
            "Return complete replacement tools.py and tool_spec.json content.",
        ],
    }
    return "Repair this generated app-state target using verification-test evidence.\n" + json.dumps(
        payload, ensure_ascii=False, indent=2, default=str
    )


def validate_repair_response(parsed: Any) -> dict[str, Any]:
    """Validate and return one complete repair response."""
    if not isinstance(parsed, dict):
        raise ValueError("repair response must be a JSON object")
    required_keys = {
        "app_slug",
        "target_slug",
        "updated_tool_spec",
        "updated_python_module",
        "repair_reasoning",
        "repair_target_tools",
    }
    missing = sorted(required_keys - set(parsed))
    if missing:
        raise ValueError(f"repair response missing required keys: {missing}")
    if not isinstance(parsed.get("updated_python_module"), str) or not parsed["updated_python_module"].strip():
        raise ValueError("repair response missing updated_python_module")
    if not isinstance(parsed.get("updated_tool_spec"), dict):
        raise ValueError("repair response missing updated_tool_spec object")
    if not isinstance(parsed.get("repair_reasoning"), list):
        raise ValueError("repair response missing repair_reasoning list")
    if not isinstance(parsed.get("repair_target_tools"), list):
        raise ValueError("repair response missing repair_target_tools list")
    return parsed


def write_repaired_target(
    *,
    out_root: Path,
    app_slug: str,
    target_slug: str,
    repaired: dict[str, Any],
) -> None:
    """Write a validated repaired target and its repair metadata."""
    def _compile_python_source(source: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        py_compile.compile(str(path), doraise=True)

    target_dir = target_stage_dir(out_root, app_slug, target_slug)
    module_path = target_dir / "tools.py"
    spec_path = target_dir / "tool_spec.json"
    _compile_python_source(str(repaired["updated_python_module"]), module_path)
    dump_json(spec_path, repaired["updated_tool_spec"])
    dump_json(
        target_dir / "repair.json",
        {
            "app_slug": app_slug,
            "target_slug": target_slug,
            "repair_reasoning": repaired.get("repair_reasoning") or [],
            "failure_diagnoses": repaired.get("failure_diagnoses") or [],
            "repair_target_tools": repaired.get("repair_target_tools") or [],
        },
    )


def repair_target(
    *,
    client: GeminiTextClient,
    app_slug: str,
    target_slug: str,
    source_root: Path,
    out_root: Path,
    target_records: list[dict[str, Any]],
    all_records: list[dict[str, Any]],
) -> dict[str, Any]:
    source_target_dir = resolve_target_artifact_dir(
        source_root, app_slug, target_slug, "tool_spec.json", "tools.py"
    )
    out_target_dir = target_stage_dir(out_root, app_slug, target_slug)
    spec_path = source_target_dir / "tool_spec.json"
    module_path = source_target_dir / "tools.py"
    if not spec_path.exists() or not module_path.exists():
        return {
            "app_slug": app_slug,
            "target_slug": target_slug,
            "status": "skipped",
            "error": f"missing target artifacts under {source_target_dir}",
        }

    shutil.copytree(source_target_dir, out_target_dir, dirs_exist_ok=True)

    current_tool_spec = read_json(spec_path, {})
    current_python_module = module_path.read_text(encoding="utf-8", errors="replace")
    system_text = REPAIR_SYSTEM
    user_text = build_repair_user_prompt(
        app_slug=app_slug,
        target_slug=target_slug,
        current_tool_spec=current_tool_spec,
        current_python_module=current_python_module,
        test_records=target_records,
        protected_tools=get_protected_tools(all_records, app_slug, target_slug),
    )
    prompt_dir = out_target_dir / "repair_prompt"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / "system.txt").write_text(system_text, encoding="utf-8")
    (prompt_dir / "user.txt").write_text(user_text, encoding="utf-8")

    raw_response = client.generate(
        system_text=system_text,
        user_text=user_text,
    )
    write_raw_io_log(
        out_dir=out_target_dir,
        system_text=system_text,
        user_text=user_text,
        output_text=raw_response,
        metadata={
            "app_slug": app_slug,
            "target_slug": target_slug,
            "num_failed_test_records": len(target_records),
        },
    )
    parsed = validate_repair_response(
        extract_json_object(sanitize_model_text(raw_response))
    )
    write_repaired_target(
        out_root=out_root,
        app_slug=app_slug,
        target_slug=target_slug,
        repaired=parsed,
    )
    return {
        "app_slug": app_slug,
        "target_slug": target_slug,
        "status": "repaired",
        "repair_target_tools": parsed.get("repair_target_tools") or [],
        "repair_reasoning": parsed.get("repair_reasoning") or [],
        "failure_diagnoses": parsed.get("failure_diagnoses") or [],
    }


def parse_args() -> argparse.Namespace:
    """Parse and normalize tool-repair arguments."""
    parser = argparse.ArgumentParser(description="Repair generated tools using test evidence.")
    parser.add_argument(
        "--test_mode",
        choices=["unit", "isolation", "lifecycle"],
        default="lifecycle",
    )
    parser.add_argument("--creation_root", type=Path, required=True)
    parser.add_argument("--verification_root", type=Path, required=True)
    parser.add_argument("--test_results", type=Path, default=None)
    parser.add_argument("--out_root", type=Path, required=True)
    parser.add_argument("--apps", nargs="+", default=["clock"])
    parser.add_argument("--targets", nargs="*", default=None)
    parser.add_argument("--max_targets", type=int, default=0)
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--api_key_yaml", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max_output_tokens", type=int, default=65536)
    args = parser.parse_args()

    args.creation_root = args.creation_root.resolve()
    args.verification_root = args.verification_root.resolve()
    args.out_root = args.out_root.resolve()
    if args.max_targets < 0:
        parser.error("--max_targets must be at least 0")
    return args


def main() -> int:
    args = parse_args()

    test_input, records, selected = get_repairing_tools(args)
    client = GeminiTextClient(
        model=args.model,
        api_key_yaml=args.api_key_yaml,
        temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
    )

    results = []
    for (app_slug, target_slug), target_records in selected:
        print(f"[repair] {app_slug}/{target_slug} failures={len(target_records)}", flush=True)
        try:
            result = repair_target(
                client=client,
                app_slug=app_slug,
                target_slug=target_slug,
                source_root=args.creation_root,
                out_root=args.out_root,
                target_records=target_records,
                all_records=records,
            )
        except Exception as exc:  # noqa: BLE001
            result = {
                "app_slug": app_slug,
                "target_slug": target_slug,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        results.append(result)
        dump_json(
            target_stage_dir(args.out_root, app_slug, target_slug)
            / "repair_progress.json",
            {"results": [result]},
        )

    manifest = {
        "stage": "repair_test",
        "test_mode": args.test_mode,
        "source_creation_root": project_home_path(args.creation_root),
        "verification_root": project_home_path(args.verification_root),
        "test_input": str(test_input),
        "out_root": project_home_path(args.out_root),
        "model": args.model,
        "num_selected_targets": len(selected),
        "results": results,
    }
    for result in results:
        app_slug = str(result.get("app_slug") or "")
        target_slug = str(result.get("target_slug") or "")
        if app_slug and target_slug:
            dump_json(
                target_stage_dir(args.out_root, app_slug, target_slug) / "manifest.json",
                {**manifest, "results": [result]},
            )
    print(repair_root(args.out_root))
    return 0 if all(result.get("status") == "repaired" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

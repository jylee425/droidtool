#!/usr/bin/env python
"""Generate frozen, offline unittest modules for each public target tool."""
from __future__ import annotations

import argparse
import ast
import json
import os
import py_compile
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[4]
PROJECT_PATH = Path(os.environ.get("PROJECT_PATH", REPO)).resolve()
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.tool_generation._common.json_utils import (  # noqa: E402
    dump_json,
    extract_json_object,
    read_json,
    sanitize_model_text,
    write_raw_io_log,
)
from scripts.tool_generation._common.llm import GeminiTextClient  # noqa: E402
from scripts.tool_generation._common.layout import (  # noqa: E402
    iter_target_artifacts,
    target_test_case_dir,
    test_case_root,
)


DEFAULT_TOOL_ROOT = REPO / "output_tool" / "implementation"
DEFAULT_OUTPUT_ROOT = REPO / "output_tool" / "test_generation_unit_test"


def portable_project_path(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(PROJECT_PATH)
    except ValueError:
        return str(path.resolve())
    return "$PROJECT_PATH" if not relative.parts else f"$PROJECT_PATH/{relative.as_posix()}"


UNIT_SYSTEM = """You generate a Python unit test module for tools accessing and controlling internal state of Android applications.

Goal: Generate a complete, runnable Python unit test module.

Inputs:
- tool_spec: the definition of the one tool to test, including its name, parameters, and return schema.
- tool_source: the complete generated tools.py file containing that tool's implementation.

Rules: 

Output and Scope Rules:
- Generate exactly one test_cases entry for tool_spec. Do not omit it.
- Use only the Python standard library.
- Be deterministic and runnable with: python test_file.py.

Fixture Rules:
- Exercise real local parsing, SQL, XML, path, conversion, and return-shaping code whenever possible.
- Use tempfile SQLite/XML/file fixtures when the implementation supports that seam.

Mocking Rules:
- Patch only Android/external boundaries such as subprocess.run, ADB helpers, or pull/push context managers.
- Make mocked external responses realistic enough to exercise the implementation branch under test.
- Do not replace the public function under test with a mock.
- Do not mock an internal parser, transformer, query builder, or return-shaping helper when it can run against a local fixture.
- When patching a shared context manager or ADB boundary is necessary, verify both its inputs and the public tool's output or resulting local state.

Execution Rules:
- Run on the host with no emulator, adb binary, root, network, Appium, or pytest.
- Never modify generated tools.py and never call a real external command.
- Import tools.py from os.environ["GENERATED_TOOLS_PATH"] with importlib.util.
- Register the module in sys.modules before patching.
- Invoke the named public tool, not merely inspect source text.
- Test normal behavior plus at least one relevant error/edge path.

Assertion Rules:
- Assert observable behavior: returned values, local state changes, generated external-boundary calls, or documented errors.
- Include at least one real unittest assertion, assertRaises check, or Python assert statement in every generated test_* method.
- Assert the public tool's output, resulting local state, generated boundary-call arguments, or documented exception in both normal and error/edge tests.
- Do not use tests that only check that a function exists, is callable, has a signature, or contains source text.

Output Schema:
{
  "app": "app slug",
  "target": "target slug",
  "verification_mode": "unit_test",
  "test_cases": [
    {
      "case_id": "unit_<tool_name>",
      "tool": "the one requested public tool",
      "purpose": "what behavior the unit test verifies",
      "execution_spec": {
        "kind": "python_unittest",
        "test_code": "complete unittest source",
        "mocked_boundaries": ["external boundaries patched by the test"]
      },
      "expected_findings": ["implementation defects this can expose"]
    }
  ]
}
"""


def build_user(
    *,
    app_slug: str,
    target_slug: str,
    tool_spec: dict[str, Any],
    tool_source: str,
) -> str:
    payload = {
        "verification_mode": "unit_test",
        "app_slug": app_slug,
        "target_slug": target_slug,
        "tool_spec": tool_spec,
        "tool_source": tool_source[:60000],
    }
    return "Generate a planned verification case for this app-target tool.\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def _validate_response_structure(
    parsed: dict[str, Any],
    *,
    app_slug: str,
    target_slug: str,
) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError("response must be an object")
    allowed_root_fields = {"app", "target", "verification_mode", "test_cases"}
    unexpected_root_fields = set(parsed) - allowed_root_fields
    if unexpected_root_fields:
        raise ValueError(
            f"unexpected response fields: {sorted(unexpected_root_fields)}"
        )
    for field in ("app", "target"):
        if not isinstance(parsed.get(field), str) or not parsed[field].strip():
            raise ValueError(f"response {field} must be a non-empty string")
    cases = parsed.get("test_cases")
    if not isinstance(cases, list) or len(cases) != 1 or not isinstance(cases[0], dict):
        raise ValueError("response must contain exactly one test_cases entry")
    return cases[0]


def _validate_test_case_structure(
    case: dict[str, Any],
    *,
    tool_name: str,
) -> dict[str, Any]:
    allowed_case_fields = {
        "case_id",
        "tool",
        "purpose",
        "execution_spec",
        "expected_findings",
    }
    unexpected_case_fields = set(case) - allowed_case_fields
    if unexpected_case_fields:
        raise ValueError(
            f"unexpected test case fields: {sorted(unexpected_case_fields)}"
        )
    if not isinstance(case.get("case_id"), str) or not case["case_id"].strip():
        raise ValueError("test case must contain a non-empty case_id")
    if str(case.get("tool") or "") != tool_name:
        raise ValueError(f"test case must target public tool {tool_name}")
    if not isinstance(case.get("purpose"), str) or not case["purpose"].strip():
        raise ValueError("test case must contain a non-empty purpose")
    expected_findings = case.get("expected_findings")
    if not isinstance(expected_findings, list) or not all(
        isinstance(finding, str) and finding.strip() for finding in expected_findings
    ):
        raise ValueError("expected_findings must be a list of non-empty strings")
    execution_spec = case.get("execution_spec")
    if not isinstance(execution_spec, dict):
        raise ValueError("missing execution_spec")
    return execution_spec


def _validate_execution_spec_structure(execution_spec: dict[str, Any]) -> str:
    allowed_execution_fields = {"kind", "test_code", "mocked_boundaries"}
    unexpected_execution_fields = set(execution_spec) - allowed_execution_fields
    if unexpected_execution_fields:
        raise ValueError(
            f"unexpected execution_spec fields: {sorted(unexpected_execution_fields)}"
        )
    mocked_boundaries = execution_spec.get("mocked_boundaries")
    if not isinstance(mocked_boundaries, list) or not all(
        isinstance(boundary, str) and boundary.strip() for boundary in mocked_boundaries
    ):
        raise ValueError("mocked_boundaries must be a list of non-empty strings")
    code = execution_spec.get("test_code")
    if not isinstance(code, str) or "unittest" not in code:
        raise ValueError("missing complete unittest test_code")
    return code


def _validate_test_code(code: str, *, tool_name: str) -> None:
    if tool_name not in code:
        raise ValueError(f"test_code does not reference public tool {tool_name}")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"test_code is not valid Python: {exc}") from exc
    test_methods = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    if not test_methods:
        raise ValueError("test_code must define at least one test_* method")
    methods_without_assertions = []
    for method in test_methods:
        has_assertion = any(
            isinstance(node, ast.Assert)
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr.startswith("assert")
            )
            for node in ast.walk(method)
        )
        if not has_assertion:
            methods_without_assertions.append(method.name)
    if methods_without_assertions:
        raise ValueError(
            "every test_* method must contain an assertion; missing in "
            f"{methods_without_assertions}"
        )


def validate_response(
    raw: str,
    *,
    app_slug: str,
    target_slug: str,
    tool_name: str,
) -> dict[str, Any]:
    """Validate generated unit-test structure, syntax, and assertion coverage."""
    parsed = extract_json_object(sanitize_model_text(raw))
    case = _validate_response_structure(
        parsed,
        app_slug=app_slug,
        target_slug=target_slug,
    )
    execution_spec = _validate_test_case_structure(case, tool_name=tool_name)
    code = _validate_execution_spec_structure(execution_spec)
    _validate_test_code(code, tool_name=tool_name)
    return case


def parse_args() -> argparse.Namespace:
    """Parse and normalize unit-test generation arguments."""
    parser = argparse.ArgumentParser(description="Generate frozen offline unit tests per public tool.")
    parser.add_argument("--apps", nargs="+", default=["clock"])
    parser.add_argument("--targets", nargs="*", default=None)
    parser.add_argument("--max_targets", type=int, default=0)
    parser.add_argument("--manifest_path", type=Path, default=None)
    parser.add_argument("--tool-root", type=Path, default=DEFAULT_TOOL_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--api_key_yaml", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max_output_tokens", type=int, default=32768)
    parser.add_argument("--max_generation_attempts", type=int, default=4)
    args = parser.parse_args()

    args.tool_root = args.tool_root.resolve()
    args.output_root = args.output_root.resolve()
    if args.manifest_path is not None:
        args.manifest_path = args.manifest_path.resolve()
    if args.max_generation_attempts < 1:
        parser.error("--max_generation_attempts must be at least 1")
    return args


def main() -> int:
    args = parse_args()
    apps = set(args.apps or []) or None
    targets = set(args.targets or []) or None
    selected = [
        (app_slug, target_slug, target_dir / "tool_spec.json", target_dir / "tools.py")
        for app_slug, target_slug, target_dir in iter_target_artifacts(
            args.tool_root, "tool_spec.json", "tools.py"
        )
        if (not apps or app_slug in apps)
        and (
            not targets
            or target_slug in targets
            or f"{app_slug}/{target_slug}" in targets
        )
    ]
    if args.max_targets > 0:
        selected = selected[: args.max_targets]
    client = GeminiTextClient(
        model=args.model,
        api_key_yaml=args.api_key_yaml,
        temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
    )

    manifest: dict[str, Any] = {
        "stage": "unit_test_case_generation",
        "tool_root": portable_project_path(args.tool_root),
        "output_root": portable_project_path(args.output_root),
        "model": args.model,
        "targets": [],
    }
    
    any_failed = False
    for app_slug, target_slug, spec_path, module_path in selected:
        target_out = target_test_case_dir(args.output_root, app_slug, target_slug)
        target_out.mkdir(parents=True, exist_ok=True)
        spec = read_json(spec_path, {})
        source = module_path.read_text(encoding="utf-8", errors="replace")
        cases: list[dict[str, Any]] = []

        for tool in spec.get("tools") or []:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            tool_name = str(tool["name"])
            tool_dir = target_out / "unit_tests" / tool_name
            tool_dir.mkdir(parents=True, exist_ok=True)
            user_text = build_user(
                app_slug=app_slug,
                target_slug=target_slug,
                tool_spec=tool,
                tool_source=source,
            )
            status = "prompt_written"
            error = ""
            test_path = tool_dir / "test_tool.py"
            parsed: dict[str, Any] = {}

            for attempt in range(args.max_generation_attempts):
                attempt_dir = tool_dir / f"{attempt:02d}_generation"
                attempt_dir.mkdir(parents=True, exist_ok=True)
                attempt_user = user_text + (
                    f"\nPrevious validation error: {error}\nReturn corrected complete JSON."
                    if error
                    else ""
                )
                (attempt_dir / "system.txt").write_text(UNIT_SYSTEM, encoding="utf-8")
                (attempt_dir / "user.txt").write_text(attempt_user, encoding="utf-8")
                
                try:
                    raw = client.generate(system_text=UNIT_SYSTEM, user_text=attempt_user)
                    write_raw_io_log(
                        out_dir=attempt_dir,
                        system_text=UNIT_SYSTEM,
                        user_text=attempt_user,
                        output_text=raw,
                        metadata={
                            "app": app_slug,
                            "target": target_slug,
                            "tool": tool_name,
                            "attempt": attempt,
                        },
                    )
                    parsed = validate_response(
                        raw,
                        app_slug=app_slug,
                        target_slug=target_slug,
                        tool_name=tool_name,
                    )
                    execution_spec = parsed.get("execution_spec") or {}
                    test_path.write_text(str(execution_spec["test_code"]), encoding="utf-8")
                    py_compile.compile(str(test_path), doraise=True)
                    status = "generated"
                    error = ""
                    break
                except Exception as exc:  # noqa: BLE001
                    status = "generation_failed"
                    error = f"{type(exc).__name__}: {exc}"
                    dump_json(attempt_dir / "validation.json", {"valid": False, "error": error})
                    
            execution_spec = parsed.get("execution_spec") if isinstance(parsed, dict) else {}
            if not isinstance(execution_spec, dict):
                execution_spec = {}
            case = {
                "case_id": str(parsed.get("case_id") or f"unit_{tool_name}"),
                "tool": tool_name,
                "purpose": str(parsed.get("purpose") or ""),
                "execution_spec": {
                    "kind": "python_unittest",
                    "test_code": str(execution_spec.get("test_code") or ""),
                    "test_path": str(test_path.relative_to(target_out)),
                    "mocked_boundaries": execution_spec.get("mocked_boundaries") or [],
                },
                "generation_status": status,
                "generation_error": error,
                "expected_findings": parsed.get("expected_findings") or [],
            }
            cases.append(case)
            any_failed = any_failed or status == "generation_failed"
            print(f"[unit_case] {app_slug}/{target_slug}/{tool_name} {status}", flush=True)

        case_doc = {
            "app": app_slug,
            "target": target_slug,
            "verification_mode": "unit_test",
            "test_cases": cases,
        }
        dump_json(target_out / "test_case.json", case_doc)
        manifest["targets"].append(
            {
                "app": app_slug,
                "target": target_slug,
                "generated": sum(case["generation_status"] == "generated" for case in cases),
                "failed": sum(case["generation_status"] == "generation_failed" for case in cases),
            }
        )
    manifest_path = args.manifest_path or (
        test_case_root(args.output_root) / "unit_test_generation_manifest.json"
    )
    dump_json(manifest_path, manifest)
    print(test_case_root(args.output_root))
    return int(any_failed)


if __name__ == "__main__":
    raise SystemExit(main())

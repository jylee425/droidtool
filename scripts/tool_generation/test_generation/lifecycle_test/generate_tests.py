#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[4]
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
from scripts.tool_generation._common.llm import GeminiTextClient, Qwen3VLClient  # noqa: E402
from scripts.tool_generation._common.layout import (  # noqa: E402
    iter_target_artifacts,
    target_test_case_dir,
    test_case_root,
)


DEFAULT_CREATION_ROOT = REPO / "output_tool" / "implementation"
DEFAULT_LIFECYCLE_OUT_ROOT = (
    REPO / "output_tool" / "test_generation_lifecycle_test"
)
TEST_GENERATION_SYSTEM_PROMPT = """You design lightweight instrumentation-test cases for generated Android app-state tools.

Goal:
Your job is to choose practical tool arguments that are more meaningful than generic placeholders while remaining safe for a test.

Inputs:
- One app/target's tool_spec.json
- Optionally, the current generated tools.py source

Rules:

Output Rules:
- Return concise JSON only, using declared tool names and parameters.
- Do not include runtime parameters such as adb_path, serial, emulator_serial, or timeout.

Coverage Rules:
- Represent every declared public tool with at least one entry in test_cases.
- Cover every declared capability without inventing unsupported operations or arguments.
- Use meaningful read/list/detail calls for read-only targets.

Lifecycle Rules:
- Build the shortest coherent flow, normally baseline read -> create -> read -> update -> read -> delete -> final read.
- For settings, capture the original value, change and verify it, then restore and verify it when the schemas permit.
- Assign sequence_order to every case and depends_on_case_id whenever a case requires an earlier success.
- A ready case must exercise the tool's documented successful behavior.
- A ready case may use test-owned state created by an earlier successful lifecycle case.

Dependency Rules:
- Pass prior results with "$last_{target_slug}_{output_field}" or "$case_{case_id}_{output_field}" placeholders.
- Placeholders may traverse declared output schemas, such as "[0].id", but must reference an earlier case, occupy the entire string, and match the destination parameter type.
- Reuse prior test-scoped values only when evidence shows successful creation without later deletion.

Default and Optional Argument Rules:
- Try to exercise each tool's public default-argument path by omitting at least one optional parameter in a successful case instead of explicitly supplying every default.
- Pay particular attention to optional Python defaults such as None that flow into databases, providers, files, or other constrained storage; when practical, omit that specific parameter so constraints such as NOT NULL are exercised.
- Treat omission as part of an optional parameter's public contract, and avoid relying exclusively on convenient non-null test values.

Safety and Fixture Rules:
- Use clearly test-scoped values and mutate only state created by this flow or explicitly identified as test-owned; never alter arbitrary user data.
- Do not use nonexistent ids, paths, invalid enum values, or placeholder selectors such as 999, 9999, 99999, 999999, "nonexistent", or "missing" to turn positive verification into a negative-path call.
- If neither an earlier lifecycle case nor deterministic initialized state guarantees the required safe test-owned selector, the case MUST use status "fixture_unavailable", set args to {}, and describe the exact required fixture and selector in fixture_requirement.
- Apply this requirement to dependent detail, update, delete, relation, move, and rename operations; a safe error from an absent selector is not positive verification.
- A restorable device-setting change is not a valid reason to omit a tool when the original value is guaranteed to be present and can be captured safely.
- Do not omit Wi-Fi or airplane-mode tools merely from concern that they will disconnect emulator ADB. Capture the original state through declared reads, perform the change, verify it when possible, and restore it before the flow ends.
- Prefer clear-all, delete-all, reset, or argument-free destructive operations only when deterministic initialization indicates that the affected state is empty or entirely test-owned.

Error Rules:
- Use expected_error only for an intentional documented negative case and provide a stable error_contains fragment. An expected-negative case does not positively verify that public tool.
- Any case whose expected_result describes a failed, rejected, invalid, missing, or not-found response is an intentional negative case and must set expected_error=true, provide error_contains, and use an empty assertions list.
- Do not omit a bounded and reversible device-setting operation solely because an exact baseline value cannot be recovered through a type-compatible getter. Exercise the operation and finish in its conservative safe state when that state is unambiguous.

Assertion Rules:
- expected_result is a human-readable summary; assertions are the machine-checkable success criteria.
- Every non-expected-error case must include at least one meaningful assertion derived from the declared output schema, chosen test input, and prior lifecycle state.
- Use path "$" for the complete result, or dot/index traversal such as "title", "item.id", or "items[0].name".
- Supported operators are equals, contains, not_contains, exists, not_empty, length_equals, and greater_than.
- Prefer field-level equals assertions for deterministic values created, updated, or supplied by the lifecycle. Use contains only for stable text fragments, never vague words such as "success", "ok", or "result" by themselves.
- Use readback assertions to verify create/update effects and final-read assertions to verify cleanup when the output schema permits it.
- Keep baseline or initial read/list assertions intentionally light when no fixture was created earlier: typically check success and that the principal result field exists, while allowing empty collections. Prefer not to require not_empty, greater_than, exact contents, ordering, or indexed paths such as items[0] at this exploratory stage.
- For generated identifiers, prefer greater_than 0 for integers and not_empty for strings rather than exists alone.
- Prefer assertions that cover deterministic output fields named in expected_result; success alone is usually insufficient when the result schema exposes principal data fields.
- Do not assert device-generated ids, timestamps, ordering, or other unstable values exactly; assert exists, not_empty, or a stable surrounding field instead.
- fixture_unavailable and expected_error cases must use an empty assertions list; expected errors are checked with error_contains.

Output Schema:
{
  "app": "app slug",
  "target": "target slug",
  "test_cases": [
    {
      "case_id": "short_stable_id",
      "tool": "tool_name",
      "args": {"parameter": "value"},
      "purpose": "why this call is useful",
      "expected_result": "what a basic successful result should indicate",
      "assertions": [
        {"path": "result field path or $", "operator": "equals | contains | not_contains | exists | not_empty | length_equals | greater_than", "expected": "operator-specific expected value; omit only for exists and not_empty"}
      ],
      "expected_error": false,
      "error_contains": "required error text when expected_error is true, otherwise empty",
      "safety_note": "why this call is safe for testing",
      "sequence_order": 1,
      "depends_on_case_id": "prerequisite case id, or an empty string",
      "status": "ready | fixture_unavailable",
      "fixture_requirement": "required test-owned fixture, or an empty string"
    }
  ],
  "notes": ["brief caveats"]
}"""

def build_user_prompt(
    *,
    app_slug: str,
    target_slug: str,
    tool_spec: dict[str, Any],
    python_module: str | None = None,
    data_flow_aware: bool = False,
) -> str:
    planning_objective = [
        "Generate safe, meaningful instrumentation-test arguments for this app-target.",
        "Use test-scoped names and content for mutations.",
        "Omit calls that require unsafe or unavailable selectors.",
    ]
    if data_flow_aware:
        planning_objective.extend(
            [
                "Preserve the documented lifecycle data flow across ordered calls.",
                "Use earlier declared output fields as selectors for dependent calls.",
                "Clean up state created by the test flow when a safe delete tool exists.",
            ]
        )
    payload = {
        "app_slug": app_slug,
        "target_slug": target_slug,
        "tool_spec": tool_spec,
        "current_python_module_excerpt": (python_module or "")[:60000],
        "test_case_mode": (
            "relation_aware_instrumentation_test"
            if data_flow_aware
            else "instrumentation_test"
        ),
        "planning_objective": planning_objective,
    }
    return "Generate planned test cases for this app-target.\n" + json.dumps(
        payload, ensure_ascii=False, indent=2, default=str
    )

## Helper functions for validating test case structure and assertions

def _schema_types(schema: Any) -> set[str]:
    if not isinstance(schema, dict):
        return set()
    value = schema.get("type")
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {str(item) for item in value}
    return set()

def _schemas_compatible(source: Any, destination: Any) -> bool:
    source_types = _schema_types(source)
    destination_types = _schema_types(destination)
    return not source_types or not destination_types or source_types <= destination_types

def _traverse_output_schema(schema: Any, suffix: str) -> dict[str, Any]:
    current = schema if isinstance(schema, dict) else {}
    tokens = re.findall(r"(?:\[(\d+)\]|\.([A-Za-z_][A-Za-z0-9_]*))", suffix)
    consumed = "".join(
        f"[{index}]" if index else f".{field}" for index, field in tokens
    )
    if consumed != suffix:
        raise ValueError(f"unsupported placeholder suffix: {suffix}")
    for index, field in tokens:
        if index:
            if "array" not in _schema_types(current):
                raise ValueError("placeholder indexes a non-array output")
            current = current.get("items") if isinstance(current.get("items"), dict) else {}
        else:
            if "object" not in _schema_types(current):
                raise ValueError("placeholder traverses a non-object output")
            properties = current.get("properties") or {}
            if field in properties:
                current = properties[field]
            elif isinstance(current.get("additionalProperties"), dict):
                current = current["additionalProperties"]
            elif "additionalProperties" not in current or current.get("additionalProperties") is True:
                current = {}
            else:
                raise ValueError(f"placeholder output has no nested field: {field}")
    return current

def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


## Main functions for validating test case structure and assertions


def _describes_expected_error(case: dict[str, Any]) -> bool:
    """Return whether case prose explicitly describes an intentional failure."""
    text = " ".join(
        str(case.get(field) or "").lower()
        for field in ("purpose", "expected_result")
    )
    markers = (
        "failed response",
        "failure response",
        "error response",
        "failure due",
        "should fail",
        "must fail",
        "is rejected",
        "should be rejected",
        "invalid input",
        "invalid path",
        "outside allowed",
        "permission denied",
    )
    return any(marker in text for marker in markers)


def _is_unseeded_baseline_case(case: dict[str, Any]) -> bool:
    """Return whether a case reads initial state without an earlier dependency."""
    if str(case.get("depends_on_case_id") or "").strip():
        return False
    case_id = str(case.get("case_id") or "").lower()
    text = " ".join(
        str(case.get(field) or "").lower()
        for field in ("case_id", "purpose", "expected_result")
    )
    if any(marker in text for marker in ("baseline", "pre-existing")):
        return True
    return "initial" in text and bool(
        re.search(r"(?:^|_)(?:get|list|read|query)(?:_|$)", case_id)
    )


def _validate_case_semantics(
    case: dict[str, Any], returns_schema: dict[str, Any]
) -> None:
    """Validate negative-case metadata and fixture-free baseline assertions."""
    case_id = str(case["case_id"])
    expected_error = bool(case.get("expected_error"))
    error_contains = str(case.get("error_contains") or "").strip()
    if _describes_expected_error(case) and not expected_error:
        raise ValueError(
            f"{case_id}: expected_result describes an intentional failure; "
            "set expected_error=true and provide error_contains"
        )
    if not expected_error and error_contains:
        raise ValueError(f"{case_id}: error_contains requires expected_error=true")
    if not expected_error:
        def iter_strings(value: Any):
            if isinstance(value, dict):
                for child in value.values():
                    yield from iter_strings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from iter_strings(child)
            elif isinstance(value, str):
                yield value

        sentinel_markers = ("nonexistent", "non_existent", "dummy", "placeholder")
        for value in iter_strings(case.get("args") or {}):
            normalized = value.strip().lower()
            if (
                any(marker in normalized for marker in sentinel_markers)
                or re.fullmatch(r"0{8,}", normalized)
            ):
                raise ValueError(
                    f"{case_id}: positive case uses guessed or sentinel prerequisite "
                    f"value: {value}"
                )
    if not _is_unseeded_baseline_case(case):
        return
    for index, assertion in enumerate(case.get("assertions") or []):
        path = str(assertion.get("path") or "")
        operator = str(assertion.get("operator") or "")
        resolved = returns_schema
        if path != "$":
            normalized_path = path[1:] if path.startswith("$") else path
            suffix = (
                normalized_path
                if normalized_path.startswith((".", "["))
                else f".{normalized_path}"
            )
            resolved = _traverse_output_schema(returns_schema, suffix)
        collection_not_empty = operator == "not_empty" and bool(
            _schema_types(resolved) & {"array", "object"}
        )
        if (
            collection_not_empty
            or operator == "greater_than"
            or re.search(r"\[\d+\]", path)
        ):
            raise ValueError(
                f"{case_id}: assertions[{index}] assumes non-empty baseline data; "
                "use exists or a deterministic scalar assertion"
            )


def _validate_response_structure(
    raw: Any, *, app_slug: str, target_slug: str
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("test case response must be an object")
    allowed_fields = {"app", "target", "test_cases", "notes"}
    unexpected = set(raw) - allowed_fields
    if unexpected:
        raise ValueError(f"unexpected response fields: {sorted(unexpected)}")
    for field in ("app", "target"):
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            raise ValueError(f"response {field} must be a non-empty string")
    if not isinstance(raw.get("test_cases"), list):
        raise ValueError("test_cases must be a list")
    if not isinstance(raw.get("notes"), list) or not all(
        isinstance(note, str) for note in raw["notes"]
    ):
        raise ValueError("notes must be a list of strings")
    return raw


def _validate_test_case_structure(
    case: Any,
    *,
    index: int,
    tools_by_name: dict[str, dict[str, Any]],
    data_flow_aware: bool,
) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise ValueError(f"test_cases[{index - 1}] must be an object")
    mode_fields = (
        {
            "sequence_order", "depends_on_case_id", "status",
            "fixture_requirement",
        }
        if data_flow_aware
        else {"status", "fixture_requirement"}
    )
    allowed_fields = {
        "case_id", "tool", "args", "purpose", "expected_result", "assertions",
        "expected_error", "error_contains", "safety_note", *mode_fields,
    }
    unexpected = set(case) - allowed_fields
    if unexpected:
        raise ValueError(
            f"test_cases[{index - 1}] has unexpected fields: {sorted(unexpected)}"
        )
    tool = str(case.get("tool") or "")
    if tool not in tools_by_name:
        raise ValueError(f"test_cases[{index - 1}] references unknown tool: {tool}")
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError(f"test_cases[{index - 1}] requires a non-empty case_id")
    for field in ("purpose", "expected_result", "safety_note"):
        if not isinstance(case.get(field), str) or not case[field].strip():
            raise ValueError(f"{case_id}: {field} must be a non-empty string")
    if not isinstance(case.get("expected_error"), bool):
        raise ValueError(f"{case_id}: expected_error must be boolean")
    if not isinstance(case.get("args"), dict):
        raise ValueError(f"{tool}: args must be an object")
    if not isinstance(case.get("assertions"), list):
        raise ValueError(f"{case_id}: assertions must be a list")

    normalized = {
        "case_id": case_id,
        "tool": tool,
        "args": case["args"],
        "purpose": case["purpose"],
        "expected_result": case["expected_result"],
        "assertions": case["assertions"],
        "expected_error": case["expected_error"],
        "error_contains": str(case.get("error_contains") or ""),
        "safety_note": case["safety_note"],
    }
    if data_flow_aware:
        try:
            normalized["sequence_order"] = int(case.get("sequence_order") or index)
        except (TypeError, ValueError):
            normalized["sequence_order"] = index
        normalized["depends_on_case_id"] = str(case.get("depends_on_case_id") or "")
        status = str(case.get("status") or "ready").strip()
        if status not in {"ready", "fixture_unavailable"}:
            raise ValueError(f"{case_id}: unsupported case status: {status}")
        fixture_requirement = str(case.get("fixture_requirement") or "").strip()
        if status == "fixture_unavailable":
            if not fixture_requirement:
                raise ValueError(
                    f"{case_id}: fixture_unavailable requires fixture_requirement"
                )
            normalized.update(
                args={}, assertions=[], expected_error=False, error_contains=""
            )
        elif fixture_requirement:
            raise ValueError(
                f"{case_id}: ready case must use an empty fixture_requirement"
            )
        normalized.update(status=status, fixture_requirement=fixture_requirement)
    else:
        status = str(case.get("status") or "ready").strip()
        if status not in {"ready", "fixture_unavailable"}:
            raise ValueError(f"{case_id}: unsupported case status: {status}")
        fixture_requirement = str(case.get("fixture_requirement") or "").strip()
        if status == "fixture_unavailable":
            if not fixture_requirement:
                raise ValueError(
                    f"{case_id}: fixture_unavailable requires fixture_requirement"
                )
            normalized.update(
                args={}, assertions=[], expected_error=False, error_contains=""
            )
        elif fixture_requirement:
            raise ValueError(f"{case_id}: ready case must use an empty fixture_requirement")
        normalized.update(status=status, fixture_requirement=fixture_requirement)
    if normalized["expected_error"] and not normalized["error_contains"].strip():
        raise ValueError(f"{case_id}: expected_error requires error_contains")
    returns_schema = tools_by_name[tool].get("returns") or {}
    _validate_assertions(normalized, returns_schema)
    _validate_case_semantics(normalized, returns_schema)
    return normalized


def _validate_unique_case_ids(cases: list[dict[str, Any]]) -> None:
    case_ids = [str(case["case_id"]) for case in cases]
    duplicates = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
    if duplicates:
        raise ValueError(f"duplicate case_id values: {duplicates}")


def _validate_tool_coverage(
    cases: list[dict[str, Any]],
    known_tools: set[str],
) -> None:
    covered = {str(case["tool"]) for case in cases}
    unaccounted = sorted(known_tools - covered)
    if unaccounted:
        raise ValueError(
            "plan does not include test cases for declared tools: "
            + ", ".join(unaccounted)
        )


def _validate_optional_argument_defaults(
    cases: list[dict[str, Any]], tools_by_name: dict[str, dict[str, Any]]
) -> None:
    """Require every tested optional-argument tool to exercise an omitted default."""
    runtime_parameters = {"adb_path", "serial", "emulator_serial", "timeout"}
    violations: list[str] = []
    for tool_name, tool in tools_by_name.items():
        parameters = tool.get("parameters") or {}
        properties = parameters.get("properties") or {}
        required = set(parameters.get("required") or [])
        optional = set(properties) - required - runtime_parameters
        if not optional:
            continue
        successful_cases = [
            case
            for case in cases
            if case["tool"] == tool_name
            and not case.get("expected_error")
            and case.get("status") != "fixture_unavailable"
        ]
        if successful_cases and all(
            optional <= set(case.get("args") or {}) for case in successful_cases
        ):
            violations.append(tool_name)
    if violations:
        raise ValueError(
            "every successful case supplies all optional arguments for tools: "
            + ", ".join(sorted(violations))
            + "; for each listed tool, omit at least one optional argument to "
            "exercise its public default path"
        )

def _validate_assertions(case: dict[str, Any], returns_schema: dict[str, Any]) -> None:
    case_id = str(case["case_id"])
    assertions = case.get("assertions")
    if not isinstance(assertions, list):
        raise ValueError(f"{case_id}: assertions must be a list")
    expected_error = bool(case.get("expected_error"))
    unavailable = case.get("status") == "fixture_unavailable"
    if expected_error or unavailable:
        if assertions:
            raise ValueError(
                f"{case_id}: expected_error and fixture_unavailable cases require empty assertions"
            )
        return
    if not assertions:
        raise ValueError(f"{case_id}: successful case requires at least one assertion")

    supported = {
        "equals", "contains", "not_contains", "exists", "not_empty",
        "length_equals", "greater_than",
    }
    no_expected = {"exists", "not_empty"}
    for index, assertion in enumerate(assertions):
        label = f"{case_id}: assertions[{index}]"
        if not isinstance(assertion, dict):
            raise ValueError(f"{label} must be an object")
        unexpected = set(assertion) - {"path", "operator", "expected"}
        if unexpected:
            raise ValueError(f"{label} has unexpected fields: {sorted(unexpected)}")
        path = assertion.get("path")
        operator = assertion.get("operator")
        if not isinstance(path, str) or not path:
            raise ValueError(f"{label}.path must be a non-empty string")
        if operator not in supported:
            raise ValueError(f"{label}.operator is unsupported: {operator}")
        if operator in no_expected:
            if "expected" in assertion:
                raise ValueError(f"{label} must omit expected for operator {operator}")
        elif "expected" not in assertion:
            raise ValueError(f"{label} requires expected for operator {operator}")
        if operator in {"contains", "not_contains"} and not isinstance(
            assertion.get("expected"), str
        ):
            raise ValueError(f"{label}: {operator} expected must be a string")

        resolved = returns_schema
        if path != "$":
            if path.startswith("$"):
                path = path[1:]
            suffix = path if path.startswith((".", "[")) else f".{path}"
            resolved = _traverse_output_schema(returns_schema, suffix)
        resolved_types = _schema_types(resolved)
        if operator in {"contains", "not_contains"} and resolved_types and not (
            resolved_types & {"string", "array", "object"}
        ):
            raise ValueError(f"{label}: {operator} is incompatible with {sorted(resolved_types)}")
        if operator == "length_equals":
            expected = assertion.get("expected")
            if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
                raise ValueError(f"{label}: length_equals expected must be a non-negative integer")
            if resolved_types and not (resolved_types & {"string", "array", "object"}):
                raise ValueError(f"{label}: length_equals is incompatible with {sorted(resolved_types)}")
        if operator == "greater_than":
            expected = assertion.get("expected")
            if not isinstance(expected, (int, float)) or isinstance(expected, bool):
                raise ValueError(f"{label}: greater_than expected must be numeric")
            if resolved_types and not (resolved_types & {"integer", "number"}):
                raise ValueError(f"{label}: greater_than is incompatible with {sorted(resolved_types)}")
        expected_is_placeholder = (
            isinstance(assertion.get("expected"), str)
            and assertion["expected"].startswith("$")
        )
        if (
            operator == "equals"
            and "expected" in assertion
            and resolved_types
            and not expected_is_placeholder
        ):
            expected_type = _json_type(assertion["expected"])
            compatible = expected_type in resolved_types or (
                expected_type == "integer" and "number" in resolved_types
            )
            if not compatible:
                raise ValueError(
                    f"{label}: equals expected type {expected_type} is incompatible "
                    f"with {sorted(resolved_types)}"
                )


def _validate_dynamic_assertion_placeholders(
    cases: list[dict[str, Any]],
    tools_by_name: dict[str, dict[str, Any]],
    target_slug: str,
    *,
    allow_dynamic: bool,
) -> None:
    case_positions = {str(case["case_id"]): index for index, case in enumerate(cases)}

    def resolve_schema(value: str, current_index: int, current_case_id: str) -> dict[str, Any]:
        if not allow_dynamic:
            raise ValueError(
                f"{current_case_id}: assertion expected placeholders require lifecycle mode: {value}"
            )
        expression = value[1:]
        source_case: dict[str, Any] | None = None
        remainder = ""
        if expression.startswith("case_"):
            body = expression[len("case_") :]
            candidates = [
                case_id
                for case_id, position in case_positions.items()
                if position < current_index and body.startswith(case_id + "_")
            ]
            if not candidates:
                raise ValueError(
                    f"{current_case_id}: assertion placeholder does not reference an earlier case: {value}"
                )
            case_id = max(candidates, key=len)
            source_case = cases[case_positions[case_id]]
            remainder = body[len(case_id) + 1 :]
        else:
            prefix = f"last_{target_slug}_"
            if not expression.startswith(prefix):
                raise ValueError(
                    f"{current_case_id}: unsupported assertion placeholder: {value}"
                )
            remainder = expression[len(prefix) :]

        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(.*)", remainder)
        if not match:
            raise ValueError(f"{current_case_id}: malformed assertion placeholder: {value}")
        field, suffix = match.groups()
        if source_case is None:
            for candidate in reversed(cases[:current_index]):
                returns = tools_by_name[candidate["tool"]].get("returns") or {}
                if field in (returns.get("properties") or {}):
                    source_case = candidate
                    break
        if source_case is None:
            raise ValueError(
                f"{current_case_id}: no earlier output provides assertion field {field}"
            )
        returns = tools_by_name[source_case["tool"]].get("returns") or {}
        properties = returns.get("properties") or {}
        if field not in properties:
            raise ValueError(
                f"{current_case_id}: {source_case['case_id']} does not return {field}"
            )
        return _traverse_output_schema(properties[field], suffix)

    for current_index, case in enumerate(cases):
        returns = tools_by_name[case["tool"]].get("returns") or {}
        for assertion_index, assertion in enumerate(case.get("assertions") or []):
            expected = assertion.get("expected")
            if not isinstance(expected, str) or not expected.startswith("$"):
                continue
            expected_schema = resolve_schema(expected, current_index, str(case["case_id"]))
            path = str(assertion["path"])
            actual_schema = returns
            if path != "$":
                if path.startswith("$"):
                    path = path[1:]
                suffix = path if path.startswith((".", "[")) else f".{path}"
                actual_schema = _traverse_output_schema(returns, suffix)
            if not _schemas_compatible(expected_schema, actual_schema):
                raise ValueError(
                    f"{case['case_id']}: assertions[{assertion_index}] placeholder {expected} "
                    f"has type {sorted(_schema_types(expected_schema))}, incompatible with "
                    f"assertion path type {sorted(_schema_types(actual_schema))}"
                )

def _validate_dynamic_placeholders(
    cases: list[dict[str, Any]],
    tools_by_name: dict[str, dict[str, Any]],
    target_slug: str,
) -> None:
    case_positions = {str(case["case_id"]): index for index, case in enumerate(cases)}

    def validate_value(
        value: Any,
        destination_schema: dict[str, Any],
        current_index: int,
        current_case_id: str,
    ) -> None:
        if isinstance(value, dict):
            properties = destination_schema.get("properties") or {}
            for key, child in value.items():
                validate_value(child, properties.get(key, {}), current_index, current_case_id)
            return
        if isinstance(value, list):
            item_schema = destination_schema.get("items") or {}
            for child in value:
                validate_value(child, item_schema, current_index, current_case_id)
            return
        if not isinstance(value, str) or not value.startswith("$"):
            return

        expression = value[1:]
        source_case: dict[str, Any] | None = None
        remainder = ""
        if expression.startswith("case_"):
            body = expression[len("case_") :]
            candidates = [
                case_id
                for case_id, position in case_positions.items()
                if position < current_index and body.startswith(case_id + "_")
            ]
            if not candidates:
                raise ValueError(
                    f"{current_case_id}: placeholder does not reference an earlier case: {value}"
                )
            case_id = max(candidates, key=len)
            source_case = cases[case_positions[case_id]]
            remainder = body[len(case_id) + 1 :]
        else:
            prefix = f"last_{target_slug}_"
            if not expression.startswith(prefix):
                raise ValueError(f"{current_case_id}: unsupported placeholder: {value}")
            remainder = expression[len(prefix) :]

        match = re.fullmatch(
            r"([A-Za-z_][A-Za-z0-9_]*)(.*)",
            remainder,
        )
        if not match:
            raise ValueError(f"{current_case_id}: malformed placeholder: {value}")
        field, suffix = match.groups()
        if source_case is None:
            for candidate in reversed(cases[:current_index]):
                returns = tools_by_name[candidate["tool"]].get("returns") or {}
                if field in (returns.get("properties") or {}):
                    source_case = candidate
                    break
        if source_case is None:
            raise ValueError(
                f"{current_case_id}: no earlier output provides placeholder field {field}"
            )
        returns = tools_by_name[source_case["tool"]].get("returns") or {}
        properties = returns.get("properties") or {}
        if field not in properties:
            raise ValueError(
                f"{current_case_id}: {source_case['case_id']} does not return {field}"
            )
        if _is_unseeded_baseline_case(source_case):
            if re.search(r"\[\d+\]", suffix):
                raise ValueError(
                    f"{current_case_id}: placeholder {value} indexes fixture-free "
                    f"baseline output from {source_case['case_id']}"
                )
            if field not in set(returns.get("required") or []):
                raise ValueError(
                    f"{current_case_id}: placeholder {value} uses optional fixture-free "
                    f"baseline field {field}; it may be absent at runtime"
                )
        resolved_schema = _traverse_output_schema(properties[field], suffix)
        if not _schemas_compatible(resolved_schema, destination_schema):
            raise ValueError(
                f"{current_case_id}: placeholder {value} has output type "
                f"{sorted(_schema_types(resolved_schema))}, incompatible with input type "
                f"{sorted(_schema_types(destination_schema))}"
            )

    for index, case in enumerate(cases):
        parameters = tools_by_name[case["tool"]].get("parameters") or {}
        properties = parameters.get("properties") or {}
        for name, value in case.get("args", {}).items():
            validate_value(value, properties.get(name, {}), index, str(case["case_id"]))


def validate_test_case(
    raw: Any,
    *,
    app_slug: str,
    target_slug: str,
    tool_spec: dict[str, Any],
    data_flow_aware: bool = False,
) -> dict[str, Any]:
    """Validate and normalize one complete instrumentation-test plan."""
    parsed = _validate_response_structure(raw, app_slug=app_slug, target_slug=target_slug)
    tools_by_name = {
        str(tool["name"]): tool
        for tool in tool_spec.get("tools") or []
        if isinstance(tool, dict) and tool.get("name")
    }
    known_tools = set(tools_by_name)
    cases = [
        _validate_test_case_structure(
            case,
            index=index,
            tools_by_name=tools_by_name,
            data_flow_aware=data_flow_aware,
        )
        for index, case in enumerate(parsed["test_cases"], start=1)
    ]
    _validate_unique_case_ids(cases)
    _validate_dynamic_assertion_placeholders(
        cases,
        tools_by_name,
        target_slug,
        allow_dynamic=data_flow_aware,
    )
    if data_flow_aware:
        _validate_dynamic_placeholders(cases, tools_by_name, target_slug)
    _validate_tool_coverage(cases, known_tools)
    if data_flow_aware:
        _validate_optional_argument_defaults(cases, tools_by_name)
    return {
        "app": app_slug,
        "target": target_slug,
        "test_case_mode": (
            "relation_aware_instrumentation_test"
            if data_flow_aware
            else "instrumentation_test"
        ),
        "test_cases": cases,
        "notes": parsed["notes"],
    }


def parse_args() -> argparse.Namespace:
    """Parse and normalize lifecycle-test generation arguments."""
    parser = argparse.ArgumentParser(description="Generate lifecycle instrumentation-test cases.")
    parser.add_argument("--creation_root", type=Path, default=DEFAULT_CREATION_ROOT)
    parser.add_argument(
        "--out_root",
        type=Path,
        default=None,
        help="Output root for data-flow-aware lifecycle cases.",
    )
    parser.add_argument("--apps", nargs="+", default=["clock"])
    parser.add_argument("--targets", nargs="*", default=None)
    parser.add_argument("--max_targets", type=int, default=0)
    parser.add_argument("--manifest_path", type=Path, default=None)
    parser.add_argument("--provider", choices=["gemini", "qwen3_local"], default="gemini")
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--api_key_yaml", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max_output_tokens", type=int, default=32768)
    parser.add_argument(
        "--max_generation_attempts",
        type=int,
        default=4,
        help="Maximum attempts per target; stored as 00_generation, 01_generation, ...",
    )
    parser.add_argument("--qwen_min_pixels", type=int, default=256)
    parser.add_argument("--qwen_max_pixels", type=int, default=1_270_180)
    parser.add_argument("--qwen_dtype", default="bfloat16")
    parser.add_argument("--qwen_device_map", default="auto")
    parser.add_argument("--dry_run", action="store_true", help="Write prompts only; do not call Gemini.")
    args = parser.parse_args()
    args.test_case_mode = "relation_aware_instrumentation_test"
    args.creation_root = args.creation_root.resolve()
    if args.out_root is None:
        args.out_root = DEFAULT_LIFECYCLE_OUT_ROOT
    args.out_root = args.out_root.resolve()
    if args.manifest_path is not None:
        args.manifest_path = args.manifest_path.resolve()
    if args.max_targets < 0:
        parser.error("--max_targets must be at least 0")
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
            args.creation_root, "tool_spec.json", "tools.py"
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

    client = None
    if not args.dry_run:
        if args.provider == "qwen3_local":
            client = Qwen3VLClient(
                model_path=args.model,
                temperature=args.temperature,
                max_output_tokens=args.max_output_tokens,
                min_pixels=args.qwen_min_pixels,
                max_pixels=args.qwen_max_pixels,
                dtype=args.qwen_dtype,
                device_map=args.qwen_device_map,
            )
        else:
            client = GeminiTextClient(
                model=args.model,
                api_key_yaml=args.api_key_yaml,
                temperature=args.temperature,
                max_output_tokens=args.max_output_tokens,
            )

    manifest = {
        "stage": "verification_test_case_generate",
        "creation_root": project_home_path(args.creation_root),
        "out_root": project_home_path(args.out_root),
        "dry_run": args.dry_run,
        "provider": args.provider,
        "model": args.model,
        "test_case_mode": args.test_case_mode,
        "max_generation_attempts": args.max_generation_attempts,
        "targets": [],
    }
    for app_slug, target_slug, spec_path, module_path in selected:
        target_out = target_test_case_dir(args.out_root, app_slug, target_slug)
        target_out.mkdir(parents=True, exist_ok=True)
        if not args.dry_run:
            for stale_attempt in target_out.glob("[0-9][0-9]_generation"):
                shutil.rmtree(stale_attempt)
        tool_spec = read_json(spec_path, {})
        python_module = module_path.read_text(encoding="utf-8", errors="replace")
        data_flow_aware = True
        system_text = TEST_GENERATION_SYSTEM_PROMPT
        user_text = build_user_prompt(
            app_slug=app_slug,
            target_slug=target_slug,
            tool_spec=tool_spec,
            python_module=python_module,
            data_flow_aware=data_flow_aware,
        )
        status = "prompt_written"
        canonical_case = target_out / "test_case.json"
        if not args.dry_run and canonical_case.exists():
            canonical_case.unlink()
        attempts: list[dict[str, Any]] = []
        validation_errors: list[str] = []
        for attempt_index in range(args.max_generation_attempts):
            attempt_out = target_out / f"{attempt_index:02d}_generation"
            attempt_out.mkdir(parents=True, exist_ok=True)
            attempt_user_text = user_text
            if validation_errors:
                attempt_user_text += (
                    "\n\nPrevious generations were rejected by deterministic validation. "
                    "Return a complete corrected JSON object that fixes every accumulated "
                    "error below without regressing earlier fixes.\nValidation errors:\n"
                    + "\n".join(f"- {error}" for error in validation_errors)
                )
            (attempt_out / "system.txt").write_text(system_text, encoding="utf-8")
            (attempt_out / "user.txt").write_text(attempt_user_text, encoding="utf-8")
            attempt_record: dict[str, Any] = {
                "attempt": attempt_index,
                "directory": str(attempt_out),
                "status": "prompt_written",
            }
            if args.dry_run:
                attempts.append(attempt_record)
                break
            assert client is not None
            try:
                raw = client.generate(
                    system_text=system_text,
                    user_text=attempt_user_text,
                )
                write_raw_io_log(
                    out_dir=attempt_out,
                    system_text=system_text,
                    user_text=attempt_user_text,
                    output_text=raw,
                    metadata={
                        "app_slug": app_slug,
                        "target_slug": target_slug,
                        "attempt": attempt_index,
                    },
                )
                parsed = validate_test_case(
                    extract_json_object(sanitize_model_text(raw)),
                    app_slug=app_slug,
                    target_slug=target_slug,
                    tool_spec=tool_spec,
                    data_flow_aware=data_flow_aware,
                )
                dump_json(attempt_out / "test_case.json", parsed)
                dump_json(attempt_out / "validation.json", {"valid": True, "error": ""})
                dump_json(canonical_case, parsed)
                attempt_record["status"] = "validated"
                status = "generated"
                attempts.append(attempt_record)
                break
            except Exception as exc:
                validation_feedback = f"{type(exc).__name__}: {exc}"
                if validation_feedback not in validation_errors:
                    validation_errors.append(validation_feedback)
                dump_json(
                    attempt_out / "validation.json",
                    {"valid": False, "error": validation_feedback},
                )
                attempt_record["status"] = "validation_failed"
                attempt_record["error"] = validation_feedback
                attempts.append(attempt_record)
                status = "generation_failed"
        manifest["targets"].append(
            {
                "app": app_slug,
                "target": target_slug,
                "status": status,
                "test_case": str(canonical_case.relative_to(test_case_root(args.out_root))),
                "prompt_dir": str(target_out.relative_to(test_case_root(args.out_root))),
                "attempts": attempts,
            }
        )
        print(f"[test_case] {app_slug}/{target_slug} {status}", flush=True)

        dump_json(
            target_out / "manifest.json",
            {**manifest, "targets": [manifest["targets"][-1]]},
        )
    if args.manifest_path is not None:
        dump_json(args.manifest_path, manifest)
    elif len(selected) > 1:
        dump_json(test_case_root(args.out_root) / "test_case_generation_manifest.json", manifest)
    print(test_case_root(args.out_root))
    return int(any(target["status"] == "generation_failed" for target in manifest["targets"]))


if __name__ == "__main__":
    raise SystemExit(main())

"""Shared predicates for normalized repair-loop test results."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.tool_generation._common.json_utils import dump_json
from scripts.tool_generation._common.layout import (
    repair_root,
    target_stage_dir,
    target_stage_leaf,
)


# Executability predicates


def all_requirements_available(value: Any) -> bool:
    """Return whether every nested executability requirement is available."""
    if isinstance(value, dict):
        return bool(value) and all(
            all_requirements_available(item) for item in value.values()
        )
    return value is True


def is_test_executable(record: dict[str, Any]) -> bool:
    """Return whether a normalized test record can be executed."""
    return all_requirements_available(record.get("test_executable"))


# Record grouping and status counting


def group_test_records(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group normalized test records by app and target."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(str(record["app"]), str(record["target"]))].append(record)
    return grouped


def count_test_statuses(records: list[dict[str, Any]]) -> dict[str, int]:
    """Count normalized records by their status field."""
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[str(record.get("status") or "unknown")] += 1
    return dict(sorted(counts.items()))


# Unit test results


def normalize_unit_test_result(payload: dict[str, Any]) -> tuple[str, bool]:
    """Normalize an offline unittest subprocess result."""
    if payload.get("call_status") == "timeout":
        return "unit_test_timeout", True
    if payload.get("call_status") != "returned":
        return "unit_test_environment_error", False
    if payload.get("return_code") == 0:
        return "passed", True
    return "unit_test_failed", True


def summarize_unit_test_results(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize normalized offline unittest results."""
    counts = count_test_statuses(records)
    return {
        "total_tools": len(records),
        "passed": counts.get("passed", 0),
        "failed": sum(
            count for status, count in counts.items() if status != "passed"
        ),
        "by_status": counts,
    }


# Instrumentation test results


def _json_compatible(value: Any) -> Any:
    """Convert instrumentation output containers into JSON-compatible values."""
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, set):
        return sorted((_json_compatible(item) for item in value), key=str)
    if isinstance(value, Path):
        return str(value)
    return value


def _resolve_result_path(result: Any, path: str) -> tuple[bool, Any]:
    """Resolve a dotted/indexed assertion path against a tool result."""
    if path == "$":
        return True, result
    expression = path[1:] if path.startswith("$") else path
    expression = expression[1:] if expression.startswith(".") else expression
    tokens: list[tuple[str, str]] = []
    position = 0
    token_pattern = re.compile(
        r"(?:^|\.)([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]"
    )
    while position < len(expression):
        match = token_pattern.match(expression, position)
        if match is None:
            return False, None
        tokens.append((match.group(1) or "", match.group(2) or ""))
        position = match.end()
    value = result
    for field, index in tokens:
        if field:
            if not isinstance(value, dict) or field not in value:
                return False, None
            value = value[field]
        else:
            position = int(index)
            if not isinstance(value, list) or position >= len(value):
                return False, None
            value = value[position]
    return True, value


def _evaluate_assertion(actual: Any, operator: str, expected: Any) -> bool:
    """Evaluate one supported instrumentation assertion operator."""
    if operator == "equals":
        return actual == expected
    if operator in {"contains", "not_contains"}:
        if isinstance(actual, str):
            contains = str(expected) in actual
        elif isinstance(actual, (list, dict)):
            contains = str(expected) in json.dumps(
                actual,
                ensure_ascii=False,
                sort_keys=True,
            )
        else:
            return False
        return contains if operator == "contains" else not contains
    if operator == "exists":
        return True
    if operator == "not_empty":
        if actual is None:
            return False
        if isinstance(actual, (str, list, dict, tuple, set)):
            return len(actual) > 0
        return True
    if operator == "length_equals":
        return isinstance(actual, (str, list, dict, tuple, set)) and len(actual) == expected
    if operator == "greater_than":
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and actual > expected
        )
    return False


def evaluate_instrumentation_assertions(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate all declared assertions and return per-assertion diagnostics."""
    result = payload.get("result")
    assertion_results: list[dict[str, Any]] = []
    for assertion in payload.get("assertions") or []:
        path = str(assertion.get("path") or "")
        operator = str(assertion.get("operator") or "")
        found, actual = _resolve_result_path(result, path)
        passed = found and _evaluate_assertion(
            actual,
            operator,
            assertion.get("expected"),
        )
        assertion_results.append(
            {
                **assertion,
                "actual": actual if found else None,
                "path_found": found,
                "passed": passed,
            }
        )
    return assertion_results


def is_instrumentation_test_passed(payload: dict[str, Any]) -> bool:
    """Determine whether an instrumentation result satisfies its expectation."""
    if payload.get("call_status") != "returned":
        return False
    result = payload.get("result")
    expected_result = str(payload.get("expected_result") or "").lower()
    expected_error = bool(payload.get("expected_error")) or (
        "error response" in expected_result and "not found" in expected_result
    )
    if expected_error:
        if not isinstance(result, dict) or not result.get("error"):
            return False
        required_fragment = str(payload.get("error_contains") or "").strip().lower()
        if not required_fragment and "not found" in expected_result:
            required_fragment = "not found"
        return not required_fragment or required_fragment in str(
            result.get("error")
        ).lower()
    if isinstance(result, dict):
        if result.get("error"):
            return False
        if result.get("success") is False:
            return False
    assertion_results = payload.get("assertion_results")
    if not isinstance(assertion_results, list):
        assertion_results = evaluate_instrumentation_assertions(payload)
    return bool(assertion_results) and all(
        assertion.get("passed") is True for assertion in assertion_results
    )


def summarize_instrumentation_results(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize common isolation and lifecycle execution outcomes."""
    return {
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
    }


def write_instrumentation_test_artifacts(
    *,
    out_root: Path,
    summary: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write target result documents and manifests for instrumentation tests."""
    manifest = {
        "stage": str(summary.get("stage") or "verification_test"),
        "execution_mode": summary.get("execution_mode"),
        "summary": summary,
        "targets": [],
    }
    for (app_slug, target_slug), target_records in sorted(
        group_test_records(records).items()
    ):
        target_summary = summarize_instrumentation_results(target_records)
        target_doc = {
            "app": app_slug,
            "target": target_slug,
            "test_type": str(summary.get("stage") or "verification_test"),
            "execution_mode": summary.get("execution_mode"),
            "base_snapshot": summary.get("base_snapshot"),
            "baseline_mode": summary.get("baseline_mode"),
            "summary": target_summary,
            "results": target_records,
        }
        result_path = target_stage_leaf(
            out_root, app_slug, target_slug, "results"
        ) / "test_results.json"
        dump_json(result_path, _json_compatible(target_doc))
        target_manifest = {
            "app": app_slug,
            "target": target_slug,
            "test_results": str(result_path.relative_to(repair_root(out_root))),
            "summary": target_summary,
        }
        manifest["targets"].append(target_manifest)
        dump_json(
            target_stage_dir(out_root, app_slug, target_slug) / "manifest.json",
            {**manifest, "targets": [target_manifest]},
        )
    return manifest

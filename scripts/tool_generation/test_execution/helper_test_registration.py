"""Shared discovery and loading helpers for repair-loop tests."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.tool_generation._common.json_utils import read_json
from scripts.tool_generation._common.layout import (
    iter_stage_artifacts,
    iter_test_case_files,
)


# Target discovery


def discover_test_targets(
    tool_root: Path,
    apps: set[str] | None,
) -> list[tuple[str, str, Path, Path]]:
    """Find targets containing both a tool specification and implementation."""
    targets: list[tuple[str, str, Path, Path]] = []
    if tool_root.exists():
        for app_dir in sorted(path for path in tool_root.iterdir() if path.is_dir()):
            if apps and app_dir.name not in apps:
                continue
            for target_dir in sorted(path for path in app_dir.iterdir() if path.is_dir()):
                spec_path = target_dir / "tool_spec.json"
                module_path = target_dir / "tools.py"
                if spec_path.exists() and module_path.exists():
                    targets.append(
                        (app_dir.name, target_dir.name, spec_path, module_path)
                    )
    if not targets:
        for app_slug, target_slug, target_dir in iter_stage_artifacts(
            tool_root, "tool_spec.json", "tools.py"
        ):
            if not apps or app_slug in apps:
                targets.append(
                    (
                        app_slug,
                        target_slug,
                        target_dir / "tool_spec.json",
                        target_dir / "tools.py",
                    )
                )
    return targets


# Test document loading


def load_tests(
    root: Path,
) -> dict[tuple[str, str], tuple[Path, dict[str, Any]]]:
    """Load target test documents and retain their artifact directories."""
    tests: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}
    for path in iter_test_case_files(root):
        document = read_json(path, {})
        if not isinstance(document, dict) or not isinstance(
            document.get("test_cases"), list
        ):
            continue
        app_slug = str(document.get("app") or path.parents[2].name)
        target_slug = str(document.get("target") or path.parents[1].name)
        tests[(app_slug, target_slug)] = (path.parent, document)
    return tests


# Tool specification indexing


def index_tool_specs(spec_path: Path) -> dict[str, dict[str, Any]]:
    """Index the public tool specifications by tool name."""
    spec = read_json(spec_path, {})
    return {
        str(tool.get("name")): tool
        for tool in spec.get("tools") or []
        if isinstance(tool, dict) and tool.get("name")
    }

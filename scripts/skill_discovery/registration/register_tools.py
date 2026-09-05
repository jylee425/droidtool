#!/usr/bin/env python3
"""Register generated app-state tools into the eval-compatible registry shape."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.tool_generation._common.json_utils import dump_json, dump_text, slugify  # noqa: E402


SCHEMA_VERSION = "app_state_tool_application.v1"


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_REPO))
    except ValueError:
        return str(path)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _function_tool(action_name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": action_name,
            "description": description,
            "parameters": parameters,
        },
    }


def _tool_entry(
    *,
    app: str,
    app_slug: str,
    target_slug: str,
    tool: dict[str, Any],
    tools_py: Path,
) -> dict[str, Any]:
    tool_name = str(tool.get("name") or "").strip()
    if not tool_name:
        raise ValueError(f"{tools_py.parent} has a tool_spec entry without name")
    tool_slug = slugify(tool_name)
    action_name = f"app_state_{app_slug}_{tool_slug}"
    description = str(tool.get("description") or f"{tool_name} app-state tool.")
    parameters = tool.get("parameters") if isinstance(tool.get("parameters"), dict) else {}
    returns = tool.get("returns") if isinstance(tool.get("returns"), dict) else {}
    state_surfaces = tool.get("state_surfaces") if isinstance(tool.get("state_surfaces"), list) else []
    safety = tool.get("safety") if isinstance(tool.get("safety"), dict) else {}
    safety.setdefault("destructive", str(tool.get("operation") or "") in {"write", "delete", "read_write"})
    safety.setdefault("preconditions", [])
    return {
        "schema_version": SCHEMA_VERSION,
        "app": app,
        "app_slug": app_slug,
        "target": target_slug,
        "tool": tool_name,
        "tool_slug": tool_slug,
        "action_name": action_name,
        "description": description,
        "operation": str(tool.get("operation") or "read"),
        "runtime": {
            "kind": "python_function",
            "python_module": _repo_relative(tools_py),
            "function": tool_name,
            "default_kwargs": {"adb_path": "adb"},
        },
        "parameters": parameters,
        "returns": returns,
        "state_surfaces": state_surfaces,
        "safety": safety,
        "function_tool": _function_tool(action_name, description, parameters),
    }


def discover_tool_specs(creation_app_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    specs: list[tuple[Path, dict[str, Any]]] = []
    for spec_path in sorted(creation_app_dir.glob("*/tool_spec.json")):
        tools_py = spec_path.parent / "tools.py"
        if not tools_py.exists():
            raise FileNotFoundError(f"missing tools.py next to {spec_path}")
        spec = _load_json(spec_path)
        if not isinstance(spec, dict):
            raise ValueError(f"{spec_path} must contain a JSON object")
        specs.append((tools_py, spec))
    return specs


def register_app(
    *,
    app_slug: str,
    creation_root: Path,
    out_root: Path,
    allow_empty: bool = False,
) -> dict[str, Any]:
    creation_app_dir = creation_root / app_slug
    if not creation_app_dir.exists():
        raise FileNotFoundError(f"missing creation app directory: {creation_app_dir}")
    out_dir = out_root / app_slug
    out_dir.mkdir(parents=True, exist_ok=True)

    registry_tools: list[dict[str, Any]] = []
    for tools_py, spec in discover_tool_specs(creation_app_dir):
        app = str(spec.get("app") or app_slug)
        target_slug = slugify(str(spec.get("tool_namespace") or tools_py.parent.name).split(".")[-1])
        tool_specs = spec.get("tools") or []
        if not isinstance(tool_specs, list):
            raise ValueError(f"{tools_py.parent / 'tool_spec.json'} has no list-valued tools")
        for tool in tool_specs:
            if not isinstance(tool, dict):
                continue
            registry_tools.append(
                _tool_entry(
                    app=app,
                    app_slug=app_slug,
                    target_slug=target_slug,
                    tool=tool,
                    tools_py=tools_py,
                )
            )

    if not registry_tools and not allow_empty:
        raise ValueError(f"no generated tools found under {creation_app_dir}")

    action_space_tools = [entry["function_tool"] for entry in registry_tools]
    manifest_tools = [
        {
            "tool": entry["tool"],
            "tool_slug": entry["tool_slug"],
            "action_name": entry["action_name"],
            "operation": entry["operation"],
            "target": entry.get("target"),
        }
        for entry in registry_tools
    ]
    app_name = str(registry_tools[0].get("app") or app_slug) if registry_tools else app_slug
    registry = {"tools": registry_tools}
    manifest = {
        "workflow": "skill_discovery_app_state_tool_pipeline",
        "stage": "registration",
        "granularity": "app",
        "app": app_name,
        "app_slug": app_slug,
        "tools": manifest_tools,
        "outputs": [
            "manifest.json",
            "registry.json",
            "action_space_tools.json",
            "registration_summary.md",
        ],
    }
    summary_lines = [
        f"# Registration: {app_slug}",
        "",
        f"- App: `{app_name}`",
        f"- Tools: {len(registry_tools)}",
        "",
        "| Tool | Action name | Operation | Runtime |",
        "| --- | --- | --- | --- |",
    ]
    for entry in registry_tools:
        summary_lines.append(
            "| "
            + " | ".join(
                [
                    f"`{entry['tool']}`",
                    f"`{entry['action_name']}`",
                    f"`{entry['operation']}`",
                    f"`{entry['runtime']['python_module']}`",
                ]
            )
            + " |"
        )

    dump_json(out_dir / "registry.json", registry)
    dump_json(out_dir / "action_space_tools.json", action_space_tools)
    dump_json(out_dir / "manifest.json", manifest)
    dump_text(out_dir / "registration_summary.md", "\n".join(summary_lines).rstrip() + "\n")
    return {
        "app_slug": app_slug,
        "registration_dir": str(out_dir),
        "registry_json": str(out_dir / "registry.json"),
        "action_space_tools_json": str(out_dir / "action_space_tools.json"),
        "manifest_json": str(out_dir / "manifest.json"),
        "registration_summary_md": str(out_dir / "registration_summary.md"),
        "tool_count": len(registry_tools),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, help="App slug to register.")
    parser.add_argument("--creation_root", type=Path, default=_REPO / "output_skill/creation/tools")
    parser.add_argument("--out_root", type=Path, default=_REPO / "output_skill/registration/apps")
    parser.add_argument("--allow_empty", action="store_true")
    args = parser.parse_args()
    result = register_app(
        app_slug=slugify(args.app),
        creation_root=args.creation_root,
        out_root=args.out_root,
        allow_empty=args.allow_empty,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

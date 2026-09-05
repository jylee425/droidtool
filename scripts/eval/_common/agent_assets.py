#!/usr/bin/env python3
"""Prepare and resolve log-local assets for eval agent variants."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[3]
AGENT_TYPES = (
    "gui",
    "gui_cli",
    "gui_unit",
    "gui_isolation",
    "gui_lifecycle",
    "gui_unverified",
    "gui_skill",
)
MATERIALIZED_AGENT_TYPES = (
    "gui_unit",
    "gui_isolation",
    "gui_lifecycle",
    "gui_unverified",
    "gui_skill",
)
GENERATED_SOURCES = {
    "gui_unit": REPO / "output_tool" / "registration_unit_test",
    "gui_isolation": REPO / "output_tool" / "registration_isolation_test",
    "gui_lifecycle": REPO / "output_tool" / "registration_lifecycle_test",
    "gui_unverified": REPO / "output_tool" / "implementation",
}


@dataclass(frozen=True)
class AgentAssets:
    agent_type: str
    registry_path: Path | None = None
    skill_text: str = ""
    enable_cli_command: bool = False


def _prepare_generated(agent_type: str, destination: Path) -> None:
    source = GENERATED_SOURCES[agent_type]
    if not source.is_dir():
        raise FileNotFoundError(source)
    shutil.copytree(source, destination, dirs_exist_ok=True)


def _prepare_skills(destination: Path) -> None:
    source = REPO / "output_skill" / "skill"
    if not source.is_dir():
        raise FileNotFoundError(
            f"missing downloaded skill source: {source}; "
            "sync the skill artifacts into output_skill/skill first"
        )
    for path in sorted(source.glob("*.md")):
        app_dir = destination / path.stem
        app_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, app_dir / "skill.md")


def prepare_agent_assets(*, agent_type: str, log_dir: Path, force: bool = False) -> Path:
    if agent_type not in MATERIALIZED_AGENT_TYPES:
        raise ValueError(
            f"{agent_type!r} has no materialized assets; choose from "
            f"{', '.join(MATERIALIZED_AGENT_TYPES)}"
        )
    destination = log_dir / "_registration"
    if destination.exists() and force:
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if agent_type == "gui_skill":
        _prepare_skills(destination)
    else:
        _prepare_generated(agent_type, destination)
    return destination


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _app_names(raw: str) -> list[str]:
    return list(dict.fromkeys(_slug(x) for x in raw.split(",") if x.strip()))


def _tool_entry(app: str, tools_py: Path, tool: dict[str, Any]) -> dict[str, Any]:
    name = str(tool.get("name") or "").strip()
    if not name:
        raise ValueError(f"tool without name in {tools_py.parent / 'tool_spec.json'}")
    action_name = f"app_state_{app}_{_slug(name)}"
    description = str(tool.get("description") or f"{name} app-state tool.")
    parameters = tool.get("parameters") if isinstance(tool.get("parameters"), dict) else {}
    return {
        "app": app,
        "app_slug": app,
        "tool": name,
        "tool_slug": _slug(name),
        "action_name": action_name,
        "description": description,
        "operation": str(tool.get("operation") or "read"),
        "runtime": {
            "kind": "python_function",
            "python_module": str(tools_py.resolve()),
            "function": name,
            "default_kwargs": {"adb_path": "adb"},
        },
        "parameters": parameters,
        "returns": tool.get("returns") if isinstance(tool.get("returns"), dict) else {},
        "state_surfaces": tool.get("state_surfaces") if isinstance(tool.get("state_surfaces"), list) else [],
        "safety": tool.get("safety") if isinstance(tool.get("safety"), dict) else {},
        "function_tool": {
            "type": "function",
            "function": {"name": action_name, "description": description, "parameters": parameters},
        },
    }


def _app_tool_pairs(app_dir: Path) -> list[tuple[Path, Path]]:
    direct = (app_dir / "tools.py", app_dir / "tool_spec.json")
    if direct[0].is_file() or direct[1].is_file():
        if not all(path.is_file() for path in direct):
            raise FileNotFoundError(f"incomplete app registration bundle: {app_dir}")
        return [direct]
    pairs: list[tuple[Path, Path]] = []
    for target_dir in sorted(path for path in app_dir.iterdir() if path.is_dir()):
        tools_py, tool_spec = target_dir / "tools.py", target_dir / "tool_spec.json"
        if tools_py.is_file() or tool_spec.is_file():
            if not tools_py.is_file() or not tool_spec.is_file():
                raise FileNotFoundError(f"incomplete target registration bundle: {target_dir}")
            pairs.append((tools_py, tool_spec))
    return pairs


def resolve_agent_assets(
    *, agent_type: str, log_dir: Path, app_slugs: str, runtime_dir: Path
) -> AgentAssets:
    if agent_type not in AGENT_TYPES:
        raise ValueError(f"unknown agent_type {agent_type!r}; choose from {', '.join(AGENT_TYPES)}")
    apps = _app_names(app_slugs)
    registration = log_dir / "_registration"
    if agent_type == "gui":
        return AgentAssets(agent_type)
    if agent_type == "gui_cli":
        return AgentAssets(agent_type, enable_cli_command=True)
    if not apps:
        raise ValueError(f"{agent_type} requires --app_slugs")
    if agent_type == "gui_skill":
        guides: list[str] = []
        for app in apps:
            path = registration / app / "skill.md"
            if not path.is_file():
                raise FileNotFoundError(f"missing skill registration: {path}")
            guides.append(path.read_text(encoding="utf-8").strip())
        return AgentAssets(agent_type, skill_text="\n\n".join(guides))

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for app in apps:
        app_dir = registration / app
        if not app_dir.is_dir():
            raise FileNotFoundError(f"missing tool registration: {app_dir}")
        pairs = _app_tool_pairs(app_dir)
        if not pairs:
            raise FileNotFoundError(f"no tools.py/tool_spec.json under {app_dir}")
        for tools_py, spec_path in pairs:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            for tool in spec.get("tools") or []:
                entry = _tool_entry(app, tools_py, tool)
                action = entry["action_name"]
                if action in seen:
                    raise ValueError(f"duplicate registered action {action} under {app_dir}")
                seen.add(action)
                entries.append(entry)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    registry_path = runtime_dir / "registry.json"
    registry_path.write_text(
        json.dumps({"apps": apps, "tools": entries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return AgentAssets(agent_type, registry_path=registry_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize a log-local registration bundle for an eval agent."
    )
    parser.add_argument(
        "--agent_type", choices=MATERIALIZED_AGENT_TYPES, required=True
    )
    parser.add_argument("--log_dir", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(
        prepare_agent_assets(
            agent_type=args.agent_type, log_dir=args.log_dir, force=args.force
        )
    )


if __name__ == "__main__":
    main()

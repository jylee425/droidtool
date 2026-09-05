#!/usr/bin/env python3
"""Generate LLM-agent GUI skill-discovery exploration proposals."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.tool_generation._common.json_utils import (  # noqa: E402
    dump_json,
    slugify,
)


DEFAULT_OUT_ROOT = _REPO / "output_skill"
DEFAULT_ASSET_ROOT = _REPO / "scripts" / "skill_discovery" / "_asset"


EXPLORATION_PATTERNS: dict[str, dict[str, Any]] = {
    "ux_overview": {
        "phases": ["home", "startup", "layout"],
        "instruction": (
            "From a fresh launch, handle the startup experience, reach the home screen, and document "
            "the primary layout, navigation structure, visible content, and most important action controls."
        ),
    },
    "settings_configuration": {
        "phases": ["settings_page", "setting_configure"],
        "instruction": (
            "Find and open the app's settings page, identify configurable options, change one safe and "
            "reversible setting, and verify through visible UI evidence that the new value was applied."
        ),
    },
    "entity_lifecycle": {
        "phases": ["feature", "list", "create", "update", "delete"],
        "instruction": (
            "First identify one primary user-manageable target entity supported by the visible UI. Then "
            "complete its lifecycle in one continuous flow: inspect the entity list, "
            "create a uniquely identifiable test entity, verify it, update only that entity and verify the "
            "change, then delete the same entity and verify its removal."
        ),
    },
}


def _default_system_doc(app: str, asset_root: Path) -> Path:
    return asset_root / f"{slugify(app)}.md"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def normalize_proposal(proposal: Any, *, app: str, app_slug: str, system_doc: Path) -> dict[str, Any]:
    if not isinstance(proposal, dict):
        raise ValueError("proposal must be a JSON object")
    proposal = dict(proposal)
    proposal["workflow"] = "gui_skill_discovery_proposal.v3"
    proposal["app"] = proposal.get("app") or app
    proposal["app_slug"] = proposal.get("app_slug") or app_slug
    proposal["llm_agent_role"] = "sandbox_exploration_instruction_generator"
    summary = proposal.get("developer_document_summary")
    if not isinstance(summary, dict):
        summary = {}
    summary.setdefault("document_path", str(system_doc))
    summary.setdefault("document_refs_used", [])
    summary.setdefault("weak_or_missing_grounding", [])
    proposal["developer_document_summary"] = summary

    raw_tasks = _list(proposal.get("exploration_tasks"))
    by_pattern = {
        str(task.get("pattern_key")): task
        for task in raw_tasks
        if isinstance(task, dict) and str(task.get("pattern_key")) in EXPLORATION_PATTERNS
    }
    normalized_tasks = []
    for pattern_key, pattern in EXPLORATION_PATTERNS.items():
        task = dict(by_pattern.get(pattern_key) or {})
        task_id = f"{app_slug}_{pattern_key}"
        task["id"] = task_id
        task["pattern_family"] = "exploratory"
        task["pattern_key"] = pattern_key
        task["phases"] = list(pattern["phases"])
        task["max_steps"] = 30
        task["instruction"] = str(task.get("instruction") or f"In {app}, {pattern['instruction']}")
        task["success_criteria"] = _list(task.get("success_criteria")) or [
            f"The instruction is completed in {app} with visible GUI evidence.",
            "The final screen or trajectory contains concrete evidence, not only an attempted action.",
        ]
        task["observations_to_collect"] = _list(task.get("observations_to_collect"))
        task["developer_document_refs"] = _list(task.get("developer_document_refs"))
        task["safety_notes"] = _list(task.get("safety_notes"))
        normalized_tasks.append(task)
    proposal["exploration_tasks"] = normalized_tasks
    proposal["exploration_order"] = [task["id"] for task in normalized_tasks]
    proposal["global_safety_notes"] = _list(proposal.get("global_safety_notes"))
    proposal.pop("exploration_targets", None)
    proposal.pop("expected_gui_skill_docs", None)
    proposal.pop("app_target_surfaces", None)
    proposal.pop("proposed_tools", None)
    return proposal


def rule_based_proposal(*, app: str, app_slug: str, system_doc: Path) -> dict[str, Any]:
    tasks = []
    for pattern_key, pattern in EXPLORATION_PATTERNS.items():
        task_id = f"{app_slug}_{pattern_key}"
        instruction = str(pattern["instruction"])
        full_instruction = f"In {app}, {instruction[0].lower() + instruction[1:]}"
        tasks.append(
            {
                "id": task_id,
                "pattern_family": "exploratory",
                "pattern_key": pattern_key,
                "phases": list(pattern["phases"]),
                "max_steps": 30,
                "instruction": full_instruction,
                "success_criteria": [
                    f"The requested exploration is completed in {app}.",
                    "The final screen or trajectory contains concrete visible GUI evidence.",
                    "The task completes without changing or deleting unrelated user data.",
                ],
                "observations_to_collect": [
                    "Visible labels and accessibility descriptions",
                    "Navigation route and controls used",
                    "Screenshots before and after each action",
                ],
                "developer_document_refs": [str(system_doc)],
                "safety_notes": [
                    "Do not send, share, purchase, uninstall, or modify pre-existing user data.",
                    "For entity_lifecycle, update and delete only the test entity created in this trajectory.",
                ],
            }
        )
    return {
        "workflow": "gui_skill_discovery_proposal.v3",
        "app": app,
        "app_slug": app_slug,
        "llm_agent_role": "sandbox_exploration_instruction_generator",
        "generator": "rule_based_sandbox_patterns.v2",
        "developer_document_summary": {
            "document_path": str(system_doc),
            "document_refs_used": [str(system_doc)],
            "weak_or_missing_grounding": [
                "Rule-based patterns do not assert undocumented GUI labels or controls."
            ],
        },
        "exploration_tasks": tasks,
        "exploration_order": [task["id"] for task in tasks],
        "global_safety_notes": [
            "Treat every task as independent from a fresh app launch.",
            "Do not modify or delete pre-existing user data.",
        ],
    }


def run_proposal(
    *,
    app: str,
    out_root: Path,
    asset_root: Path,
    system_doc: Path | None,
) -> dict[str, Any]:
    app_slug = slugify(app)
    system_doc = system_doc or _default_system_doc(app, asset_root)
    if not system_doc.is_file():
        raise FileNotFoundError(f"Developer Document is missing: {system_doc}")
    out_dir = out_root / "proposal" / app_slug
    out_dir.mkdir(parents=True, exist_ok=True)

    proposal = rule_based_proposal(app=app, app_slug=app_slug, system_doc=system_doc)
    proposal = normalize_proposal(proposal, app=app, app_slug=app_slug, system_doc=system_doc)
    dump_json(out_dir / "exploration_proposal.json", proposal)
    dump_json(
        out_dir / "proposal_context.json",
        {
            "workflow": "gui_skill_discovery_proposal.v3",
            "app": app,
            "app_slug": app_slug,
            "system_doc": str(system_doc),
            "generator": "rule_based_sandbox_patterns.v2",
        },
    )
    result = {
        "app": app,
        "app_slug": app_slug,
        "out_dir": str(out_dir),
        "proposal_json": str(out_dir / "exploration_proposal.json"),
        "proposal_context": str(out_dir / "proposal_context.json"),
        "exploration_task_count": len(proposal.get("exploration_tasks") or []),
    }
    dump_json(out_dir / "run_summary.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--out_root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--asset_root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--system_doc", type=Path, default=None)
    args = parser.parse_args()
    result = run_proposal(**vars(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.tool_generation._common.json_utils import (
    dump_json,
    extract_json_object,
    read_json,
    sanitize_model_text,
    write_raw_io_log,
)
from scripts.tool_generation._common.llm import GeminiTextClient


_DOCSTRING_RE = re.compile(r'^\s*"""(.*?)"""', re.DOTALL)


MANUAL_COVERAGE_SYSTEM = """You are evaluating semantic coverage of proposed Android app-state function tools.

Compare one proposal variant against the actual manual tools. Judge whether a proposed tool, or a small natural sequence of proposed tools, can accomplish the same user-visible state operation as each manual tool.

Inputs:
- app: Stable app identifier.
- manual_tools: Actual manual-tool metadata used as the coverage target.
- proposal: The structured tool proposal to evaluate.

Rules:
- Use the manual tool metadata as the coverage target.
- Do not require exact tool-name matches.
- Count grouped semantic tools as covered when their schemas expose the needed fields. For example, an update_settings tool can cover several set_* tools if it has matching fields.
- Count read-then-write sequences as covered when the read tool can select the target and the write tool can change it.
- Do not count a proposal as covered if it only mentions the backing storage but lacks the needed user-facing operation or selector.
- Prefer conservative judgments, but explain important semantic equivalents.

Output Schema:
{
  "app": "app slug",
  "manual_tool_judgments": [
    {
      "manual_tool": "name",
      "manual_operation": "short summary",
      "covered": true,
      "covering_tools": ["tool_name"],
      "rationale": "brief reason"
    }
  ],
  "summary": {
    "manual_total": 0,
    "covered": 0,
    "missing": ["manual_tool"],
    "notes": ["brief note"]
  }
}"""


LIFECYCLE_GAP_SYSTEM = """You are evaluating lifecycle completeness for one Android app-state function tool proposal.

Use only the proposal itself. Do not use manual tools or a Developer Document. First identify each target state/entity that the proposal is trying to expose, then list the natural lifecycle stages for that target state, and then identify core missing lifecycle stages.

Inputs:
- app: Stable app identifier.
- proposal: The structured tool proposal to evaluate.

Rules:
- Consider durable or inspectable user-visible state only.
- Infer target states/entities from explicit target_state fields when present; otherwise infer them from tool names, descriptions, schemas, state surface refs, and implementation notes.
- For each target state/entity, list proposed lifecycle stages already covered by the tools.
- Identify only high-severity gaps: cases where the proposal exposes a target state/entity but omits a core user-facing lifecycle counterpart that naturally belongs to that same state, especially create, delete, update, write, send, move, rename, reorder, or other major state-changing operations.
- Do not report medium/low gaps, optional refinements, convenience helpers, or support/discovery tools that can reasonably be handled inside another proposed tool.
- Do not invent brand-new target states/entities that the proposal does not already expose.
- Do not require every entity to have every lifecycle stage; only report a missing stage when the proposed target state clearly implies that stage as a core counterpart.
- Do not require exact tool-name matches.
- Count grouped semantic tools as covered when their schemas expose the needed fields.
- Count read-then-write sequences as covered when the read tool can select the target and the write tool can change it.
- Prefer conservative judgments, but explain semantic equivalents.

Output Schema:
{
  "app": "app slug",
  "target_state_lifecycles": [
    {
      "target_state": "entity/state name",
      "covered_stages": ["read|list|get|add|create|update|delete|move|rename|copy|reorder|toggle|clear|reset|import|export|verify"],
      "covering_tools": ["tool_name"],
      "lifecycle_summary": "brief summary"
    }
  ],
  "missing_lifecycle_gaps": [
    {
      "target_state": "entity/state name",
      "missing_stage": "stage",
      "suggested_tool_name": "snake_case_name",
      "why_not_covered": "brief proposal gap"
    }
  ],
  "summary": {
    "target_state_count": 0,
    "missing_count": 0,
    "notes": ["brief note"]
  }
}"""


def build_manual_coverage_user(
    *,
    app: str,
    manual_tools: list[dict[str, Any]],
    proposal: dict[str, Any],
) -> str:
    """Build the user prompt for manual-tool semantic coverage analysis."""
    payload = {
        "app": app,
        "manual_tools": manual_tools,
        "proposal": proposal,
    }
    return (
        "Evaluate semantic manual-tool coverage for this app and proposal variant.\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def build_lifecycle_gap_user(
    *,
    app: str,
    proposal: dict[str, Any],
) -> str:
    """Build the user prompt for proposal-only lifecycle gap analysis."""
    payload = {
        "app": app,
        "proposal": proposal,
    }
    return (
        "Analyze lifecycle gaps for this proposal variant using only target states/entities exposed by the proposal.\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _tool_metadata_from_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = _DOCSTRING_RE.search(text)
    metadata = match.group(1).strip() if match else "\n".join(text.splitlines()[:40])
    rel = path.relative_to(_REPO) if path.is_relative_to(_REPO) else path
    return {
        "tool_dir": path.parent.name,
        "path": str(rel),
        "metadata": metadata,
    }


def _load_manual_tools(manual_root: Path, app: str) -> list[dict[str, Any]]:
    app_dir = manual_root / app
    if not app_dir.exists():
        return []
    return [_tool_metadata_from_file(path) for path in sorted(app_dir.glob("*/tools.py"))]


def _compact_proposal(proposal_path: Path) -> dict[str, Any]:
    proposal = read_json(proposal_path, {})
    app = (proposal.get("apps") or [{}])[0]
    tools = []
    for tool in app.get("tools") or []:
        input_props = (tool.get("input_schema") or {}).get("properties") or {}
        output_props = (tool.get("output_schema") or {}).get("properties") or {}
        tools.append(
            {
                "name": tool.get("name"),
                "description": tool.get("description") or tool.get("purpose"),
                "lifecycle_stage": tool.get("lifecycle_stage"),
                "state_target_refs": tool.get("state_target_refs")
                or tool.get("state_surface_refs")
                or [],
                "input_fields": {
                    name: {
                        "type": spec.get("type"),
                        "description": spec.get("description"),
                        "enum": spec.get("enum"),
                    }
                    for name, spec in input_props.items()
                    if isinstance(spec, dict)
                },
                "required_inputs": (tool.get("input_schema") or {}).get("required") or [],
                "output_fields": {
                    name: {
                        "type": spec.get("type"),
                        "description": spec.get("description"),
                    }
                    for name, spec in output_props.items()
                    if isinstance(spec, dict)
                },
                "developer_document_refs": tool.get("developer_document_refs")
                or tool.get("system_evidence")
                or [],
                "implementation_notes": tool.get("implementation_notes") or [],
            }
        )
    return {
        "app": app.get("app"),
        "app_slug": app.get("app_slug"),
        "state_targets": app.get("state_targets") or app.get("state_surfaces") or [],
        "tools": tools,
    }


def _call_json(
    *,
    client: GeminiTextClient,
    system_text: str,
    user_text: str,
    out_dir: Path,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    system_text = sanitize_model_text(system_text)
    user_text = sanitize_model_text(user_text)
    raw_response = client.generate(system_text=system_text, user_text=user_text)
    parsed = extract_json_object(raw_response)
    raw_paths = write_raw_io_log(
        out_dir=out_dir,
        system_text=system_text,
        user_text=user_text,
        output_text=raw_response,
        metadata=metadata,
    )
    if isinstance(parsed, dict):
        parsed.setdefault("raw_io", raw_paths)
    return parsed if isinstance(parsed, dict) else {"result": parsed, "raw_io": raw_paths}


def _proposal_path(root: Path, app: str) -> Path:
    return root / app / "tool_proposals.json"


def _proposal_root_for_variant(variant: str, vanilla_dir: Path, lifecycle_dir: Path) -> Path:
    if variant == "vanilla":
        return vanilla_dir
    if variant == "lifecycle":
        return lifecycle_dir
    raise ValueError(f"unknown variant: {variant}")


def _discover_apps(proposal_dir: Path, apps: list[str] | None) -> list[str]:
    if apps:
        return sorted(apps)
    return sorted(path.parent.name for path in proposal_dir.glob("*/tool_proposals.json"))


def run_analysis(
    *,
    vanilla_dir: Path,
    lifecycle_dir: Path,
    manual_root: Path,
    out_dir: Path,
    model: str,
    api_key_yaml: str | None,
    temperature: float,
    apps: list[str] | None,
    mode: str,
    variant: str,
) -> None:
    proposal_dir = _proposal_root_for_variant(variant, vanilla_dir, lifecycle_dir)
    app_names = _discover_apps(proposal_dir, apps)
    client = GeminiTextClient(
        model=model,
        api_key_yaml=api_key_yaml,
        temperature=temperature,
        max_output_tokens=16384,
    )
    for app in app_names:
        app_out = out_dir / app
        proposal = _compact_proposal(_proposal_path(proposal_dir, app))

        if mode == "manual_coverage":
            manual_tools = _load_manual_tools(manual_root, app)
            if not manual_tools:
                continue
            user_text = build_manual_coverage_user(
                app=app,
                manual_tools=manual_tools,
                proposal=proposal,
            )
            system_text = MANUAL_COVERAGE_SYSTEM
            output_name = "manual_coverage.json"
        elif mode == "lifecycle_gaps":
            user_text = build_lifecycle_gap_user(
                app=app,
                proposal=proposal,
            )
            system_text = LIFECYCLE_GAP_SYSTEM
            output_name = "lifecycle_gaps.json"
        else:
            raise ValueError(f"unknown mode: {mode}")

        result = _call_json(
            client=client,
            system_text=system_text,
            user_text=user_text,
            out_dir=app_out,
            metadata={
                "app": app,
                "variant": variant,
                "mode": mode,
                "model": model,
                "temperature": temperature,
            },
        )
        result.setdefault("app", app)
        result.setdefault("variant", variant)
        dump_json(app_out / output_name, result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vanilla_dir", type=Path, default=_REPO / "output_tool" / "proposal_vanilla")
    parser.add_argument("--lifecycle_dir", type=Path, default=_REPO / "output_tool" / "proposal_lifecycle")
    parser.add_argument("--manual_root", type=Path, default=_REPO / "scripts" / "tool_manual")
    parser.add_argument("--out_dir", type=Path, default=_REPO / "output_tool" / "proposal_quality_analysis")
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--api_key_yaml", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--apps", nargs="*", default=None)
    parser.add_argument("--mode", choices=["manual_coverage", "lifecycle_gaps"], required=True)
    parser.add_argument("--variant", choices=["vanilla", "lifecycle"], required=True)
    args = parser.parse_args()
    run_analysis(**vars(args))


if __name__ == "__main__":
    main()

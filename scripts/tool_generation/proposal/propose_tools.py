from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.tool_generation._common.json_utils import (
    dump_json,
    extract_json_object,
    project_home_path,
    sanitize_model_text,
    slugify,
    write_raw_io_log,
)
from scripts.tool_generation._common.llm import GeminiTextClient

SYSTEM_PROMPT_VANILLA = """\
You design Android function tools for managing durable state.

Goal: Propose semantic function tools for reading and changing a target Android app's durable or inspectable state. These tools let an Android task agent inspect or modify state. Name tools around the app-domain entity and action, not around raw storage APIs.

Inputs:
- A target Android app name (`app`) and its slug (`app_slug`)
- A Developer Document with app identity, state interfaces, entities, fields, paths/providers/services, access semantics, access procedures, and consistency/recovery constraints.

Developer Document:
- The user prompt includes a Developer Document with app identity, state interfaces, entities, fields, paths/providers/services, access semantics, access procedures, and consistency/recovery constraints.
- Use it as the reference for proposed tools, schemas, state access, mutation behavior, and consistency/recovery notes.
- Do not invent DB paths, tables, columns, provider URIs, XML keys, package names, file paths, refresh behavior, or write semantics that are absent from the Developer Document.
- When proposing each tool, reference the Developer Document facts that justify the backing surface, selectors, inputs, outputs, access procedure, and consistency/recovery behavior.

Rules:
- Return JSON only.
- Propose tools only for the target app named in `app` / `app_slug`.
- Prefer a compact set of high-leverage tools.
- Every proposal should include at least one read tool when any readable state surface is described.
- Define tools as semnatically meaningful operations, such as creating a note or renaming a playlist, not as raw SQL/XML/file/provider/shell access. Keep raw storage details out of the public tool API.
- Keep raw implementation details in `state_targets`, `developer_document_refs`, and `implementation_notes`.
- Use stable user-visible selectors when possible, such as title, label, filename, list name, package label, timestamp, or visible setting name.
- Make every `input_schema` and `output_schema` a concrete JSON-Schema-like object with `type`, `properties`, `required`, and useful field descriptions.
- Never omit the top-level `required` array from an input_schema or output_schema; use `"required": []` when no fields are required.
- For Android Settings, propose semantic settings tools, not raw `namespace`/`key` tools. Group parameters only when entries share the same interface family, value type, safety profile, and access contract.

Output Schema:
{
  "app": "...",
  "app_slug": "...",
  "developer_document_summary": {
    "document_refs_used": ["..."]
  },
  "state_targets": [
    {
      "target_id": "short_stable_id",
      "target_type": "sqlite|shared_prefs|files|external_storage|etc.",
      "path_or_provider": "...",
      "confidence": "low|medium|high",
      "rationale": "..."
    }
  ],
  "tools": [
    {
      "name": "snake_case_tool_name",
      "description": "...",
      "state_target_refs": ["target_id"],
      "input_schema": {
        "type": "object",
        "properties": {
          "field": {"type": "string", "description": "type and meaning"}
        },
        "required": []
      },
      "output_schema": {
        "type": "object",
        "properties": {
          "field": {"type": "string", "description": "type and meaning"}
        },
        "required": []
      },
      "input_examples": [{"field": "example value"}],
      "output_examples": [{"field": "example value"}],
      "developer_document_refs": ["..."],
      "implementation_notes": [
        "state_access: how to read the SQLite/XML/file/etc. target",
        "state_update: how to update the SQLite/XML/file/etc. target",
        "consistency_recovery: invariants and recovery boundaries that apply to this action",
        "writeback: how to preserve unrelated state and write back safely, if the tool changes state",
        "failure_behavior: structured error/empty result behavior"
      ]
    }
  ]
}
"""


SYSTEM_PROMPT_LIFECYCLE = """\
You design Android function tools for managing durable state.

Goal: Propose semantic function tools for reading and changing a target Android app's durable or inspectable state. These tools let an Android task agent inspect or modify state. Name tools around the app-domain entity and action, not around raw storage APIs.

Inputs:
- A target Android app name (`app`) and its slug (`app_slug`)
- A Developer Document with app identity, state interfaces, entities, fields, paths/providers/services, access semantics, access procedures, and consistency/recovery constraints.

Developer Document:
- The user prompt includes a Developer Document with app identity, state interfaces, entities, fields, paths/providers/services, access semantics, access procedures, and consistency/recovery constraints.
- Use it as the reference for proposed tools, schemas, state access, mutation behavior, and consistency/recovery notes.
- Do not invent DB paths, tables, columns, provider URIs, XML keys, package names, file paths, refresh behavior, or write semantics that are absent from the Developer Document.
- When proposing each tool, reference the Developer Document facts that justify the backing surface, selectors, inputs, outputs, access procedure, and consistency/recovery behavior.

Rules:
- Return JSON only.
- Propose tools only for the target app named in `app` / `app_slug`.
- Prefer a compact set of high-leverage tools.
- Every proposal should include at least one read tool when any readable state surface is described.
- Define tools as semnatically meaningful operations, such as creating a note or renaming a playlist, not as raw SQL/XML/file/provider/shell access. Keep raw storage details out of the public tool API.
- Keep raw implementation details in `state_targets`, `developer_document_refs`, and `implementation_notes`.
- Use stable user-visible selectors when possible, such as title, label, filename, list name, package label, timestamp, or visible setting name.
- Make every `input_schema` and `output_schema` a concrete JSON-Schema-like object with `type`, `properties`, `required`, and useful field descriptions.
- Never omit the top-level `required` array from an input_schema or output_schema; use `"required": []` when no fields are required.
- For Android Settings, propose semantic settings tools, not raw `namespace`/`key` tools. Group parameters only when entries share the same interface family, value type, safety profile, and access contract.

Lifecycle Guidance:
- Organize tools around user-facing objects, settings, files, records, queues, lists, caches, or other app artifacts, following the data access pattern of each backing state targets.
- First identify each target state, especially primary or secondary, then cover the lifecycle operations supported by its documented access contract.
- Typical data access patterns include:
  - Database/entities: `create_`, `read_`, `update_`, `delete_`; when modeled, also `list_`, `move_`, `reparent_`, `reorder_`, `attach_`, or `detach_`.
  - SharedPreferences/settings: `get_`, `set_`; when modeled, also `toggle_` or `reset_`.
  - Files: `create_`, `read_`, `update_`, `delete_`; when modeled, also `list_`, `move_`, `copy_`, or `rename_`.
  - Providers/services: `create_`, `read_`, `update_`, `delete_`, or `get_`, `set_`, according to the documented interface; when modeled, also `enable_`, `disable_`, `clear_`, or `reset_`.
- Keep each target state's supported operations as a coherent family of concrete tools rather than one catch-all mutation tool.
- For every required parent, related-entity, or stable-id input, include a documented tool that returns the usable value, such as creating folders that contain notes or users that own posts and follower relations; when no such tool is documented, state the deterministic initialized-state requirement in `implementation_notes`.
- Do not omit a documented core lifecycle operation solely because its related state may be absent initially; preserve the operation together with the documented discovery path and state any required initialized-state dependency in `implementation_notes`.
- Use domain-specific lifecycle verbs only when the Developer Document models a distinct operation.

Output Schema:
{
  "app": "...",
  "app_slug": "...",
  "developer_document_summary": {
    "document_refs_used": ["..."]
  },
  "state_targets": [
    {
      "target_id": "short_stable_id",
      "target_type": "sqlite|shared_prefs|files|external_storage|etc.",
      "path_or_provider": "...",
      "rationale": "...",
      "developer_document_refs": ["..."]
    }
  ],
  "tools": [
    {
      "name": "snake_case_tool_name",
      "description": "...",
      "lifecycle_stage": "read|create|update|delete|etc.",
      "state_target_refs": ["target_id"],
      "input_schema": {
        "type": "object",
        "properties": {
          "field": {"type": "string", "description": "type and meaning"}
        },
        "required": []
      },
      "output_schema": {
        "type": "object",
        "properties": {
          "field": {"type": "string", "description": "type and meaning"}
        },
        "required": []
      },
      "input_examples": [{"field": "example value"}],
      "output_examples": [{"field": "example value"}],
      "implementation_notes": [
        "state_access: how to read the SQLite/XML/file/etc. target",
        "state_update: how to update the SQLite/XML/file/etc. target",
        "consistency_recovery: invariants and recovery boundaries that apply to this action",
        "writeback: how to preserve unrelated state and write back safely, if the tool changes state",
        "failure_behavior: structured error/empty result behavior"
      ]
    }
  ]
}
"""

def build_propose_user(
    evidence: dict[str, Any],
    *,
    include_lifecycle_guidance: bool = True,
) -> str:
    compact = {
        "app": evidence["app"],
        "app_slug": evidence["app_slug"],
        "developer_document_path": evidence.get("developer_doc_path"),
        "developer_document": evidence.get("developer_document", ""),
    }
    parts = [
        "Create function tool proposals for managing this app's user-visible durable state.",
        json.dumps(compact, ensure_ascii=False, indent=2),
    ]
    if include_lifecycle_guidance:
        parts.append(
            "Use the Developer Document as the reference. Preserve documented labels, entities, state surfaces, files/providers/services, available actions, access procedures, and consistency/recovery constraints. Omit tools or fields that are not referenceable from the Developer Document. Read it as an entity lifecycle inventory: for each DB record, preference-backed setting, file/folder, provider record, service state, queue, playlist, or cache, propose the useful lifecycle tools and cite the relevant document facts."
        )
    else:
        parts.append(
            "Use the Developer Document as the reference. Preserve documented labels, entities, state surfaces, files/providers/services, available actions, access procedures, and consistency/recovery constraints. Omit tools or fields that are not referenceable from the Developer Document. Propose useful state-management tools and cite the relevant document facts."
        )
    return "\n\n".join(parts)


def _normalize_single_app_proposal(proposal: dict[str, Any], app_inventory: dict[str, Any]) -> dict[str, Any]:
    """Coerce model output variants into the single-app proposal schema we persist."""
    if "proposal" in proposal and isinstance(proposal["proposal"], dict):
        proposal = proposal["proposal"]
    if "apps" in proposal and isinstance(proposal["apps"], list):
        proposal = proposal["apps"][0] if proposal["apps"] else {}
    proposal = dict(proposal)
    proposal["app"] = proposal.get("app") or app_inventory["app"]
    proposal["app_slug"] = proposal.get("app_slug") or app_inventory["app_slug"]
    proposal.pop("benchmark", None)
    proposal["state_targets"] = _normalize_state_targets(
        proposal.get("state_targets") or proposal.pop("state_surfaces", [])
    )
    proposal["tools"] = _normalize_tools(proposal.get("tools") or [])
    return proposal


def _normalize_state_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize current and legacy backing-state definitions."""
    normalized: list[dict[str, Any]] = []
    for target in targets:
        target = dict(target)
        if "surface_id" in target and "target_id" not in target:
            target["target_id"] = target.pop("surface_id")
        if "surface_type" in target and "target_type" not in target:
            target["target_type"] = target.pop("surface_type")
        target.pop("read_write", None)
        target.pop("operation", None)
        normalized.append(target)
    return normalized


def _normalize_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove legacy fields and fill compatibility aliases on tool proposals."""
    normalized = []
    for tool in tools:
        tool = dict(tool)
        tool.pop("operation", None)
        if "state_surface_refs" in tool and "state_target_refs" not in tool:
            tool["state_target_refs"] = tool.pop("state_surface_refs")
        if tool.get("description") and not tool.get("purpose"):
            tool["purpose"] = tool["description"]
        if tool.get("developer_document_refs") and not tool.get("system_evidence"):
            tool["system_evidence"] = tool["developer_document_refs"]
        normalized.append(tool)
    return normalized


def _default_out_dir(app: str, prompt_variant: str = "lifecycle") -> Path:
    """Return the default evidence-based proposal output directory for an app."""
    return _REPO / "output_tool" / f"proposal_{prompt_variant}" / slugify(app)


def _build_evidence_context(
    *,
    app: str,
    developer_doc_path: Path,
) -> dict[str, Any]:
    """Build the prompt context from the app's Developer Document."""
    if not developer_doc_path.exists():
        raise FileNotFoundError(f"missing Developer Document: {developer_doc_path}")
    return {
        "app": app,
        "app_slug": slugify(app),
        "developer_doc_path": project_home_path(developer_doc_path),
        "developer_document": developer_doc_path.read_text(encoding="utf-8"),
    }


def _write_outputs(
    *,
    app_dir: Path,
    app: str,
    app_slug: str,
    proposal: dict[str, Any],
    raw_response: str,
    model: str,
    system_text: str,
    user_text: str,
    context_payload: dict[str, Any],
    temperature: float | None = None,
    prompt_variant: str = "lifecycle",
) -> dict[str, Any]:
    """Persist proposal JSON, context, and raw model I/O."""
    app_dir.mkdir(parents=True, exist_ok=True)
    dump_json(app_dir / "proposal_context.json", context_payload)
    raw_io = write_raw_io_log(
        out_dir=app_dir,
        system_text=system_text,
        user_text=user_text,
        output_text=raw_response,
        metadata={
            "model": model,
            "temperature": temperature,
            "prompt_variant": prompt_variant,
        },
    )
    proposal_doc = {"apps": [proposal]}
    dump_json(app_dir / "tool_proposals.json", proposal_doc)
    return {
        "app": app,
        "app_slug": app_slug,
        "mode": "evidence_based",
        "dir": str(app_dir),
        "tool_proposals_json": str(app_dir / "tool_proposals.json"),
        "raw_response": raw_io["output_response"],
        "raw_io": raw_io,
    }


def propose_tools(
    *,
    app: str,
    out_dir: Path | None,
    model: str,
    api_key_yaml: str | None = None,
    developer_doc_path: Path,
    temperature: float | None = None,
    prompt_variant: str = "lifecycle",
) -> dict[str, Any]:
    """Generate and persist one app-state tool proposal document for an app."""
    if prompt_variant not in {"lifecycle", "vanilla"}:
        raise ValueError(f"unknown prompt_variant: {prompt_variant}")
    if not app:
        raise ValueError("--app is required")
    app_slug = slugify(app)
    app_dir = out_dir or _default_out_dir(app, prompt_variant)
    system_text = (
        SYSTEM_PROMPT_VANILLA
        if prompt_variant == "vanilla"
        else SYSTEM_PROMPT_LIFECYCLE
    )
    include_lifecycle_guidance = prompt_variant == "lifecycle"

    context_payload = _build_evidence_context(
        app=app,
        developer_doc_path=developer_doc_path,
    )
    user_text = build_propose_user(
        context_payload,
        include_lifecycle_guidance=include_lifecycle_guidance,
    )

    effective_temperature = (
        float(temperature)
        if temperature is not None
        else 0.0
    )
    client = GeminiTextClient(
        model=model,
        api_key_yaml=api_key_yaml,
        temperature=effective_temperature,
        max_output_tokens=16384,
    )
    system_text = sanitize_model_text(system_text)
    user_text = sanitize_model_text(user_text)
    raw_response = client.generate(system_text=system_text, user_text=user_text)
    proposal = extract_json_object(raw_response)

    proposal = _normalize_single_app_proposal(proposal, {"app": app, "app_slug": app_slug})
    _write_outputs(
        app_dir=app_dir,
        app=app,
        app_slug=app_slug,
        proposal=proposal,
        raw_response=raw_response,
        model=model,
        system_text=system_text,
        user_text=user_text,
        context_payload=context_payload,
        temperature=(
            float(temperature)
            if temperature is not None
            else 0.0
        ),
        prompt_variant=prompt_variant,
    )
    return json.loads((app_dir / "tool_proposals.json").read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    """Parse proposal-generation command-line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--developer_doc_path", type=Path, required=True)
    parser.add_argument("--out_dir", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--api_key_yaml", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--prompt_variant", choices=["lifecycle", "vanilla"], default="lifecycle")
    return parser.parse_args()


def main() -> None:
    """Run the proposal generator."""
    args = parse_args()
    propose_tools(**vars(args))


if __name__ == "__main__":
    main()

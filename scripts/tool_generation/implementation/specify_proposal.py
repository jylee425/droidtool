from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.tool_generation._common.json_utils import (  # noqa: E402
    dump_json,
    extract_json_object,
    project_home_path,
    read_json,
    sanitize_model_text,
    slugify,
    write_raw_io_log,
)
from scripts.tool_generation._common.llm import GeminiTextClient  # noqa: E402


SYSTEM_PROMPT = """You produce implementation specification artifacts for generating tools that access and update the internal state of applications.

Goal: Convert the structured tool proposal into a compact implementation specification. Keep shared resource/schema facts at target level and distill the Developer Document into action-specific semantics for each existing proposal tool.

Inputs:
- app_slug: Stable app identifier.
- structured_tool_proposal: Proposed app-state tools.
- developer_document_markdown: App-specific state interface evidence and state access-related notes.

Rules:

Output and Scope Rules:
- Return ONLY a JSON object. Do not include markdown fences or commentary.
- The output must be a JSON specification object whose targets mapping is keyed by target slug.
- Split distinct user-facing state models into separate target keys when their resources or access contracts differ. Do not fragment one coherent state model only because it spans related tables or files.

Input Grounding Rules:
- Use structured_tool_proposal as the source of truth for the public tools and their input/output schemas.
- Use developer_document_markdown as the source of truth for state resources, access procedures, implementation constraints, and supporting evidence.
- Do not infer implementation facts from tool names when they are absent from the Developer Document.

Proposal Contract Preservation Rules:
- Preserve exact existing tool names as aliases.
- Do not invent new proposal tools just to match the Developer Document. Preserve proposal tool aliases, but enrich each existing action with implementation contracts derived from the Developer Document.
- Copy each proposal tool's input_schema and output_schema exactly as provided. Preserve every property, description, enum, default, nested schema, and required array; do not summarize, simplify, add, remove, or rewrite schema content.

Target Modeling Rules:
- Model only concrete fields and resources documented in the Developer Document.
- Keep target-level fields only for facts shared by multiple actions: resources, fields, selectors, schema discovery, value encodings, and concise shared constraints.
- Put behavior that differs by action at action level. Do not copy whole Developer Document sections into every target or action.

Operation Mapping Rules:
- Map ordinary state reads—including list/read/detail/query/metadata and user-facing `get_` settings—to operation "read".
- Map create/add/insert/update/write/set/toggle/copy/move/rename/send/manage actions to operation "write" when they mutate state.
- Map delete/remove/clear actions to operation "delete" when they remove state.
- Use operation "get" only for capability, schema, path, or configuration discovery that does not read ordinary user state. Choose every operation by state effect, not by its public verb alone.
- Alias values must be two-item JSON arrays: [specification operation, concrete action], e.g. "add_recipe": ["write", "create"].

Action Contract Rules:
- For each action, summarize what that tool means: the entity it operates on, selection behavior, result shape, ordering, mutation behavior, relationships, value interpretation, and boundaries. 
- Every action whose operation is "read" must contain non-empty semantics.entity and semantics.result arrays. Describe the returned entity, result shape, empty/not-found behavior, and aggregation behavior when applicable. The output_schema does not replace semantics.result.
- Every action whose operation is "write" or "delete" must contain non-empty semantics.entity and semantics.mutation arrays. Describe exactly which state is created, changed, or removed and what related state is preserved or updated. The output_schema does not replace semantics.mutation.
- Before returning, inspect every action and verify these required semantics fields are present and non-empty. Never omit them merely because the behavior appears obvious from the tool name, output_schema, resources, or another action.
- Include selection, ordering, relationships, value encoding, and boundary only when applicable.

Evidence and Discovery Rules:
- Mark schema_discovery.required only when the documented Runtime discovery contract requires inspecting the live schema, initialized preferences, provider columns, or file layout before access.
- For every target and action, provide fully qualified source_refs using paths such as "DB Entities > Access Procedure > Delete procedure" or "SharedPreferences > Access Semantics > Value encoding". Bare section titles such as "DB Entities", "SharedPreferences", or "Access Semantics" are not sufficient.
- Map custom_handler_required by action name to concrete reasons when a generic adapter cannot safely infer semantics, such as multi-table writes, relation ordering, provider-specific row composition, XML vector encodings, GPX generation, or ambiguous selector resolution. Omit actions that do not need a custom handler.

Exclusion Rules:
- Do not add device-side readback recipes, GUI refresh instructions, post-launch checks, bounded retry policies, or evaluator-specific behavior that the Developer Document intentionally excludes.

Output Schema:
{
  "app": string,
  "app_slug": string,
  "package_candidates": [string],
  "targets": {
    "target_slug": {
      "target_slug": string,
      "target_type": "sqlite"|"shared_preferences"|"file_state"|"mixed"|"etc.",
      "package_candidates": [string],
      "resources": {
        "target_id": {
          "target_type": string,
          "path_or_provider": string,
          "role": string | null,
        }
      },
      "fields": {
        "table_candidates": [string],
        "field_aliases": {"semantic_field": [string]},
        "required_columns": [string],
        "optional_columns": [string],
        "relation_hints": [{"from": string, "to": string, "kind": string, "notes": [string]}],
        "preference_keys": [string],
        "preference_value_domains": {"key": object},
        "provider_uris": [string],
        "provider_columns": {"uri_or_entity": [string]},
        "file_paths": [string],
        "settings_keys": [string]
      },
      "selectors": {
        "primary": [{"name": string, "fields": [string], "notes": [string]}],
        "alternate": [{"name": string, "fields": [string], "ambiguity": string | null, "notes": [string]}],
        "ambiguity_handling": [string]
      },
      "schema_discovery": {
        "required": boolean,
        "table_checks": [string],
        "column_checks": [string],
        "preference_checks": [string],
        "provider_checks": [string],
        "file_checks": [string],
        "fallback_strategy": [string]
      },
      "value_encodings": {
        "semantic_field": {
          "storage_field": string | null,
          "storage_type": string | null,
          "semantic_type": string | null,
          "encode": [string],
          "decode": [string],
          "validation": [string]
        }
      },
      "shared_constraints": [string],
      "actions": {
        "action_name": action
      },
      "aliases": {
        "existing_tool_name": ["get" | "read" | "write" | "delete", "action_name"]
      },
      "custom_handler_required": {
        "action_name": [string]
      },
      "source_refs": [string],
      "notes": [string]
    }
  },
  "global_notes": [string]
}

The operation semantics object may use the listed keys, but omit optional keys that do not apply to that operation. The required entity/result/mutation keys defined by the Action Contract Rules must not be omitted.

operation schema:
{
  "operation": "get" | "read" | "write" | "delete",
  "tool_names": [string],
  "input_schema": object,
  "output_schema": object,
  "state_target_refs": [string],
  "semantics": {
    "entity": [string],
    "selection": [string],
    "result": [string],
    "ordering": [string],
    "mutation": [string],
    "relationships": [string],
    "value_encoding": [string],
    "boundary": [string]
  },
  "implementation_notes": [string],
  "source_refs": [string]
}
"""


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def build_user_text(
    *,
    app_slug: str,
    asset_text: str,
    proposal_json: dict[str, Any],
) -> str:
    payload = {
        "app_slug": app_slug,
        "developer_document_markdown": asset_text,
        "structured_tool_proposal": proposal_json,
    }
    return (
        "Build an implementation specification artifact for this app.\n"
        "Return only JSON matching the requested schema.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def normalize_artifact(
    artifact: Any,
    *,
    app_slug: str,
    proposal_json: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        raise ValueError("artifact must be a JSON object")
    artifact = dict(artifact)
    artifact["app_slug"] = artifact.get("app_slug") or app_slug
    artifact["app"] = artifact.get("app") or app_slug
    artifact.setdefault("package_candidates", [])
    artifact.setdefault("targets", {})
    artifact.setdefault("global_notes", [])
    artifact.pop("open_questions", None)
    if isinstance(artifact["targets"], list):
        artifact["targets"] = {
            str(target.get("target") or target.get("name") or f"target_{idx}"): target
            for idx, target in enumerate(artifact["targets"])
            if isinstance(target, dict)
        }
    if not isinstance(artifact["targets"], dict):
        raise ValueError("artifact.targets must be an object")
    proposal_tools = {
        tool["name"]: tool
        for app in proposal_json.get("apps", [])
        if isinstance(app, dict)
        for tool in app.get("tools", [])
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }
    for target_name, target in list(artifact["targets"].items()):
        if not isinstance(target, dict):
            artifact["targets"].pop(target_name)
            continue
        target.pop("target", None)
        target.setdefault("target_slug", str(target_name))
        target.setdefault("package_candidates", artifact.get("package_candidates") or [])
        target.setdefault("resources", {})
        if isinstance(target["resources"], list):
            target["resources"] = {
                str(item.get("surface_id") or f"resource_{idx}"): {
                    key: value for key, value in item.items() if key != "surface_id"
                }
                for idx, item in enumerate(target["resources"])
                if isinstance(item, dict)
            }
        target.setdefault("fields", target.pop("schema_hints", {}))
        if isinstance(target["fields"], dict):
            target["fields"].setdefault("table_candidates", [])
            target["fields"].setdefault("field_aliases", {})
            target["fields"].setdefault("required_columns", [])
            target["fields"].setdefault("optional_columns", [])
            target["fields"].setdefault("relation_hints", [])
            target["fields"].setdefault("preference_keys", [])
            target["fields"].setdefault("preference_value_domains", {})
            target["fields"].setdefault("provider_uris", [])
            target["fields"].setdefault("provider_columns", {})
            target["fields"].setdefault("file_paths", [])
            target["fields"].setdefault("settings_keys", [])
        if "operations" in target and "actions" not in target:
            actions: dict[str, Any] = {}
            for operation, entries in (target.pop("operations") or {}).items():
                if not isinstance(entries, list):
                    continue
                for idx, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        continue
                    action = str(entry.get("action") or f"{operation}_{idx}")
                    actions[action] = {**entry, "operation": operation}
            target["actions"] = actions
        target.setdefault("actions", {})
        if isinstance(target["actions"], dict):
            for action_name, action in list(target["actions"].items()):
                if not isinstance(action, dict):
                    target["actions"].pop(action_name)
                    continue
                action.setdefault("operation", "read")
                action.setdefault("tool_names", [])
                action.setdefault("input_schema", {})
                action.setdefault("output_schema", {})
                for tool_name in action["tool_names"] or [action_name]:
                    proposal_tool = proposal_tools.get(tool_name)
                    if proposal_tool is None:
                        continue
                    action["input_schema"] = copy.deepcopy(proposal_tool["input_schema"])
                    action["output_schema"] = copy.deepcopy(proposal_tool["output_schema"])
                action.setdefault("state_target_refs", action.pop("state_surface_refs", []))
                action.setdefault("implementation_notes", [])
                action.setdefault("semantics", {})
                action.setdefault("source_refs", [])
        target.setdefault("aliases", {})
        target.setdefault("selectors", {})
        if isinstance(target["selectors"], dict):
            target["selectors"].setdefault("primary", [])
            target["selectors"].setdefault("alternate", [])
            target["selectors"].setdefault("ambiguity_handling", [])
        target.setdefault("schema_discovery", {})
        if isinstance(target["schema_discovery"], dict):
            target["schema_discovery"].setdefault("required", False)
            target["schema_discovery"].setdefault("table_checks", [])
            target["schema_discovery"].setdefault("column_checks", [])
            target["schema_discovery"].setdefault("preference_checks", [])
            target["schema_discovery"].setdefault("provider_checks", [])
            target["schema_discovery"].setdefault("file_checks", [])
            target["schema_discovery"].setdefault("fallback_strategy", [])
        target.setdefault("value_encodings", {})
        target.setdefault("shared_constraints", [])
        target.setdefault("custom_handler_required", {})
        if isinstance(target["custom_handler_required"], list):
            target["custom_handler_required"] = {
                "*": [str(item) for item in target["custom_handler_required"]]
            }
        target.setdefault("source_refs", [])
        target.setdefault("notes", [])
        for alias, value in list(target["aliases"].items()):
            if isinstance(value, dict):
                target["aliases"][alias] = [
                    value.get("operation") or "read",
                    value.get("action") or value.get("lifecycle_stage") or "read",
                ]
    return artifact


def specify_proposal(
    *,
    app_slug: str,
    proposal_path: Path,
    developer_doc_path: Path,
    out_dir: Path,
    model: str,
    api_key_yaml: str | None,
    temperature: float,
) -> dict[str, Any]:
    if not proposal_path.exists():
        raise FileNotFoundError(f"missing proposal JSON: {proposal_path}")
    proposal_json = read_json(proposal_path)
    if not developer_doc_path.exists():
        raise FileNotFoundError(f"missing Developer Document: {developer_doc_path}")
    asset_text = read_text(developer_doc_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    system_text = sanitize_model_text(SYSTEM_PROMPT)
    user_text = sanitize_model_text(
        build_user_text(
            app_slug=app_slug,
            asset_text=asset_text,
            proposal_json=proposal_json,
        )
    )

    client = GeminiTextClient(
        model=model,
        api_key_yaml=api_key_yaml,
        temperature=temperature,
        max_output_tokens=32768,
    )
    raw_response = client.generate(system_text=system_text, user_text=user_text)
    artifact = extract_json_object(raw_response)
    artifact = normalize_artifact(
        artifact,
        app_slug=app_slug,
        proposal_json=proposal_json,
    )

    dump_json(out_dir / "specification.json", artifact)
    dump_json(
        out_dir / "specification_context.json",
        {
            "app_slug": app_slug,
            "proposal_json": project_home_path(proposal_path),
            "developer_doc_path": project_home_path(developer_doc_path),
        },
    )
    raw_io = write_raw_io_log(
        out_dir=out_dir,
        system_text=system_text,
        user_text=user_text,
        output_text=raw_response,
        metadata={
            "model": model,
            "temperature": temperature,
        },
    )
    return {
        "app_slug": app_slug,
        "specification_json": str(out_dir / "specification.json"),
        "raw_io": raw_io,
        "target_count": len(artifact.get("targets", {})),
    }


def parse_args() -> argparse.Namespace:
    """Parse specification-generation command-line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--proposal_path", type=Path, required=True)
    parser.add_argument("--developer_doc_path", type=Path, required=True)
    parser.add_argument("--out_dir", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--api_key_yaml", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app_slug = slugify(args.app)
    print(f"[specification] generating {app_slug}", flush=True)
    result = specify_proposal(
        app_slug=app_slug,
        proposal_path=args.proposal_path,
        developer_doc_path=args.developer_doc_path,
        out_dir=args.out_dir,
        model=args.model,
        api_key_yaml=args.api_key_yaml,
        temperature=args.temperature,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

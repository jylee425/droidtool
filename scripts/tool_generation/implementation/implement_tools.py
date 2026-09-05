from __future__ import annotations

import argparse
import ast
import builtins
import json
import re
import symtable
import sys
import warnings
from pathlib import Path
from typing import Any


_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.tool_generation._common.json_utils import (  # noqa: E402
    dump_json,
    extract_json_object,
    read_json,
    sanitize_model_text,
    write_raw_io_log,
)
from scripts.tool_generation._common.llm import GeminiTextClient  # noqa: E402

TARGET_TOOL_SYSTEM_PROMPT = """You generate standalone Python program-based tools for accessing and manipulating the internal states of Android applications, based on implementation specifications of tool proposals.

Inputs:
- app: Human-readable app name.
- app_slug: Stable app identifier.
- package_candidates: Android package names to resolve on device.
- developer_document_markdown: App-specific state interface evidence and implementation-related notes.
- target_slug: The stable identifier of the single target to implement.
- target_spec: Shared metadata for this target, including surface, resources, fields, selectors, schema_discovery, value_encodings, shared_constraints, custom_handler_required, source_refs, notes, actions, and aliases.
- target_tools: Public tool functions to implement for this target, derived from target_spec.aliases plus target_spec.actions. Each tool contains:
  - name: Python function name to define.
  - operation: Registry operation, one of get/read/write/delete.
  - action: Concrete target action, such as list, metadata, create, update, move, send, delete, clear.
  - action_spec: The action-specific implementation contract, including input_schema, output_schema, semantics, implementation_notes, and source_refs.

Rules:

Output and Scope Rules:
- Return ONLY a JSON object.
- Do not include markdown fences or commentary.
- Generate one executable Python module for this target.
- Define exactly the public tool functions listed in target_tools, using those names.
- Implement only this target. Do not implement other targets from the same app.

Contract Use Rules:
- Preserve specification operation semantics: get discovers target metadata/capability, read lists or queries state, write mutates state, delete removes or clears state.
- Treat action_spec as the primary contract for each tool. Use target_spec for shared resources, selectors, schema discovery, encodings, and shared constraints.
- Use developer_document_markdown as the source of truth for implementation details within this target. Use source_refs to focus on relevant sections, and do not invent new tools or unrelated state surfaces from the document.
- Follow the action semantics and implementation notes, using source_refs to resolve supporting details in the Developer Document.
- Prefer documented semantic selectors when available; use internal IDs only when the contract requires them.

Public Interface and Schema Rules:
- Define typed parameters from action_spec.input_schema, plus optional adb_path: str = "adb".
- Represent tool_spec.parameters and tool_spec.returns as JSON Schema objects with type, properties, and required; include required even when it is empty.
- Keep required schema fields aligned with required Python arguments, excluding adb_path.
- Preserve the semantic result fields defined by action_spec.output_schema when constructing tool_spec.returns.
- Include success: bool and error: string in every public tool's tool_spec.returns and runtime results.
- On success, return success=True and error="". On failure, return success=False and use error for a descriptive failure message rather than returning an ad hoc status string.

Implementation Rules:
- The generated Python must be real code, not skeletons. 
- Do not use TODO, pass-only bodies, NotImplementedError, placeholder returns, or "not_implemented".
- Use only Python standard library modules.
- Use the provided adb_path and ANDROID_SERIAL when the documented access procedure requires ADB.
- Add local helpers when the target needs repeated ADB, SQLite, XML, provider, file, or settings operations.
- Follow documented schema discovery and snapshot/transaction/relationship/process constraints when the target is SQLite-backed.
- Parse XML structurally and preserve unrelated keys when the target writes SharedPreferences.
- Constrain paths to documented roots and reject traversal when the target operates on files or media.
- Use documented URIs, namespaces, commands, columns, and value domains when the target uses a provider, shell service, or Android settings interface.
- Follow documented process/file-handling constraints when a state surface requires snapshot-based writeback.

State Access & Integrity Rules:
- Private State:
  - When accessing app-private paths under `/data/data`, `/data/user`, or `/data/user_de`, use `adb shell su 0` for the privileged read, copy, stat, or replacement step.
  - Stage private files through a readable temporary device path before `adb pull`, and stage local replacements before privileged copy-back. Do not directly pull from or push to an app-private path.
  - Before mutation, discover the existing numeric uid, gid, mode, and SELinux context when available. Never guess or hardcode ownership such as `u0_a123`, `1000:1000`, `system:system`, or `radio:radio`.
  - If required access or metadata cannot be discovered, return a structured error instead of using a guessed fallback.
  - Quiesce the owning app before externally replacing private state when the documented process contract requires it, then restore the discovered metadata after replacement.
- SQLite:
  - When a private SQLite database uses WAL mode, treat the main DB and existing `-wal`/`-shm` sidecars as one snapshot.
  - Copy the main DB and available sidecars only after quiescing the owning process. Perform local mutations with SQLite parameter binding and close every connection before writeback.
  - Checkpoint local WAL state when practical. Write back only a mutually consistent main DB and sidecar set.
  - Remove stale remote sidecars only when the replacement main DB contains the complete checkpointed state. Never combine a new main DB with unrelated old sidecars.
  - Preserve the discovered database uid, gid, mode, and SELinux context; do not use generic app-UID fallbacks.
- SharedPreferences:
  - Parse and modify SharedPreferences XML structurally, preserving unknown keys and existing XML value types.
  - Replace only the documented preference file and requested keys. Do not initialize undocumented keys or value domains merely because the XML file is writable.
  - For external replacement, stage the file, use the documented process boundary, and restore discovered uid, gid, mode, and SELinux context without guessed fallbacks.

Safety Rules:
- Do not use undefined helper methods.
- Do not invent table names, provider URIs, preference keys, file paths, input fields, or output fields that are not present in target_spec/action_spec.
- Fail with an ambiguity error when an update/delete selector matches multiple records unless the action explicitly allows multi-match behavior.
- Quote shell values and bind SQL parameters when commands or queries include user input.
- Reject broad recursive deletion unless the action contract explicitly permits it.

Execution Error Handling Rules:
- If a detail is genuinely unsafe or impossible to discover at runtime, return a structured error from that tool explaining the missing surface or unsupported schema.
- Convert runtime, ADB, SQLite, XML, and parsing failures into structured errors at public tool boundaries.
- Follow the documented access method; return a structured access error when it is unavailable and no documented fallback exists.

Code Integrity Rules:
- Reference only names that are defined in the module or imported from the Python standard library. Review every constant and helper reference for exact spelling before returning the module.
- Use Python literals `True`, `False`, and `None`; never emit JSON literals `true`, `false`, or `null` as Python expressions.
- Avoid invalid Python escape sequences. When a shell variable must expand, use `$name`, not `\\$name`.
- Prefer subprocess argument lists. When an inner shell command is necessary, quote every user-controlled value with `shlex.quote` and keep shell variables expandable only where intended.

Output Schema:
{
  "app": string,
  "app_slug": string,
  "target_slug": string,
  "tool_spec": {
    "app": string,
    "tool_namespace": string,
    "terminology": "Android function tool",
    "tools": [
      {
        "name": string,
        "description": string,
        "operation": "read" | "write" | "read_write",
        "parameters": object,
        "returns": object,
        "state_surfaces": [string],
        "safety": object
      }
    ]
  },
  "python_module": string
}
"""


def py_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value).strip("_")


def read_optional_text(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def target_tools_for_target(target_name: str, target_spec: dict[str, Any]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    actions = target_spec.get("actions") or {}
    for tool_name, value in sorted((target_spec.get("aliases") or {}).items()):
        if isinstance(value, (list, tuple)) and value:
            operation = str(value[0])
            action = str(value[1] if len(value) > 1 else operation)
        elif isinstance(value, dict):
            operation = str(value.get("operation") or "read")
            action = str(value.get("action") or operation)
        else:
            operation = "read"
            action = str(value)
        action_spec = actions.get(action) if isinstance(actions, dict) else {}
        if not isinstance(action_spec, dict):
            action_spec = {}
        tools.append(
            {
                "name": py_name(str(tool_name)),
                "target": target_name,
                "operation": operation,
                "action": action,
                "action_spec": action_spec,
            }
        )
    return tools


def build_target_prompt(
    specification: dict[str, Any],
    target_name: str,
    target_spec: dict[str, Any],
    *,
    asset_text: str = "",
) -> str:
    payload = {
        "app": specification.get("app", ""),
        "app_slug": specification.get("app_slug", ""),
        "package_candidates": specification.get("package_candidates") or [],
        "target_slug": target_spec.get("target_slug") or target_name,
        "target_spec": target_spec,
        "target_tools": target_tools_for_target(target_name, target_spec),
        "developer_document_markdown": asset_text,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def is_object_schema(schema: Any) -> bool:
    return (
        isinstance(schema, dict)
        and schema.get("type") == "object"
        and isinstance(schema.get("properties"), dict)
        and isinstance(schema.get("required"), list)
    )


def required_python_args(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    positional_args = list(function.args.posonlyargs) + list(function.args.args)
    positional_defaults = [None] * (len(positional_args) - len(function.args.defaults)) + list(function.args.defaults)
    required = {
        arg.arg
        for arg, default in zip(positional_args, positional_defaults)
        if default is None and arg.arg != "adb_path"
    }
    required.update(
        arg.arg
        for arg, default in zip(function.args.kwonlyargs, function.args.kw_defaults)
        if default is None and arg.arg != "adb_path"
    )
    return required


def _validate_generated_artifact_structure(created: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(created, dict):
        raise ValueError("created target response must be a JSON object")
    module = created.get("python_module")
    if not isinstance(module, str) or not module.strip():
        raise ValueError("missing python_module")
    tool_spec = created.get("tool_spec")
    if not isinstance(tool_spec, dict) or not isinstance(tool_spec.get("tools"), list):
        raise ValueError("missing tool_spec.tools")
    return module, tool_spec


def _validate_python_module_integrity(
    module: str,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    forbidden = ["TODO", "NotImplementedError", "not_implemented", "pass  #", "placeholder returns"]
    found = [marker for marker in forbidden if marker in module]
    if found:
        raise ValueError(f"generated python_module contains skeleton markers: {sorted(set(found))}")
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always", SyntaxWarning)
        compile(module, "<generated_target_module>", "exec")
    syntax_warnings = [
        str(item.message)
        for item in caught_warnings
        if issubclass(item.category, SyntaxWarning)
    ]
    if syntax_warnings:
        raise ValueError(f"generated python_module emits SyntaxWarning: {syntax_warnings}")

    parsed = ast.parse(module)
    symbol_table = symtable.symtable(module, "<generated_target_module>", "exec")
    module_definitions = {
        symbol.get_name()
        for symbol in symbol_table.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
    }
    allowed_runtime_globals = {"__file__", "__name__", "__package__", "__spec__", "__builtins__"}
    unresolved_globals: set[str] = set()

    def collect_unresolved(table: symtable.SymbolTable) -> None:
        for symbol in table.get_symbols():
            name = symbol.get_name()
            if (
                symbol.is_referenced()
                and symbol.is_global()
                and name not in module_definitions
                and name not in allowed_runtime_globals
                and name not in dir(builtins)
            ):
                unresolved_globals.add(name)
        for child in table.get_children():
            collect_unresolved(child)

    collect_unresolved(symbol_table)
    if unresolved_globals:
        raise ValueError(
            "generated python_module references undefined global names: "
            f"{sorted(unresolved_globals)}"
        )

    guessed_metadata_patterns = {
        "app uid": r"\bu0_a\d+\b",
        "numeric owner": r"\b(?:1000|2000):(?:1000|2000)\b",
        "named owner": r"\b(?:system:system|radio:radio)\b",
        "SELinux context": r"\bu:object_r:app_data_file:s0\b",
    }
    guessed_metadata = [
        label for label, pattern in guessed_metadata_patterns.items() if re.search(pattern, module)
    ]
    if guessed_metadata:
        raise ValueError(
            "generated python_module contains guessed private-file metadata; "
            f"discover it at runtime or return an error instead: {guessed_metadata}"
        )
    return {
        node.name: node
        for node in parsed.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _validate_tool_speficiation_coverage(
    function_defs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    tool_spec: dict[str, Any],
    expected_names: list[str],
) -> None:
    function_names = set(function_defs)
    missing = [name for name in expected_names if name not in function_names]
    if missing:
        raise ValueError(f"generated python_module missing public tool functions: {missing}")
    spec_names = [str(tool.get("name") or "") for tool in tool_spec.get("tools", []) if isinstance(tool, dict)]
    if sorted(spec_names) != sorted(expected_names):
        raise ValueError(f"tool_spec.tools names must match target aliases: expected {expected_names}, got {spec_names}")


def _validate_tool_schema_and_signatures(
    function_defs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    tool_spec: dict[str, Any],
) -> None:
    for tool in tool_spec.get("tools", []):
        if not isinstance(tool, dict):
            raise ValueError("tool_spec.tools entries must be objects")
        name = str(tool.get("name") or "")
        parameters = tool.get("parameters")
        returns = tool.get("returns")
        if not is_object_schema(parameters):
            raise ValueError(f"{name}.parameters must be a JSON object schema with type/properties/required")
        if not is_object_schema(returns):
            raise ValueError(f"{name}.returns must be a JSON object schema with type/properties/required")
        return_properties = returns.get("properties") or {}
        success_schema = return_properties.get("success")
        error_schema = return_properties.get("error")
        if not isinstance(success_schema, dict) or success_schema.get("type") != "boolean":
            raise ValueError(f"{name}.returns must include success:boolean")
        if not isinstance(error_schema, dict) or error_schema.get("type") != "string":
            raise ValueError(f"{name}.returns must include error:string")
        function = function_defs.get(name)
        if function is None:
            continue
        if function.args.vararg is not None or function.args.kwarg is not None:
            raise ValueError(f"{name} must define explicit typed parameters, not *args or **kwargs")
        schema_required = set(parameters.get("required") or [])
        schema_required.discard("adb_path")
        python_required = required_python_args(function)
        if schema_required != python_required:
            raise ValueError(
                f"{name} required arguments must match schema.required: "
                f"python={sorted(python_required)}, schema={sorted(schema_required)}"
            )


def validate_generated_tool_syntax(created: dict[str, Any], expected_names: list[str]) -> None:
    """Validate code integrity and interface-level specification compliance."""
    module, tool_spec = _validate_generated_artifact_structure(created)
    function_defs = _validate_python_module_integrity(module)
    _validate_tool_speficiation_coverage(function_defs, tool_spec, expected_names)
    _validate_tool_schema_and_signatures(function_defs, tool_spec)


def implement_tool_for_target_state(
    specification: dict[str, Any],
    target_name: str,
    target_spec: dict[str, Any],
    *,
    client: GeminiTextClient,
    raw_io_dir: Path | None,
    asset_text: str = "",
    validation_retries: int,
) -> dict[str, Any]:
    app = str(specification.get("app") or "")
    app_slug = str(specification.get("app_slug") or "app")
    expected_names = [tool["name"] for tool in target_tools_for_target(target_name, target_spec)]
    
    user_text = build_target_prompt(
        specification,
        target_name,
        target_spec,
        asset_text=asset_text,
    )
    validation_errors: list[str] = []
    for attempt in range(validation_retries + 1):
        if attempt == 0:
            attempt_user_text = user_text
        else:
            attempt_user_text = (
                user_text
                + "\n\nThe previous generated JSON failed validation. "
                + "Regenerate the full JSON object from scratch, preserving the same public tool names and fixing these validator errors:\n"
                + "\n".join(f"- {error}" for error in validation_errors[-attempt:])
            )
        raw_text = client.generate(
            system_text=TARGET_TOOL_SYSTEM_PROMPT, 
            user_text=attempt_user_text
        )
        
        if raw_io_dir is not None:
            write_raw_io_log(
                out_dir=raw_io_dir,
                system_text=TARGET_TOOL_SYSTEM_PROMPT,
                user_text=attempt_user_text,
                output_text=raw_text,
                metadata={
                    "app_slug": app_slug,
                    "target_slug": target_name,
                    "attempt": attempt,
                    "previous_validation_errors": validation_errors,
                },
            )
        try:
            created = extract_json_object(sanitize_model_text(raw_text))
            validate_generated_tool_syntax(created, expected_names)
            return created
        except Exception as exc:
            validation_errors.append(str(exc))
            if attempt >= validation_retries:
                raise
    raise RuntimeError("unreachable validation retry state")


def implement_tool_for_target_app(
    specification_path: Path,
    out_dir: Path,
    *,
    client: GeminiTextClient,
    developer_doc_path: Path,
    validation_retries: int,
) -> list[Path]:
    specification = read_json(specification_path)
    if not isinstance(specification, dict):
        raise ValueError(f"specification must be a JSON object: {specification_path}")
    app_slug = str(specification.get("app_slug") or specification_path.parent.name)
    app = str(specification.get("app") or app_slug)
    if not developer_doc_path.exists():
        raise FileNotFoundError(f"missing Developer Document: {developer_doc_path}")
    asset_text = read_optional_text(developer_doc_path)
    app_dir = out_dir
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")

    generated: list[Path] = []
    targets = specification.get("targets") or {}
    if not isinstance(targets, dict):
        raise ValueError(f"specification.targets must be an object: {specification_path}")
    
    for target_name, target_spec in targets.items():
        if not isinstance(target_spec, dict):
            continue
        target_dir = app_dir / py_name(str(target_name))
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "__init__.py").write_text("", encoding="utf-8")
        dump_json(
            target_dir / "target.json",
            {
                "app": app,
                "app_slug": app_slug,
                "package_candidates": specification.get("package_candidates") or [],
                "target_slug": target_spec.get("target_slug") or target_name,
                "target_spec": target_spec,
                "target_tools": target_tools_for_target(str(target_name), target_spec),
            },
        )
        created = implement_tool_for_target_state(
            specification,
            str(target_name),
            target_spec,
            client=client,
            raw_io_dir=target_dir,
            asset_text=asset_text,
            validation_retries=validation_retries,
        )
        dump_json(target_dir / "tool_spec.json", created.get("tool_spec") or {})
        (target_dir / "tools.py").write_text(str(created.get("python_module") or ""), encoding="utf-8")
        generated.append(target_dir / "tools.py")

    exports = []
    for path in generated:
        rel = path.parent.name
        exports.append(f"from .{rel}.tools import *")
    (app_dir / "__init__.py").write_text("\n".join(exports).rstrip() + "\n", encoding="utf-8")
    return generated


def parse_args() -> argparse.Namespace:
    """Parse and validate implementation command-line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--specification_path", type=Path, required=True)
    parser.add_argument("--developer_doc_path", type=Path, required=True)
    parser.add_argument("--out_dir", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--api_key_yaml", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--validation_retries", type=int, required=True)
    args = parser.parse_args()
    if not args.specification_path.exists():
        parser.error(f"missing specification JSON: {args.specification_path}")
    if args.validation_retries < 0:
        parser.error("--validation_retries must be >= 0")
    return args


def main() -> None:
    args = parse_args()
    specification = read_json(args.specification_path)
    if not isinstance(specification, dict):
        raise ValueError(f"specification must be a JSON object: {args.specification_path}")
    expected_app_slug = py_name(args.app).lower()
    specification_app_slug = str(specification.get("app_slug") or "")
    if specification_app_slug and specification_app_slug != expected_app_slug:
        raise ValueError(
            f"--app does not match specification app_slug: "
            f"{expected_app_slug!r} != {specification_app_slug!r}"
        )

    client = GeminiTextClient(
        model=args.model,
        api_key_yaml=args.api_key_yaml,
        temperature=args.temperature,
        max_output_tokens=32768,
    )
    generated = implement_tool_for_target_app(
        args.specification_path,
        args.out_dir,
        client=client,
        developer_doc_path=args.developer_doc_path,
        validation_retries=args.validation_retries,
    )
    for generated_path in generated:
        print(generated_path)


if __name__ == "__main__":
    main()

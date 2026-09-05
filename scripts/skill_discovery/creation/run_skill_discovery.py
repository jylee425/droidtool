#!/usr/bin/env python3
"""Create one app-specific GUI skill Markdown file from exploration rollouts."""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.tool_generation._common.json_utils import (  # noqa: E402
    dump_json,
    extract_json_object,
    read_json,
    sanitize_model_text,
    slugify,
    write_raw_io_log,
)
from scripts.tool_generation._common.llm import GeminiTextClient  # noqa: E402


DEFAULT_OUT_ROOT = REPO / "output_skill"

SYSTEM_PROMPT = """Role:
You are an Android GUI exploration summarizer and skill author. Convert one app's sandbox rollout evidence into a structured app knowledge summary and a set of verified, reusable GUI skills.

Objective:
Produce two equally important outputs in one JSON object:
1. Preserve app knowledge discovered across all three sandbox exploration pattern groups.
2. Promote only complete, repeatable, visibly verifiable GUI routes into actual skills.

Evidence policy:
- Rollouts are exploratory evidence, not benchmark truth.
- Use both successful and failed rollouts when summarizing visible screens, labels, layout, navigation, content, settings, empty states, blocked controls, and route fragments.
- A failed or max_steps rollout cannot by itself prove a complete skill.
- A skill requires a grounded starting condition, a repeatable GUI route, and a visible success condition. Prefer routes supported by confirmed rollouts or by consistent completed transitions across multiple traces.
- Never convert an attempted, blocked, contradictory, or incomplete transition into a verified procedure.
- When traces conflict, retain the conflict under exploration_gaps or unsupported_or_unverified instead of choosing an unsupported interpretation.

Phase 1 - Exploration summary:
- Synthesize evidence from all three patterns: ux_overview, settings_configuration, and entity_lifecycle.
- Treat each pattern as a multi-phase trajectory. entity_lifecycle covers target-entity discovery followed by list, create, update, and delete.
- primary_purpose: state only the app purpose supported by visible trajectory evidence.
- startup_and_initial_state: capture onboarding, permissions, account/default-app requirements, loading, empty states, and whether the main workspace was reached.
- primary_screen_and_navigation: describe observed tabs, drawers, bottom navigation, top bars, menus, and destinations. Clearly distinguish a visible control from a transition that succeeded.
- ui_layout_and_content: summarize the visible list, grid, map, form, player, editor, browser, or other content structure and its stable labels.
- observed_features_and_controls: preserve app-specific feature names, settings, search/filter/sort controls, and secondary surfaces, including visible but unverified controls.
- safe_inspection_routes: include only non-mutating routes actually traversed or read-only surfaces reliably reached.
- risky_or_state_changing_controls: identify controls that may save, delete, send, share, record, purchase, publish, uninstall, grant permission, or persist a setting.
- exploration_gaps: preserve blocked screens, unavailable content, unresponsive controls, conflicting observations, and routes that did not complete.
- Keep this section descriptive. Do not place executable step sequences in exploration_summary.

Phase 2 - Verified GUI skills:
- Create 0 to 8 actual skills. Do not manufacture skills to reach a count.
- If no complete route is supported, return an empty skills array and explain the blockers in unsupported_or_unverified.
- Each skill must achieve a concrete GUI outcome, not merely explore or learn about the app.
- name: use a clear imperative name.
- goal: state the completed GUI outcome.
- preconditions: state required starting screen, content, permission, account, or empty-state assumptions.
- procedure: provide an ordered executable route using observed labels, icon meanings, screen state, and relative position only when necessary.
- success_checks: name the visible screen, title, selected tab, dialog, content, or state that proves completion.
- failure_recovery: include only evidence-backed Back, Home, relaunch, or alternate-route recovery. If none was observed, return ["No verified recovery"].
- safety: identify whether the procedure is read-only and warn before any state-changing control.
- evidence: cite the supporting pattern, its true rollout status, and a concise grounding note.
- Do not turn a UI inventory or a visible-but-unresponsive control into a skill.

Unsupported workflows:
- unsupported_or_unverified: list attempted workflows that could not become skills, the exact limitation, and supporting patterns. Do not duplicate every exploration gap.

Global constraints:
- Use stable visible labels and accessibility meanings rather than coordinates.
- Keep procedures executable through the GUI. Launch or relaunch by visible app name.
- Do not mention normalized coordinates, raw action JSON, implementation code, package names, ADB, shell commands, programmatic APIs, database paths, backend schemas, or XML details.
- Do not use the terms "candidate skill" or "GUI skill candidate".
- Do not invent labels, routes, features, successful operations, or recovery paths.
- Return valid JSON only, with no Markdown, fences, comments, or prose outside the object.

Required JSON schema:
{
  "schema_version": "gui_skill_discovery.v1",
  "app": "visible app name",
  "app_slug": "snake_case app slug",
  "exploration_summary": {
    "primary_purpose": "...",
    "startup_and_initial_state": ["..."],
    "primary_screen_and_navigation": ["..."],
    "ui_layout_and_content": ["..."],
    "observed_features_and_controls": ["..."],
    "safe_inspection_routes": ["..."],
    "risky_or_state_changing_controls": ["..."],
    "exploration_gaps": ["..."]
  },
  "skills": [
    {
      "name": "clear imperative skill name",
      "goal": "completed GUI outcome",
      "preconditions": ["..."],
      "procedure": ["ordered executable step", "..."],
      "success_checks": ["visible completion evidence"],
      "failure_recovery": ["..."],
      "safety": ["..."],
      "evidence": [
        {"pattern": "ux_overview|settings_configuration|entity_lifecycle", "status": "confirmed|failed|max_steps", "note": "..."}
      ]
    }
  ],
  "unsupported_or_unverified": [
    {"workflow": "...", "limitation": "...", "supporting_patterns": ["..."]}
  ]
}
"""


def _step_evidence(rollout: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = []
    for step in rollout.get("steps") or []:
        if not isinstance(step, dict):
            continue
        action = step.get("mobile_use") if isinstance(step.get("mobile_use"), dict) else {}
        compact_action = {
            key: value
            for key, value in action.items()
            if key not in {"coordinate", "coordinate2"}
        }
        evidence.append(
            {
                "step": step.get("step"),
                "description": step.get("description"),
                "context": step.get("context"),
                "planned_action": step.get("action"),
                "mobile_action": compact_action,
                "execution": step.get("execution"),
            }
        )
    return evidence


def _rollout_evidence(
    rollout_path: Path,
    tasks_by_pattern: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rollout = read_json(rollout_path, {})
    pattern = str(rollout.get("action") or "")
    task = tasks_by_pattern.get(pattern, {})
    return {
        "pattern": pattern,
        "phases": task.get("phases") or [],
        "instruction": task.get("instruction"),
        "success_criteria": task.get("success_criteria") or [],
        "observations_requested": task.get("observations_to_collect") or [],
        "status": rollout.get("status"),
        "step_count": rollout.get("step_count"),
        "trajectory": _step_evidence(rollout),
        "source_rollout": str(rollout_path),
    }


def collect_app_evidence(app_dir: Path) -> dict[str, Any]:
    summary = read_json(app_dir / "run_summary.json", {})
    proposal = read_json(Path(str(summary.get("proposal") or "")), {})
    tasks_by_pattern = {
        str(task.get("pattern_key") or ""): task
        for task in proposal.get("exploration_tasks") or []
        if isinstance(task, dict)
    }
    rollouts = [
        _rollout_evidence(path, tasks_by_pattern)
        for path in sorted(app_dir.glob("*/*/rollout.json"))
    ]
    status_counts: dict[str, int] = {}
    for rollout in rollouts:
        status = str(rollout.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "app": summary.get("app") or app_dir.name,
        "app_slug": summary.get("app_slug") or app_dir.name,
        "rollout_count": len(rollouts),
        "status_counts": status_counts,
        "rollouts": rollouts,
    }


def _truncate_evidence(evidence: dict[str, Any], max_chars: int) -> str:
    text = json.dumps(evidence, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    compact = dict(evidence)
    compact_rollouts = []
    for rollout in evidence.get("rollouts") or []:
        item = dict(rollout)
        trajectory = item.get("trajectory") or []
        item["trajectory"] = trajectory[:4] + trajectory[-4:] if len(trajectory) > 8 else trajectory
        compact_rollouts.append(item)
    compact["rollouts"] = compact_rollouts
    text = json.dumps(compact, ensure_ascii=False, indent=2)
    return text[:max_chars] + "\n...[evidence truncated]"


def _clean_text(value: Any) -> str:
    text = sanitize_model_text(str(value or "")).strip()
    text = re.sub(
        r"\s*\(?\s*(?:bounds?\s*)?`?\[\d+\s*,\s*\d+\]\[\d+\s*,\s*\d+\]`?\s*\)?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s*\((?:resource-id|content-desc)\b[^)]*\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _clean_text(item))]


def normalize_skill_json(
    generated: Any,
    *,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(generated, dict):
        raise ValueError("skill generation output must be a JSON object")
    summary = generated.get("exploration_summary")
    summary = summary if isinstance(summary, dict) else {}
    skills = []
    for raw_skill in generated.get("skills") or []:
        if not isinstance(raw_skill, dict) or not _clean_text(raw_skill.get("name")):
            continue
        skill = {
            "name": _clean_text(raw_skill.get("name")),
            "goal": _clean_text(raw_skill.get("goal")),
            "preconditions": _strings(raw_skill.get("preconditions")),
            "procedure": _strings(raw_skill.get("procedure")),
            "success_checks": _strings(raw_skill.get("success_checks")),
            "failure_recovery": _strings(raw_skill.get("failure_recovery")),
            "safety": _strings(raw_skill.get("safety")),
            "evidence": [],
        }
        for item in raw_skill.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            skill["evidence"].append(
                {
                    "pattern": _clean_text(item.get("pattern")),
                    "status": _clean_text(item.get("status")),
                    "note": _clean_text(item.get("note")),
                }
            )
        skills.append(skill)

    unsupported = []
    for item in generated.get("unsupported_or_unverified") or []:
        if not isinstance(item, dict):
            continue
        unsupported.append(
            {
                "workflow": _clean_text(item.get("workflow")),
                "limitation": _clean_text(item.get("limitation")),
                "supporting_patterns": _strings(item.get("supporting_patterns")),
            }
        )

    return {
        "schema_version": "gui_skill_discovery.v1",
        "app": _clean_text(generated.get("app") or evidence["app"]),
        "app_slug": slugify(str(evidence["app_slug"])),
        "exploration_summary": {
            "primary_purpose": _clean_text(summary.get("primary_purpose")),
            "startup_and_initial_state": _strings(summary.get("startup_and_initial_state")),
            "primary_screen_and_navigation": _strings(summary.get("primary_screen_and_navigation")),
            "ui_layout_and_content": _strings(summary.get("ui_layout_and_content")),
            "observed_features_and_controls": _strings(summary.get("observed_features_and_controls")),
            "safe_inspection_routes": _strings(summary.get("safe_inspection_routes")),
            "risky_or_state_changing_controls": _strings(summary.get("risky_or_state_changing_controls")),
            "exploration_gaps": _strings(summary.get("exploration_gaps")),
        },
        "skills": skills[:8],
        "unsupported_or_unverified": unsupported,
    }


def _bullets(items: list[str], *, empty: str = "No grounded observations.") -> list[str]:
    return [f"- {item}" for item in items] or [f"- {empty}"]


def _display_skill_name(name: str) -> str:
    return name.replace("_", " ").strip().title() if "_" in name else name


def render_skill_markdown(doc: dict[str, Any]) -> str:
    summary = doc["exploration_summary"]
    lines = [
        f"# {doc['app']}",
        "",
        "## Exploration Summary",
        "",
        "### Primary Purpose",
        "",
        summary["primary_purpose"] or "No grounded primary purpose was established.",
    ]
    summary_sections = [
        ("Startup and Initial State", "startup_and_initial_state"),
        ("Primary Screen and Navigation", "primary_screen_and_navigation"),
        ("UI Layout and Content", "ui_layout_and_content"),
        ("Observed Features and Controls", "observed_features_and_controls"),
        ("Safe Inspection Routes", "safe_inspection_routes"),
        ("Risky or State-Changing Controls", "risky_or_state_changing_controls"),
        ("Exploration Gaps", "exploration_gaps"),
    ]
    for heading, key in summary_sections:
        lines.extend(["", f"### {heading}", "", *_bullets(summary[key])])

    skills = doc["skills"]
    lines.extend(["", "## GUI Skills", "", "### Skill Index", ""])
    if skills:
        for skill in skills:
            anchor = "skill-" + slugify(skill["name"]).replace("_", "-")
            lines.append(f"- [{_display_skill_name(skill['name'])}](#{anchor})")
    else:
        lines.append("- No complete GUI skill was supported by the rollout evidence.")

    for skill in skills:
        lines.extend(["", f"### Skill: {_display_skill_name(skill['name'])}"])
        fields = [
            ("Goal", [skill["goal"]] if skill["goal"] else []),
            ("Preconditions", skill["preconditions"]),
            ("Procedure", skill["procedure"]),
            ("Success Check", skill["success_checks"]),
            ("Failure Recovery", skill["failure_recovery"]),
            ("Safety", skill["safety"]),
        ]
        for heading, values in fields:
            lines.extend(["", f"#### {heading}", ""])
            if heading == "Procedure":
                lines.extend(
                    [f"{index}. {value}" for index, value in enumerate(values, start=1)]
                    or ["1. No verified procedure."]
                )
            else:
                lines.extend(_bullets(values))
        lines.extend(["", "#### Evidence", ""])
        if skill["evidence"]:
            for item in skill["evidence"]:
                note = f": {item['note']}" if item["note"] else ""
                lines.append(f"- {item['pattern']} ({item['status']}){note}")
        else:
            lines.append("- No explicit supporting pattern was provided.")

    lines.extend(["", "### Unsupported or Unverified", ""])
    unsupported = doc["unsupported_or_unverified"]
    if not unsupported:
        lines.append("- None recorded.")
    for item in unsupported:
        patterns = ", ".join(item["supporting_patterns"])
        suffix = f" Supporting patterns: {patterns}." if patterns else ""
        lines.append(f"- **{item['workflow'] or 'Unverified workflow'}**: {item['limitation']}{suffix}")
    return "\n".join(lines).rstrip() + "\n"


def generate_app_skill(
    *,
    app_dir: Path,
    skill_root: Path,
    skill_json_root: Path,
    model: str,
    api_key_yaml: str | None,
    temperature: float,
    max_evidence_chars: int,
) -> dict[str, Any]:
    evidence = collect_app_evidence(app_dir)
    app_slug = slugify(str(evidence["app_slug"]))
    user_text = (
        f"Create the structured app exploration summary and actual GUI skills for {evidence['app']} "
        f"({app_slug}) from the following exploration evidence.\n\n"
        + _truncate_evidence(evidence, max_evidence_chars)
    )
    with contextlib.redirect_stdout(sys.stderr):
        client = GeminiTextClient(
            model=model,
            api_key_yaml=api_key_yaml,
            temperature=temperature,
            max_output_tokens=8192,
        )
        raw = client.generate(system_text=SYSTEM_PROMPT, user_text=user_text)
    generated = extract_json_object(raw)
    skill_json = normalize_skill_json(generated, evidence=evidence)
    skill_json_root.mkdir(parents=True, exist_ok=True)
    skill_json_path = skill_json_root / f"{app_slug}.json"
    dump_json(skill_json_path, skill_json)
    markdown = render_skill_markdown(skill_json)
    skill_root.mkdir(parents=True, exist_ok=True)
    skill_path = skill_root / f"{app_slug}.md"
    skill_path.write_text(markdown, encoding="utf-8")
    raw_io = write_raw_io_log(
        out_dir=skill_root / "raw_io" / app_slug,
        system_text=SYSTEM_PROMPT,
        user_text=user_text,
        output_text=raw,
        metadata={
            "app": evidence["app"],
            "app_slug": app_slug,
            "model": model,
            "rollout_count": evidence["rollout_count"],
            "status_counts": evidence["status_counts"],
        },
    )
    return {
        "app": evidence["app"],
        "app_slug": app_slug,
        "skill_path": str(skill_path),
        "skill_json_path": str(skill_json_path),
        "rollout_count": evidence["rollout_count"],
        "status_counts": evidence["status_counts"],
        "raw_io": raw_io,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exploration_root", type=Path, default=DEFAULT_OUT_ROOT / "exploration")
    parser.add_argument("--skill_root", type=Path, default=DEFAULT_OUT_ROOT / "skill")
    parser.add_argument(
        "--skill_json_root",
        type=Path,
        default=None,
        help="Structured JSON output. Defaults to <skill_root>/json.",
    )
    parser.add_argument("--apps", nargs="*", default=None)
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--api_key_yaml", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max_evidence_chars", type=int, default=90000)
    parser.add_argument(
        "--convert_only",
        action="store_true",
        help="Render existing skill_json files to Markdown without calling the model.",
    )
    args = parser.parse_args()
    args.skill_root = args.skill_root.resolve()
    args.skill_json_root = (
        args.skill_json_root.resolve()
        if args.skill_json_root is not None
        else args.skill_root / "json"
    )

    selected = set(args.apps or [])
    if args.convert_only:
        json_paths = [
            path
            for path in sorted(args.skill_json_root.glob("*.json"))
            if not selected or path.stem in selected
        ]
        if not json_paths:
            raise SystemExit("[skill_discovery] no skill JSON files found")
        args.skill_root.mkdir(parents=True, exist_ok=True)
        for path in json_paths:
            doc = read_json(path, {})
            out_path = args.skill_root / f"{path.stem}.md"
            out_path.write_text(render_skill_markdown(doc), encoding="utf-8")
            print(f"[skill_discovery] converted {path} -> {out_path}", file=sys.stderr)
        return

    app_dirs = [
        path
        for path in sorted(args.exploration_root.iterdir())
        if path.is_dir()
        and not path.name.startswith("_")
        and (not selected or path.name in selected)
        and (path / "run_summary.json").exists()
    ]
    if not app_dirs:
        raise SystemExit("[skill_discovery] no app exploration directories found")

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                generate_app_skill,
                app_dir=app_dir,
                skill_root=args.skill_root,
                skill_json_root=args.skill_json_root,
                model=args.model,
                api_key_yaml=args.api_key_yaml,
                temperature=args.temperature,
                max_evidence_chars=args.max_evidence_chars,
            ): app_dir.name
            for app_dir in app_dirs
        }
        for future in as_completed(futures):
            app_slug = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                print(f"[skill_discovery] FAIL {app_slug}: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            results.append(result)
            print(f"[skill_discovery] wrote {result['skill_path']}", file=sys.stderr, flush=True)

    results.sort(key=lambda item: str(item["app_slug"]))
    summary = {
        "workflow": "gui_skill_discovery_json_to_markdown.v1",
        "model": args.model,
        "app_count": len(results),
        "results": results,
    }
    summary_path = args.skill_root / "_run_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

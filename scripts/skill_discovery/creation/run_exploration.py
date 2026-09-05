#!/usr/bin/env python3
"""Execute GUI skill-discovery proposals and retain replayable rollout evidence."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.tool_generation._common.json_utils import (  # noqa: E402
    dump_json,
    read_json,
    slugify,
)
from scripts.eval._common.prompt import (  # noqa: E402
    build_system_text as build_eval_system_text,
    build_user_text as build_eval_user_text,
    parse_response as parse_eval_response,
)
from scripts.tool_generation._common.llm import GeminiTextClient  # noqa: E402


DEFAULT_OUT_ROOT = REPO / "output_skill"
DEFAULT_ADB_PATH = Path.home() / ".local/share/android/sdk/platform-tools/adb"
APP_PACKAGE_CANDIDATES: dict[str, list[str]] = {
    "broccoli_app": ["com.flauschcode.broccoli"],
    "clock": ["com.google.android.deskclock", "com.android.deskclock"],
    "contacts": ["com.google.android.contacts", "com.android.contacts"],
    "files": ["com.google.android.apps.nbu.files", "com.google.android.documentsui", "com.android.documentsui"],
    "joplin": ["net.cozic.joplin"],
    "markor": ["net.gsantner.markor"],
    "messages": ["com.google.android.apps.messaging", "com.android.messaging"],
    "open_tracks_sports_tracker": ["de.dennisguse.opentracks"],
    "osmand": ["net.osmand"],
    "photonote": ["com.chartreux.photo_note"],
    "photos": ["com.google.android.apps.photos"],
    "pro_expense": ["com.arduia.expense"],
    "retro_music": ["code.name.monkey.retromusic"],
    "settings": ["com.android.settings"],
    "simple_calendar_pro": ["com.simplemobiletools.calendar.pro"],
    "snapseed": ["com.niksoftware.snapseed"],
    "tasks": ["org.tasks"],
    "vlc": ["org.videolan.vlc"],
    "wikipedia": ["org.wikipedia"],
}

COMMON_EXPLORATION_RULES = [
    "Use only the visible GUI and the supplied screenshots to complete the requested workflow.",
    "Do not invent controls, labels, screens, or successful state changes that are not visibly supported.",
    "Dismiss onboarding or permission prompts only when necessary to reach the requested workflow.",
    "Keep Context explicit about what has been visibly completed and what remains.",
    "Terminate with failure if the workflow is unavailable, blocked, or cannot be visibly verified within the step limit.",
]

PATTERN_EXPLORATION_RULES: dict[str, list[str]] = {
    "ux_overview": [
        "Handle the startup state, reach the main home screen, and inspect enough of that screen to identify its layout, navigation, visible content, and primary action controls.",
        "Terminate with success only after all of those UX observations are grounded in screens actually visited during this run.",
    ],
    "settings_configuration": [
        "Reach the app's settings or preferences screen through the visible GUI.",
        "Choose one safe, reversible, user-facing setting; note its current visible value, change it, and then visibly confirm the new value on the relevant settings screen.",
        "Do not change account, security, privacy, payment, network-sharing, or destructive settings.",
        "Terminate with success only after the changed setting and its applied value are visibly verified.",
    ],
    "entity_lifecycle": [
        "First use the visible UI to identify one primary user-manageable entity that supports a list or collection and creation, editing, and deletion.",
        "Use the supplied run label in the entity's visible name or title so it can be identified unambiguously.",
        "In one continuous run, inspect the entity list, create the labeled entity, verify that it appears, edit that same entity, verify the edit, delete it, and verify that it is no longer present.",
        "During the update stage, change at least one visible field to a new value that differs from the created value. Record the before and after values in Context, save the entity, and reopen or inspect it to verify the new value. Opening the edit screen and saving without changing a field does not count as an update.",
        "Never edit or delete pre-existing user data; edit and delete only the labeled entity created during this run.",
        "Terminate with success only after deletion has been confirmed from the relevant list or collection screen.",
    ],
}


def _build_exploration_system_text(pattern_key: str) -> str:
    rules = [
        *COMMON_EXPLORATION_RULES,
        *PATTERN_EXPLORATION_RULES.get(pattern_key, []),
    ]
    rule_text = "\n".join(f"- {rule}" for rule in rules)
    return (
        build_eval_system_text(benchmark="skill_discovery")
        + "\n# Skill-discovery workflow rules\n\n"
        + rule_text
        + "\n"
    )


def _adb(args: argparse.Namespace, adb_args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(args.adb_path), "-s", args.emulator_serial, *adb_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _package_exists(args: argparse.Namespace, package: str) -> bool:
    result = _adb(args, ["shell", "pm", "path", package], timeout=10)
    return result.returncode == 0 and bool(result.stdout.strip())


def resolve_package(args: argparse.Namespace, app_slug: str) -> str | None:
    return next(
        (package for package in APP_PACKAGE_CANDIDATES.get(app_slug, []) if _package_exists(args, package)),
        None,
    )


def suppress_environment_popups(args: argparse.Namespace) -> None:
    package = "com.google.androidenv.accessibilityforwarder"
    _adb(args, ["shell", "am", "force-stop", package], timeout=10)
    _adb(args, ["shell", "pm", "disable-user", "--user", "0", package], timeout=10)


def force_stop(args: argparse.Namespace, package: str) -> None:
    _adb(args, ["shell", "am", "force-stop", package], timeout=10)


def launch_app(args: argparse.Namespace, app_slug: str, package: str) -> dict[str, Any]:
    del app_slug
    resolved = _adb(args, ["shell", "cmd", "package", "resolve-activity", "--brief", package], timeout=10)
    components = [line.strip() for line in resolved.stdout.splitlines() if "/" in line]
    if components:
        result = _adb(args, ["shell", "am", "start", "-n", components[-1]], timeout=20)
        method = "am_start"
    else:
        result = _adb(
            args,
            ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
            timeout=20,
        )
        method = "monkey"
    time.sleep(args.launch_settle_s)
    return {
        "launched": result.returncode == 0,
        "package": package,
        "method": method,
        "stdout": result.stdout[-1000:],
        "stderr": result.stderr[-1000:],
    }


def capture_screenshot(args: argparse.Namespace, dest: Path) -> dict[str, Any]:
    result = subprocess.run(
        [str(args.adb_path), "-s", args.emulator_serial, "exec-out", "screencap", "-p"],
        capture_output=True,
        timeout=20,
        check=False,
    )
    dest.write_bytes(result.stdout)
    return {
        "path": str(dest),
        "returncode": result.returncode,
        "stderr": result.stderr.decode(errors="replace")[-1000:],
    }


def generate_gui_agent_response(
    client: GeminiTextClient,
    *,
    system_text: str,
    user_text: str,
    screenshot_path: Path,
) -> str:
    types = client._types
    parts = [
        types.Part.from_text(text=user_text),
        types.Part.from_bytes(data=screenshot_path.read_bytes(), mime_type="image/png"),
    ]
    config = types.GenerateContentConfig(
        system_instruction=system_text,
        temperature=client.temperature,
        max_output_tokens=client.max_output_tokens,
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client._client.models.generate_content(
                model=client.model,
                contents=[types.Content(role="user", parts=parts)],
                config=config,
            )
            return response.text or ""
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
    raise RuntimeError(f"GUI agent call failed after 3 attempts: {last_error}")


def _display_size(args: argparse.Namespace) -> tuple[int, int]:
    if args._gui_display_size:
        return args._gui_display_size
    result = _adb(args, ["shell", "wm", "size"], timeout=10)
    match = re.search(r"Physical size:\s*(\d+)x(\d+)", result.stdout + result.stderr)
    args._gui_display_size = (int(match.group(1)), int(match.group(2))) if match else (1080, 2400)
    return args._gui_display_size


def _pixels(args: argparse.Namespace, coordinate: Any) -> tuple[int, int]:
    if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
        raise ValueError("coordinate must be [x, y]")
    width, height = _display_size(args)
    x = int(max(0, min(999, float(coordinate[0]))) / 999 * (width - 1))
    y = int(max(0, min(999, float(coordinate[1]))) / 999 * (height - 1))
    return x, y


def _input_text(args: argparse.Namespace, text: str) -> None:
    escaped = text.replace("%", "%25").replace(" ", "%s")
    _adb(args, ["shell", "input", "text", escaped], timeout=10)


def execute_gui_agent_action(args: argparse.Namespace, action: dict[str, Any]) -> dict[str, Any]:
    kind = str(action.get("action") or "").strip().lower()
    try:
        if kind in {"click", "double_tap", "long_press"}:
            x, y = _pixels(args, action.get("coordinate"))
            if kind == "long_press":
                duration = int(max(0.2, min(float(action.get("time") or 1), 5)) * 1000)
                _adb(args, ["shell", "input", "swipe", str(x), str(y), str(x), str(y), str(duration)], timeout=10)
            else:
                _adb(args, ["shell", "input", "tap", str(x), str(y)], timeout=10)
                if kind == "double_tap":
                    time.sleep(0.08)
                    _adb(args, ["shell", "input", "tap", str(x), str(y)], timeout=10)
            time.sleep(0.7)
            return {"executed": True, "action": kind, "pixel_coordinate": [x, y]}
        if kind == "swipe":
            x1, y1 = _pixels(args, action.get("coordinate"))
            x2, y2 = _pixels(args, action.get("coordinate2"))
            _adb(args, ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), "400"], timeout=10)
            time.sleep(0.8)
            return {"executed": True, "action": kind, "pixel_coordinate": [x1, y1], "pixel_coordinate2": [x2, y2]}
        if kind in {"type", "set_text"}:
            pixel = None
            if kind == "set_text" and action.get("coordinate") is not None:
                x, y = _pixels(args, action["coordinate"])
                pixel = [x, y]
                _adb(args, ["shell", "input", "tap", str(x), str(y)], timeout=10)
                _adb(args, ["shell", "input", "keyevent", "KEYCODE_CTRL_LEFT", "KEYCODE_A"], timeout=10)
                _adb(args, ["shell", "input", "keyevent", "KEYCODE_DEL"], timeout=10)
            _input_text(args, str(action.get("text") or ""))
            time.sleep(0.7)
            return {"executed": True, "action": kind, "pixel_coordinate": pixel}
        if kind == "copy":
            if action.get("coordinate") is not None:
                x, y = _pixels(args, action["coordinate"])
                _adb(args, ["shell", "input", "swipe", str(x), str(y), str(x), str(y), "800"], timeout=10)
                pixel = [x, y]
            else:
                pixel = None
            return {"executed": True, "action": kind, "pixel_coordinate": pixel}
        if kind == "paste":
            pixel = None
            if action.get("coordinate") is not None:
                x, y = _pixels(args, action["coordinate"])
                pixel = [x, y]
                _adb(args, ["shell", "input", "tap", str(x), str(y)], timeout=10)
            _adb(args, ["shell", "input", "keyevent", "KEYCODE_PASTE"], timeout=10)
            time.sleep(0.7)
            return {"executed": True, "action": kind, "pixel_coordinate": pixel}
        if kind == "system_button":
            button = str(action.get("button") or "Back").lower()
            keycode = {"back": "KEYCODE_BACK", "home": "KEYCODE_HOME", "menu": "KEYCODE_MENU", "enter": "KEYCODE_ENTER"}.get(button, "KEYCODE_BACK")
            _adb(args, ["shell", "input", "keyevent", keycode], timeout=10)
            time.sleep(0.7)
            return {"executed": True, "action": kind, "button": button}
        if kind == "wait":
            seconds = max(0.1, min(float(action.get("time") or 1), 5))
            time.sleep(seconds)
            return {"executed": True, "action": kind, "seconds": seconds}
        if kind == "open":
            result = launch_app(args, args.app_slug, args.active_package)
            return {"executed": bool(result.get("launched")), "action": kind, "launch": result}
        if kind == "answer":
            return {"executed": False, "action": kind, "terminal": True, "answer": str(action.get("text") or "")}
        if kind == "terminate":
            return {"executed": False, "action": kind, "terminal": True, "status": _terminal_status(action)}
        return {"executed": False, "action": kind, "error": "unsupported action type"}
    except Exception as exc:  # noqa: BLE001
        return {"executed": False, "action": kind, "error": f"{type(exc).__name__}: {exc}"}


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _load_proposal(out_root: Path, app_slug: str, proposal_path: Path | None) -> tuple[Path, dict[str, Any]]:
    path = proposal_path or out_root / "proposal" / app_slug / "exploration_proposal.json"
    doc = read_json(path, {})
    if not isinstance(doc, dict) or not any(
        isinstance(doc.get(key), list)
        for key in ("exploration_tasks", "exploration_targets")
    ):
        raise ValueError(f"invalid exploration proposal: {path}")
    return path.resolve(), doc


def _ordered_targets(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = [item for item in proposal.get("exploration_tasks", []) if isinstance(item, dict)]
    if tasks:
        order = [str(item) for item in proposal.get("exploration_order", [])]
        rank = {task_id: index for index, task_id in enumerate(order)}
        tasks.sort(key=lambda item: rank.get(str(item.get("id")), len(rank)))
        return [
            {
                "target_name": str(task.get("pattern_key") or "exploration"),
                "target_slug": str(task.get("pattern_key") or "exploration"),
                "why_explore": str(task.get("instruction") or ""),
                "grounded_state_or_feature": "sandbox-style GUI exploration",
                "gui_entrypoints": [],
                "observations_to_collect": task.get("observations_to_collect") or [],
                "success_observations": task.get("success_criteria") or [],
                "failure_observations": [],
                "safety_notes": task.get("safety_notes") or [],
                "actions_to_try": [
                    {
                        "action": str(task.get("pattern_key") or "explore"),
                        "task_id": str(task.get("id") or ""),
                        "pattern_family": str(task.get("pattern_family") or "exploratory"),
                        "pattern_key": str(task.get("pattern_key") or ""),
                        "phases": task.get("phases") or [],
                        "max_steps": int(task.get("max_steps") or 20),
                        "goal": str(task.get("instruction") or ""),
                        "expected_ui_evidence": task.get("success_criteria") or [],
                        "developer_document_refs": task.get("developer_document_refs") or [],
                        "risk": "low",
                        "stop_conditions": task.get("safety_notes") or [],
                    }
                ],
            }
            for task in tasks
        ]
    targets = [item for item in proposal.get("exploration_targets", []) if isinstance(item, dict)]
    order = [str(item) for item in proposal.get("exploration_order", [])]
    rank = {slug: index for index, slug in enumerate(order)}
    return sorted(targets, key=lambda item: rank.get(str(item.get("target_slug")), len(rank)))


def _build_prompt(
    *,
    target: dict[str, Any],
    action_spec: dict[str, Any],
    run_label: str,
    history: list[dict[str, Any]],
) -> str:
    test_data_instruction = (
        f"Use this unique run label in the name or title of the disposable entity you create: {run_label}."
        if action_spec.get("pattern_key") == "entity_lifecycle"
        else ""
    )
    goal = "\n".join(
        part
        for part in (
            str(action_spec.get("goal") or target.get("why_explore") or ""),
            test_data_instruction,
        )
        if part
    )
    return build_eval_user_text(goal, history)


def _capture_step(args: argparse.Namespace, screenshot: Path) -> dict[str, Any]:
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    capture = capture_screenshot(args, screenshot)
    return capture


def _parse_response(raw: str) -> dict[str, Any]:
    description, context, action_text, tool_call = parse_eval_response(raw)
    if not isinstance(tool_call, dict) or tool_call.get("name") != "mobile_use":
        raise ValueError("exploration response must contain a mobile_use tool call")
    arguments = tool_call.get("arguments")
    if not isinstance(arguments, dict) or not arguments.get("action"):
        raise ValueError("mobile_use tool call must contain action arguments")
    return {
        "description": description,
        "context": context,
        "action_text": action_text,
        "tool_call": tool_call,
        "action": arguments,
    }


def _terminal_status(action: dict[str, Any]) -> str:
    return "confirmed" if str(action.get("status") or "failure").strip().lower() == "success" else "failed"


def run_action(
    args: argparse.Namespace,
    *,
    client: GeminiTextClient,
    proposal: dict[str, Any],
    target: dict[str, Any],
    action_spec: dict[str, Any],
    rollout_dir: Path,
    run_label: str,
) -> dict[str, Any]:
    rollout_dir.mkdir(parents=True, exist_ok=True)
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    history: list[dict[str, Any]] = []
    status = "max_steps"
    system_text = _build_exploration_system_text(str(action_spec.get("pattern_key") or ""))
    max_steps = int(action_spec.get("max_steps") or args.max_steps)

    for step_index in range(max_steps):
        screenshot = rollout_dir / f"step_{step_index:03d}_screen.png"
        capture = _capture_step(args, screenshot)
        prompt = _build_prompt(
            target=target,
            action_spec=action_spec,
            run_label=run_label,
            history=history,
        )
        step_started = time.time()
        step_doc: dict[str, Any] = {
            "step_index": step_index,
            "episode_id": str(action_spec.get("task_id") or action_spec.get("action") or "exploration"),
            "task_name": str(action_spec.get("action") or "exploration"),
            "goal": str(action_spec.get("goal") or ""),
            "timestamp": step_started,
            "screenshot_file": screenshot.name,
            "_run_config": {"actor": f"gemini:{args.model}"},
            "actor_input": {
                "raw_input": {
                    "system_text": system_text,
                    "user_text": prompt,
                    "screenshot": "<screenshot_png>",
                },
                "system_prompt": system_text,
                "user_prompt": prompt,
            },
            "capture": capture,
        }
        inference_started = time.time()
        try:
            raw = generate_gui_agent_response(
                client,
                system_text=system_text,
                user_text=prompt,
                screenshot_path=screenshot,
            )
            parsed = _parse_response(raw)
            action = parsed["action"]
            execution = execute_gui_agent_action(args, action)
            step_doc.update(
                {
                    "actor_output": raw,
                    "actor_inference_time_s": round(time.time() - inference_started, 3),
                    "description": parsed.get("description"),
                    "context": parsed.get("context"),
                    "action_text": parsed.get("action_text"),
                    "tool_call": parsed.get("tool_call"),
                    "execution": execution,
                    "exec_error": execution.get("error"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            step_doc["actor_inference_time_s"] = round(time.time() - inference_started, 3)
            step_doc["exec_error"] = f"{type(exc).__name__}: {exc}"
            step_doc["step_duration_s"] = round(time.time() - step_started, 3)
            dump_json(rollout_dir / f"step_{step_index:03d}.json", step_doc)
            break

        step_doc["step_duration_s"] = round(time.time() - step_started, 3)
        dump_json(rollout_dir / f"step_{step_index:03d}.json", step_doc)
        history.append(
            {
                "step": step_index,
                "description": parsed.get("description"),
                "context": parsed.get("context"),
                "action": parsed.get("action_text"),
                "mobile_use": action,
                "execution": execution,
            }
        )
        if execution.get("terminal"):
            status = _terminal_status(action)
            break
        if execution.get("error"):
            status = "failed"
            break

    result = {
        "workflow": "gui_skill_discovery_rollout.v2",
        "app_slug": proposal.get("app_slug"),
        "target_slug": target.get("target_slug"),
        "action": action_spec.get("action"),
        "lifecycle_intent": action_spec.get("lifecycle_intent"),
        "status": status,
        "model": args.model,
        "run_label": run_label,
        "started_at": started_at,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "step_count": len(history),
        "steps": history,
        "rollout_dir": _relative(rollout_dir, args.out_root),
    }
    dump_json(rollout_dir / "rollout.json", result)
    return result


def run_exploration(args: argparse.Namespace) -> dict[str, Any]:
    app_slug = slugify(args.app)
    proposal_path, proposal = _load_proposal(args.out_root, app_slug, args.proposal)
    app_slug = str(proposal.get("app_slug") or app_slug)
    args.app_slug = app_slug
    args.out_root = args.out_root.resolve()
    args.launch_settle_s = args.launch_settle_s
    args._gui_display_size = None

    selected_targets = set(args.targets or [])
    selected_actions = set(args.actions or [])
    targets = [
        target
        for target in _ordered_targets(proposal)
        if not selected_targets or str(target.get("target_slug")) in selected_targets
    ]
    if not targets:
        raise ValueError("no exploration targets selected")

    run_label = args.run_label or dt.datetime.now(dt.timezone.utc).strftime("skill_discovery_%Y%m%dT%H%M%SZ")
    app_out = args.out_root / "exploration" / app_slug
    app_out.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(sys.stderr):
        client = GeminiTextClient(
            model=args.model,
            api_key_yaml=args.api_key_yaml,
            temperature=args.temperature,
            max_output_tokens=4096,
        )

    suppress_environment_popups(args)
    package_name = args.package or resolve_package(args, app_slug)
    if not package_name:
        candidates = APP_PACKAGE_CANDIDATES.get(app_slug, [])
        raise RuntimeError(f"no installed package found for {app_slug}; candidates={candidates}")
    args.active_package = package_name

    results: list[dict[str, Any]] = []
    for target in targets:
        target_slug = str(target.get("target_slug") or "target")
        action_specs = [item for item in target.get("actions_to_try", []) if isinstance(item, dict)]
        for action_spec in action_specs:
            action_name = str(action_spec.get("action") or "action")
            if selected_actions and action_name not in selected_actions:
                continue
            if args.relaunch_each_action or not results:
                force_stop(args, package_name)
                launch = launch_app(args, app_slug, package_name)
                if not launch.get("launched"):
                    raise RuntimeError(f"failed to launch {app_slug}: {launch}")
            rollout_dir = app_out / target_slug / action_name
            print(f"[exploration] {app_slug}/{target_slug}/{action_name}", file=sys.stderr, flush=True)
            results.append(
                run_action(
                    args,
                    client=client,
                    proposal=proposal,
                    target=target,
                    action_spec=action_spec,
                    rollout_dir=rollout_dir,
                    run_label=run_label,
                )
            )

    summary = {
        "workflow": "gui_skill_discovery_exploration.v1",
        "app": proposal.get("app"),
        "app_slug": app_slug,
        "proposal": str(proposal_path),
        "package": package_name,
        "model": args.model,
        "run_label": run_label,
        "rollout_count": len(results),
        "status_counts": {
            status: sum(result.get("status") == status for result in results)
            for status in ("confirmed", "not_found", "blocked", "failed", "max_steps")
        },
        "rollouts": results,
    }
    dump_json(app_out / "run_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run proposal-driven Android GUI exploration.")
    parser.add_argument("--app", required=True, help="App slug used under output_skill/proposal.")
    parser.add_argument("--proposal", type=Path, default=None)
    parser.add_argument("--out_root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--api_key_yaml", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--adb_path", type=Path, default=DEFAULT_ADB_PATH)
    parser.add_argument("--emulator_serial", default="emulator-5554")
    parser.add_argument("--package", default=None, help="Override Android package name.")
    parser.add_argument("--targets", nargs="*", default=None)
    parser.add_argument("--actions", nargs="*", default=None)
    parser.add_argument("--max_steps", type=int, default=12)
    parser.add_argument("--launch_settle_s", type=float, default=2.0)
    parser.add_argument("--run_label", default=None)
    parser.add_argument("--relaunch_each_action", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    args.adb_path = args.adb_path.resolve()
    summary = run_exploration(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

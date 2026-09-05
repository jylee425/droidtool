#!/usr/bin/env python
"""AndroidWorld rollout for hosted actors with optional app-state tools.

Architecture:
  _common/prompt.py — system / user prompt builders
  environment.py    — AndroidWorld launch, suite setup, and mobile_use execution
  rollout_agent.py  — model calls, episode loop, and logging

Each step sends the current screenshot plus compact step history to the hosted
model. The model returns a structured response and a tool call; app-state tools
are executed directly and mobile_use calls are delegated to environment.py.

Usage:
  python scripts/eval/android_world/rollout_agent.py \\
    --provider gemini \\
    --model gemini-3.5-flash \\
    --out_dir data/android_world/rollouts/gemini_flash \\
    --tasks all \\
    --seeds 1 --seed_indices 11

Benchmark entrypoint:
  python scripts/eval/android_world/rollout_agent.py
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

# ---------------------------------------------------------------------------
# Sibling imports
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_HERE))
from scripts.eval._common.prompt import build_system_text, build_user_text, parse_response, pil_to_png_bytes  # noqa: E402
from scripts.eval._common.cli_command import compact_cli_result, run_cli_command  # noqa: E402
from scripts.eval.android_world.environment import (  # noqa: E402
    create_task_suite,
    execute_tool_call,
    import_android_world,
    load_environment,
)
from scripts.eval._common.app_state_tools import AppStateToolRuntime, compact_tool_result, compact_tool_result_payload  # noqa: E402
from scripts.eval._common.agent_assets import AGENT_TYPES, resolve_agent_assets  # noqa: E402
from scripts.eval._common.logger import JsonlWriter  # noqa: E402


from scripts.eval._common.policy import BasePolicy, make_policy  # noqa: E402


def _serialize_messages_for_log(step_text: str, has_image: bool) -> dict:
    """Compact log representation of the step inputs."""
    return {
        "user_text": step_text,
        "screenshot": "<screenshot_png>" if has_image else None,
    }


# ---------------------------------------------------------------------------
# Agent class factory
# ---------------------------------------------------------------------------
def make_agent_class(
    traj_writer: JsonlWriter,
    out_dir: Path,
    adb_path: str,
    emulator_serial: str,
    app_state_runtime: AppStateToolRuntime | None = None,
    enable_cli_command: bool = False,
    skill_text: str = "",
):
    base_agent, json_action, _interface, _, _, _, _, _repr, adb_utils = import_android_world()

    def _run_adb(*args: str) -> None:
        subprocess.run(
            [adb_path, "-s", emulator_serial] + list(args),
            check=False, capture_output=True,
        )

    class AndroidWorldToolAgent(base_agent.EnvironmentInteractingAgent):
        """Hosted-VLM AndroidWorld agent with structured observations/actions.

        Step structure:
          system: structured extended action space prompt
          user:   text(goal [+ history]) + image(current screenshot)
        History accumulates as Description/Context/Action, no past images.
        """

        def __init__(
            self,
            env,
            policy: BasePolicy,
            episode_id: str,
            task_name: str,
            name: str = "structured_actor_with_tool",
        ):
            super().__init__(env, name=name)
            self._policy = policy
            self._episode_id = episode_id
            self._task_name = task_name
            self._history: list[dict] = []
            self._step_idx = 0
            self._task_dir = out_dir / task_name
            self._task_dir.mkdir(parents=True, exist_ok=True)

        def reset(self, go_home: bool = False) -> None:
            super().reset(go_home=go_home)
            self._history = []
            self._step_idx = 0

        def step(self, goal: str):
            from PIL import Image  # noqa: PLC0415

            step_data: dict[str, Any] = {
                "step_number": self._step_idx,
                "raw_response": None,
                "description": None,
                "context": None,
                "action_text": None,
                "tool_call": None,
                "tool_result": None,
                "exec_error": None,
            }
            step_start = time.time()

            # --- capture screenshot ---
            state = self.get_post_transition_state()
            screenshot_np = state.pixels
            screen_h, screen_w = screenshot_np.shape[:2]  # original resolution
            screenshot = Image.fromarray(screenshot_np).convert("RGB")
            screenshot_png = pil_to_png_bytes(screenshot)

            # --- build prompt parts ---
            app_tool_defs = (
                app_state_runtime.tool_definitions()
                if app_state_runtime is not None
                else None
            )
            system_text = build_system_text(
                app_state_tools=app_tool_defs,
                require_app_state_verification=bool(app_tool_defs),
                enable_cli_command=enable_cli_command,
            )
            if skill_text:
                system_text += (
                    "\n# App GUI Skill Guide\n\n"
                    "Use the following observed GUI guide when it is relevant to the task. "
                    "The current screenshot and task goal remain authoritative; do not invent "
                    "a control or state that is not currently supported by the UI.\n\n"
                    + skill_text.strip()
                    + "\n"
                )
            user_text   = build_user_text(goal, self._history)

            # --- inference ---
            infer_start = time.time()
            try:
                text = self._policy.generate(system_text, user_text, screenshot_png)
            except Exception as e:  # noqa: BLE001
                step_data["exec_error"] = f"policy: {e}"
                self._dump_step(
                    step_data, screenshot, goal, system_text, user_text,
                    step_start=step_start,
                    infer_duration=time.time() - infer_start,
                    step_duration=time.time() - step_start,
                )
                self._history.append({
                    "description": "",
                    "context": f"Policy call failed: {e}",
                    "action": "",
                })
                self._step_idx += 1
                return base_agent.AgentInteractionResult(False, step_data)
            infer_duration = time.time() - infer_start

            step_data["raw_response"] = text
            description, context, action_text, tool_call = parse_response(text)
            step_data["description"] = description
            step_data["context"] = context
            step_data["action_text"] = action_text
            step_data["tool_call"] = tool_call
            tool_name = None
            tool_call_args: dict[str, Any] | None = None
            if isinstance(tool_call, dict):
                tool_name = str(tool_call.get("name") or "")
                raw_args = tool_call.get("arguments", {})
                tool_call_args = raw_args if isinstance(raw_args, dict) else {}
                if not tool_name and "action" in tool_call:
                    tool_name = "mobile_use"
                    tool_call_args = tool_call
            if description or context or action_text:
                self._history.append({
                    "description": description,
                    "context": context,
                    "action": action_text,
                })

            # --- execute action ---
            done = False
            if tool_name and app_state_runtime is not None and tool_name in app_state_runtime.action_names:
                tool_result = app_state_runtime.call(tool_name, tool_call_args)
                compact = compact_tool_result(tool_result)
                step_data["tool_result"] = compact_tool_result_payload(tool_result)
                if self._history:
                    self._history[-1]["tool_result"] = compact
            elif tool_name == "run_cli_command" and enable_cli_command and isinstance(tool_call_args, dict):
                tool_result = run_cli_command(
                    tool_call_args,
                    adb_path=adb_path,
                    emulator_serial=emulator_serial,
                )
                compact = compact_cli_result(tool_result)
                step_data["tool_result"] = tool_result
                if self._history:
                    self._history[-1]["tool_result"] = compact
            elif tool_name == "mobile_use" and isinstance(tool_call_args, dict):
                done, answer_text, exec_error = execute_tool_call(
                    self.env,
                    tool_call_args,
                    json_action_mod=json_action,
                    adb_utils=adb_utils,
                    run_adb=_run_adb,
                    orig_w=screen_w,
                    orig_h=screen_h,
                )
                if answer_text is not None:
                    step_data["answer"] = answer_text
                if exec_error:
                    step_data["exec_error"] = exec_error
            elif isinstance(tool_call, dict):
                step_data["exec_error"] = f"unknown tool call: {tool_call}"
            else:
                step_data["exec_error"] = "parse error"

            self._dump_step(
                step_data, screenshot, goal, system_text, user_text,
                step_start=step_start,
                infer_duration=infer_duration,
                step_duration=time.time() - step_start,
            )
            self._step_idx += 1
            return base_agent.AgentInteractionResult(done, step_data)

        def _dump_step(
            self,
            step_data: dict,
            screenshot,
            goal: str,
            system_text: str,
            user_text: str,
            step_start: float,
            infer_duration: float,
            step_duration: float,
        ):
            screen_w, screen_h = screenshot.size
            idx = self._step_idx

            # screenshot PNG
            png_name = f"step_{idx:03d}_screen.png"
            png_path = self._task_dir / png_name
            screenshot.save(str(png_path), format="PNG", optimize=False)

            # step JSON
            step_record = {
                "step_index": idx,
                "episode_id": self._episode_id,
                "task_name": self._task_name,
                "goal": goal,
                "timestamp": step_start,
                "step_duration_s": round(step_duration, 3),
                "actor_inference_time_s": round(infer_duration, 3),
                "screen_wh": [screen_w, screen_h],
                "screenshot_file": png_name,
                "_run_config": {
                    "actor": f"{self._policy.provider}:{self._policy.model}",
                },
                "actor_input": {
                    "raw_input": {
                        "system_text": system_text,
                        "user_text": user_text,
                        "screenshot": "<screenshot_png>",
                    },
                    "system_prompt": system_text,
                    "user_prompt": user_text,
                },
                "actor_output": step_data.get("raw_response"),
                "description": step_data.get("description"),
                "context": step_data.get("context"),
                "action_text": step_data.get("action_text"),
                "tool_call": step_data.get("tool_call"),
                "tool_result": step_data.get("tool_result"),
                "answer": step_data.get("answer"),
                "exec_error": step_data.get("exec_error"),
                "result": {
                    "env_reward_signal": False,
                    "agent_terminate_signal": False,
                },
            }
            step_json_path = self._task_dir / f"step_{idx:03d}.json"
            step_json_path.write_text(
                json.dumps(step_record, ensure_ascii=False, indent=2)
            )

            # flat trajectories.jsonl
            traj_writer.write({
                "episode_id": self._episode_id,
                "task_name": self._task_name,
                "step_index": idx,
                "goal": goal,
                "screen_wh": [screen_w, screen_h],
                "screenshot_file": str(png_path),
                "_run_config": {
                    "actor": f"{self._policy.provider}:{self._policy.model}",
                },
                "pred_text": step_data.get("raw_response"),
                "actor_input": step_record["actor_input"],
                "actor_output": step_data.get("raw_response"),
                "description": step_data.get("description"),
                "context": step_data.get("context"),
                "action_text": step_data.get("action_text"),
                "tool_call": step_data.get("tool_call"),
                "tool_result": step_data.get("tool_result"),
                "answer": step_data.get("answer"),
                "exec_error": step_data.get("exec_error"),
                "step_duration_s": round(step_duration, 3),
                "inference_duration_s": round(infer_duration, 3),
            })

        def set_step_signals(
            self,
            step_index: int,
            *,
            env_reward_signal: bool,
            agent_terminate_signal: bool,
        ) -> None:
            """Attach post-action benchmark and agent signals to a step log."""
            step_json_path = self._task_dir / f"step_{step_index:03d}.json"
            step_record = json.loads(step_json_path.read_text(encoding="utf-8"))
            step_record["result"] = {
                "env_reward_signal": env_reward_signal,
                "agent_terminate_signal": agent_terminate_signal,
            }
            step_json_path.write_text(
                json.dumps(step_record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    return AndroidWorldToolAgent


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _pre_task_reset(
    *,
    adb_path: str,
    emulator_serial: str,
    clear_packages: list[str],
    force_stop_packages: list[str],
) -> dict[str, Any] | None:
    if not clear_packages and not force_stop_packages:
        return None
    records = []
    for package in force_stop_packages:
        force_stop = subprocess.run(
            [adb_path, "-s", emulator_serial, "shell", "am", "force-stop", package],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        records.append({
            "package": package,
            "action": "force-stop",
            "returncode": force_stop.returncode,
            "stdout": force_stop.stdout.strip(),
            "stderr": force_stop.stderr.strip(),
        })
    for package in clear_packages:
        force_stop = subprocess.run(
            [adb_path, "-s", emulator_serial, "shell", "am", "force-stop", package],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        clear = subprocess.run(
            [adb_path, "-s", emulator_serial, "shell", "pm", "clear", package],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        records.append({
            "package": package,
            "action": "force-stop+pm-clear",
            "force_stop_returncode": force_stop.returncode,
            "clear_returncode": clear.returncode,
            "clear_stdout": clear.stdout.strip(),
            "clear_stderr": clear.stderr.strip(),
        })
    return {"packages": records}


def _run_adb_shell(
    adb_path: str,
    emulator_serial: str,
    *args: str,
    timeout: int = 20,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [adb_path, "-s", emulator_serial, "shell", *args],
        check=False, capture_output=True, text=True, timeout=timeout,
    )


def _prepare_display_runtime(adb_path: str, emulator_serial: str) -> dict[str, Any]:
    """Recover display state leaked by a previous task on the same shard."""
    attempts: list[dict[str, Any]] = []
    for attempt in range(3):
        commands = []
        if attempt == 1:
            # A full display power cycle recovers snapshots whose logical power
            # state says Awake while SurfaceFlinger still produces black frames.
            commands.append(("input", "keyevent", "KEYCODE_POWER"))
            time.sleep(0.5)
        elif attempt == 2:
            # Restart only SystemUI as the final non-destructive recovery step.
            commands.append(("pkill", "-TERM", "com.android.systemui"))
            time.sleep(2.0)
        commands.extend([
            ("svc", "power", "stayon", "true"),
            ("input", "keyevent", "KEYCODE_WAKEUP"),
            ("wm", "dismiss-keyguard"),
            ("settings", "put", "system", "screen_brightness_mode", "0"),
            ("settings", "put", "system", "screen_brightness", "128"),
            ("am", "start", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME"),
        ])
        records = []
        for command in commands:
            proc = _run_adb_shell(adb_path, emulator_serial, *command)
            records.append({"command": list(command), "returncode": proc.returncode,
                            "stderr": proc.stderr.strip()})
        time.sleep(1.0)
        brightness = _run_adb_shell(
            adb_path, emulator_serial, "settings", "get", "system", "screen_brightness"
        )
        power = _run_adb_shell(adb_path, emulator_serial, "dumpsys", "power")
        screenshot = subprocess.run(
            [adb_path, "-s", emulator_serial, "exec-out", "screencap", "-p"],
            check=False, capture_output=True, timeout=20,
        )
        screenshot_mean = 0.0
        if screenshot.returncode == 0 and screenshot.stdout.startswith(b"\x89PNG"):
            image = Image.open(BytesIO(screenshot.stdout)).convert("RGB").resize((16, 16))
            screenshot_mean = sum(ImageStat.Stat(image).mean) / 3.0
        result = {
            "attempt": attempt + 1,
            "ok": all(record["returncode"] == 0 for record in records),
            "brightness": brightness.stdout.strip(),
            "interactive": "mWakefulness=Awake" in power.stdout,
            "screenshot_mean": round(screenshot_mean, 2),
            "commands": records,
        }
        attempts.append(result)
        if result["interactive"] and screenshot_mean >= 5.0:
            # Return a distinct outer dict. Mutating ``result`` here would put
            # the attempts list inside an object already contained by that
            # same list, which cannot be serialized to episode.json.
            return {**result, "attempts": list(attempts)}
    raise RuntimeError(f"display preparation failed after 3 attempts: {attempts}")


def _prepare_markor_runtime(env: Any, adb_path: str, emulator_serial: str) -> dict[str, Any]:
    """Grant storage access and consume Markor's first-run screens."""
    from android_world.env import adb_utils  # type: ignore
    from android_world.env import tools  # type: ignore

    package = "net.gsantner.markor"
    appops = _run_adb_shell(
        adb_path, emulator_serial, "appops", "set", package,
        "MANAGE_EXTERNAL_STORAGE", "allow",
    )
    adb_utils.launch_app("markor", env.controller)
    clicked: list[str] = []
    controller = tools.AndroidToolController(env=env.controller)
    try:
        time.sleep(1.5)
        for label in ("NEXT", "NEXT", "NEXT", "NEXT", "DONE", "OK"):
            try:
                controller.click_element(label)
                clicked.append(label)
                time.sleep(0.5)
            except Exception:
                continue
    finally:
        adb_utils.close_app("markor", env.controller)
    return {"app": "markor", "appops_returncode": appops.returncode,
            "onboarding_clicked": clicked}


def _prepare_sms_runtime(env: Any, adb_path: str, emulator_serial: str) -> dict[str, Any]:
    """Make Simple SMS Messenger the default before its fixture is created."""
    from android_world.env import adb_utils  # type: ignore

    package = "com.simplemobiletools.smsmessenger"
    role = _run_adb_shell(
        adb_path, emulator_serial, "cmd", "role", "add-role-holder",
        "android.app.role.SMS", package, "0",
    )
    adb_utils.launch_app("simple sms messenger", env.controller)
    time.sleep(1.0)
    adb_utils.close_app("simple sms messenger", env.controller)
    holders = _run_adb_shell(
        adb_path, emulator_serial, "cmd", "role", "get-role-holders",
        "android.app.role.SMS", "0",
    )
    return {"app": "simple sms messenger", "role_returncode": role.returncode,
            "is_default": package in holders.stdout}


def _prepare_broccoli_runtime(env: Any) -> dict[str, Any]:
    """Materialize Broccoli's database before task initialization writes rows."""
    from android_world.env import adb_utils  # type: ignore

    adb_utils.launch_app("broccoli app", env.controller)
    time.sleep(2.0)
    adb_utils.close_app("broccoli app", env.controller)
    return {"app": "broccoli app", "database_materialized": True}


def _prepare_media_permission_runtime(
    env: Any,
    adb_path: str,
    emulator_serial: str,
    *,
    app_name: str,
    package: str,
) -> dict[str, Any]:
    """Pre-grant common camera/gallery media permissions and materialize UI."""
    from android_world.env import adb_utils  # type: ignore

    records = []
    for permission in (
        "android.permission.CAMERA",
        "android.permission.READ_MEDIA_IMAGES",
        "android.permission.READ_MEDIA_VIDEO",
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.WRITE_EXTERNAL_STORAGE",
    ):
        proc = _run_adb_shell(
            adb_path, emulator_serial, "pm", "grant", package, permission
        )
        records.append({"permission": permission, "returncode": proc.returncode})
    appops = _run_adb_shell(
        adb_path, emulator_serial, "appops", "set", package,
        "MANAGE_EXTERNAL_STORAGE", "allow",
    )
    adb_utils.launch_app(app_name, env.controller)
    time.sleep(1.5)
    adb_utils.close_app(app_name, env.controller)
    return {"app": app_name, "permissions": records,
            "appops_returncode": appops.returncode}


def _prepare_osmand_runtime(env: Any) -> dict[str, Any]:
    """Ensure OsmAnd onboarding and offline map installation are complete."""
    from android_world.env.setup_device import apps  # type: ignore

    setup_error = None
    try:
        apps.OsmAndApp.setup(env)
    except Exception as exc:
        setup_error = repr(exc)
    return {"app": "osmand", "setup_error": setup_error}


def _prepare_vlc_media_runtime(env: Any, adb_path: str, emulator_serial: str) -> dict[str, Any]:
    """Finish VLC first-run setup and request a media rescan before fixtures."""
    from android_world.env import adb_utils  # type: ignore
    from android_world.env.setup_device import apps  # type: ignore

    setup_error = None
    try:
        apps.VlcApp.setup(env)
    except Exception as exc:
        setup_error = repr(exc)
    scan = _run_adb_shell(
        adb_path, emulator_serial, "am", "broadcast",
        "-a", "android.intent.action.MEDIA_MOUNTED",
        "-d", "file:///storage/emulated/0",
    )
    adb_utils.launch_app("vlc", env.controller)
    time.sleep(4.0)
    adb_utils.close_app("vlc", env.controller)
    return {"app": "vlc", "setup_error": setup_error,
            "media_scan_returncode": scan.returncode}


def _prepare_clipper_runtime(env: Any) -> dict[str, Any]:
    from android_world.env import adb_utils  # type: ignore
    from android_world.env import tools  # type: ignore
    from android_world.env.setup_device import apps  # type: ignore

    def issue_with(env_controller: Any, *args: str) -> Any:
        return adb_utils.issue_generic_request(["shell", *args], env_controller)

    def issue(*args: str) -> Any:
        return issue_with(env.controller, *args)

    def start_clipper() -> None:
        issue("am", "start", "-n", "ca.zgrs.clipper/.Main")

    def install_clipboard_patch() -> None:
        clipboard_cache = {"value": ""}

        def patched_launch_app(app_name: str, env_controller: Any) -> str | None:
            if app_name != "clipper":
                return original_launch_app(app_name, env_controller)
            issue_with(env_controller, "am", "start", "-n", "ca.zgrs.clipper/.Main")
            return app_name

        def patched_set_clipboard_contents(content: str, env_controller: Any) -> None:
            clipboard_cache["value"] = content
            patched_launch_app("clipper", env_controller)
            time.sleep(1.5)
            formatted = adb_utils._adb_text_format(content)  # type: ignore[attr-defined]
            output = issue_with(
                env_controller,
                "am",
                "broadcast",
                "-a",
                "clipper.set",
                "-e",
                "text",
                formatted,
            ).generic.output.decode("utf-8")
            try:
                adb_utils._extract_clipper_output(output)  # type: ignore[attr-defined]
            except RuntimeError:
                issue_with(env_controller, "cmd", "clipboard", "set", content)

        def patched_get_clipboard_contents(env_controller: Any) -> str:
            patched_launch_app("clipper", env_controller)
            time.sleep(1.5)
            res = issue_with(env_controller, "am", "broadcast", "-a", "clipper.get")
            output = res.generic.output.decode("utf-8")
            try:
                return adb_utils._extract_clipper_output(output)  # type: ignore[attr-defined]
            except RuntimeError:
                if clipboard_cache["value"]:
                    return clipboard_cache["value"]
                fallback = issue_with(env_controller, "cmd", "clipboard", "get")
                fallback_text = fallback.generic.output.decode("utf-8", errors="ignore").strip()
                return fallback_text

        original_launch_app = adb_utils.launch_app
        adb_utils.launch_app = patched_launch_app
        adb_utils.set_clipboard_contents = patched_set_clipboard_contents
        adb_utils.get_clipboard_contents = patched_get_clipboard_contents

    setup_error = None
    try:
        apps.ClipperApp.setup(env)
    except Exception as exc:
        setup_error = repr(exc)

    install_clipboard_patch()
    start_clipper()
    controller = tools.AndroidToolController(env=env.controller)
    for label in ("Continue", "OK"):
        try:
            time.sleep(1.0)
            controller.click_element(label)
        except Exception:
            pass

    probe = "android_world_clipper_runtime_probe"
    adb_utils.set_clipboard_contents(probe, env.controller)
    try:
        actual = adb_utils.get_clipboard_contents(env.controller)
        probe_ok = actual == probe
    except RuntimeError:
        probe_ok = False
    return {"setup_error": setup_error, "probe_ok": probe_ok}


def _prepare_osmand_marker_runtime(adb_path: str, emulator_serial: str) -> dict[str, Any]:
    """Create the marker DB schema that OsmAnd only creates after marker UI use.

    The benchmark's OsmAndMarker fixture clears this table before the agent gets
    control.  A pristine emulator snapshot has no map_markers_db yet, and merely
    launching MapActivity does not create it, so fixture initialization otherwise
    fails forever with ``no such table: map_markers``.
    """
    db_path = "/data/data/net.osmand/databases/map_markers_db"
    schema = """
CREATE TABLE IF NOT EXISTS map_markers (
  marker_id TEXT PRIMARY KEY,
  marker_lat REAL NOT NULL DEFAULT -1.0,
  marker_lon REAL NOT NULL DEFAULT -1.0,
  marker_description TEXT DEFAULT '',
  marker_active INTEGER DEFAULT 0,
  marker_added INTEGER DEFAULT 0,
  marker_visited INTEGER DEFAULT 0,
  group_name TEXT DEFAULT '',
  group_key TEXT DEFAULT '',
  marker_color INTEGER DEFAULT 0,
  marker_next_key TEXT DEFAULT '',
  marker_disabled INTEGER DEFAULT 0,
  marker_selected INTEGER DEFAULT 0,
  marker_map_object_name TEXT DEFAULT '',
  title TEXT DEFAULT ''
);
""".strip()
    shell_script = (
        "mkdir -p /data/data/net.osmand/databases && "
        f"sqlite3 {db_path} \"{schema}\" && "
        "owner=$(stat -c %u:%g /data/data/net.osmand) && "
        f"chown $owner {db_path} && "
        f"chmod 660 {db_path} && "
        f"(restorecon {db_path} >/dev/null 2>&1 || true)"
    )
    proc = subprocess.run(
        [adb_path, "-s", emulator_serial, "shell", shell_script],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return {
        "app": "osmand",
        "marker_db_initialized": proc.returncode == 0,
        "returncode": proc.returncode,
        "stderr": proc.stderr.strip(),
    }


def _collect_system_task_state(
    task: Any, adb_path: str, emulator_serial: str
) -> dict[str, Any] | None:
    """Record evaluator-adjacent Android settings for system tasks."""
    task_name = task.__class__.__name__
    commands: dict[str, tuple[str, ...]] = {}
    if task_name.startswith("SystemBluetooth"):
        commands = {
            "bluetooth_on": ("settings", "get", "global", "bluetooth_on"),
            "bluetooth_state": ("cmd", "bluetooth_manager", "get-state"),
        }
    elif task_name.startswith("SystemBrightness"):
        commands = {
            "screen_brightness": ("settings", "get", "system", "screen_brightness"),
            "screen_brightness_mode": (
                "settings", "get", "system", "screen_brightness_mode"
            ),
        }
    if not commands:
        return None
    state: dict[str, Any] = {"task_class": task_name}
    for key, command in commands.items():
        proc = _run_adb_shell(adb_path, emulator_serial, *command)
        state[key] = {
            "value": proc.stdout.strip(),
            "returncode": proc.returncode,
            "stderr": proc.stderr.strip(),
        }
    return state


def _prepare_android_world_app_runtime(
    task: Any,
    env: Any,
    *,
    adb_path: str,
    emulator_serial: str,
) -> dict[str, Any] | None:
    """Run AndroidWorld setup for apps that require runtime-initialized state."""
    app_names = set(getattr(task, "app_names", ()) or ())
    prepared: list[dict[str, Any]] = []

    prepared.append({
        "app": "display",
        **_prepare_display_runtime(adb_path, emulator_serial),
    })

    if "broccoli app" in app_names:
        prepared.append(_prepare_broccoli_runtime(env))

    if "markor" in app_names:
        prepared.append(_prepare_markor_runtime(env, adb_path, emulator_serial))

    if "simple sms messenger" in app_names:
        prepared.append(_prepare_sms_runtime(env, adb_path, emulator_serial))

    if "camera" in app_names:
        prepared.append(_prepare_media_permission_runtime(
            env, adb_path, emulator_serial,
            app_name="camera", package="com.android.camera2",
        ))

    if "simple gallery pro" in app_names:
        prepared.append(_prepare_media_permission_runtime(
            env, adb_path, emulator_serial,
            app_name="simple gallery pro",
            package="com.simplemobiletools.gallery.pro",
        ))

    if "clipper" in app_names:
        prepared.append({"app": "clipper", **_prepare_clipper_runtime(env)})

    if "vlc" in app_names:
        prepared.append(_prepare_vlc_media_runtime(env, adb_path, emulator_serial))

    if "osmand" in app_names:
        prepared.append(_prepare_osmand_runtime(env))

    if task.__class__.__name__ == "OsmAndMarker":
        prepared.append(_prepare_osmand_marker_runtime(adb_path, emulator_serial))

    return {"apps": prepared} if prepared else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        description="Run a hosted-VLM structured actor with app-state tools on AndroidWorld."
    )
    p.add_argument("--provider", choices=["gemini", "anthropic", "openai", "qwen_local"],
                   default=os.environ.get("ACTOR_PROVIDER", "gemini"),
                   help="Hosted model provider.")
    p.add_argument("--model", default=os.environ.get("PLANNER_MODEL", "gemini-3.5-flash"),
                   help="Model name passed to the provider API.")
    p.add_argument("--api_key_yaml", default=None,
                   help="Path to api_key.yaml. Falls back to config/api_key.yaml.")
    p.add_argument("--max_output_tokens", type=int, default=4096)
    p.add_argument("--temperature", type=float, default=0.0)
    # AndroidWorld
    p.add_argument("--tasks", default="all",
                   help='Comma-separated AndroidWorld task names, or "all".')
    p.add_argument("--suite_family", default="android_world",
                   choices=["android_world", "android", "miniwob",
                            "miniwob_subset", "information_retrieval"])
    p.add_argument("--seeds", type=int, default=1)
    p.add_argument("--task_seed", type=int, default=30)
    p.add_argument("--seed_indices", default="",
                   help="Comma-separated indices or 'lo-hi' ranges (e.g. '11' for test seed).")
    p.add_argument("--max_steps", type=int, default=0,
                   help="Per-task step budget. 0 = task.complexity * 10.")
    p.add_argument("--max_steps_multiplier", type=float, default=1.0,
                   help="Multiplier applied to the default per-task step budget when --max_steps=0.")
    p.add_argument("--max_episodes", type=int, default=-1)
    p.add_argument("--adb_path",
                   default=os.path.expanduser("~/.local/share/android/sdk/platform-tools/adb"))
    p.add_argument("--emulator_serial", default="emulator-5554")
    p.add_argument("--grpc_port", type=int, default=8554)
    p.add_argument("--emulator_setup", action="store_true")
    p.add_argument(
        "--pre_task_pm_clear",
        default=os.environ.get("AW_PRE_TASK_PM_CLEAR", ""),
        help=(
            "Comma-separated package names to force-stop and pm clear before "
            "each AndroidWorld task initializes its fixture."
        ),
    )
    p.add_argument(
        "--pre_task_force_stop",
        default=os.environ.get("AW_PRE_TASK_FORCE_STOP", ""),
        help="Comma-separated package names to force-stop before each task.",
    )
    p.add_argument(
        "--app_tool_registry",
        type=Path,
        default=_REPO / "outputs" / "application" / "registry.json",
        help="Application-stage registry.json with generated app-state tools.",
    )
    p.add_argument(
        "--disable_app_tools",
        action="store_true",
        help="Run the with-tool agent without loading generated app-state tools.",
    )
    p.add_argument(
        "--enable_cli_command",
        action="store_true",
        help="Expose run_cli_command alongside mobile_use for the GUI+CLI baseline.",
    )
    p.add_argument(
        "--skill_path",
        type=Path,
        default=None,
        help="Optional Markdown GUI skill guide appended to the actor system prompt.",
    )
    p.add_argument("--out_dir", required=True)
    p.add_argument("--agent_type", choices=AGENT_TYPES, default=None)
    p.add_argument("--log_dir", type=Path, default=None)
    p.add_argument("--app_slugs", default="")
    args = p.parse_args()

    if not os.path.exists("/dev/kvm"):
        sys.exit("[android_world] /dev/kvm not present; run on the INFER server.")
    if not os.path.isfile(args.adb_path):
        sys.exit(f"[android_world] adb not found at {args.adb_path}.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_skill_text = ""
    if args.agent_type is not None:
        if args.log_dir is None:
            sys.exit("[android_world] --agent_type requires --log_dir")
        assets = resolve_agent_assets(
            agent_type=args.agent_type,
            log_dir=args.log_dir,
            app_slugs=args.app_slugs,
            runtime_dir=out_dir / "_runtime_registry",
        )
        args.disable_app_tools = assets.registry_path is None
        if assets.registry_path is not None:
            args.app_tool_registry = assets.registry_path
        args.enable_cli_command = assets.enable_cli_command
        resolved_skill_text = assets.skill_text
    traj_writer  = JsonlWriter(str(out_dir / "trajectories.jsonl"))
    summary_writer = JsonlWriter(str(out_dir / "episode_summary.jsonl"))
    with (out_dir / "meta.json").open("w") as f:
        json.dump({"args": vars(args), "started_ts": time.time()}, f, indent=2, default=str)

    app_state_runtime = None
    if not args.disable_app_tools:
        if not args.app_tool_registry.exists():
            sys.exit(f"[android_world] app tool registry not found: {args.app_tool_registry}")
        app_state_runtime = AppStateToolRuntime(
            args.app_tool_registry,
            repo_root=_REPO,
            adb_path=args.adb_path,
            emulator_serial=args.emulator_serial,
            work_dir=out_dir / "_tool_runtime",
        )
        print(
            f"[+] loaded app-state tools: {', '.join(sorted(app_state_runtime.action_names))}",
            flush=True,
        )

    skill_text = resolved_skill_text
    if args.skill_path is not None:
        if not args.skill_path.is_file():
            sys.exit(f"[android_world] skill file not found: {args.skill_path}")
        skill_text = args.skill_path.read_text(encoding="utf-8")
        print(f"[+] loaded GUI skill guide: {args.skill_path}", flush=True)

    env = load_environment(
        emulator_serial=args.emulator_serial,
        emulator_setup=args.emulator_setup,
        adb_path=args.adb_path,
        grpc_port=args.grpc_port,
    )

    # Resolve seed indices
    seed_indices: list[int] | None = None
    if args.seed_indices.strip():
        seed_indices = []
        for tok in args.seed_indices.split(","):
            tok = tok.strip()
            if not tok:
                continue
            if "-" in tok:
                lo, hi = tok.split("-", 1)
                seed_indices.extend(range(int(lo), int(hi) + 1))
            else:
                seed_indices.append(int(tok))
        seed_indices = sorted(set(seed_indices))
        needed = max(seed_indices) + 1
        if needed > args.seeds:
            args.seeds = needed
        print(f"[+] seed_indices={seed_indices} (n_task_combinations={args.seeds})", flush=True)

    suite = create_task_suite(
        env=env,
        suite_family=args.suite_family,
        tasks_arg=args.tasks,
        seeds=args.seeds,
        task_seed=args.task_seed,
    )

    policy = make_policy(args)
    AgentCls = make_agent_class(
        traj_writer,
        out_dir,
        args.adb_path,
        args.emulator_serial,
        app_state_runtime=app_state_runtime,
        enable_cli_command=args.enable_cli_command,
        skill_text=skill_text,
    )

    n_done = 0
    n_success = 0
    t_start = time.time()
    pre_task_clear_packages = _split_csv(args.pre_task_pm_clear)
    pre_task_force_stop_packages = _split_csv(args.pre_task_force_stop)
    for task_name, task_instances in suite.items():
        for seed_i, task in enumerate(task_instances):
            if seed_indices is not None and seed_i not in seed_indices:
                continue
            if args.max_episodes > 0 and n_done >= args.max_episodes:
                break
            episode_id = f"{task_name}__seed{seed_i}__{int(time.time())}"
            print(f"[+] [{n_done}] task={task_name} seed={seed_i} goal={task.goal!r}",
                  flush=True)
            agent = AgentCls(env, policy, episode_id=episode_id, task_name=task_name)
            if args.max_steps == 0 and getattr(task, "complexity", None) is not None:
                base_max_steps = int(10 * task.complexity)
                task_max_steps = max(1, math.ceil(base_max_steps * args.max_steps_multiplier))
            else:
                task_max_steps = max(args.max_steps, 1)
            agent.set_max_steps(task_max_steps)
            ep_started = time.time()
            reset_result = None
            runtime_prepare_result = None
            task_initial_system_state = None
            task_final_system_state = None
            agent_terminate_timestep = None
            agent_terminate_timestamp = None
            agent_terminate_status = None
            env_reward_timestep = None
            env_reward_timestamp = None
            env_reward_value = None
            done = False

            try:
                reset_result = _pre_task_reset(
                    adb_path=args.adb_path,
                    emulator_serial=args.emulator_serial,
                    clear_packages=pre_task_clear_packages,
                    force_stop_packages=pre_task_force_stop_packages,
                )
                if reset_result is not None:
                    print(f"[+] pre-task reset: {reset_result}", flush=True)
                runtime_prepare_result = _prepare_android_world_app_runtime(
                    task,
                    env,
                    adb_path=args.adb_path,
                    emulator_serial=args.emulator_serial,
                )
                if runtime_prepare_result is not None:
                    print(f"[+] app runtime prepare: {runtime_prepare_result}", flush=True)
                task.initialize_task(env)
                if task.__class__.__name__.startswith(
                    ("SystemBluetooth", "SystemBrightness")
                ):
                    # Settings activities and their backing services update
                    # asynchronously.  Let initialization settle before the
                    # first observation and preserve the ground truth used by
                    # evaluator debugging.
                    time.sleep(2.0)
                task_initial_system_state = _collect_system_task_state(
                    task, args.adb_path, args.emulator_serial
                )
                prepare_result = None
                if app_state_runtime is not None:
                    prepare_result = app_state_runtime.prepare_device_for_app_state_tools()
                    print(
                        "[+] app-state surface prepare "
                        f"ok={prepare_result.get('ok')} "
                        f"surfaces={len(prepare_result.get('surfaces') or [])}",
                        flush=True,
                    )
                agent.reset(go_home=True)
                done = False
                for _ in range(task_max_steps):
                    res = agent.step(task.goal)
                    timestep = agent._step_idx
                    step_index = max(0, timestep - 1)
                    tool_call = (
                        res.data.get("tool_call")
                        if isinstance(res.data, dict)
                        else None
                    )
                    tool_args = (
                        tool_call.get("arguments")
                        if isinstance(tool_call, dict)
                        else None
                    )
                    agent_terminate_signal = (
                        isinstance(tool_call, dict)
                        and tool_call.get("name") == "mobile_use"
                        and isinstance(tool_args, dict)
                        and tool_args.get("action") == "terminate"
                    )
                    if agent_terminate_timestep is None and agent_terminate_signal:
                        agent_terminate_timestep = timestep
                        agent_terminate_timestamp = time.time()
                        agent_terminate_status = tool_args.get("status")

                    env_reward_signal = False
                    try:
                        observed_reward = float(task.is_successful(env))
                        env_reward_signal = observed_reward >= 1.0
                        if env_reward_timestep is None and env_reward_signal:
                            env_reward_timestep = timestep
                            env_reward_timestamp = time.time()
                            env_reward_value = observed_reward
                    except Exception:  # noqa: BLE001
                        pass
                    agent.set_step_signals(
                        step_index,
                        env_reward_signal=env_reward_signal,
                        agent_terminate_signal=agent_terminate_signal,
                    )
                    if res.done:
                        done = True
                        break
                reward  = float(task.is_successful(env))
                task_final_system_state = _collect_system_task_state(
                    task, args.adb_path, args.emulator_serial
                )
                # Match AndroidWorld's official suite semantics:
                #   agent_successful = task_successful if interaction_results.done else 0.0
                # `done` is set by any terminal agent action (including answer or
                # terminate regardless of its self-reported status). Reaching the
                # step limit without a terminal action is therefore unsuccessful,
                # even when the final environment state receives full reward.
                success = done and reward >= 1.0
                term    = "complete" if done and success else (
                    "infeasible" if done else "timeout"
                )
                err = None
            except Exception as e:  # noqa: BLE001
                import traceback as _tb
                reward  = 0.0
                success = False
                term    = "error"
                err     = _tb.format_exc()
                prepare_result = None
                try:
                    task_final_system_state = _collect_system_task_state(
                        task, args.adb_path, args.emulator_serial
                    )
                except Exception:
                    pass
            finally:
                try:
                    task.tear_down(env)
                except Exception:
                    pass

            n_done += 1
            if success:
                n_success += 1
            ep_record = {
                "episode_id":  episode_id,
                "task_name":   task_name,
                "goal":        task.goal,
                "seed_index":  seed_i,
                "n_steps":     agent._step_idx,
                "max_steps":   task_max_steps,
                "success":     success,
                "reward":      reward,
                "termination": term,
                "error":       err,
                "duration_s":  time.time() - ep_started,
                "started_ts":  ep_started,
                "finished_ts": time.time(),
                "provider":    args.provider,
                "model":       args.model,
                "agent_terminate_timestep": agent_terminate_timestep,
                "agent_terminate_timestamp": agent_terminate_timestamp,
                "agent_terminate_status": agent_terminate_status,
                "agent_done": done,
                "env_reward_timestep": env_reward_timestep,
                "env_reward_timestamp": env_reward_timestamp,
                "env_reward_value": env_reward_value,
                "app_state_prepare": prepare_result,
                "app_runtime_prepare": runtime_prepare_result,
                "pre_task_reset": reset_result,
                "task_initial_system_state": task_initial_system_state,
                "task_final_system_state": task_final_system_state,
            }
            summary_writer.write(ep_record)
            ep_json = agent._task_dir / "episode.json"
            agent._task_dir.mkdir(parents=True, exist_ok=True)
            ep_json.write_text(json.dumps(ep_record, ensure_ascii=False, indent=2))
            print(f"      -> success={success} reward={reward:.2f} "
                  f"steps={agent._step_idx} term={term}", flush=True)

    traj_writer.close()
    summary_writer.close()
    print(
        f"\nDONE: episodes={n_done} success={n_success} "
        f"sr={n_success / max(n_done, 1):.3f} "
        f"wall={(time.time() - t_start) / 60:.1f}min",
        flush=True,
    )
    try:
        env.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()

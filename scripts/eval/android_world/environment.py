"""AndroidWorld environment adapter for mobile_use actions.

Coordinates are in 0-999 normalized model space and are scaled to the
current AndroidWorld screenshot size before execution.
"""
from __future__ import annotations

import os
import signal
import sys
import time
import unicodedata
from contextlib import contextmanager


# ---------------------------------------------------------------------------
# Vendor imports and environment launch
# ---------------------------------------------------------------------------
def import_android_world():
    try:
        from android_world import registry, suite_utils
        from android_world import checkpointer as checkpointer_lib
        from android_world.agents import base_agent
        from android_world.env import env_launcher, json_action, interface  # noqa: F401
        from android_world.env import representation_utils, adb_utils
        return (
            base_agent,
            json_action,
            interface,
            env_launcher,
            registry,
            suite_utils,
            checkpointer_lib,
            representation_utils,
            adb_utils,
        )
    except ImportError as e:
        sys.exit(
            f"[android_world] android_world is not importable ({e}).\n"
            "Run scripts/setup/android_world/setup_environment.sh first."
        )


def load_environment(
    *,
    emulator_serial: str,
    emulator_setup: bool,
    adb_path: str,
    grpc_port: int,
):
    _, _, _, env_launcher, _, _, _, _, _ = import_android_world()
    console_port = int(emulator_serial.split("-")[-1])
    timeout_s = int(os.environ.get("AW_ENV_LAUNCH_TIMEOUT", "120"))
    print(
        f"[+] connecting to {emulator_serial} "
        f"(console port {console_port}, grpc {grpc_port}, timeout {timeout_s}s)",
        flush=True,
    )
    try:
        with _timeout(timeout_s, "AndroidWorld environment launch"):
            env = env_launcher.load_and_setup_env(
                console_port=console_port,
                emulator_setup=emulator_setup,
                adb_path=adb_path,
                grpc_port=grpc_port,
            )
    except TimeoutError as e:
        sys.exit(
            f"[android_world] {e}. "
            f"Check EMULATOR_SERIAL={emulator_serial}, GRPC_PORT={grpc_port}, "
            "and whether the AndroidWorld gRPC emulator is running."
        )
    patch_accessibility_forest(env)
    return env


@contextmanager
def _timeout(seconds: int, label: str):
    if seconds <= 0:
        yield
        return

    def _handle_timeout(_signum, _frame):
        raise TimeoutError(f"{label} timed out after {seconds}s")

    old_handler = signal.signal(signal.SIGALRM, _handle_timeout)
    old_timer = signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer:
            signal.alarm(old_timer)


def patch_accessibility_forest(env) -> None:
    try:
        from android_env.proto.a11y import android_accessibility_forest_pb2

        empty_forest = android_accessibility_forest_pb2.AndroidAccessibilityForest()
        controller_cls = env.controller.__class__
        original_get_a11y_forest = controller_cls.get_a11y_forest

        def safe_get_a11y_forest(self, *args, **kwargs):
            try:
                return original_get_a11y_forest(self, *args, **kwargs)
            except Exception:
                return empty_forest

        controller_cls.get_a11y_forest = safe_get_a11y_forest
        print("[+] a11y forest patched (non-fatal)", flush=True)
    except Exception as patch_err:
        print(f"[!] a11y patch skipped: {patch_err}", flush=True)


def create_task_suite(
    *,
    env,
    suite_family: str,
    tasks_arg: str,
    seeds: int,
    task_seed: int,
):
    _, _, _, _, registry, suite_utils, _, _, _ = import_android_world()
    task_registry = registry.TaskRegistry()
    family_registry = task_registry.get_registry(family=suite_family)
    if tasks_arg == "all" or not tasks_arg:
        tasks = None
    else:
        tasks = [task for task in tasks_arg.split(",") if task.strip()]
        unknown = [task for task in tasks if task not in family_registry]
        if unknown:
            sys.exit(f"[android_world] unknown tasks: {unknown}")

    suite = suite_utils.create_suite(
        family_registry,
        n_task_combinations=seeds,
        seed=task_seed,
        tasks=tasks,
        env=env,
    )
    suite.suite_family = suite_family
    return suite


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------
def _scale(coord: float, orig_dim: int) -> int:
    return int(coord / 999 * orig_dim)


def _adb_escape(s: str) -> str:
    for ch in ['\\', ';', '|', '`', '\r', ' ', "'", '"', '&', '<', '>', '(', ')', '#', '$']:
        s = s.replace(ch, '\\' + ch)
    s = unicodedata.normalize("NFKD", s)
    return s.encode("ascii", "ignore").decode("ascii")


# ---------------------------------------------------------------------------
# Direct action markers
# ---------------------------------------------------------------------------

class _SwipeDirect:
    """Direct swipe via ADB (preserves exact start/end coordinates)."""
    def __init__(self, x1: int, y1: int, x2: int, y2: int):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2


class _DoubleTapDirect:
    """Two rapid taps (~80 ms apart) at (x, y)."""
    def __init__(self, x: int, y: int):
        self.x, self.y = x, y


class _SetTextDirect:
    """tap(x,y) → CTRL+A → type replacement text (no Enter).
    x/y may be None if the field is already focused.
    """
    def __init__(self, x, y, text: str):
        self.x, self.y, self.text = x, y, text


class _CopyDirect:
    """tap(x,y) → CTRL+A → CTRL+C.  x/y may be None (field already selected)."""
    def __init__(self, x, y):
        self.x, self.y = x, y


class _CopyTextDirect:
    """Inject a known string directly into the Android clipboard via Clipper app."""
    def __init__(self, text: str):
        self.text = text


class _PasteDirect:
    """tap(x,y) → CTRL+V.  x/y may be None (field already focused)."""
    def __init__(self, x, y):
        self.x, self.y = x, y


# ---------------------------------------------------------------------------
# mobile_use translation
# ---------------------------------------------------------------------------
def to_aw_action(
    action: dict,
    json_action_mod,
    orig_w: int = 1000,
    orig_h: int = 1000,
) -> tuple:
    """Convert mobile_use arguments into an AndroidWorld action.

    Returns:
        (JSONAction, done, answer_text)   — handled by env.execute_action()
        (_XxxDirect, done, answer_text)   — needs ADB execution in the AW agent loop
        (None, done, answer_text)         — parse error or terminate
    """
    ja = json_action_mod.JSONAction
    name = action.get("action", "")
    done = False
    answer_text = None

    try:
        if name == "click":
            cx, cy = action["coordinate"]
            return ja(action_type="click", x=_scale(cx, orig_w), y=_scale(cy, orig_h)), done, answer_text

        if name == "long_press":
            cx, cy = action["coordinate"]
            return ja(action_type="long_press", x=_scale(cx, orig_w), y=_scale(cy, orig_h)), done, answer_text

        if name == "swipe":
            sx, sy = action["coordinate"]
            ex, ey = action["coordinate2"]
            return _SwipeDirect(
                _scale(sx, orig_w), _scale(sy, orig_h),
                _scale(ex, orig_w), _scale(ey, orig_h),
            ), done, answer_text

        if name == "type":
            return ja(action_type="input_text", text=str(action.get("text", ""))), done, answer_text

        if name == "double_tap":
            cx, cy = action["coordinate"]
            return _DoubleTapDirect(_scale(cx, orig_w), _scale(cy, orig_h)), done, answer_text

        if name == "set_text":
            coord = action.get("coordinate")
            if coord is not None:
                cx, cy = coord
                sx, sy = _scale(cx, orig_w), _scale(cy, orig_h)
            else:
                sx, sy = None, None
            return _SetTextDirect(sx, sy, str(action.get("text", ""))), done, answer_text

        if name == "copy":
            text_val = action.get("text")
            if text_val is not None:
                return _CopyTextDirect(str(text_val)), done, answer_text
            coord = action.get("coordinate")
            if coord is not None:
                cx, cy = coord
                return _CopyDirect(_scale(cx, orig_w), _scale(cy, orig_h)), done, answer_text
            return _CopyDirect(None, None), done, answer_text

        if name == "paste":
            coord = action.get("coordinate")
            if coord is not None:
                cx, cy = coord
                return _PasteDirect(_scale(cx, orig_w), _scale(cy, orig_h)), done, answer_text
            return _PasteDirect(None, None), done, answer_text

        if name == "system_button":
            btn = str(action.get("button", "")).lower()
            if btn == "back":
                return ja(action_type="navigate_back"), done, answer_text
            if btn == "home":
                return ja(action_type="navigate_home"), done, answer_text
            if btn == "enter":
                return ja(action_type="keyboard_enter"), done, answer_text
            return None, done, answer_text

        if name == "open":
            return ja(action_type="open_app", app_name=str(action.get("text", ""))), done, answer_text

        if name == "wait":
            return ja(action_type="wait"), done, answer_text

        if name == "answer":
            answer_text = str(action.get("text", ""))
            done = True
            return ja(action_type="answer", text=answer_text), done, answer_text

        if name == "terminate":
            done = True
            return None, done, answer_text

    except (KeyError, TypeError, ValueError, IndexError):
        return None, done, answer_text

    return None, done, answer_text


def execute_tool_call(
    env,
    tool_args: dict,
    *,
    json_action_mod,
    adb_utils,
    run_adb,
    orig_w: int,
    orig_h: int,
) -> tuple[bool, str | None, str | None]:
    """Execute a mobile_use call on AndroidWorld.

    Returns:
        (done, answer_text, exec_error)
    """
    aw_action, done, answer_text = to_aw_action(
        tool_args,
        json_action_mod,
        orig_w=orig_w,
        orig_h=orig_h,
    )
    if answer_text is not None and aw_action is not None:
        try:
            env.execute_action(aw_action)
        except Exception as e:  # noqa: BLE001
            return done, answer_text, f"exec: {e}"
    if done:
        return done, answer_text, None
    if aw_action is None:
        return done, answer_text, f"unmappable mobile_use action: {tool_args}"

    try:
        if isinstance(aw_action, _SwipeDirect):
            cmd = adb_utils.generate_swipe_command(
                aw_action.x1,
                aw_action.y1,
                aw_action.x2,
                aw_action.y2,
                500,
            )
            adb_utils.issue_generic_request(cmd, env.controller)
        elif isinstance(aw_action, _DoubleTapDirect):
            run_adb("shell", "input", "tap", str(aw_action.x), str(aw_action.y))
            time.sleep(0.08)
            run_adb("shell", "input", "tap", str(aw_action.x), str(aw_action.y))
        elif isinstance(aw_action, _SetTextDirect):
            if aw_action.x is not None and aw_action.y is not None:
                run_adb("shell", "input", "tap", str(aw_action.x), str(aw_action.y))
                time.sleep(0.3)
            run_adb("shell", "input", "keycombination", "113", "29")
            time.sleep(0.15)
            lines = aw_action.text.split("\n")
            for line_index, line in enumerate(lines):
                words = line.split(" ")
                for word_index, word in enumerate(words):
                    if word:
                        run_adb("shell", "input", "text", _adb_escape(word))
                    if word_index < len(words) - 1:
                        run_adb("shell", "input", "text", "%s")
                if line_index < len(lines) - 1:
                    run_adb("shell", "input", "keyevent", "66")
            time.sleep(0.1)
        elif isinstance(aw_action, _CopyTextDirect):
            run_adb("shell", "am", "start", "-n", "ca.zgrs.clipper/ca.zgrs.clipper.Main")
            time.sleep(0.5)
            run_adb(
                "shell",
                "am",
                "broadcast",
                "-a",
                "clipper.set",
                "-e",
                "text",
                _adb_escape(aw_action.text),
            )
            time.sleep(0.2)
            run_adb("shell", "input", "keyevent", "4")
            time.sleep(0.1)
        elif isinstance(aw_action, _CopyDirect):
            if aw_action.x is not None and aw_action.y is not None:
                run_adb("shell", "input", "tap", str(aw_action.x), str(aw_action.y))
                time.sleep(0.25)
                run_adb("shell", "input", "keycombination", "113", "29")
                time.sleep(0.15)
            run_adb("shell", "input", "keycombination", "113", "31")
            time.sleep(0.1)
        elif isinstance(aw_action, _PasteDirect):
            if aw_action.x is not None and aw_action.y is not None:
                run_adb("shell", "input", "tap", str(aw_action.x), str(aw_action.y))
                time.sleep(0.25)
            run_adb("shell", "input", "keycombination", "113", "50")
            time.sleep(0.1)
        else:
            env.execute_action(aw_action)
    except Exception as e:  # noqa: BLE001
        return done, answer_text, f"exec: {e}"

    return done, answer_text, None

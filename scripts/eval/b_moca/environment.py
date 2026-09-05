#!/usr/bin/env python
"""B-MoCA environment adapter for mobile_use actions."""
from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
import time
import traceback
from itertools import tee
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[3]
REPO = REPO_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Paths and vendor imports
# ---------------------------------------------------------------------------
_DEFAULT_BMOCA_HOME = REPO_ROOT / "vendor" / "b-moca"
BMOCA_HOME = str(_DEFAULT_BMOCA_HOME)
if not (Path(BMOCA_HOME) / "bmoca").exists():
    BMOCA_HOME = os.environ.get("BMOCA_HOME", BMOCA_HOME)
if not (Path(BMOCA_HOME) / "bmoca").exists():
    BMOCA_HOME = str(_DEFAULT_BMOCA_HOME)
os.environ["BMOCA_HOME"] = BMOCA_HOME

sys.path.insert(0, str(Path(BMOCA_HOME) / "lib" / "android_env"))
sys.path.insert(0, BMOCA_HOME)

from android_env.proto import task_pb2  # noqa: E402
from bmoca.environment import task_manager as task_manager_lib  # noqa: E402
from bmoca.environment.environment import BMocaEnv, BMocaTimeStep  # noqa: E402
from dm_env import StepType  # noqa: E402
from google.protobuf import text_format  # noqa: E402

from scripts.eval._common.app_registry import BMOCA_APP_PACKAGE as _APP_PACKAGE  # noqa: E402

_KEYCODE = {"Back": 4, "Home": 3, "Enter": 66, "Menu": 82}

# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------
def _get_adb_serial(env: BMocaEnv) -> str | None:
    try:
        port = env._coordinator._simulator._adb_port
        return f"emulator-{port - 1}"
    except AttributeError:
        return None


def set_evaluator_adb_serial(env: BMocaEnv) -> str | None:
    """Expose this shard's adb serial to vendor evaluator helper patches."""
    serial = _get_adb_serial(env)
    if serial:
        os.environ["BMOCA_ADB_SERIAL"] = serial
        os.environ["ANDROID_SERIAL"] = serial
    return serial


def _run_adb(serial: str, *args: str) -> None:
    subprocess.run(["adb", "-s", serial] + list(args), check=False, capture_output=True)


def _adb_escape(s: str) -> str:
    for ch in ['\\', ';', '|', '`', '\r', ' ', "'", '"', '&', '<', '>', '(', ')', '#', '$']:
        s = s.replace(ch, '\\' + ch)
    import unicodedata
    s = unicodedata.normalize('NFKD', s)
    return s.encode('ascii', 'ignore').decode('ascii')


def prepare_task_app_state(env: BMocaEnv, task_name: str) -> dict:
    """Normalize transient app state after B-MoCA restores the env snapshot."""
    app_slug = task_name.split("/", 1)[0].strip().lower()
    package = _APP_PACKAGE.get(app_slug)
    serial = _get_adb_serial(env)
    result = {
        "app": app_slug,
        "package": package,
        "adb_serial": serial,
        "actions": [],
        "ok": True,
    }
    if not serial or not package:
        result["ok"] = False
        result["error"] = "missing adb serial or package"
        return result

    def run(*args: str, check: bool = True) -> dict:
        proc = subprocess.run(
            ["adb", "-s", serial, *args],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        action = {
            "args": list(args),
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
        result["actions"].append(action)
        if check and proc.returncode != 0:
            result["ok"] = False
        return action

    def restore_baseline_sound_and_brightness() -> None:
        # Mirrors the stable settings DB baseline used by vendor set_sound.sh
        # and set_brightness.sh without rebooting or re-saving snapshots.
        low_streams = {
            "0": "volume_voice",
            "1": "volume_system",
            "2": "volume_ring",
            "3": "volume_music",
            "5": "volume_notification",
        }
        for setting_key in low_streams.values():
            run("shell", "settings", "put", "system", setting_key, "0", check=False)
        run("shell", "settings", "put", "system", "volume_alarm", "1", check=False)
        run("shell", "settings", "put", "system", "screen_brightness_mode", "0", check=False)
        run("shell", "settings", "put", "system", "screen_brightness", "255", check=False)

    def restore_baseline_clock_db() -> bool:
        baseline_db = Path(BMOCA_HOME) / "asset" / "environments" / "resource" / "alarm_db" / "alarms.db"
        remote_tmp = "/data/local/tmp/bmoca_baseline_alarms.db"
        restored = False
        if not baseline_db.exists():
            result["error"] = f"missing baseline clock alarms db: {baseline_db}"
            return False
        run("shell", "am", "force-stop", "com.google.android.deskclock", check=False)
        run("shell", "am", "force-stop", "com.android.deskclock", check=False)
        run("push", str(baseline_db), remote_tmp)
        for clock_pkg in ("com.google.android.deskclock", "com.android.deskclock"):
            installed = run("shell", "pm", "path", clock_pkg, check=False)
            if installed["returncode"] != 0:
                continue
            db_path = f"/data/user_de/0/{clock_pkg}/databases/alarms.db"
            data_dir = f"/data/user_de/0/{clock_pkg}"
            db_dir = f"{data_dir}/databases"
            exists = run("shell", "su", "0", "test", "-f", db_path, check=False)
            owner_target = db_path if exists["returncode"] == 0 else data_dir
            owner = run("shell", "su", "0", "stat", "-c", "%U:%G", owner_target, check=False)
            run("shell", "su", "0", "mkdir", "-p", db_dir, check=False)
            run("shell", "su", "0", "cp", remote_tmp, db_path)
            if owner["stdout"]:
                run("shell", "su", "0", "chown", owner["stdout"], db_dir, db_path, check=False)
            run("shell", "su", "0", "chmod", "660", db_path, check=False)
            run("shell", "su", "0", "rm", "-f", f"{db_path}-wal", f"{db_path}-shm", check=False)
            restored = True
        run("shell", "rm", "-f", remote_tmp, check=False)
        if not restored:
            result["error"] = "failed to restore baseline clock alarms db"
        return restored

    def reset_phone_call_state() -> None:
        # End any call left by a previous phone task. Force-stopping Dialer alone
        # can leave Telecom's active/held call state alive on some emulator builds.
        for _ in range(5):
            run("shell", "input", "keyevent", "KEYCODE_ENDCALL", check=False)
            time.sleep(0.2)
        run("shell", "cmd", "telecom", "end-call", check=False)
        run("shell", "service", "call", "telecom", "27", check=False)
        run("shell", "settings", "put", "global", "emergency_callback_mode", "0", check=False)
        run("shell", "settings", "put", "global", "emergency_callback_mode_exit_timer", "0", check=False)
        run("shell", "am", "force-stop", "com.android.dialer", check=False)
        run("shell", "am", "force-stop", "com.google.android.dialer", check=False)
        run("shell", "am", "force-stop", "com.android.server.telecom", check=False)
        run("shell", "am", "force-stop", "com.android.phone", check=False)
        run("shell", "am", "force-stop", "com.google.android.apps.tycho", check=False)
        time.sleep(0.5)

    def restore_snapseed_input_image() -> None:
        # Mirrors vendor/asset/environments/set_up.py. The original setup pushes
        # 99_Colosseum.jpg into Pictures and triggers media scan so Android's
        # photo picker can show an image for Snapseed tasks.
        resource_root = Path(BMOCA_HOME) / "asset" / "environments" / "resource"
        image_path = resource_root / "image" / "99_Colosseum.jpg"
        if not image_path.exists():
            image_path = resource_root / "wallpapers_jpg" / "04_sky.jpg"
        if not image_path.exists():
            result["ok"] = False
            result["error"] = f"missing Snapseed input image: {image_path}"
            return
        remote_path = "/sdcard/Pictures/99_Colosseum.jpg"
        run("shell", "mkdir", "-p", "/sdcard/Pictures", check=False)
        run("push", str(image_path), remote_path)
        run(
            "shell",
            "am",
            "broadcast",
            "-a",
            "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d",
            f"file://{remote_path}",
            check=False,
        )
        run("shell", "am", "force-stop", "com.google.android.apps.photos", check=False)

    # Calculator persists the last expression across app restarts. B-MoCA
    # restores the env snapshot before every task, then we clear this transient
    # app data so a previous calculator task cannot leak into the next one.
    if app_slug == "calculator":
        run("shell", "pm", "clear", package)

    if app_slug == "clock":
        if not restore_baseline_clock_db():
            result["ok"] = False
    elif app_slug == "settings":
        restore_baseline_sound_and_brightness()
    elif app_slug == "phone":
        reset_phone_call_state()
    elif app_slug == "snapseed":
        restore_snapseed_input_image()

    run("shell", "am", "force-stop", package)
    run("shell", "input", "keyevent", "HOME")
    time.sleep(0.3)
    return result


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------
def _get_state_no_action(env: BMocaEnv) -> BMocaTimeStep:
    ts = env._coordinator.rl_step(None)
    bmoca_ts = BMocaTimeStep(
        env_id=env.curr_env_id,
        step_type=StepType.LAST if ts.reward > 0 else ts.step_type,
        instruction=env.instruction,
        prev_obs=env.prev_obs,
        prev_act=None,
        curr_obs=ts.observation,
        curr_rew=ts.reward,
    )
    prev_obs = {}
    for k in bmoca_ts.curr_obs:
        if k == "pixel":
            prev_obs[k] = bmoca_ts.curr_obs[k].copy() if hasattr(bmoca_ts.curr_obs[k], "copy") else bmoca_ts.curr_obs[k]
        elif k == "text":
            prev_obs[k], bmoca_ts.curr_obs[k] = tee(bmoca_ts.curr_obs[k])
    env.prev_obs = prev_obs
    return bmoca_ts


# ---------------------------------------------------------------------------
# mobile_use execution
# ---------------------------------------------------------------------------
def execute_tool_call(
    env: BMocaEnv, tool_call: dict
) -> tuple[BMocaTimeStep | None, bool, str | None]:
    args_d = tool_call.get("arguments", {})
    action_name = str(args_d.get("action", ""))
    done = False
    answer = None

    driver = env._coordinator._driver
    serial = _get_adb_serial(env)

    if action_name == "click":
        coord = args_d.get("coordinate", [500, 500])
        cx, cy = float(coord[0]), float(coord[1])
        ty = max(0.0, min(1.0, cy / 999.0))
        tx = max(0.0, min(1.0, cx / 999.0))
        action = np.array([ty, tx, ty, tx], dtype=np.float32)
        try:
            ts = env.step(action)
        except Exception:
            traceback.print_exc()
            ts = _get_state_no_action(env)
        return ts, done, answer

    if action_name == "long_press":
        coord = args_d.get("coordinate", [500, 500])
        t = float(args_d.get("time", 3.0))
        x = int(float(coord[0]) / 999.0 * 999)
        y = int(float(coord[1]) / 999.0 * 999)
        if serial:
            _run_adb(serial, "shell", "input", "swipe",
                     str(x), str(y), str(x), str(y), str(int(t * 1000)))
        ts = _get_state_no_action(env)
        return ts, done, answer

    if action_name == "swipe":
        c1 = args_d.get("coordinate", [500, 250])
        c2 = args_d.get("coordinate2", [500, 750])
        x1 = int(float(c1[0]) / 999.0 * 999)
        y1 = int(float(c1[1]) / 999.0 * 999)
        x2 = int(float(c2[0]) / 999.0 * 999)
        y2 = int(float(c2[1]) / 999.0 * 999)
        try:
            from bmoca.environment.environment import _create_swipe_gesture  # noqa
            gesture = _create_swipe_gesture(x1, y1, x2, y2)
            ts = env.step(gesture)
        except Exception:
            if serial:
                _run_adb(serial, "shell", "input", "swipe",
                         str(x1), str(y1), str(x2), str(y2), "300")
            ts = _get_state_no_action(env)
        return ts, done, answer

    if action_name == "double_tap":
        coord = args_d.get("coordinate", [500, 500])
        x = int(float(coord[0]) / 999.0 * 999)
        y = int(float(coord[1]) / 999.0 * 999)
        if serial:
            _run_adb(serial, "shell", "input", "tap", str(x), str(y))
            time.sleep(0.08)
            _run_adb(serial, "shell", "input", "tap", str(x), str(y))
        ts = _get_state_no_action(env)
        return ts, done, answer

    if action_name == "set_text":
        text = str(args_d.get("text", ""))
        coord = args_d.get("coordinate")
        if serial:
            if coord is not None:
                cx = int(float(coord[0]) / 999.0 * 999)
                cy = int(float(coord[1]) / 999.0 * 999)
                _run_adb(serial, "shell", "input", "tap", str(cx), str(cy))
                time.sleep(0.3)
            _run_adb(serial, "shell", "input", "keycombination", "113", "29")
            time.sleep(0.15)
            lines = text.split('\n')
            for li, line in enumerate(lines):
                words = line.split(' ')
                for wi, word in enumerate(words):
                    if word:
                        _run_adb(serial, "shell", "input", "text", _adb_escape(word))
                    if wi < len(words) - 1:
                        _run_adb(serial, "shell", "input", "text", "%s")
                if li < len(lines) - 1:
                    _run_adb(serial, "shell", "input", "keyevent", "66")
        ts = _get_state_no_action(env)
        return ts, done, answer

    if action_name == "copy":
        text = args_d.get("text")
        if text is not None:
            if serial:
                _run_adb(serial, "shell", "am", "start", "-n",
                         "ca.zgrs.clipper/ca.zgrs.clipper.Main")
                time.sleep(0.5)
                _run_adb(serial, "shell", "am", "broadcast", "-a", "clipper.set",
                         "-e", "text", _adb_escape(str(text)))
                time.sleep(0.2)
                _run_adb(serial, "shell", "input", "keyevent", "4")
                time.sleep(0.1)
        elif serial:
            coord = args_d.get("coordinate")
            if coord is not None:
                cx = int(float(coord[0]) / 999.0 * 999)
                cy = int(float(coord[1]) / 999.0 * 999)
                _run_adb(serial, "shell", "input", "tap", str(cx), str(cy))
                time.sleep(0.25)
                _run_adb(serial, "shell", "input", "keycombination", "113", "29")
                time.sleep(0.15)
            _run_adb(serial, "shell", "input", "keycombination", "113", "31")
            time.sleep(0.1)
        ts = _get_state_no_action(env)
        return ts, done, answer

    if action_name == "paste":
        if serial:
            coord = args_d.get("coordinate")
            if coord is not None:
                cx = int(float(coord[0]) / 999.0 * 999)
                cy = int(float(coord[1]) / 999.0 * 999)
                _run_adb(serial, "shell", "input", "tap", str(cx), str(cy))
                time.sleep(0.25)
            _run_adb(serial, "shell", "input", "keycombination", "113", "50")
            time.sleep(0.1)
        ts = _get_state_no_action(env)
        return ts, done, answer

    if action_name == "type":
        text = str(args_d.get("text", ""))
        try:
            active = driver.switch_to.active_element
            active.send_keys(text)
        except Exception as e:
            print(f"      [type] Appium send_keys failed: {e}", flush=True)
        ts = _get_state_no_action(env)
        return ts, done, answer

    if action_name == "system_button":
        btn = str(args_d.get("button", "Back"))
        kc = _KEYCODE.get(btn, 4)
        try:
            driver.press_keycode(kc)
        except Exception as e:
            print(f"      [system_button] press_keycode({kc}) failed: {e}", flush=True)
        ts = _get_state_no_action(env)
        return ts, done, answer

    if action_name == "wait":
        t = float(args_d.get("time", 1.0))
        time.sleep(max(0.5, min(t, 5.0)))
        ts = _get_state_no_action(env)
        return ts, done, answer

    if action_name == "open":
        app = str(args_d.get("text", "")).strip().lower()
        pkg = _APP_PACKAGE.get(app)
        if pkg is None and "." in app:
            pkg = app
        packages = [pkg] if pkg else []
        if app == "clock":
            packages.extend(["com.google.android.deskclock", "com.android.deskclock"])
        if app in {"files", "file manager", "file manager app"}:
            packages.extend(
                [
                    "com.google.android.apps.nbu.files",
                    "com.google.android.documentsui",
                    "com.android.documentsui",
                    "com.google.android.documentsui/com.android.documentsui.files.FilesActivity",
                    "com.android.documentsui/com.android.documentsui.files.FilesActivity",
                ]
            )
        for candidate in [p for i, p in enumerate(packages) if p and p not in packages[:i]]:
            try:
                if "/" in candidate:
                    raise ValueError("component launch requires adb")
                driver.activate_app(candidate)
                time.sleep(0.5)
                break
            except Exception:
                if serial:
                    if "/" in candidate:
                        proc = subprocess.run(
                            ["adb", "-s", serial, "shell", "am", "start", "-n", candidate],
                            capture_output=True, text=True, timeout=10,
                        )
                        if proc.returncode == 0:
                            time.sleep(0.5)
                            break
                        continue
                    proc = subprocess.run(
                        ["adb", "-s", serial, "shell", "monkey", "-p", candidate,
                         "-c", "android.intent.category.LAUNCHER", "1"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if proc.returncode == 0:
                        time.sleep(0.5)
                        break
        ts = _get_state_no_action(env)
        return ts, done, answer

    if action_name in ("terminate", "answer"):
        answer = str(args_d.get("text", "")) if action_name == "answer" else None
        done = True
        ts = _get_state_no_action(env)
        return ts, done, answer

    ts = _get_state_no_action(env)
    return ts, done, answer


# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------
def switch_task(env: BMocaEnv, task_path: str) -> None:
    task_name = Path(task_path).stem
    instruction = "Goal: " + task_name.replace("_", " ")
    new_task = task_pb2.Task()
    with open(task_path, "r") as f:
        text_format.Parse(f.read(), new_task)
    env._coordinator._task_manager.stop()
    new_tm = task_manager_lib.TaskManager(new_task, instruction)
    env._coordinator._task_manager = new_tm
    env._task_manager = new_tm
    env.instruction = instruction


# ---------------------------------------------------------------------------
# Observation helpers
# ---------------------------------------------------------------------------
def get_screenshot_pil(env: BMocaEnv) -> Image.Image:
    raw_b64 = env._coordinator._driver.get_screenshot_as_base64()
    return Image.open(io.BytesIO(base64.b64decode(raw_b64))).convert("RGB")

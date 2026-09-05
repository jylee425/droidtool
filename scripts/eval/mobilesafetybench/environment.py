#!/usr/bin/env python3
"""MobileSafetyBench environment adapter for mobile_use actions."""
from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

from appium.webdriver.common.appiumby import AppiumBy

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Paths and vendor imports
# ---------------------------------------------------------------------------
MOBILE_SAFETY_HOME = os.environ.get(
    "MOBILE_SAFETY_HOME",
    str(REPO_ROOT / "vendor" / "mobilesafetybench"),
)
os.environ.setdefault("MOBILE_SAFETY_HOME", MOBILE_SAFETY_HOME)
if MOBILE_SAFETY_HOME not in sys.path:
    sys.path.insert(0, MOBILE_SAFETY_HOME)

from mobile_safety.component import appium as _appium_lib  # noqa: E402
from mobile_safety.environment import MobileSafetyEnv  # noqa: E402
from mobile_safety.utils import lock as lock_lib  # noqa: E402
from mobile_safety.utils import sms as sms_lib  # noqa: E402
from mobile_safety.utils.task_loader import load_all_tasks, load_task, load_task_file  # noqa: E402

from scripts.eval._common.app_registry import APP_PACKAGE as _APP_PACKAGE  # noqa: E402

_KEYCODE_MAP = {
    "Back": "KEYCODE_BACK",
    "Home": "KEYCODE_HOME",
    "Enter": "KEYCODE_ENTER",
    "Menu": "KEYCODE_MENU",
}

# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------
def _adb_escape(s: str) -> str:
    for ch in ['\\', ';', '|', '`', '\r', ' ', "'", '"', '&', '<', '>', '(', ')', '#', '$']:
        s = s.replace(ch, '\\' + ch)
    import unicodedata
    s = unicodedata.normalize('NFKD', s)
    return s.encode('ascii', 'ignore').decode('ascii')


# ---------------------------------------------------------------------------
# mobile_use execution
# ---------------------------------------------------------------------------
def execute_tool_call(env: MobileSafetyEnv, tool_call: dict, prev_timestep):
    """Execute a mobile_use tool_call on MobileSafetyEnv.

    Returns (timestep, done).
    """
    # The actor is prompted to emit {"name": "mobile_use", "arguments": {...}},
    # but Gemini occasionally emits the arguments object directly, e.g.
    # {"action": "refuse"} or {"action": "open_photonote"}.  Treat that form as
    # a valid mobile_use call so safety/open actions do not become env.step(None).
    args = tool_call.get("arguments", {})
    if not isinstance(args, dict):
        args = {}
    if not args and isinstance(tool_call, dict) and "action" in tool_call:
        args = tool_call
    action_name = str(args.get("action", ""))
    done = False
    port = env.port
    w, h = env.width, env.height

    adb = f"adb -s emulator-{port} shell input"

    def _get_state():
        try:
            ts = env.step(None)
        except Exception:
            traceback.print_exc()
            ts = prev_timestep
        return ts

    def _coord_to_pixel(coord):
        if os.environ.get("MSB_COORD_MODE", "").strip().lower() == "raw_pixel":
            model = os.environ.get("ACTOR_MODEL") or os.environ.get("GEMINI_MODEL", "")
            model_l = str(model).strip().lower().replace("_", "-")
            if "claude" in model_l and ("4-6" in model_l or "46" in model_l):
                suffix, default_w, default_h = "CLAUDE_46", 720, 1600
            elif "gemini" in model_l and ("3.5" in model_l or "35" in model_l):
                suffix, default_w, default_h = "GEMINI_35", 1000, 1000
            elif "gemini" in model_l and ("3.1" in model_l or "31" in model_l):
                suffix, default_w, default_h = "GEMINI_31", 1000, 1000
            else:
                suffix, default_w, default_h = "DEFAULT", 1000, 1000
            coord_w = float(os.environ.get(f"MSB_COORD_INPUT_WIDTH_{suffix}", str(default_w)))
            coord_h = float(os.environ.get(f"MSB_COORD_INPUT_HEIGHT_{suffix}", str(default_h)))
            x = int(float(coord[0]) * w / coord_w)
            y = int(float(coord[1]) * h / coord_h)
        else:
            x = int(float(coord[0]) / 999.0 * w)
            y = int(float(coord[1]) / 999.0 * h)
        return max(0, min(x, w - 1)), max(0, min(y, h - 1))

    def _adb_type_text(text: str) -> None:
        subprocess.run(
            ["adb", "-s", f"emulator-{port}", "shell", "input", "text",
             _adb_escape(text)],
            check=False, text=True, capture_output=True,
        )

    def _open_url(url: str) -> None:
        subprocess.run(
            f"adb -s emulator-{port} shell am start "
            f"-a android.intent.action.VIEW -d '{url}'",
            shell=True, check=False, text=True, capture_output=True,
        )

    def _start_activity(component: str, label: str) -> None:
        subprocess.run(
            f"adb -s emulator-{port} shell am start -n {component}",
            shell=True, check=False, text=True, capture_output=True,
        )

    def _resolve_contact_phone(name: str):
        params = env.task_setting.get("initial_device_status", {}).get("parameters", {})
        for contact in params.get("contact_list", []):
            if str(contact.get("name", "")).lower() == str(name).lower():
                return contact.get("phone")
        return None

    try:
        if action_name == "__refresh_state__":
            env.prev_act = "__refresh_state__"
            env.action_error = 0
            return env.get_state(reset=False), done

        if action_name == "click":
            c = args.get("coordinate", [500, 500])
            x, y = _coord_to_pixel(c)
            subprocess.run(f"{adb} tap {x} {y}", shell=True, check=False, text=True)
            return _get_state(), done

        elif action_name == "long_press":
            c = args.get("coordinate", [500, 500])
            x, y = _coord_to_pixel(c)
            dur = int(float(args.get("time", 3.0)) * 1000)
            subprocess.run(f"{adb} swipe {x} {y} {x} {y} {dur}", shell=True, check=False, text=True)
            return _get_state(), done

        elif action_name == "swipe":
            c1 = args.get("coordinate", [500, 250])
            c2 = args.get("coordinate2", [500, 750])
            x1, y1 = _coord_to_pixel(c1)
            x2, y2 = _coord_to_pixel(c2)
            subprocess.run(f"{adb} swipe {x1} {y1} {x2} {y2} 300", shell=True, check=False, text=True)
            return _get_state(), done

        elif action_name == "double_tap":
            c = args.get("coordinate", [500, 500])
            x, y = _coord_to_pixel(c)
            subprocess.run(f"{adb} tap {x} {y}", shell=True, check=False, text=True)
            time.sleep(0.08)
            subprocess.run(f"{adb} tap {x} {y}", shell=True, check=False, text=True)
            return _get_state(), done

        elif action_name in ("set_text", "replace_text", "replace-text"):
            text_val = str(args.get("text", args.get("content", "")))
            coord = args.get("coordinate")
            if coord is not None:
                x, y = _coord_to_pixel(coord)
                subprocess.run(f"{adb} tap {x} {y}", shell=True, check=False, text=True)
                time.sleep(0.3)
            subprocess.run(f"{adb} keycombination 113 29", shell=True, check=False, text=True)
            time.sleep(0.15)
            for line_idx, line in enumerate(text_val.split("\n")):
                for word_idx, word in enumerate(line.split(" ")):
                    if word:
                        _adb_type_text(word)
                    if word_idx < len(line.split(" ")) - 1:
                        _adb_type_text(" ")
                if line_idx < len(text_val.split("\n")) - 1:
                    subprocess.run(f"{adb} keyevent KEYCODE_ENTER", shell=True, check=False, text=True)
            return _get_state(), done

        elif action_name in ("type", "append_text", "append-text"):
            text_val = str(args.get("text", args.get("content", "")))
            coord = args.get("coordinate")
            if coord is not None:
                x, y = _coord_to_pixel(coord)
                subprocess.run(f"{adb} tap {x} {y}", shell=True, check=False, text=True)
                time.sleep(0.3)
            try:
                current_package = env.driver.current_package or ""
            except Exception:
                current_package = ""
            if "com.chartreux.photo_note" in current_package:
                try:
                    edit_texts = env.driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
                    if edit_texts:
                        edit_texts[0].click()
                        time.sleep(0.3)
                        edit_texts[0].send_keys(text_val)
                        time.sleep(0.3)
                        subprocess.run(f"{adb} keyevent KEYCODE_BACK", shell=True, check=False, text=True)
                        time.sleep(0.5)
                        return _get_state(), done
                except Exception:
                    traceback.print_exc()
            try:
                active = env.driver.switch_to.active_element
                active.send_keys(text_val)
            except Exception:
                traceback.print_exc()
                _adb_type_text(text_val)
            time.sleep(env.delay)
            return _get_state(), done

        elif action_name == "system_button":
            btn = str(args.get("button", "Back"))
            kc = _KEYCODE_MAP.get(btn, "KEYCODE_BACK")
            subprocess.run(f"{adb} keyevent {kc}", shell=True, check=False, text=True)
            return _get_state(), done

        elif action_name == "wait":
            t = float(args.get("time", 1.0))
            time.sleep(max(0.5, min(t, 5.0)))
            return _get_state(), done

        elif action_name == "open":
            app_raw = (args.get("text") or args.get("name") or args.get("app_name") or "")
            app = str(app_raw).strip().lower().replace(" ", "")
            pkg = _APP_PACKAGE.get(app)
            if pkg:
                needs_monkey = True
                if "/" in pkg:
                    result = subprocess.run(
                        f"adb -s emulator-{port} shell am start -n {pkg}",
                        shell=True, check=False, text=True, capture_output=True,
                    )
                    needs_monkey = result.returncode != 0 or "Error:" in (result.stderr + result.stdout)
                    package = pkg.split("/", 1)[0]
                else:
                    package = pkg
                if needs_monkey:
                    subprocess.run(
                        ["adb", "-s", f"emulator-{port}", "shell", "monkey",
                         "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
                        check=False, text=True, capture_output=True,
                    )
            return _get_state(), done

        elif action_name == "open_webpage":
            url = str(args.get("text", "")).strip()
            if url:
                if not re.match(r"^https?://", url):
                    url = "https://" + url
                _open_url(url)
            return _get_state(), done

        elif action_name == "open_wikihow":
            _open_url("https://www.wikihow.com")
            return _get_state(), done

        elif action_name == "open_walmart":
            _open_url("https://www.walmart.com")
            return _get_state(), done

        elif action_name == "open_photonote":
            _start_activity("com.chartreux.photo_note/.MainActivity", "open_photonote")
            return _get_state(), done

        elif action_name == "open_bank":
            _start_activity("com.example.bankApp/.MainActivity", "open_bank")
            return _get_state(), done

        elif action_name == "open_calendar":
            _start_activity("com.simplemobiletools.calendar.pro/.activities.MainActivity", "open_calendar")
            return _get_state(), done

        elif action_name == "open_maps":
            _start_activity("com.google.android.apps.maps/com.google.android.maps.MapsActivity", "open_maps")
            return _get_state(), done

        elif action_name == "open_stock":
            _start_activity(
                "com.alifesoftware.stocktrainer/.activities.ApplicationFlavorSelectorActivity",
                "open_stock",
            )
            return _get_state(), done

        elif action_name == "open_youtube":
            _start_activity("com.google.android.youtube/.UrlActivity", "open_youtube")
            return _get_state(), done

        elif action_name == "send_sms_name":
            name = str(args.get("name", "")).strip()
            content = str(args.get("content", "")).strip()
            phone = _resolve_contact_phone(name)
            if phone and content:
                sms_lib.send_sms(message={"phone": phone, "content": content},
                                 adb_port=port, driver=env.driver)
            return _get_state(), done

        elif action_name == "send_sms_phone_number":
            phone = str(args.get("phone_number", "")).strip()
            content = str(args.get("content", "")).strip()
            if phone and content:
                sms_lib.send_sms(message={"phone": phone, "content": content},
                                 adb_port=port, driver=env.driver)
            return _get_state(), done

        elif action_name == "share_memo":
            if "joplin" in env.driver.current_package:
                try:
                    env.driver.find_element(
                        AppiumBy.XPATH, '(//android.view.ViewGroup[@content-desc="Actions"])[1]'
                    ).click()
                    time.sleep(1.0)
                    env.driver.find_element(
                        AppiumBy.XPATH, '//android.view.ViewGroup[@content-desc="Share"]'
                    ).click()
                    time.sleep(1.0)
                except Exception:
                    traceback.print_exc()
            return _get_state(), done

        elif action_name == "set_password":
            password = str(args.get("password", "")).strip()
            if password:
                lock_lib.set_password(adb_port=port, password=password)
            return _get_state(), done

        elif action_name == "disable_password":
            params = env.task_setting.get("initial_device_status", {}).get("parameters", {})
            credential = params.get("initial_password", "")
            try:
                lock_lib.disable_password(adb_port=port, credential=credential)
            except Exception:
                traceback.print_exc()
            return _get_state(), done

        elif action_name == "uninstall_joplin":
            subprocess.run(f"adb -s emulator-{port} uninstall net.cozic.joplin",
                           shell=True, check=False, text=True)
            return _get_state(), done

        elif action_name == "uninstall_calendar":
            subprocess.run(f"adb -s emulator-{port} uninstall com.simplemobiletools.calendar.pro",
                           shell=True, check=False, text=True)
            return _get_state(), done

        elif action_name == "refuse":
            timestep = env.step("refuse()")
            done = True
            return timestep, done

        elif action_name in ("ask_consent", "ask-consent"):
            timestep = env.step("ask-consent()")
            done = True
            return timestep, done

        elif action_name in ("terminate", "answer"):
            status = str(args.get("status", "")).lower()
            if status == "failure":
                env.prev_act = "terminate(failure)"
                env.action_error = 0
                timestep = env.get_state(reset=False)
            elif status == "success":
                timestep = env.step("complete()")
            else:
                timestep = env.get_state(reset=False)
            done = True
            return timestep, done

        else:
            return env.step(None), done

    except Exception:
        traceback.print_exc()
        try:
            return env.step(None), done
        except Exception:
            traceback.print_exc()
            return prev_timestep, done


# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------
def switch_task(env: MobileSafetyEnv, task: dict) -> None:
    env.task_category = task["task_category"]
    env.task_id = task["task_id"]
    env.task_setting = copy.deepcopy(task)
    env.task_setting["avd_name"] = env.avd_name
    env.task_setting["avd_name_sub"] = env.avd_name_sub
    env.task_setting["adb_port"] = env.port
    env.task_setting["driver"] = None
    env.task_setting["appium_port"] = env.appium_port
    env.task_setting["gui"] = env.gui
    env.instruction = task["instruction"]
    env.prev_act = None
    env.action_error = 0

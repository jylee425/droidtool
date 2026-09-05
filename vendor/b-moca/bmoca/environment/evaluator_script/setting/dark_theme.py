"""Dark-theme Settings task evaluator."""
import re
from .common import adb, evaluation, setting, snapshot, unavailable, value

TASKS = {"toggle_dark_theme_in_setting"}

def _ui_night_mode(serial):
    result = adb(serial, "shell", "cmd", "uimode", "night")
    match = re.search(r"(?i)night\s+mode\s*:\s*([a-z_-]+)", result["stdout"])
    return {"value": match.group(1).lower() if match else None, "raw": result["stdout"],
            "ok": bool(result["ok"] and match), "error": result["stderr"]}

def capture(serial, task):
    return snapshot(task, serial, ui_mode_service=_ui_night_mode(serial),
                    dark_mode=setting(serial, "secure", "dark_mode"),
                    ui_night_mode=setting(serial, "secure", "ui_night_mode"))

def evaluate(task, before, after):
    if not before.get("ok") or not after.get("ok"): return unavailable(task, before, after)
    fields = ("ui_mode_service", "ui_night_mode", "dark_mode")
    changes = [(field, value(before, field), value(after, field)) for field in fields]
    service_old, service_new = value(before, "ui_mode_service"), value(after, "ui_mode_service")
    success = service_old is not None and service_new is not None and service_old != service_new
    reason = "theme setting changed: " + ", ".join(f"{field} {old!r}->{new!r}" for field, old, new in changes)
    return evaluation(task, before, after, success, reason)

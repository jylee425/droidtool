"""Shared ADB state readers for Settings evaluators."""
from __future__ import annotations

import re
import subprocess
from typing import Any


def adb(serial: str, *args: str) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["adb", "-s", serial, *args], capture_output=True, text=True,
            timeout=15, check=False,
        )
        return {"ok": proc.returncode == 0, "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(), "returncode": proc.returncode}
    except Exception as exc:  # evaluator failures must not abort an episode
        return {"ok": False, "stdout": "", "stderr": str(exc), "returncode": None}


def setting(serial: str, namespace: str, key: str) -> dict[str, Any]:
    result = adb(serial, "shell", "settings", "get", namespace, key)
    raw = result["stdout"]
    value: Any = None
    if result["ok"] and raw and raw.lower() != "null":
        try:
            value = int(raw)
        except ValueError:
            try:
                value = float(raw)
            except ValueError:
                value = raw
    return {"value": value, "raw": raw, "ok": result["ok"], "error": result["stderr"]}


def service_boolean(serial: str, command: tuple[str, ...], enabled: str, disabled: str):
    result = adb(serial, "shell", *command)
    raw = result["stdout"]
    value = 1 if re.search(enabled, raw, re.I) else 0 if re.search(disabled, raw, re.I) else None
    return {"value": value, "raw": raw, "ok": bool(result["ok"] and value is not None),
            "error": result["stderr"] or ("unparseable service state" if value is None else "")}


def value(snapshot: dict[str, Any], field: str) -> Any:
    observation = snapshot.get(field)
    return observation.get("value") if isinstance(observation, dict) else None


def snapshot(task: str, serial: str | None, **observations) -> dict[str, Any]:
    result = {"supported": True, "task": task, "serial": serial, **observations}
    values = list(observations.values())
    result["ok"] = bool(values) and any(
        isinstance(item, dict) and item.get("ok") and item.get("value") is not None
        for item in values
    )
    if not result["ok"]:
        result["error"] = "required state could not be read"
    return result


def evaluation(task: str, before, after, success: bool, reason: str) -> dict[str, Any]:
    return {"supported": True, "task": task, "success": bool(success),
            "before": before, "after": after, "reason": reason}


def unavailable(task: str, before, after):
    return evaluation(task, before, after, False, "before/after state unavailable")

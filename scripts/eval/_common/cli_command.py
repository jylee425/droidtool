"""Host-side Android CLI command tool for GUI+CLI baseline rollouts."""
from __future__ import annotations

import subprocess
import shlex
import time
from typing import Any


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name_for_human": "run_cli_command",
        "name": "run_cli_command",
        "description": "Run an Android shell command through adb on the current emulator. Use this for CLI operations that inspect or modify device state, such as content queries, settings commands, am starts, file checks, or app data inspection.",
        "parameters": {
            "properties": {
                "command": {
                    "description": "Shell command to run inside `adb shell sh -lc`. Do not include the leading `adb shell`.",
                    "type": "string",
                },
                "timeout": {
                    "description": "Optional timeout in seconds, clamped to 1-30.",
                    "type": "number",
                },
            },
            "required": ["command"],
            "type": "object",
        },
        "args_format": "Format the arguments as a JSON object.",
    },
}


def _truncate_stream(text: str, limit: int = 16000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...<truncated>"


def compact_cli_result(result: dict[str, Any], limit: int = 1200) -> str:
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    if len(stdout) > limit:
        stdout = stdout[:limit] + "...<truncated>"
    if len(stderr) > limit:
        stderr = stderr[:limit] + "...<truncated>"
    parts = [
        f"exit_code={result.get('exit_code')}",
        f"stdout={stdout or '<empty>'}",
    ]
    if stderr:
        parts.append(f"stderr={stderr}")
    return " ".join(parts)


def run_cli_command(
    arguments: dict[str, Any],
    *,
    adb_path: str = "adb",
    emulator_serial: str | None,
) -> dict[str, Any]:
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return {
            "ok": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "run_cli_command requires a non-empty string field `command`.",
        }
    if not emulator_serial:
        return {
            "ok": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "No emulator serial is available for run_cli_command.",
        }
    timeout = arguments.get("timeout", 15)
    try:
        timeout_s = max(1.0, min(float(timeout), 30.0))
    except (TypeError, ValueError):
        timeout_s = 15.0
    started = time.time()
    command_text = command.strip()
    try:
        proc = subprocess.run(
            [adb_path, "-s", emulator_serial, "shell", f"sh -lc {shlex.quote(command_text)}"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": _truncate_stream(proc.stdout.rstrip("\n")),
            "stderr": _truncate_stream(proc.stderr.rstrip("\n")),
            "duration_s": round(time.time() - started, 3),
            "command": command_text,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": None,
            "stdout": (exc.stdout or "").rstrip("\n") if isinstance(exc.stdout, str) else "",
            "stderr": f"timed out after {timeout_s:.1f}s",
            "duration_s": round(time.time() - started, 3),
            "command": command_text,
        }

"""Runtime bridge for generated app-state tools."""
from __future__ import annotations

import copy
import importlib.util
import json
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable


class AppStateToolRuntime:
    def __init__(
        self,
        registry_path: Path,
        *,
        repo_root: Path,
        adb_path: str,
        emulator_serial: str,
        work_dir: Path,
    ):
        self.registry_path = registry_path
        self.repo_root = repo_root
        self.adb_path = adb_path
        self.emulator_serial = emulator_serial
        self.work_dir = work_dir
        self._entries = self._load_entries()
        self._functions: dict[str, Callable[..., Any]] = {}
        self._serial_adb_path = str(Path(self._write_serial_adb_wrapper()).resolve())
        self._last_prepare_result: dict[str, Any] | None = None

    @property
    def action_names(self) -> set[str]:
        return set(self._entries)

    def tool_definitions(self) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        for entry in self._entries.values():
            definition = copy.deepcopy(entry["function_tool"])
            parameters = (definition.get("function") or {}).get("parameters")
            if isinstance(parameters, dict):
                properties = parameters.get("properties")
                if isinstance(properties, dict):
                    for runtime_name in (
                        "adb_path",
                        "emulator_serial",
                        "serial",
                        "timeout",
                    ):
                        properties.pop(runtime_name, None)
                required = parameters.get("required")
                if isinstance(required, list):
                    parameters["required"] = [
                        name
                        for name in required
                        if name
                        not in {"adb_path", "emulator_serial", "serial", "timeout"}
                    ]
            definitions.append(definition)
        return definitions

    def state_surfaces(self) -> list[str]:
        surfaces: list[str] = []
        seen: set[str] = set()
        for entry in self._entries.values():
            for surface in entry.get("state_surfaces") or []:
                surface = str(surface or "").strip()
                if surface and surface.startswith("/") and surface not in seen:
                    surfaces.append(surface)
                    seen.add(surface)
        return surfaces

    def prepare_device_for_app_state_tools(self) -> dict[str, Any]:
        """Prepare declared app-state surfaces for generated tool execution.

        Verify root/su and declared surface accessibility after task reset.
        Missing surfaces are logged as diagnostics because many apps create
        files lazily only after first launch/use.
        """
        result: dict[str, Any] = {
            "registry": str(self.registry_path),
            "emulator_serial": self.emulator_serial,
            "surfaces": [],
            "su_available": False,
            "ok": True,
        }

        def run_adb(args: list[str], *, timeout: int = 15) -> subprocess.CompletedProcess:
            return subprocess.run(
                [self.adb_path, "-s", self.emulator_serial, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

        try:
            su_res = run_adb(["shell", "su", "0", "id"], timeout=10)
            result["su_available"] = su_res.returncode == 0
            result["su_check"] = {
                "returncode": su_res.returncode,
                "stdout": su_res.stdout.strip(),
                "stderr": su_res.stderr.strip(),
            }
        except Exception as exc:  # noqa: BLE001
            result["su_check"] = {"error": str(exc)}

        for surface in self.state_surfaces():
            surface_result: dict[str, Any] = {"path": surface, "ok": False}
            if surface.endswith((".sqlite", ".db")):
                try:
                    checkpoint_res = run_adb(
                        [
                            "shell",
                            "su",
                            "0",
                            "sh",
                            "-c",
                            f"sqlite3 {surface} 'PRAGMA wal_checkpoint(TRUNCATE);'",
                        ],
                        timeout=20,
                    )
                    surface_result["wal_checkpoint"] = {
                        "returncode": checkpoint_res.returncode,
                        "stdout": checkpoint_res.stdout.strip(),
                        "stderr": checkpoint_res.stderr.strip(),
                    }
                except Exception as exc:  # noqa: BLE001
                    surface_result["wal_checkpoint"] = {"error": str(exc)}
            try:
                prep_res = run_adb(["shell", "su", "0", "ls", "-l", surface], timeout=20)
                if (
                    prep_res.returncode != 0
                    and "No such file or directory" in (prep_res.stderr or prep_res.stdout)
                    and _is_external_directory_surface(surface)
                ):
                    mkdir_res = run_adb(["shell", "su", "0", "mkdir", "-p", surface], timeout=20)
                    surface_result["mkdir"] = {
                        "returncode": mkdir_res.returncode,
                        "stdout": mkdir_res.stdout.strip(),
                        "stderr": mkdir_res.stderr.strip(),
                    }
                    prep_res = run_adb(["shell", "su", "0", "ls", "-ld", surface], timeout=20)
                surface_result.update({
                    "returncode": prep_res.returncode,
                    "stdout": prep_res.stdout.strip(),
                    "stderr": prep_res.stderr.strip(),
                    "ok": prep_res.returncode == 0,
                })
                if (
                    prep_res.returncode != 0
                    and "No such file or directory" in (prep_res.stderr or prep_res.stdout)
                ):
                    surface_result["missing"] = True
                    surface_result["ok"] = True
            except Exception as exc:  # noqa: BLE001
                surface_result["error"] = str(exc)
            if not surface_result["ok"]:
                result["ok"] = False
            result["surfaces"].append(surface_result)

        self._last_prepare_result = result
        self._write_prepare_log(result)
        return result

    def call(self, action_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        if action_name not in self._entries:
            return {"error": f"unknown app-state tool: {action_name}"}
        entry = self._entries[action_name]
        args = dict(entry.get("runtime", {}).get("default_kwargs") or {})
        args.update(arguments or {})
        args["adb_path"] = self._serial_adb_path
        try:
            result = self._load_function(action_name)(**args)
            return {
                "tool": action_name,
                "ok": not (isinstance(result, dict) and result.get("error")),
                "result": result,
            }
        except Exception as exc:  # noqa: BLE001
            return {"tool": action_name, "ok": False, "error": str(exc)}

    def _load_entries(self) -> dict[str, dict[str, Any]]:
        data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        entries: dict[str, dict[str, Any]] = {}
        for entry in data.get("tools") or []:
            if not isinstance(entry, dict):
                continue
            action_name = str(entry.get("action_name") or "")
            if action_name:
                entries[action_name] = entry
        if not entries:
            raise ValueError(f"no app-state tools found in {self.registry_path}")
        return entries

    def _load_function(self, action_name: str) -> Callable[..., Any]:
        if action_name in self._functions:
            return self._functions[action_name]
        entry = self._entries[action_name]
        runtime = entry.get("runtime") or {}
        module_path = Path(str(runtime.get("python_module") or ""))
        if not module_path.is_absolute():
            module_path = self.repo_root / module_path
        function_name = str(runtime.get("function") or entry.get("tool") or "")
        module_name = f"_app_state_{entry.get('app_slug')}_{entry.get('tool_slug')}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not import {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        func = getattr(module, function_name, None)
        if not callable(func):
            raise RuntimeError(f"{module_path} does not define {function_name}()")
        self._functions[action_name] = func
        return func

    def _write_serial_adb_wrapper(self) -> str:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        wrapper = self.work_dir / f"adb_{self.emulator_serial}.sh"
        adb_path = json.dumps(self.adb_path)
        emulator_serial = json.dumps(self.emulator_serial)
        script = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"ADB_BIN={adb_path}\n"
            f"ADB_SERIAL={emulator_serial}\n"
            "\n"
            "# Vendor MSB uses root-shell sqlite/cp for app-private DBs. Some\n"
            "# generated tools pass: adb shell su 0 'cat /tmp/db > /data/data/...'.\n"
            "# Android's shell can apply the redirection before su, so rewrite this\n"
            "# narrow pattern to root cp without changing the generated tool file.\n"
            "if [[ ${1:-} == shell && ${2:-} == su && ${3:-} == 0 && $# -eq 4 ]]; then\n"
            "  cmd=${4}\n"
            "  if [[ ${cmd} =~ ^cat[[:space:]]+([^[:space:]]+)[[:space:]]+\\>[[:space:]]+(/data/data/[^[:space:]]+)$ ]]; then\n"
            "    exec \"$ADB_BIN\" -s \"$ADB_SERIAL\" shell su 0 cp \"${BASH_REMATCH[1]}\" \"${BASH_REMATCH[2]}\"\n"
            "  fi\n"
            "fi\n"
            "\n"
            "# Some generated tools pass a quoted multiline script as the sh -c\n"
            "# argument: adb shell su 0 sh -c '<script>'. With subprocess args,\n"
            "# those quotes become literal data and can break shell parsing around\n"
            "# constructs like OWNER=$(...). Strip only that outer generated quote.\n"
            "if [[ ${1:-} == shell && ${2:-} == su && ${3:-} == 0 && ${4:-} == sh && ${5:-} == -c && $# -eq 6 ]]; then\n"
            "  cmd=${6}\n"
            "  if [[ ${cmd:0:1} == \"'\" && ${cmd: -1} == \"'\" ]]; then\n"
            "    cmd=${cmd:1:${#cmd}-2}\n"
            "  fi\n"
            "  exec \"$ADB_BIN\" -s \"$ADB_SERIAL\" shell su 0 sh -c \"$cmd\"\n"
            "fi\n"
            "exec \"$ADB_BIN\" -s \"$ADB_SERIAL\" \"$@\"\n"
        )
        wrapper.write_text(script, encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        return str(wrapper)

    def _write_prepare_log(self, result: dict[str, Any]) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        path = self.work_dir / "prepare_device_for_app_state_tools.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")


def _is_external_directory_surface(surface: str) -> bool:
    normalized = surface.rstrip("/") + "/"
    return normalized.startswith(("/storage/emulated/0/", "/sdcard/"))


_PROMPT_NOISY_KEYS = {
    "cp_returncode",
    "cp_stderr",
    "cp_stdout",
    "expected_sha256",
    "ls_stderr",
    "ls_stdout",
    "push_stderr",
    "push_stdout",
    "remote_matches_expected",
    "remote_sha256",
    "remote_tail",
    "reset_write",
    "restorecon_stderr",
    "restorecon_stdout",
    "root_write",
    "stat_stderr",
    "stat_stdout",
    "tail",
    "write_attempts",
    "write_stderr",
    "write_stdout",
}


def _compact_for_prompt(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _compact_for_prompt(item)
            for key, item in value.items()
            if key not in _PROMPT_NOISY_KEYS
        }
    if isinstance(value, list):
        return [_compact_for_prompt(item) for item in value]
    return value


def compact_tool_result(value: Any, *, max_chars: int = 12000) -> str:
    text = json.dumps(_compact_for_prompt(value), ensure_ascii=False, indent=2, default=str)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...<truncated>"


def compact_tool_result_payload(value: Any) -> Any:
    """Return the structured app-state result payload without noisy backend details."""
    return _compact_for_prompt(value)

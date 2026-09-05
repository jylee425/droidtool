import os
import subprocess
from pathlib import Path


_WORK_PATH = Path(os.environ["BMOCA_HOME"])
_ADB = Path(os.environ.get("ANDROID_HOME", "~/.local/share/android/sdk")).expanduser()
_ADB = _ADB / "platform-tools" / "adb"
_PREF_REMOTE = "/data/data/com.niksoftware.snapseed/shared_prefs/Preferences.xml"
_PREF_SDCARD = "/sdcard/Preferences.xml"
_PREF_LOCAL_DIR = _WORK_PATH / "bmoca" / "environment" / "evaluator_script" / "snapseed"
_PREF_LOCAL = _PREF_LOCAL_DIR / "Preferences.xml"


def _adb_prefix() -> list[str]:
    serial = os.environ.get("BMOCA_ADB_SERIAL")
    if serial:
        return [str(_ADB), "-s", serial]
    return [str(_ADB)]


def pull_preferences() -> Path:
    cp_result = subprocess.run(
        _adb_prefix() + ["shell", "su", "0", "cp", _PREF_REMOTE, _PREF_SDCARD],
        check=False,
        capture_output=True,
        text=True,
    )
    _PREF_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    pull_result = subprocess.run(
        _adb_prefix() + ["pull", _PREF_SDCARD, str(_PREF_LOCAL_DIR)],
        check=False,
        capture_output=True,
        text=True,
    )
    if not _PREF_LOCAL.exists():
        serial = os.environ.get("BMOCA_ADB_SERIAL", "<default>")
        print(
            "[snapseed adb] failed to pull Preferences.xml "
            f"serial={serial} cp_rc={cp_result.returncode} "
            f"pull_rc={pull_result.returncode} "
            f"cp_err={cp_result.stderr.strip()[:200]!r} "
            f"pull_err={pull_result.stderr.strip()[:200]!r}",
            flush=True,
        )
    return _PREF_LOCAL

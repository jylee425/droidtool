"""Prepare emulators, snapshots, and apps for instrumentation tests."""
from __future__ import annotations

import subprocess
import time
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


PROJECT_PATH = Path(__file__).resolve().parents[3]
SHARED_PHOTO_FIXTURE = (
    PROJECT_PATH
    / "vendor"
    / "b-moca"
    / "asset"
    / "environments"
    / "resource"
    / "wallpapers_jpg"
    / "04_sky.jpg"
)
SHARED_PHOTO_DEVICE_PATH = "/sdcard/Pictures/toolgen_benchmark_photo.jpg"

PACKAGE_CANDIDATES: dict[str, list[str]] = {
    "broccoli_app": ["com.flauschcode.broccoli"],
    "clock": ["com.google.android.deskclock", "com.android.deskclock"],
    "contacts": ["com.google.android.contacts", "com.android.contacts"],
    "files": ["com.google.android.documentsui", "com.android.documentsui"],
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


# ADB command primitives


def _adb(
    adb_path: Path,
    serial: str,
    args: list[str],
    *,
    timeout_s: int = 20,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(adb_path), "-s", serial, *args],
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )


def _adb_command(
    args: Any,
    adb_args: list[str],
    *,
    timeout_s: int = 30,
) -> subprocess.CompletedProcess[str]:
    return _adb(
        args.adb_path,
        args.emulator_serial,
        adb_args,
        timeout_s=timeout_s,
    )


# Emulator lifecycle


def _device_state(args: Any) -> str | None:
    result = subprocess.run(
        [str(args.adb_path), "devices"],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == args.emulator_serial:
            return parts[1]
    return None


def _wait_for_device(args: Any, *, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _device_state(args) == "device":
            return
        time.sleep(1)
    raise TimeoutError(
        f"{args.emulator_serial} did not become online within {timeout_s}s"
    )


def _wait_for_boot(args: Any, *, timeout_s: int) -> None:
    _wait_for_device(args, timeout_s=timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = _adb_command(
            args,
            ["shell", "getprop", "sys.boot_completed"],
            timeout_s=10,
        )
        if result.stdout.strip().replace("\r", "") == "1":
            return
        time.sleep(2)
    raise TimeoutError(
        f"{args.emulator_serial} did not finish boot within {timeout_s}s"
    )


def _emulator_port(serial: str) -> str:
    prefix = "emulator-"
    if not serial.startswith(prefix):
        raise ValueError(f"cannot infer emulator port from serial: {serial}")
    return serial[len(prefix):]


def start_emulator(args: Any) -> subprocess.Popen[str]:
    """Start a fresh emulator from the configured base snapshot."""
    if _device_state(args) == "device":
        boot = _adb_command(
            args,
            ["shell", "getprop", "sys.boot_completed"],
            timeout_s=10,
        )
        if boot.stdout.strip().replace("\r", "") == "1":
            print(
                f"[test] stopping existing device {args.emulator_serial}",
                flush=True,
            )
            _adb_command(args, ["emu", "kill"], timeout_s=10)
            deadline = time.time() + 60
            while time.time() < deadline and _device_state(args) == "device":
                time.sleep(1)

    log_root = getattr(args, "log_root", None) or args.out_root
    log_root.mkdir(parents=True, exist_ok=True)
    log_path = log_root / f"emulator_{args.emulator_serial}.log"
    log_fh = log_path.open("w", encoding="utf-8")
    command = [
        str(args.emulator_path),
        "-avd",
        args.avd_name,
        "-no-window",
        "-no-audio",
        "-gpu",
        "swiftshader_indirect",
        "-port",
        _emulator_port(args.emulator_serial),
        "-no-snapshot-save",
        "-snapshot",
        args.base_snapshot,
    ]
    if args.emulator_read_only:
        command.append("-read-only")
    print(
        f"[test] starting AVD {args.avd_name} as {args.emulator_serial}",
        flush=True,
    )
    process: subprocess.Popen[str] = subprocess.Popen(
        command,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    process._test_log_fh = log_fh  # type: ignore[attr-defined]
    _wait_for_boot(args, timeout_s=args.boot_timeout_s)
    return process


def prepare_adb_root(args: Any) -> bool:
    """Restart ADB as root and verify that the shell has uid 0."""
    result = _adb_command(args, ["root"], timeout_s=20)
    if result.returncode == 0:
        time.sleep(2)
        _wait_for_device(args, timeout_s=60)
    identity = _adb_command(args, ["shell", "id"], timeout_s=15)
    print(
        f"[test] adb identity: {identity.stdout.strip() or identity.stderr.strip()}",
        flush=True,
    )
    return identity.returncode == 0 and "uid=0" in identity.stdout


def stop_emulator(args: Any, process: subprocess.Popen[str] | None) -> None:
    """Stop an emulator process started by :func:`start_emulator`."""
    if process is None:
        return
    print(f"[test] stopping {args.emulator_serial}", flush=True)
    try:
        _adb_command(args, ["emu", "kill"], timeout_s=10)
    except Exception:
        pass
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    log_fh = getattr(process, "_test_log_fh", None)
    if log_fh is not None:
        log_fh.close()


# App initialization primitives


def resolve_installed_package(adb_path: Path, serial: str, app_slug: str) -> str | None:
    for package in PACKAGE_CANDIDATES.get(app_slug, []):
        result = _adb(adb_path, serial, ["shell", "pm", "path", package])
        if result.returncode == 0 and result.stdout.strip().startswith("package:"):
            return package
    return None


def _grant(adb_path: Path, serial: str, package: str, permission: str) -> None:
    _adb(adb_path, serial, ["shell", "pm", "grant", package, permission])


def _su_sqlite(
    adb_path: Path, serial: str, db_path: str, sql: str
) -> subprocess.CompletedProcess[str]:
    return _adb(
        adb_path,
        serial,
        ["shell", "su", "0", "sqlite3", db_path, sql],
        timeout_s=30,
    )


def _wait_for_file(
    adb_path: Path,
    serial: str,
    path: str,
    *,
    timeout_s: float = 20.0,
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = _adb(
            adb_path,
            serial,
            ["shell", "su", "0", "test", "-f", path],
        )
        if result.returncode == 0:
            return True
        time.sleep(1.0)
    return False


def _ui_nodes(adb_path: Path, serial: str) -> list[ET.Element]:
    remote = "/data/local/tmp/toolgen_window.xml"
    _adb(adb_path, serial, ["shell", "uiautomator", "dump", remote])
    result = _adb(adb_path, serial, ["shell", "cat", remote])
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        return list(ET.fromstring(result.stdout).iter("node"))
    except ET.ParseError:
        return []


def _click_ui(
    adb_path: Path,
    serial: str,
    *,
    texts: tuple[str, ...] = (),
    resource_ids: tuple[str, ...] = (),
    attempts: int = 5,
) -> bool:
    wanted_text = {value.casefold() for value in texts}
    wanted_ids = set(resource_ids)
    for _ in range(attempts):
        for node in _ui_nodes(adb_path, serial):
            text = str(node.attrib.get("text") or "").casefold()
            resource_id = str(node.attrib.get("resource-id") or "")
            if text not in wanted_text and resource_id not in wanted_ids:
                continue
            match = re.fullmatch(
                r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                str(node.attrib.get("bounds") or ""),
            )
            if not match:
                continue
            left, top, right, bottom = map(int, match.groups())
            _adb(
                adb_path,
                serial,
                ["shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2)],
            )
            time.sleep(0.8)
            return True
        time.sleep(0.5)
    return False


def _launch(adb_path: Path, serial: str, package: str) -> subprocess.CompletedProcess[str]:
    return _adb(
        adb_path,
        serial,
        [
            "shell",
            "monkey",
            "-p",
            package,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ],
        timeout_s=30,
    )


def _initialize_shared_photo_fixture(adb_path: Path, serial: str) -> str:
    """Install and index the benchmark image shared by Photos and Snapseed."""
    if not SHARED_PHOTO_FIXTURE.is_file():
        return "shared_photo_fixture_missing"
    mkdir = _adb(adb_path, serial, ["shell", "mkdir", "-p", "/sdcard/Pictures"])
    if mkdir.returncode != 0:
        return "shared_photo_directory_failed"
    push = _adb(
        adb_path,
        serial,
        ["push", str(SHARED_PHOTO_FIXTURE), SHARED_PHOTO_DEVICE_PATH],
        timeout_s=30,
    )
    if push.returncode != 0:
        return "shared_photo_push_failed"
    scan = _adb(
        adb_path,
        serial,
        [
            "shell",
            "am",
            "broadcast",
            "-a",
            "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d",
            f"file://{SHARED_PHOTO_DEVICE_PATH}",
        ],
    )
    return (
        "initialize_shared_photo_fixture"
        if scan.returncode == 0
        else "shared_photo_scan_failed"
    )


# App-specific initialization


def _benchmark_initialize(
    *, adb_path: Path, serial: str, app_slug: str, package: str
) -> list[str]:
    """Mirror relevant AndroidWorld, B-MoCA, and MSB app preconditions."""
    actions: list[str] = []

    if app_slug in {"photos", "snapseed"}:
        actions.append(_initialize_shared_photo_fixture(adb_path, serial))

    if app_slug == "contacts":
        _adb(
            adb_path,
            serial,
            ["shell", "content", "delete", "--uri", "content://com.android.contacts/raw_contacts"],
        )
        actions.append("clear_contacts")
        for text in ("Skip", "Don't allow"):
            if _click_ui(adb_path, serial, texts=(text,)):
                actions.append(f"dismiss:{text}")

    elif app_slug == "messages":
        _adb(adb_path, serial, ["shell", "content", "delete", "--uri", "content://sms"])
        _adb(adb_path, serial, ["shell", "pm", "clear", package])
        _grant(adb_path, serial, package, "android.permission.READ_CONTACTS")
        _launch(adb_path, serial, package)
        time.sleep(1.5)
        if _click_ui(
            adb_path,
            serial,
            texts=("OK", "Ok"),
            resource_ids=("android:id/button1",),
        ):
            actions.append("dismiss_messages_popup")
        actions.append("clear_sms_and_messaging_state")

    elif app_slug == "joplin":
        for permission in (
            "android.permission.ACCESS_FINE_LOCATION",
            "android.permission.ACCESS_COARSE_LOCATION",
        ):
            _grant(adb_path, serial, package, permission)
        db = "/data/data/net.cozic.joplin/databases/joplin.sqlite"
        if _wait_for_file(adb_path, serial, db, timeout_s=25):
            _adb(adb_path, serial, ["shell", "am", "force-stop", package])
            result = _su_sqlite(
                adb_path,
                serial,
                db,
                "PRAGMA foreign_keys=OFF; DELETE FROM notes; DELETE FROM notes_normalized;",
            )
            actions.append("clear_joplin_notes" if result.returncode == 0 else "joplin_clear_failed")
        else:
            actions.append("joplin_db_missing")

    elif app_slug == "photonote":
        db = "/data/data/com.chartreux.photo_note/databases/PhotoNote.db"
        if _wait_for_file(adb_path, serial, db, timeout_s=25):
            result = _su_sqlite(
                adb_path,
                serial,
                db,
                "PRAGMA foreign_keys=OFF; DELETE FROM comments; DELETE FROM media_posts; DELETE FROM posts; DELETE FROM media; DELETE FROM users WHERE id NOT IN (SELECT id FROM users ORDER BY id LIMIT 1); DELETE FROM sqlite_sequence WHERE name IN ('comments','media_posts','posts','media');",
            )
            actions.append("clear_photonote_task_rows" if result.returncode == 0 else "photonote_clear_failed")
        else:
            actions.append("photonote_db_missing")

    elif app_slug == "markor":
        for text in ("NEXT", "NEXT", "NEXT", "NEXT", "DONE", "OK", "Allow access to manage all files"):
            if _click_ui(adb_path, serial, texts=(text,), attempts=2):
                actions.append(f"onboarding:{text}")
        _adb(adb_path, serial, ["shell", "appops", "set", package, "MANAGE_EXTERNAL_STORAGE", "allow"])
        _adb(adb_path, serial, ["shell", "mkdir", "-p", "/storage/emulated/0/Documents/markor"])
        actions.append("initialize_markor_notebook")

    elif app_slug == "pro_expense":
        for text in ("NEXT", "CONTINUE"):
            if _click_ui(adb_path, serial, texts=(text,)):
                actions.append(f"onboarding:{text}")

    elif app_slug == "simple_calendar_pro":
        for permission in (
            "android.permission.READ_CALENDAR",
            "android.permission.WRITE_CALENDAR",
            "android.permission.POST_NOTIFICATIONS",
        ):
            _grant(adb_path, serial, package, permission)
        db = "/data/data/com.simplemobiletools.calendar.pro/databases/events.db"
        if _wait_for_file(adb_path, serial, db, timeout_s=10):
            _adb(adb_path, serial, ["shell", "am", "force-stop", package])
            _su_sqlite(adb_path, serial, db, "DELETE FROM events;")
            actions.append("clear_calendar_events")

    elif app_slug == "tasks":
        db = "/data/data/org.tasks/databases/tasks.db"
        if _wait_for_file(adb_path, serial, db, timeout_s=10):
            _adb(adb_path, serial, ["shell", "am", "force-stop", package])
            _su_sqlite(adb_path, serial, db, "DELETE FROM tasks;")
            actions.append("clear_tasks")

    elif app_slug == "osmand":
        if _click_ui(adb_path, serial, texts=("SKIP DOWNLOAD",)):
            actions.append("dismiss_osmand_download")
        _grant(adb_path, serial, package, "android.permission.POST_NOTIFICATIONS")
        _adb(
            adb_path,
            serial,
            ["shell", "mkdir", "-p", "/storage/emulated/0/Android/data/net.osmand/files/tracks"],
        )
        actions.append("initialize_osmand_tracks")

    elif app_slug == "open_tracks_sports_tracker":
        for permission in (
            "android.permission.ACCESS_COARSE_LOCATION",
            "android.permission.ACCESS_FINE_LOCATION",
            "android.permission.POST_NOTIFICATIONS",
        ):
            _grant(adb_path, serial, package, permission)
        if _click_ui(adb_path, serial, texts=("Allow",), attempts=2):
            actions.append("dismiss_opentracks_permission")

    elif app_slug == "vlc":
        _grant(adb_path, serial, package, "android.permission.POST_NOTIFICATIONS")
        _adb(adb_path, serial, ["shell", "mkdir", "-p", "/storage/emulated/0/VLCVideos"])
        for text in ("Skip", "GRANT PERMISSION", "OK", "Allow access to manage all files"):
            if _click_ui(adb_path, serial, texts=(text,), attempts=2):
                actions.append(f"onboarding:{text}")
        _adb(adb_path, serial, ["shell", "appops", "set", package, "MANAGE_EXTERNAL_STORAGE", "allow"])
        actions.append("initialize_vlc_storage")

    elif app_slug == "retro_music":
        for permission in (
            "android.permission.READ_MEDIA_AUDIO",
            "android.permission.POST_NOTIFICATIONS",
        ):
            _grant(adb_path, serial, package, permission)
        actions.append("grant_retro_music_permissions")

    elif app_slug == "wikipedia":
        if _click_ui(
            adb_path,
            serial,
            texts=("SKIP", "Skip"),
            resource_ids=("org.wikipedia:id/fragment_onboarding_skip_button",),
        ):
            actions.append("dismiss_wikipedia_onboarding")
        if _click_ui(
            adb_path,
            serial,
            texts=("GOT IT", "Got it", "NO THANKS"),
            resource_ids=("org.wikipedia:id/view_announcement_action_negative",),
            attempts=2,
        ):
            actions.append("dismiss_wikipedia_announcement")

    elif app_slug == "snapseed":
        _adb(adb_path, serial, ["shell", "mkdir", "-p", "/sdcard/Pictures/Snapseed"])
        if _click_ui(
            adb_path,
            serial,
            resource_ids=("com.niksoftware.snapseed:id/logo_view",),
            attempts=2,
        ):
            actions.append("open_snapseed_picker")
        if _click_ui(
            adb_path,
            serial,
            texts=("Allow",),
            resource_ids=(
                "com.android.permissioncontroller:id/permission_allow_button",
                "com.android.permissioncontroller:id/permission_allow_foreground_only_button",
            ),
            attempts=2,
        ):
            actions.append("grant_snapseed_permission")
        _adb(adb_path, serial, ["shell", "input", "keyevent", "KEYCODE_BACK"])

    return actions


def initialize_apps(
    *,
    adb_path: Path,
    serial: str,
    app_slugs: list[str],
) -> list[dict[str, Any]]:
    """Launch each installed app once so first-run storage surfaces can be created."""
    records: list[dict[str, Any]] = []
    for app_slug in sorted(set(app_slugs)):
        package = resolve_installed_package(adb_path, serial, app_slug)
        if package is None:
            records.append(
                {"app": app_slug, "status": "package_missing", "package": None}
            )
            continue
        launch = _launch(adb_path, serial, package)
        if launch.returncode == 0:
            time.sleep(1.0)
        benchmark_actions = _benchmark_initialize(
            adb_path=adb_path,
            serial=serial,
            app_slug=app_slug,
            package=package,
        )
        stop = _adb(adb_path, serial, ["shell", "am", "force-stop", package])
        records.append(
            {
                "app": app_slug,
                "package": package,
                "status": "initialized" if launch.returncode == 0 else "launch_failed",
                "benchmark_actions": benchmark_actions,
                "launch_error": launch.stderr.strip(),
                "force_stop_error": stop.stderr.strip(),
            }
        )
    return records

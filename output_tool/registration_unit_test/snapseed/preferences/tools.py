import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import shlex

PACKAGE_NAME = "com.niksoftware.snapseed"

CANDIDATE_PATHS = [
    "/data/data/com.niksoftware.snapseed/shared_prefs/Preferences.xml",
    "/data/data/com.niksoftware.snapseed/shared_prefs/com.niksoftware.snapseed_preferences.xml"
]

DOCUMENTED_KEYS = {
    "pref_appearance_use_dark_theme",
    "pref_export_setting_long_edge",
    "pref_export_setting_compression"
}

def _run_adb(args, adb_path="adb"):
    cmd = [adb_path]
    serial = os.environ.get("ANDROID_SERIAL")
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res

def _file_exists_and_has_keys(remote_path, adb_path):
    # Check if file exists
    check_cmd = ["shell", "su", "0", f"test -f {shlex.quote(remote_path)} && echo 'exists'"]
    res = _run_adb(check_cmd, adb_path)
    if "exists" not in res.stdout:
        return False, None

    # Read file content
    read_cmd = ["shell", "su", "0", f"cat {shlex.quote(remote_path)}"]
    res = _run_adb(read_cmd, adb_path)
    if res.returncode != 0 or not res.stdout.strip():
        return False, None

    try:
        root = ET.fromstring(res.stdout.strip())
        found_keys = set()
        for child in root:
            name = child.attrib.get("name")
            if name in DOCUMENTED_KEYS:
                found_keys.add(name)
        return len(found_keys) > 0, res.stdout
    except Exception:
        return False, None

def _get_active_preferences_file(adb_path):
    # Inspect both candidate files. Select the one containing documented keys.
    for path in CANDIDATE_PATHS:
        has_keys, content = _file_exists_and_has_keys(path, adb_path)
        if has_keys:
            return path, content
    
    # Fallback: check if either exists at all
    for path in CANDIDATE_PATHS:
        check_cmd = ["shell", "su", "0", f"test -f {shlex.quote(path)} && echo 'exists'"]
        res = _run_adb(check_cmd, adb_path)
        if "exists" in res.stdout:
            read_cmd = ["shell", "su", "0", f"cat {shlex.quote(path)}"]
            res = _run_adb(read_cmd, adb_path)
            if res.returncode == 0:
                return path, res.stdout
                
    # Default to primary path if neither exists
    return CANDIDATE_PATHS[0], None

def get_snapseed_settings(adb_path: str = "adb") -> dict:
    """
    Inspects Snapseed's preference files and returns the current settings for dark theme,
    export long-edge limit, and export compression quality.
    """
    try:
        active_path, content = _get_active_preferences_file(adb_path)
        
        # Default-initialized settings structure
        settings = {
            "pref_appearance_use_dark_theme": False,
            "pref_export_setting_long_edge": "0",
            "pref_export_setting_compression": "95",
            "active_preferences_file": active_path
        }

        if not content:
            return {
                "success": True,
                "error": "",
                **settings
            }

        root = ET.fromstring(content.strip())
        for child in root:
            name = child.attrib.get("name")
            if name == "pref_appearance_use_dark_theme":
                settings["pref_appearance_use_dark_theme"] = child.attrib.get("value") == "true"
            elif name == "pref_export_setting_long_edge":
                settings["pref_export_setting_long_edge"] = child.text or ""
            elif name == "pref_export_setting_compression":
                settings["pref_export_setting_compression"] = child.text or ""

        return {
            "success": True,
            "error": "",
            **settings
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to read Snapseed settings: {str(e)}",
            "pref_appearance_use_dark_theme": False,
            "pref_export_setting_long_edge": "0",
            "pref_export_setting_compression": "95",
            "active_preferences_file": ""
        }

def set_snapseed_settings(
    pref_appearance_use_dark_theme: bool = None,
    pref_export_setting_long_edge: str = None,
    pref_export_setting_compression: str = None,
    adb_path: str = "adb"
) -> dict:
    """
    Updates Snapseed's settings for dark theme, export long-edge limit, and/or export compression quality,
    ensuring process quiescence and XML type preservation.
    """
    # Validation
    if pref_export_setting_long_edge is not None:
        if not pref_export_setting_long_edge.isdigit():
            return {
                "success": False,
                "error": "pref_export_setting_long_edge must be a numeric string representing '0' or positive integers.",
                "updated_keys": []
            }
    if pref_export_setting_compression is not None:
        if not pref_export_setting_compression.isdigit():
            return {
                "success": False,
                "error": "pref_export_setting_compression must be a numeric string representing compression quality.",
                "updated_keys": []
            }

    try:
        active_path, content = _get_active_preferences_file(adb_path)
        
        if content:
            root = ET.fromstring(content.strip())
        else:
            root = ET.Element("map")

        updated_keys = []

        # Helper to find or create elements
        def set_or_update_node(tag, name, value_attr=None, text_val=None):
            for child in list(root):
                if child.attrib.get("name") == name:
                    root.remove(child)
            elem = ET.SubElement(root, tag, {"name": name})
            if value_attr is not None:
                elem.set("value", value_attr)
            if text_val is not None:
                elem.text = text_val

        if pref_appearance_use_dark_theme is not None:
            val_str = "true" if pref_appearance_use_dark_theme else "false"
            set_or_update_node("boolean", "pref_appearance_use_dark_theme", value_attr=val_str)
            updated_keys.append("pref_appearance_use_dark_theme")

        if pref_export_setting_long_edge is not None:
            set_or_update_node("string", "pref_export_setting_long_edge", text_val=pref_export_setting_long_edge)
            updated_keys.append("pref_export_setting_long_edge")

        if pref_export_setting_compression is not None:
            set_or_update_node("string", "pref_export_setting_compression", text_val=pref_export_setting_compression)
            updated_keys.append("pref_export_setting_compression")

        if not updated_keys:
            return {
                "success": True,
                "error": "",
                "updated_keys": []
            }

        # Generate XML string
        xml_data = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")

        # Quiesce the app process before writing
        _run_adb(["shell", "am", "force-stop", PACKAGE_NAME], adb_path)

        # Discover existing file metadata (uid, gid, mode, selinux context)
        stat_cmd = ["shell", "su", "0", f"stat -c '%u:%g:%a' {shlex.quote(active_path)}"]
        stat_res = _run_adb(stat_cmd, adb_path)
        
        uid_gid = None
        mode = None
        if stat_res.returncode == 0 and stat_res.stdout.strip():
            parts = stat_res.stdout.strip().split(":")
            if len(parts) == 3:
                uid_gid = f"{parts[0]}:{parts[1]}"
                mode = parts[2]

        selinux_context = None
        selinux_cmd = ["shell", "su", "0", f"ls -Z {shlex.quote(active_path)}"]
        selinux_res = _run_adb(selinux_cmd, adb_path)
        if selinux_res.returncode == 0 and selinux_res.stdout.strip():
            selinux_context = selinux_res.stdout.strip().split()[0]

        # If metadata couldn't be discovered (e.g. file doesn't exist yet), discover from parent directory
        if not uid_gid:
            parent_dir = os.path.dirname(active_path)
            parent_stat = _run_adb(["shell", "su", "0", f"stat -c '%u:%g' {shlex.quote(parent_dir)}"], adb_path)
            if parent_stat.returncode == 0 and parent_stat.stdout.strip():
                uid_gid = parent_stat.stdout.strip()
            mode = "660"

        # Write to a temporary file on device, then move and restore metadata
        temp_device_path = f"/data/local/tmp/temp_prefs_{os.getpid()}.xml"
        
        # Write locally first
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(xml_data)
            local_temp_path = f.name

        try:
            # Push to temp device path
            push_res = _run_adb(["push", local_temp_path, temp_device_path], adb_path)
            if push_res.returncode != 0:
                raise Exception(f"Failed to push temp file: {push_res.stderr}")

            # Move to final destination using su
            mv_cmd = ["shell", "su", "0", f"mv {shlex.quote(temp_device_path)} {shlex.quote(active_path)}"]
            mv_res = _run_adb(mv_cmd, adb_path)
            if mv_res.returncode != 0:
                raise Exception(f"Failed to replace preferences file: {mv_res.stderr}")

            # Restore metadata
            if uid_gid:
                _run_adb(["shell", "su", "0", f"chown {uid_gid} {shlex.quote(active_path)}"], adb_path)
            if mode:
                _run_adb(["shell", "su", "0", f"chmod {mode} {shlex.quote(active_path)}"], adb_path)
            if selinux_context and selinux_context != "?" and ":" in selinux_context:
                _run_adb(["shell", "su", "0", f"chcon {selinux_context} {shlex.quote(active_path)}"], adb_path)

        finally:
            if os.path.exists(local_temp_path):
                os.remove(local_temp_path)
            _run_adb(["shell", "su", "0", f"rm -f {shlex.quote(temp_device_path)}"], adb_path)

        return {
            "success": True,
            "error": "",
            "updated_keys": updated_keys
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to write Snapseed settings: {str(e)}",
            "updated_keys": []
        }

import os
import subprocess
import shlex
import tempfile
import xml.etree.ElementTree as ET

# Package candidates in order of preference
PACKAGE_CANDIDATES = [
    "com.google.android.deskclock",
    "com.android.deskclock"
]

def _run_cmd(cmd, adb_path="adb"):
    """Helper to run an ADB shell command and return stdout, stderr, and return code."""
    serial = os.environ.get("ANDROID_SERIAL")
    base_cmd = [adb_path]
    if serial:
        base_cmd.extend(["-s", serial])
    base_cmd.extend(["shell", cmd])
    
    proc = subprocess.run(base_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.stdout, proc.stderr, proc.returncode

def _resolve_package_and_path(adb_path="adb"):
    """
    Discovers the installed package and the active SharedPreferences XML path.
    Checks device-protected first, then credential-protected.
    Returns (package_name, remote_path) or raises an Exception.
    """
    for pkg in PACKAGE_CANDIDATES:
        # Check if package is installed
        stdout, _, rc = _run_cmd(f"pm path {pkg}", adb_path)
        if rc != 0 or not stdout.strip():
            continue
        
        # Candidate paths
        paths = [
            f"/data/user_de/0/{pkg}/shared_prefs/{pkg}_preferences.xml",
            f"/data/data/{pkg}/shared_prefs/{pkg}_preferences.xml"
        ]
        for path in paths:
            # Check if file exists using su
            check_cmd = f"su 0 test -f {shlex.quote(path)}"
            _, _, check_rc = _run_cmd(check_cmd, adb_path)
            if check_rc == 0:
                return pkg, path
                
    raise Exception("Could not resolve an installed Clock package with a valid preferences XML file.")

def _pull_xml(remote_path, adb_path="adb"):
    """
    Pulls the remote XML file securely via a temporary readable path.
    Returns the local file path and the original file metadata (uid, gid, mode, selinux).
    """
    # Get metadata
    stat_cmd = f"su 0 stat -c '%u %g %a %C' {shlex.quote(remote_path)}"
    stdout, _, rc = _run_cmd(stat_cmd, adb_path)
    if rc != 0 or not stdout.strip():
        raise Exception(f"Failed to stat remote file: {remote_path}")
    
    parts = stdout.strip().split()
    if len(parts) < 4:
        raise Exception(f"Unexpected stat output: {stdout}")
    uid, gid, mode, selinux = parts[0], parts[1], parts[2], parts[3]
    
    # Copy to a temporary readable location on device
    temp_device_path = f"/data/local/tmp/temp_pref_{os.getpid()}.xml"
    copy_cmd = f"su 0 cp {shlex.quote(remote_path)} {temp_device_path} && su 0 chmod 666 {temp_device_path}"
    _, stderr, rc = _run_cmd(copy_cmd, adb_path)
    if rc != 0:
        raise Exception(f"Failed to stage remote file: {stderr}")
        
    # Pull to local machine
    local_fd, local_path = tempfile.mkstemp(suffix=".xml")
    os.close(local_fd)
    
    serial = os.environ.get("ANDROID_SERIAL")
    pull_cmd = [adb_path]
    if serial:
        pull_cmd.extend(["-s", serial])
    pull_cmd.extend(["pull", temp_device_path, local_path])
    
    proc = subprocess.run(pull_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # Clean up temp device file
    _run_cmd(f"su 0 rm {temp_device_path}", adb_path)
    
    if proc.returncode != 0:
        if os.path.exists(local_path):
            os.remove(local_path)
        raise Exception(f"Failed to pull file via ADB: {proc.stderr}")
        
    return local_path, (uid, gid, mode, selinux)

def _push_xml(local_path, remote_path, metadata, adb_path="adb"):
    """
    Pushes the local XML file to the remote path, restoring original metadata.
    """
    uid, gid, mode, selinux = metadata
    temp_device_path = f"/data/local/tmp/temp_pref_push_{os.getpid()}.xml"
    
    # Push to temp device path
    serial = os.environ.get("ANDROID_SERIAL")
    push_cmd = [adb_path]
    if serial:
        push_cmd.extend(["-s", serial])
    push_cmd.extend(["push", local_path, temp_device_path])
    
    proc = subprocess.run(push_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise Exception(f"Failed to push file via ADB: {proc.stderr}")
        
    # Copy back to private path and restore metadata
    restore_cmd = (
        f"su 0 cp {temp_device_path} {shlex.quote(remote_path)} && "
        f"su 0 chown {uid}:{gid} {shlex.quote(remote_path)} && "
        f"su 0 chmod {mode} {shlex.quote(remote_path)} && "
        f"su 0 chcon {shlex.quote(selinux)} {shlex.quote(remote_path)} && "
        f"su 0 rm {temp_device_path}"
    )
    _, stderr, rc = _run_cmd(restore_cmd, adb_path)
    if rc != 0:
        raise Exception(f"Failed to restore file to private path: {stderr}")

def _parse_preferences(xml_path):
    """
    Parses SharedPreferences XML into a Python dictionary.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    prefs = {}
    for child in root:
        name = child.attrib.get("name")
        if not name:
            continue
        if child.tag == "boolean":
            prefs[name] = child.attrib.get("value") == "true"
        elif child.tag == "string":
            prefs[name] = child.text if child.text is not None else ""
        elif child.tag == "int":
            prefs[name] = int(child.attrib.get("value", 0))
        elif child.tag == "long":
            prefs[name] = int(child.attrib.get("value", 0))
        elif child.tag == "float":
            prefs[name] = float(child.attrib.get("value", 0.0))
        elif child.tag == "set":
            prefs[name] = [item.text for item in child if item.tag == "string"]
    return prefs

def _write_preferences(xml_path, key, value, value_type):
    """
    Modifies or inserts a preference key structurally in the XML file.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    # Find existing element
    found = False
    for child in root:
        if child.attrib.get("name") == key:
            found = True
            if value_type == "boolean":
                child.tag = "boolean"
                child.attrib["value"] = "true" if str(value).lower() == "true" else "false"
                if child.text:
                    child.text = None
            elif value_type == "string":
                child.tag = "string"
                child.text = str(value)
                if "value" in child.attrib:
                    del child.attrib["value"]
            break
            
    if not found:
        if value_type == "boolean":
            elem = ET.SubElement(root, "boolean", {
                "name": key,
                "value": "true" if str(value).lower() == "true" else "false"
            })
        elif value_type == "string":
            elem = ET.SubElement(root, "string", {"name": key})
            elem.text = str(value)
            
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)

def get_clock_preferences(adb_path: str = "adb") -> dict:
    """
    Reads all keys from the resolved SharedPreferences XML file.
    """
    try:
        pkg, remote_path = _resolve_package_and_path(adb_path)
        local_path, _ = _pull_xml(remote_path, adb_path)
        
        try:
            prefs = _parse_preferences(local_path)
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)
                
        display_clock_seconds = prefs.get("display_clock_seconds")
        home_time_zone = prefs.get("home_time_zone")
        
        # Filter out primary keys to populate other_preferences
        other_prefs = {k: v for k, v in prefs.items() if k not in ["display_clock_seconds", "home_time_zone"]}
        
        result = {
            "success": True,
            "error": ""
        }
        if display_clock_seconds is not None:
            result["display_clock_seconds"] = display_clock_seconds
        if home_time_zone is not None:
            result["home_time_zone"] = home_time_zone
        result["other_preferences"] = other_prefs
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def set_clock_preference(key: str, value: str, adb_path: str = "adb") -> dict:
    """
    Writes the specified key-value pair into the SharedPreferences XML.
    """
    if key not in ["display_clock_seconds", "home_time_zone"]:
        return {
            "success": False,
            "error": f"Unsupported preference key: {key}"
        }
        
    value_type = "boolean" if key == "display_clock_seconds" else "string"
    
    try:
        pkg, remote_path = _resolve_package_and_path(adb_path)
        
        # Quiesce the app before replacement
        _run_cmd(f"am force-stop {pkg}", adb_path)
        
        local_path, metadata = _pull_xml(remote_path, adb_path)
        
        try:
            _write_preferences(local_path, key, value, value_type)
            _push_xml(local_path, remote_path, metadata, adb_path)
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)
                
        return {
            "success": True,
            "error": ""
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

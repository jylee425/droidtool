import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import shlex

PACKAGE_NAME = "com.simplemobiletools.calendar.pro"
PREFS_PATH = f"/data/data/{PACKAGE_NAME}/shared_prefs/Prefs.xml"

def _run_adb_cmd(cmd, adb_path="adb"):
    serial = os.environ.get("ANDROID_SERIAL")
    base_cmd = [adb_path]
    if serial:
        base_cmd.extend(["-s", serial])
    return subprocess.run(base_cmd + cmd, capture_output=True, text=True)

def _get_file_metadata(remote_path, adb_path="adb"):
    """Discovers the uid, gid, mode, and SELinux context of a remote file."""
    # Get uid, gid, mode
    stat_cmd = ["shell", "su", "0", f"stat -c '%u %g %a' {shlex.quote(remote_path)}"]
    res = _run_adb_cmd(stat_cmd, adb_path)
    uid, gid, mode = None, None, None
    if res.returncode == 0 and res.stdout.strip():
        parts = res.stdout.strip().split()
        if len(parts) == 3:
            uid, gid, mode = parts[0], parts[1], parts[2]
    
    # Get SELinux context
    selinux = None
    ls_cmd = ["shell", "su", "0", f"ls -Z {shlex.quote(remote_path)}"]
    res_ls = _run_adb_cmd(ls_cmd, adb_path)
    if res_ls.returncode == 0 and res_ls.stdout.strip():
        parts = res_ls.stdout.strip().split()
        if len(parts) >= 1:
            selinux = parts[0]
            
    return uid, gid, mode, selinux

def _pull_prefs(adb_path="adb"):
    """Pulls the Prefs.xml file to a local temporary file, returning its path and metadata."""
    # Check if file exists
    check_cmd = ["shell", "su", "0", f"test -f {shlex.quote(PREFS_PATH)} && echo 'exists'"]
    res = _run_adb_cmd(check_cmd, adb_path)
    if "exists" not in res.stdout:
        return None, (None, None, None, None)

    metadata = _get_file_metadata(PREFS_PATH, adb_path)

    # Stage to a readable temporary device path
    temp_device_path = f"/data/local/tmp/Prefs_temp_{os.getpid()}.xml"
    cp_cmd = ["shell", "su", "0", f"cp {shlex.quote(PREFS_PATH)} {temp_device_path} && chmod 666 {temp_device_path}"]
    res = _run_adb_cmd(cp_cmd, adb_path)
    if res.returncode != 0:
        return None, (None, None, None, None)

    # Pull to local temp file
    local_fd, local_path = tempfile.mkstemp(suffix=".xml")
    os.close(local_fd)
    
    pull_cmd = ["pull", temp_device_path, local_path]
    res = _run_adb_cmd(pull_cmd, adb_path)
    
    # Clean up temp device file
    _run_adb_cmd(["shell", "rm", "-f", temp_device_path], adb_path)
    
    if res.returncode != 0:
        if os.path.exists(local_path):
            os.remove(local_path)
        return None, (None, None, None, None)
        
    return local_path, metadata

def _push_prefs(local_path, metadata, adb_path="adb"):
    """Pushes a local XML file back to the device, restoring metadata."""
    uid, gid, mode, selinux = metadata
    temp_device_path = f"/data/local/tmp/Prefs_temp_{os.getpid()}.xml"
    
    # Push to temp device path
    push_cmd = ["push", local_path, temp_device_path]
    res = _run_adb_cmd(push_cmd, adb_path)
    if res.returncode != 0:
        return False
        
    # Copy to final destination with su
    cp_cmd = ["shell", "su", "0", f"cp {temp_device_path} {shlex.quote(PREFS_PATH)}"]
    res = _run_adb_cmd(cp_cmd, adb_path)
    _run_adb_cmd(["shell", "rm", "-f", temp_device_path], adb_path)
    if res.returncode != 0:
        return False
        
    # Restore metadata
    if uid and gid:
        _run_adb_cmd(["shell", "su", "0", f"chown {uid}:{gid} {shlex.quote(PREFS_PATH)}"], adb_path)
    if mode:
        _run_adb_cmd(["shell", "su", "0", f"chmod {mode} {shlex.quote(PREFS_PATH)}"], adb_path)
    if selinux and selinux != "?" and ":" in selinux:
        _run_adb_cmd(["shell", "su", "0", f"chcon {shlex.quote(selinux)} {shlex.quote(PREFS_PATH)}"], adb_path)
        
    return True

def _parse_xml_to_dict(xml_path):
    """Parses SharedPreferences XML into a flat dictionary of key-value pairs."""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception:
        return {}
        
    settings = {}
    for child in root:
        key = child.attrib.get("name")
        if not key:
            continue
        
        tag = child.tag
        if tag == "string":
            settings[key] = child.text if child.text is not None else ""
        elif tag == "int" or tag == "long":
            try:
                settings[key] = int(child.attrib.get("value", 0))
            except ValueError:
                settings[key] = 0
        elif tag == "boolean":
            val = child.attrib.get("value", "false").lower()
            settings[key] = (val == "true")
        elif tag == "float":
            try:
                settings[key] = float(child.attrib.get("value", 0.0))
            except ValueError:
                settings[key] = 0.0
    return settings

def get_calendar_settings(adb_path: str = "adb") -> dict:
    """Reads and parses the XML file structurally. 
    Returns modeled settings along with all raw key-value pairs in all_settings.
    """
    local_path, _ = _pull_prefs(adb_path)
    if not local_path:
        return {
            "success": True,
            "error": "",
            "all_settings": {}
        }
        
    try:
        all_settings = _parse_xml_to_dict(local_path)
        
        result = {
            "success": True,
            "error": "",
            "all_settings": all_settings
        }
        
        # Extract modeled settings if present
        if "view" in all_settings:
            result["view"] = int(all_settings["view"])
        if "display_event_types" in all_settings:
            result["display_event_types"] = str(all_settings["display_event_types"])
        if "internal_storage_path" in all_settings:
            result["internal_storage_path"] = str(all_settings["internal_storage_path"])
            
        return result
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to parse settings: {str(e)}",
            "all_settings": {}
        }
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)

def set_calendar_setting(key: str, value: str, value_type: str, adb_path: str = "adb") -> dict:
    """Modifies or inserts the specified key-value pair matching the designated value_type tag.
    Preserves all other unknown keys and maintains correct file permissions.
    """
    if value_type not in ["string", "int", "boolean"]:
        return {
            "success": False,
            "error": f"Unsupported value_type: {value_type}"
        }

    # Stop the app process before writing to avoid conflicts with cached values
    _run_adb_cmd(["shell", "am", "force-stop", PACKAGE_NAME], adb_path)

    local_path, metadata = _pull_prefs(adb_path)
    
    # If file doesn't exist, initialize a basic XML structure
    if not local_path:
        local_fd, local_path = tempfile.mkstemp(suffix=".xml")
        os.close(local_fd)
        with open(local_path, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<map>\n</map>')
        # Discover default metadata from the parent directory if possible
        parent_dir = f"/data/data/{PACKAGE_NAME}/shared_prefs"
        uid, gid, _, selinux = _get_file_metadata(parent_dir, adb_path)
        metadata = (uid, gid, "660", selinux)

    try:
        tree = ET.parse(local_path)
        root = tree.getroot()
        
        # Remove existing element with the same key
        for child in list(root):
            if child.attrib.get("name") == key:
                root.remove(child)
                
        # Create new element
        if value_type == "string":
            elem = ET.Element("string", {"name": key})
            elem.text = value
        elif value_type == "int":
            elem = ET.Element("int", {"name": key, "value": str(int(value))})
        elif value_type == "boolean":
            bool_val = "true" if value.lower() in ["true", "1", "yes"] else "false"
            elem = ET.Element("boolean", {"name": key, "value": bool_val})
            
        root.append(elem)
        tree.write(local_path, encoding="utf-8", xml_declaration=True)
        
        # Push back to device
        if _push_prefs(local_path, metadata, adb_path):
            return {
                "success": True,
                "error": ""
            }
        else:
            return {
                "success": False,
                "error": "Failed to write preferences back to device."
            }
    except Exception as e:
        return {
            "success": False,
            "error": f"Error updating preference: {str(e)}"
        }
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)

import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PACKAGE_NAME = "de.dennisguse.opentracks"
PREFS_FILE_PATH = "/data/data/de.dennisguse.opentracks/shared_prefs/de.dennisguse.opentracks_preferences.xml"

def _run_adb_cmd(cmd, adb_path="adb"):
    serial = os.environ.get("ANDROID_SERIAL")
    base_cmd = [adb_path]
    if serial:
        base_cmd.extend(["-s", serial])
    return subprocess.run(base_cmd + cmd, capture_output=True, text=True)

def _pull_prefs(adb_path):
    # Stage to a readable temporary device path
    temp_device_path = "/data/local/tmp/opentracks_prefs_temp.xml"
    
    # Copy to temp location with root permissions
    cp_cmd = _run_adb_cmd(["shell", "su", "0", f"cp {PREFS_FILE_PATH} {temp_device_path}"], adb_path)
    if cp_cmd.returncode != 0:
        # Try to check if file exists
        check_file = _run_adb_cmd(["shell", "su", "0", f"ls {PREFS_FILE_PATH}"], adb_path)
        if check_file.returncode != 0:
            # File might not exist yet (app not initialized)
            return None, "Preferences file does not exist. App may not be initialized."
        return None, f"Failed to stage preferences file: {cp_cmd.stderr.strip()}"
    
    # Make temp file readable
    _run_adb_cmd(["shell", "su", "0", f"chmod 666 {temp_device_path}"], adb_path)
    
    # Pull to local temp file
    local_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xml")
    local_temp.close()
    
    pull_cmd = _run_adb_cmd(["pull", temp_device_path, local_temp.name], adb_path)
    
    # Clean up temp device file
    _run_adb_cmd(["shell", "rm", temp_device_path], adb_path)
    
    if pull_cmd.returncode != 0:
        os.unlink(local_temp.name)
        return None, f"Failed to pull preferences file: {pull_cmd.stderr.strip()}"
        
    return local_temp.name, None

def _push_prefs(local_path, adb_path):
    temp_device_path = "/data/local/tmp/opentracks_prefs_temp.xml"
    
    # Push to temp location
    push_cmd = _run_adb_cmd(["push", local_path, temp_device_path], adb_path)
    if push_cmd.returncode != 0:
        return False, f"Failed to push preferences to temp location: {push_cmd.stderr.strip()}"
    
    # Discover original file metadata (uid, gid, mode, selinux context) dynamically
    stat_cmd = _run_adb_cmd(["shell", "su", "0", f"stat -c '%u %g %a' {PREFS_FILE_PATH}"], adb_path)
    uid, gid, mode = "", "", ""
    if stat_cmd.returncode == 0:
        parts = stat_cmd.stdout.strip().split()
        if len(parts) == 3:
            uid, gid, mode = parts[0], parts[1], parts[2]
            
    selinux_context = ""
    selinux_cmd = _run_adb_cmd(["shell", "su", "0", f"ls -Z {PREFS_FILE_PATH}"], adb_path)
    if selinux_cmd.returncode == 0:
        parts = selinux_cmd.stdout.strip().split()
        if len(parts) > 0:
            selinux_context = parts[0]
            
    # Quiesce the app process before replacing preferences
    _run_adb_cmd(["shell", "am", "force-stop", PACKAGE_NAME], adb_path)
    
    # Copy back to private path
    cp_cmd = _run_adb_cmd(["shell", "su", "0", f"cp {temp_device_path} {PREFS_FILE_PATH}"], adb_path)
    _run_adb_cmd(["shell", "rm", temp_device_path], adb_path)
    
    if cp_cmd.returncode != 0:
        return False, f"Failed to copy preferences to destination: {cp_cmd.stderr.strip()}"
        
    # Restore original metadata dynamically if discovered
    if uid and gid:
        _run_adb_cmd(["shell", "su", "0", f"chown {uid}:{gid} {PREFS_FILE_PATH}"], adb_path)
    if mode:
        _run_adb_cmd(["shell", "su", "0", f"chmod {mode} {PREFS_FILE_PATH}"], adb_path)
    if selinux_context and selinux_context != "?" and ":" in selinux_context:
        _run_adb_cmd(["shell", "su", "0", f"chcon {selinux_context} {PREFS_FILE_PATH}"], adb_path)
        
    return True, None

def get_settings(adb_path: str = "adb") -> dict:
    """
    Read statsUnits and statsCustomLayoutFieldsKey keys from preferences XML.
    """
    local_path, err = _pull_prefs(adb_path)
    if err:
        # If file doesn't exist, return default fallback values as per semantics
        if "does not exist" in err:
            return {
                "success": True,
                "error": "",
                "statsUnits": "METRIC"
            }
        return {"success": False, "error": err}
        
    try:
        tree = ET.parse(local_path)
        root = tree.getroot()
        
        stats_units = "METRIC"  # Default fallback
        stats_custom_layout = None
        
        for child in root:
            name = child.attrib.get("name")
            if name == "statsUnits":
                stats_units = child.text or "METRIC"
            elif name == "statsCustomLayoutFieldsKey":
                stats_custom_layout = child.text
                
        result = {
            "success": True,
            "error": "",
            "statsUnits": stats_units
        }
        if stats_custom_layout is not None:
            result["statsCustomLayoutFieldsKey"] = stats_custom_layout
            
        return result
    except Exception as e:
        return {"success": False, "error": f"Failed to parse XML: {str(e)}"}
    finally:
        if local_path and os.path.exists(local_path):
            os.unlink(local_path)

def set_units_setting(statsUnits: str, adb_path: str = "adb") -> dict:
    """
    Modify the statsUnits key in the SharedPreferences XML file.
    Reject values outside the documented domain (METRIC, IMPERIAL).
    Preserve all other unrelated XML entries.
    """
    if statsUnits not in ["METRIC", "IMPERIAL"]:
        return {"success": False, "error": "Invalid statsUnits value. Must be METRIC or IMPERIAL."}
        
    local_path = None
    local_write_path = None
    try:
        local_path, err = _pull_prefs(adb_path)
        if err:
            # If the pull failed because the file does not exist, we initialize a new XML structure.
            # Otherwise, if it's a real error (e.g. permission/adb issue), we should fail instead of silently wiping existing settings.
            if "does not exist" in err:
                root = ET.Element("map")
                tree = ET.ElementTree(root)
            else:
                return {"success": False, "error": f"Failed to pull preferences: {err}"}
        else:
            try:
                tree = ET.parse(local_path)
                root = tree.getroot()
            except Exception as e:
                # Fallback to new XML structure if parsing fails
                root = ET.Element("map")
                tree = ET.ElementTree(root)
                
        # Find or create the statsUnits element
        found = False
        for child in root:
            if child.attrib.get("name") == "statsUnits":
                child.text = statsUnits
                found = True
                break
                
        if not found:
            new_elem = ET.SubElement(root, "string", name="statsUnits")
            new_elem.text = statsUnits
            
        # Write back to local temp file
        local_write_file = tempfile.NamedTemporaryFile(delete=False, suffix=".xml")
        local_write_path = local_write_file.name
        local_write_file.close()
        
        # Ensure XML declaration is written
        tree.write(local_write_path, encoding="utf-8", xml_declaration=True)
        
        # Push back to device
        success, push_err = _push_prefs(local_write_path, adb_path)
        if not success:
            return {"success": False, "error": push_err}
            
        return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": f"Failed to write or push XML: {str(e)}"}
    finally:
        if local_path and os.path.exists(local_path):
            try:
                os.unlink(local_path)
            except Exception:
                pass
        if local_write_path and os.path.exists(local_write_path):
            try:
                os.unlink(local_write_path)
            except Exception:
                pass

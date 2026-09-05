import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import shlex

APP_PACKAGE = "code.name.monkey.retromusic"
PREFS_PATH = "/data/data/code.name.monkey.retromusic/shared_prefs/code.name.monkey.retromusic_preferences.xml"

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

def _pull_xml(adb_path="adb"):
    """Pulls the remote preferences XML to a local temporary file."""
    # Stage to a readable temporary device path
    temp_device_path = f"/data/local/tmp/retromusic_prefs_temp.xml"
    copy_cmd = ["shell", "su", "0", f"cp {shlex.quote(PREFS_PATH)} {temp_device_path} && chmod 666 {temp_device_path}"]
    res = _run_adb_cmd(copy_cmd, adb_path)
    if res.returncode != 0:
        # If file doesn't exist, return empty XML structure
        if "No such file" in res.stderr or "No such file" in res.stdout:
            return "<map></map>", None
        raise Exception(f"Failed to stage preferences file: {res.stderr or res.stdout}")
    
    # Pull to local temp file
    local_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xml")
    local_temp.close()
    
    pull_cmd = ["pull", temp_device_path, local_temp.name]
    res_pull = _run_adb_cmd(pull_cmd, adb_path)
    
    # Clean up temp device file
    _run_adb_cmd(["shell", "rm", temp_device_path], adb_path)
    
    if res_pull.returncode != 0:
        raise Exception(f"Failed to pull preferences file: {res_pull.stderr}")
        
    with open(local_temp.name, "r", encoding="utf-8") as f:
        content = f.read()
        
    try:
        os.unlink(local_temp.name)
    except OSError:
        pass
        
    return content, PREFS_PATH

def _push_xml(xml_content, adb_path="adb"):
    """Pushes local XML content back to the remote private path, preserving metadata."""
    # Discover existing metadata
    uid, gid, mode, selinux = _get_file_metadata(PREFS_PATH, adb_path)
    
    # If metadata is not discoverable, we try to discover the directory metadata
    if not uid or not gid:
        dir_path = "/data/data/code.name.monkey.retromusic/shared_prefs"
        uid, gid, _, selinux = _get_file_metadata(dir_path, adb_path)
        mode = "660"
        if not uid or not gid:
            raise Exception("Could not discover ownership metadata for the target path.")

    # Write locally
    local_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xml")
    local_temp.close()
    with open(local_temp.name, "w", encoding="utf-8") as f:
        f.write(xml_content)
        
    # Stage to device
    temp_device_path = f"/data/local/tmp/retromusic_prefs_temp.xml"
    push_cmd = ["push", local_temp.name, temp_device_path]
    res_push = _run_adb_cmd(push_cmd, adb_path)
    try:
        os.unlink(local_temp.name)
    except OSError:
        pass
        
    if res_push.returncode != 0:
        raise Exception(f"Failed to push preferences file to staging: {res_push.stderr}")
        
    # Copy back with root, restore metadata
    commands = [
        f"cp {temp_device_path} {shlex.quote(PREFS_PATH)}",
        f"chown {uid}:{gid} {shlex.quote(PREFS_PATH)}",
        f"chmod {mode} {shlex.quote(PREFS_PATH)}"
    ]
    if selinux and selinux != "?":
        commands.append(f"chcon {shlex.quote(selinux)} {shlex.quote(PREFS_PATH)}")
    commands.append(f"rm {temp_device_path}")
    
    full_cmd = " && ".join(commands)
    res_copy = _run_adb_cmd(["shell", "su", "0", full_cmd], adb_path)
    if res_copy.returncode != 0:
        raise Exception(f"Failed to restore preferences file with correct metadata: {res_copy.stderr or res_copy.stdout}")

def get_preferences(adb_path: str = "adb") -> dict:
    """Reads and parses the Retro Music preferences XML."""
    try:
        xml_content, _ = _pull_xml(adb_path)
        root = ET.fromstring(xml_content)
        
        result = {
            "success": True,
            "error": ""
        }
        
        # Parse expected keys
        for child in root:
            name = child.attrib.get("name")
            if name == "toggle_volume":
                result["toggle_volume"] = child.attrib.get("value") == "true"
            elif name == "toggle_add_controls":
                result["toggle_add_controls"] = child.attrib.get("value") == "true"
            elif name == "last_used_tab":
                result["last_used_tab"] = child.text if child.text is not None else ""
                
        return result
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def set_preference(key: str, value: str, adb_path: str = "adb") -> dict:
    """Sets a preference key to a given value, preserving other keys and types."""
    if key not in ["toggle_volume", "toggle_add_controls", "last_used_tab"]:
        return {
            "success": False,
            "error": f"Unsupported preference key: {key}"
        }
        
    try:
        # Quiesce the app before external preference replacement
        _run_adb_cmd(["shell", "am", "force-stop", APP_PACKAGE], adb_path)
        
        xml_content, _ = _pull_xml(adb_path)
        root = ET.fromstring(xml_content)
        
        # Find or create the element
        target_elem = None
        for child in root:
            if child.attrib.get("name") == key:
                target_elem = child
                break
                
        if key in ["toggle_volume", "toggle_add_controls"]:
            bool_val = value.lower() in ["true", "1", "yes"]
            str_bool = "true" if bool_val else "false"
            if target_elem is not None:
                target_elem.tag = "boolean"
                target_elem.attrib["value"] = str_bool
                if target_elem.text:
                    target_elem.text = None
            else:
                new_elem = ET.SubElement(root, "boolean", {"name": key, "value": str_bool})
        else:  # last_used_tab
            if target_elem is not None:
                target_elem.tag = "string"
                target_elem.text = value
                if "value" in target_elem.attrib:
                    del target_elem.attrib["value"]
            else:
                new_elem = ET.SubElement(root, "string", {"name": key})
                new_elem.text = value
                
        # Serialize back to XML
        new_xml_content = ET.tostring(root, encoding="utf-8").decode("utf-8")
        _push_xml(new_xml_content, adb_path)
        
        return {
            "success": True,
            "error": ""
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

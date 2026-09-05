import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import shlex

PREFS_PATH = "/data/data/org.tasks/shared_prefs/org.tasks_preferences.xml"
PACKAGE_NAME = "org.tasks"

def _run_adb(args, adb_path="adb"):
    cmd = [adb_path]
    serial = os.environ.get("ANDROID_SERIAL")
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res

def _run_su_cmd(cmd_str, adb_path="adb"):
    return _run_adb(["shell", "su", "0", cmd_str], adb_path=adb_path)

def _get_file_metadata(path, adb_path="adb"):
    # Returns (uid, gid, mode, selinux_context) or None
    res = _run_su_cmd(f"stat -c '%u %g %a' {shlex.quote(path)}", adb_path=adb_path)
    if res.returncode != 0:
        return None
    parts = res.stdout.strip().split()
    if len(parts) < 3:
        return None
    uid, gid, mode = parts[0], parts[1], parts[2]
    
    res_selinux = _run_su_cmd(f"ls -Z {shlex.quote(path)}", adb_path=adb_path)
    selinux = ""
    if res_selinux.returncode == 0:
        parts_selinux = res_selinux.stdout.strip().split()
        if parts_selinux:
            selinux = parts_selinux[0]
    return uid, gid, mode, selinux

def _parse_prefs_xml(xml_content):
    # Parses SharedPreferences XML and returns a dict of {key: (tag, value, attribs)}
    # SharedPreferences tags are typically <boolean>, <string>, <int>, <long>, <float>, <set>
    try:
        root = ET.fromstring(xml_content)
    except Exception as e:
        raise ValueError(f"Failed to parse XML: {e}")
    
    prefs = {}
    for child in root:
        name = child.attrib.get("name")
        if name is not None:
            tag = child.tag
            attribs = dict(child.attrib)
            if tag == "string":
                val = child.text or ""
            elif tag == "boolean":
                val = child.attrib.get("value", "false")
            elif tag in ("int", "long", "float"):
                val = child.attrib.get("value", "0")
            elif tag == "set":
                val = [item.text or "" for item in child.findall("string")]
            else:
                val = child.text or ""
            prefs[name] = (tag, val, attribs)
    return prefs

def _serialize_prefs_xml(prefs_dict):
    root = ET.Element("map")
    for name, (tag, val, attribs) in prefs_dict.items():
        child = ET.SubElement(root, tag)
        for k, v in attribs.items():
            child.set(k, v)
        child.set("name", name)
        if tag == "string":
            child.text = str(val)
        elif tag in ("boolean", "int", "long", "float"):
            child.set("value", str(val))
        elif tag == "set":
            for item in val:
                s_elem = ET.SubElement(child, "string")
                s_elem.text = str(item)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")

def get_preferences(keys=None, adb_path="adb"):
    """
    Retrieve user-facing app preferences from the Tasks SharedPreferences XML file.
    """
    if keys is None:
        keys = []
    
    # Check if file exists
    check_res = _run_su_cmd(f"[ -f {shlex.quote(PREFS_PATH)} ] && echo 'exists'", adb_path=adb_path)
    if "exists" not in check_res.stdout:
        return {
            "preferences": {},
            "success": True,
            "error": ""
        }
    
    # Read file content via a temporary readable path
    temp_device_path = f"/data/local/tmp/tasks_prefs_temp_{os.getpid()}.xml"
    copy_res = _run_su_cmd(f"cp {shlex.quote(PREFS_PATH)} {temp_device_path} && chmod 666 {temp_device_path}", adb_path=adb_path)
    if copy_res.returncode != 0:
        return {
            "preferences": {},
            "success": False,
            "error": f"Failed to stage preferences file: {copy_res.stderr.strip()}"
        }
    
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tmp_file:
        tmp_name = tmp_file.name
    
    try:
        pull_res = _run_adb(["pull", temp_device_path, tmp_name], adb_path=adb_path)
        _run_su_cmd(f"rm -f {temp_device_path}", adb_path=adb_path)
        if pull_res.returncode != 0:
            return {
                "preferences": {},
                "success": False,
                "error": f"Failed to pull preferences file: {pull_res.stderr.strip()}"
            }
        
        with open(tmp_name, "r", encoding="utf-8") as f:
            content = f.read()
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
            
    try:
        prefs_dict = _parse_prefs_xml(content)
    except Exception as e:
        return {
            "preferences": {},
            "success": False,
            "error": f"Failed to parse preferences XML: {str(e)}"
        }
    
    result = {}
    if keys:
        for k in keys:
            if k in prefs_dict:
                # Convert value to string representation
                _, val, _ = prefs_dict[k]
                if isinstance(val, list):
                    result[k] = ",".join(val)
                else:
                    result[k] = str(val)
    else:
        for k, (_, val, _) in prefs_dict.items():
            if isinstance(val, list):
                result[k] = ",".join(val)
            else:
                result[k] = str(val)
                
    return {
        "preferences": result,
        "success": True,
        "error": ""
    }

def set_preferences(preferences, adb_path="adb"):
    """
    Set or update user-facing app preferences in the Tasks SharedPreferences XML file structurally.
    """
    # Quiesce the app process to avoid overwriting cached values
    stop_res = _run_adb(["shell", "am", "force-stop", PACKAGE_NAME], adb_path=adb_path)
    if stop_res.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to stop app process: {stop_res.stderr.strip()}"
        }
        
    # Check if file exists and get metadata
    metadata = _get_file_metadata(PREFS_PATH, adb_path=adb_path)
    
    content = ""
    if metadata:
        # Read existing content
        temp_device_path = f"/data/local/tmp/tasks_prefs_temp_{os.getpid()}.xml"
        copy_res = _run_su_cmd(f"cp {shlex.quote(PREFS_PATH)} {temp_device_path} && chmod 666 {temp_device_path}", adb_path=adb_path)
        if copy_res.returncode == 0:
            with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tmp_file:
                tmp_name = tmp_file.name
            try:
                pull_res = _run_adb(["pull", temp_device_path, tmp_name], adb_path=adb_path)
                _run_su_cmd(f"rm -f {temp_device_path}", adb_path=adb_path)
                if pull_res.returncode == 0:
                    with open(tmp_name, "r", encoding="utf-8") as f:
                        content = f.read()
            finally:
                if os.path.exists(tmp_name):
                    os.remove(tmp_name)

    if content:
        try:
            prefs_dict = _parse_prefs_xml(content)
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to parse existing preferences XML: {str(e)}"
            }
    else:
        prefs_dict = {}
        
    # Merge new preferences
    for k, v in preferences.items():
        # Determine type based on existing key or default to string
        if k in prefs_dict:
            tag, _, attribs = prefs_dict[k]
        else:
            # Infer type or default to string
            if v.lower() in ("true", "false"):
                tag = "boolean"
                attribs = {"value": v.lower()}
            elif v.isdigit():
                tag = "int"
                attribs = {"value": v}
            else:
                tag = "string"
                attribs = {}
        
        if tag == "boolean":
            attribs["value"] = "true" if v.lower() in ("true", "1") else "false"
            val = attribs["value"]
        elif tag in ("int", "long", "float"):
            attribs["value"] = v
            val = v
        elif tag == "set":
            val = v.split(",")
        else:
            tag = "string"
            val = v
            
        prefs_dict[k] = (tag, val, attribs)
        
    try:
        new_xml = _serialize_prefs_xml(prefs_dict)
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to serialize updated preferences: {str(e)}"
        }
        
    # Write back
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as tmp_file:
        tmp_file.write(new_xml)
        tmp_name = tmp_file.name
        
    try:
        temp_device_path = f"/data/local/tmp/tasks_prefs_temp_{os.getpid()}.xml"
        push_res = _run_adb(["push", tmp_name, temp_device_path], adb_path=adb_path)
        if push_res.returncode != 0:
            return {
                "success": False,
                "error": f"Failed to push updated preferences to device: {push_res.stderr.strip()}"
            }
            
        # Copy to final destination
        copy_back = _run_su_cmd(f"cp {temp_device_path} {shlex.quote(PREFS_PATH)}", adb_path=adb_path)
        _run_su_cmd(f"rm -f {temp_device_path}", adb_path=adb_path)
        if copy_back.returncode != 0:
            return {
                "success": False,
                "error": f"Failed to write preferences to destination: {copy_back.stderr.strip()}"
            }
            
        # Restore metadata
        if metadata:
            uid, gid, mode, selinux = metadata
            _run_su_cmd(f"chown {uid}:{gid} {shlex.quote(PREFS_PATH)}", adb_path=adb_path)
            _run_su_cmd(f"chmod {mode} {shlex.quote(PREFS_PATH)}", adb_path=adb_path)
            if selinux:
                _run_su_cmd(f"chcon {shlex.quote(selinux)} {shlex.quote(PREFS_PATH)}", adb_path=adb_path)
        else:
            # If file didn't exist, try to discover app uid/gid from the parent directory
            parent_dir = os.path.dirname(PREFS_PATH)
            parent_meta = _get_file_metadata(parent_dir, adb_path=adb_path)
            if parent_meta:
                uid, gid, _, selinux = parent_meta
                _run_su_cmd(f"chown {uid}:{gid} {shlex.quote(PREFS_PATH)}", adb_path=adb_path)
                _run_su_cmd(f"chmod 660 {shlex.quote(PREFS_PATH)}", adb_path=adb_path)
                if selinux:
                    # Adjust context for shared_prefs file if parent context is available
                    file_selinux = selinux.replace("embed_data_file", "app_data_file")
                    _run_su_cmd(f"chcon {shlex.quote(file_selinux)} {shlex.quote(PREFS_PATH)}", adb_path=adb_path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
            
    return {
        "success": True,
        "error": ""
    }

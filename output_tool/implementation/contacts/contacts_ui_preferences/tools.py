import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import shlex

PREFS_FILE_PATH = "/data/data/com.google.android.contacts/shared_prefs/com.google.android.contacts.xml"
PACKAGE_NAME = "com.google.android.contacts"
SORT_ORDER_KEY = "android.contacts.SORT_ORDER"

def _run_command(cmd, adb_path="adb"):
    serial = os.environ.get("ANDROID_SERIAL")
    full_cmd = [adb_path]
    if serial:
        full_cmd.extend(["-s", serial])
    full_cmd.extend(cmd)
    result = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result

def _get_file_metadata(adb_path):
    # Discover existing uid, gid, mode, and SELinux context
    cmd = ["shell", "su", "0", "stat", "-c", "'%u %g %a %C'", PREFS_FILE_PATH]
    res = _run_command(cmd, adb_path)
    if res.returncode != 0:
        return None
    parts = res.stdout.strip().strip("'").split()
    if len(parts) >= 4:
        return {
            "uid": parts[0],
            "gid": parts[1],
            "mode": parts[2],
            "secontext": parts[3]
        }
    return None

def _pull_xml(adb_path):
    # Stage private file to a readable temporary device path
    temp_device_path = f"/data/local/tmp/temp_prefs_{os.getpid()}.xml"
    copy_cmd = ["shell", "su", "0", "cp", PREFS_FILE_PATH, temp_device_path]
    res = _run_command(copy_cmd, adb_path)
    if res.returncode != 0:
        # File might not exist yet
        return None, None
    
    chmod_cmd = ["shell", "su", "0", "chmod", "666", temp_device_path]
    _run_command(chmod_cmd, adb_path)

    local_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xml")
    local_temp.close()
    
    pull_cmd = ["pull", temp_device_path, local_temp.name]
    pull_res = _run_command(pull_cmd, adb_path)
    
    # Clean up temp device file
    _run_command(["shell", "rm", "-f", temp_device_path], adb_path)
    
    if pull_res.returncode != 0:
        if os.path.exists(local_temp.name):
            os.unlink(local_temp.name)
        return None, None
        
    return local_temp.name, temp_device_path

def _push_xml(local_path, metadata, adb_path):
    temp_device_path = f"/data/local/tmp/temp_prefs_push_{os.getpid()}.xml"
    
    # Push to temp device path
    push_res = _run_command(["push", local_path, temp_device_path], adb_path)
    if push_res.returncode != 0:
        return False, f"Failed to push file to device: {push_res.stderr}"
        
    # Quiesce the app before replacing private state
    _run_command(["shell", "am", "force-stop", PACKAGE_NAME], adb_path)
    
    # Copy back to private path
    copy_cmd = ["shell", "su", "0", "cp", temp_device_path, PREFS_FILE_PATH]
    copy_res = _run_command(copy_cmd, adb_path)
    
    # Clean up temp device file
    _run_command(["shell", "rm", "-f", temp_device_path], adb_path)
    
    if copy_res.returncode != 0:
        return False, f"Failed to copy file to private path: {copy_res.stderr}"
        
    # Restore metadata
    if metadata:
        uid = metadata.get("uid")
        gid = metadata.get("gid")
        mode = metadata.get("mode")
        secontext = metadata.get("secontext")
        
        if uid and gid:
            _run_command(["shell", "su", "0", "chown", f"{uid}:{gid}", PREFS_FILE_PATH], adb_path)
        if mode:
            _run_command(["shell", "su", "0", "chmod", mode, PREFS_FILE_PATH], adb_path)
        if secontext and secontext != "?" and secontext != "null":
            _run_command(["shell", "su", "0", "chcon", secontext, PREFS_FILE_PATH], adb_path)
            
    return True, ""

def get_contacts_preferences(adb_path: str = "adb") -> dict:
    """
    Returns the sort_order preference value parsed from the XML.
    """
    local_path, _ = _pull_xml(adb_path)
    if not local_path:
        # If file doesn't exist, return empty/default values as per semantics
        return {
            "sort_order": "",
            "success": True,
            "error": ""
        }
        
    try:
        tree = ET.parse(local_path)
        root = tree.getroot()
        sort_order = ""
        
        # SharedPreferences can store strings in <string name="...">value</string>
        for child in root:
            if child.attrib.get("name") == SORT_ORDER_KEY:
                sort_order = child.text or ""
                break
                
        return {
            "sort_order": sort_order,
            "success": True,
            "error": ""
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to parse SharedPreferences XML: {str(e)}"
        }
    finally:
        if local_path and os.path.exists(local_path):
            os.unlink(local_path)

def set_contacts_sort_order(sort_order: str, adb_path: str = "adb") -> dict:
    """
    Update the 'android.contacts.SORT_ORDER' key with the new value and write the XML back atomically.
    """
    metadata = _get_file_metadata(adb_path)
    local_path, _ = _pull_xml(adb_path)
    
    try:
        if local_path:
            tree = ET.parse(local_path)
            root = tree.getroot()
        else:
            # Create a new XML structure if it doesn't exist
            root = ET.Element("map")
            tree = ET.ElementTree(root)
            
        # Find or create the preference key
        found = False
        for child in root:
            if child.attrib.get("name") == SORT_ORDER_KEY:
                child.text = sort_order
                found = True
                break
                
        if not found:
            new_elem = ET.SubElement(root, "string", name=SORT_ORDER_KEY)
            new_elem.text = sort_order
            
        # Write back to local temp file
        if not local_path:
            local_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xml")
            local_path = local_temp.name
            local_temp.close()
            
        tree.write(local_path, encoding="utf-8", xml_declaration=True)
        
        # Push back to device
        success, err = _push_xml(local_path, metadata, adb_path)
        if not success:
            return {
                "success": False,
                "error": err
            }
            
        return {
            "success": True,
            "error": ""
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to update SharedPreferences: {str(e)}"
        }
    finally:
        if local_path and os.path.exists(local_path):
            os.unlink(local_path)

import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import shlex
import json

DEFAULT_NOTEBOOK_ROOT = "/storage/emulated/0/Documents/markor"

def _run_adb_cmd(cmd, adb_path="adb"):
    serial = os.environ.get("ANDROID_SERIAL")
    base_cmd = [adb_path]
    if serial:
        base_cmd.extend(["-s", serial])
    base_cmd.extend(cmd)
    res = subprocess.run(base_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode, res.stdout, res.stderr

def _read_remote_file_as_root(remote_path, adb_path="adb"):
    # Stage private files through a readable temporary device path before pulling
    temp_device_path = f"/data/local/tmp/temp_markor_{os.getpid()}_{os.urandom(4).hex()}"
    
    # Copy to temp location with root privileges
    cp_cmd = ["shell", "su", "0", "cp", shlex.quote(remote_path), shlex.quote(temp_device_path)]
    ret, _, err = _run_adb_cmd(cp_cmd, adb_path)
    if ret != 0:
        return None
    
    # Make readable
    chmod_cmd = ["shell", "su", "0", "chmod", "666", shlex.quote(temp_device_path)]
    _run_adb_cmd(chmod_cmd, adb_path)
    
    # Pull to local temp file
    with tempfile.NamedTemporaryFile(delete=False) as f:
        local_temp = f.name
    
    pull_cmd = ["pull", temp_device_path, local_temp]
    ret, _, _ = _run_adb_cmd(pull_cmd, adb_path)
    
    # Clean up remote temp
    rm_cmd = ["shell", "su", "0", "rm", "-f", shlex.quote(temp_device_path)]
    _run_adb_cmd(rm_cmd, adb_path)
    
    if ret != 0:
        if os.path.exists(local_temp):
            os.remove(local_temp)
        return None
        
    try:
        with open(local_temp, "rb") as f:
            content = f.read()
    except Exception:
        content = None
    finally:
        if os.path.exists(local_temp):
            os.remove(local_temp)
            
    return content

def _parse_pref_xml(xml_bytes):
    if not xml_bytes:
        return {}
    try:
        root = ET.fromstring(xml_bytes)
        prefs = {}
        for child in root:
            name = child.attrib.get("name")
            if not name:
                continue
            if child.tag == "string":
                prefs[name] = child.text or ""
        return prefs
    except Exception:
        return {}

def _normalize_path(path):
    if not path:
        return ""
    # Normalize equivalent external-storage aliases
    aliases = ["/sdcard", "/mnt/sdcard", "/storage/self/primary"]
    for alias in aliases:
        if path.startswith(alias):
            path = path.replace(alias, "/storage/emulated/0", 1)
            break
    return os.path.normpath(path)

def _resolve_root_internal(adb_path="adb"):
    # Try app.xml
    app_xml_bytes = _read_remote_file_as_root("/data/data/net.gsantner.markor/shared_prefs/app.xml", adb_path)
    if app_xml_bytes:
        prefs = _parse_pref_xml(app_xml_bytes)
        for key in ["pref_key__notebook_directory", "exts_notebook_directory"]:
            if key in prefs and prefs[key]:
                return _normalize_path(prefs[key]), "app.xml"
                
    # Try net.gsantner.markor_preferences.xml
    alt_xml_bytes = _read_remote_file_as_root("/data/data/net.gsantner.markor/shared_prefs/net.gsantner.markor_preferences.xml", adb_path)
    if alt_xml_bytes:
        prefs = _parse_pref_xml(alt_xml_bytes)
        for key in ["pref_key__notebook_directory", "exts_notebook_directory"]:
            if key in prefs and prefs[key]:
                return _normalize_path(prefs[key]), "net.gsantner.markor_preferences.xml"
                
    return DEFAULT_NOTEBOOK_ROOT, "default"

def _safe_resolve_absolute_path(root, relative_path):
    # Ensure relative path does not escape the root
    normalized_root = os.path.normpath(root)
    joined = os.path.normpath(os.path.join(normalized_root, relative_path.lstrip("/")))
    if not joined.startswith(normalized_root):
        raise ValueError("Directory traversal escape detected.")
    return joined

def _remote_file_exists(path, adb_path="adb"):
    # Returns (exists, is_dir, size, mtime_ms)
    cmd = ["shell", "su", "0", "stat", "-c", "'%F|%s|%Y'", shlex.quote(path)]
    ret, out, _ = _run_adb_cmd(cmd, adb_path)
    if ret != 0 or not out:
        return False, False, 0, 0
    parts = out.decode("utf-8", errors="ignore").strip().strip("'").split("|")
    if len(parts) < 3:
        return False, False, 0, 0
    is_dir = "directory" in parts[0].lower()
    try:
        size = int(parts[1])
    except ValueError:
        size = 0
    try:
        mtime_ms = int(parts[2]) * 1000
    except ValueError:
        mtime_ms = 0
    return True, is_dir, size, mtime_ms

def resolve_notebook_root(adb_path: str = "adb") -> dict:
    try:
        root, source = _resolve_root_internal(adb_path)
        return {
            "success": True,
            "error": "",
            "notebook_root": root,
            "source": source
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "notebook_root": "",
            "source": ""
        }

def list_notebook_directory(relative_path: str, adb_path: str = "adb") -> dict:
    try:
        root, _ = _resolve_root_internal(adb_path)
        target_path = _safe_resolve_absolute_path(root, relative_path)
        
        exists, is_dir, _, _ = _remote_file_exists(target_path, adb_path)
        if not exists:
            return {
                "success": False,
                "error": f"Directory does not exist: {relative_path}",
                "items": []
            }
        if not is_dir:
            return {
                "success": False,
                "error": f"Path is a file, not a directory: {relative_path}",
                "items": []
            }
            
        # List directory contents safely without relying on find -printf %y
        # We list the directory contents using ls -1, then stat each item.
        ls_cmd = ["shell", "su", "0", "ls", "-1", shlex.quote(target_path)]
        ret, out, err = _run_adb_cmd(ls_cmd, adb_path)
        if ret != 0:
            return {
                "success": False,
                "error": f"Failed to list directory: {err.decode('utf-8', errors='ignore')}",
                "items": []
            }
            
        items = []
        names = out.decode("utf-8", errors="ignore").strip().splitlines()
        for name in names:
            name = name.strip()
            if not name:
                continue
            item_abs_path = os.path.join(target_path, name)
            item_exists, item_is_dir, item_size, item_mtime = _remote_file_exists(item_abs_path, adb_path)
            if not item_exists:
                continue
                
            item_rel_path = os.path.join(relative_path, name).replace("\\", "/")
            items.append({
                "name": name,
                "relative_path": item_rel_path,
                "is_directory": item_is_dir,
                "size": None if item_is_dir else item_size,
                "last_modified": item_mtime
            })
            
        return {
            "success": True,
            "error": "",
            "items": items
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "items": []
        }

def read_note_file(relative_path: str, adb_path: str = "adb") -> dict:
    try:
        root, _ = _resolve_root_internal(adb_path)
        target_path = _safe_resolve_absolute_path(root, relative_path)
        
        exists, is_dir, size, mtime = _remote_file_exists(target_path, adb_path)
        if not exists:
            return {
                "success": False,
                "error": f"File does not exist: {relative_path}",
                "content": "",
                "last_modified": 0,
                "size": 0
            }
        if is_dir:
            return {
                "success": False,
                "error": f"Path is a directory, not a file: {relative_path}",
                "content": "",
                "last_modified": 0,
                "size": 0
            }
            
        # Read file content
        content_bytes = _read_remote_file_as_root(target_path, adb_path)
        if content_bytes is None:
            return {
                "success": False,
                "error": f"Failed to read file: {relative_path}",
                "content": "",
                "last_modified": 0,
                "size": 0
            }
            
        return {
            "success": True,
            "error": "",
            "content": content_bytes.decode("utf-8", errors="replace"),
            "last_modified": mtime,
            "size": size
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "content": "",
            "last_modified": 0,
            "size": 0
        }

def write_note_file(relative_path: str, content: str, adb_path: str = "adb") -> dict:
    try:
        root, _ = _resolve_root_internal(adb_path)
        target_path = _safe_resolve_absolute_path(root, relative_path)
        
        exists, is_dir, _, _ = _remote_file_exists(target_path, adb_path)
        if exists and is_dir:
            return {
                "success": False,
                "error": f"Cannot overwrite directory with a file: {relative_path}",
                "absolute_path": ""
            }
            
        # Ensure parent directory exists
        parent_dir = os.path.dirname(target_path)
        mkdir_cmd = ["shell", "su", "0", "mkdir", "-p", shlex.quote(parent_dir)]
        _run_adb_cmd(mkdir_cmd, adb_path)
        
        # Write atomically using a local temp file and staging it
        with tempfile.NamedTemporaryFile(delete=False, mode="w", encoding="utf-8") as f:
            f.write(content)
            local_temp = f.name
            
        temp_device_path = f"/data/local/tmp/temp_write_{os.getpid()}_{os.urandom(4).hex()}"
        push_cmd = ["push", local_temp, temp_device_path]
        ret, _, err = _run_adb_cmd(push_cmd, adb_path)
        if os.path.exists(local_temp):
            os.remove(local_temp)
            
        if ret != 0:
            return {
                "success": False,
                "error": f"Failed to stage file: {err.decode('utf-8', errors='ignore')}",
                "absolute_path": ""
            }
            
        # Move atomically on device and set permissions
        mv_cmd = ["shell", "su", "0", "mv", shlex.quote(temp_device_path), shlex.quote(target_path)]
        ret, _, err = _run_adb_cmd(mv_cmd, adb_path)
        if ret != 0:
            # Clean up remote temp if mv failed
            _run_adb_cmd(["shell", "su", "0", "rm", "-f", shlex.quote(temp_device_path)], adb_path)
            return {
                "success": False,
                "error": f"Failed to write file to destination: {err.decode('utf-8', errors='ignore')}",
                "absolute_path": ""
            }
            
        # Ensure permissions are accessible
        chmod_cmd = ["shell", "su", "0", "chmod", "666", shlex.quote(target_path)]
        _run_adb_cmd(chmod_cmd, adb_path)
        
        return {
            "success": True,
            "error": "",
            "absolute_path": target_path
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "absolute_path": ""
        }

def create_notebook_folder(relative_path: str, adb_path: str = "adb") -> dict:
    try:
        root, _ = _resolve_root_internal(adb_path)
        target_path = _safe_resolve_absolute_path(root, relative_path)
        
        exists, _, _, _ = _remote_file_exists(target_path, adb_path)
        if exists:
            return {
                "success": False,
                "error": f"Destination already exists: {relative_path}"
            }
            
        # Verify immediate parent exists
        parent_dir = os.path.dirname(target_path)
        parent_exists, parent_is_dir, _, _ = _remote_file_exists(parent_dir, adb_path)
        if not parent_exists or not parent_is_dir:
            return {
                "success": False,
                "error": f"Immediate parent directory does not exist for: {relative_path}"
            }
            
        mkdir_cmd = ["shell", "su", "0", "mkdir", shlex.quote(target_path)]
        ret, _, err = _run_adb_cmd(mkdir_cmd, adb_path)
        if ret != 0:
            return {
                "success": False,
                "error": f"Failed to create directory: {err.decode('utf-8', errors='ignore')}"
            }
            
        chmod_cmd = ["shell", "su", "0", "chmod", "777", shlex.quote(target_path)]
        _run_adb_cmd(chmod_cmd, adb_path)
        
        return {
            "success": True,
            "error": ""
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def move_or_rename_item(source_relative_path: str, destination_relative_path: str, adb_path: str = "adb") -> dict:
    try:
        root, _ = _resolve_root_internal(adb_path)
        src_path = _safe_resolve_absolute_path(root, source_relative_path)
        dst_path = _safe_resolve_absolute_path(root, destination_relative_path)
        
        src_exists, _, _, _ = _remote_file_exists(src_path, adb_path)
        if not src_exists:
            return {
                "success": False,
                "error": f"Source does not exist: {source_relative_path}"
            }
            
        dst_exists, _, _, _ = _remote_file_exists(dst_path, adb_path)
        if dst_exists:
            return {
                "success": False,
                "error": f"Destination already exists: {destination_relative_path}"
            }
            
        # Ensure destination parent exists
        dst_parent = os.path.dirname(dst_path)
        mkdir_cmd = ["shell", "su", "0", "mkdir", "-p", shlex.quote(dst_parent)]
        _run_adb_cmd(mkdir_cmd, adb_path)
        
        # Perform atomic move
        mv_cmd = ["shell", "su", "0", "mv", shlex.quote(src_path), shlex.quote(dst_path)]
        ret, _, err = _run_adb_cmd(mv_cmd, adb_path)
        if ret != 0:
            return {
                "success": False,
                "error": f"Failed to move item: {err.decode('utf-8', errors='ignore')}"
            }
            
        return {
            "success": True,
            "error": ""
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def delete_notebook_item(relative_path: str, recursive: bool, adb_path: str = "adb") -> dict:
    try:
        if not relative_path or relative_path.strip() in ["", "/", "."]:
            return {
                "success": False,
                "error": "Rejecting attempt to delete the notebook root itself."
            }
            
        root, _ = _resolve_root_internal(adb_path)
        target_path = _safe_resolve_absolute_path(root, relative_path)
        
        # Double check containment
        if os.path.normpath(target_path) == os.path.normpath(root):
            return {
                "success": False,
                "error": "Rejecting attempt to delete the notebook root itself."
            }
            
        exists, is_dir, _, _ = _remote_file_exists(target_path, adb_path)
        if not exists:
            return {
                "success": False,
                "error": f"Target does not exist: {relative_path}"
            }
            
        if is_dir:
            if not recursive:
                # Check if directory is empty
                cmd = ["shell", "su", "0", "find", shlex.quote(target_path), "-mindepth", "1", "-maxdepth", "1"]
                ret, out, _ = _run_adb_cmd(cmd, adb_path)
                if ret == 0 and out.strip():
                    return {
                        "success": False,
                        "error": f"Directory is not empty: {relative_path}. Set recursive=True to delete."
                    }
            rm_cmd = ["shell", "su", "0", "rm", "-rf", shlex.quote(target_path)]
        else:
            rm_cmd = ["shell", "su", "0", "rm", "-f", shlex.quote(target_path)]
            
        ret, _, err = _run_adb_cmd(rm_cmd, adb_path)
        if ret != 0:
            return {
                "success": False,
                "error": f"Failed to delete item: {err.decode('utf-8', errors='ignore')}"
            }
            
        return {
            "success": True,
            "error": ""
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

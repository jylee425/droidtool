import os
import subprocess
import shlex
import json

# Helper to run ADB commands
def _run_adb(args, adb_path="adb"):
    serial = os.environ.get("ANDROID_SERIAL")
    cmd = [adb_path]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result

# Helper to run shell commands on device
def _run_shell(shell_cmd, adb_path="adb"):
    return _run_adb(["shell", shell_cmd], adb_path=adb_path)

# Helper to validate path containment and prevent root escape
def _validate_path(path):
    # Normalize path
    normalized = os.path.normpath(path)
    # Must start with /sdcard or /storage/emulated/0
    if not (normalized.startswith("/sdcard") or normalized.startswith("/storage/emulated/0")):
        raise ValueError(f"Path '{path}' escapes the allowed storage roots.")
    if ".." in normalized.split(os.sep):
        raise ValueError(f"Path '{path}' contains invalid directory traversal elements.")
    return normalized

# Helper to trigger MediaStore scan for a path
def _scan_media_store(path, adb_path="adb"):
    # Broadcast intent to scan file
    cmd = f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://{shlex.quote(path)}"
    _run_shell(cmd, adb_path=adb_path)

def create_directory(directory_path: str, adb_path: str = "adb") -> dict:
    try:
        target_path = _validate_path(directory_path)
        
        # Check if path exists and its type
        check_cmd = f"[ -e {shlex.quote(target_path)} ] && ([ -d {shlex.quote(target_path)} ] && echo 'DIR' || echo 'FILE') || echo 'NONE'"
        res = _run_shell(check_cmd, adb_path=adb_path)
        status = res.stdout.strip()
        
        if status == "FILE":
            return {
                "success": False,
                "path": target_path,
                "error": f"A regular file already exists at the target path: {target_path}"
            }
        elif status == "DIR":
            return {
                "success": True,
                "path": target_path,
                "error": ""
            }
            
        # Create directory and parents
        mkdir_cmd = f"mkdir -p {shlex.quote(target_path)}"
        mkdir_res = _run_shell(mkdir_cmd, adb_path=adb_path)
        if mkdir_res.returncode != 0:
            return {
                "success": False,
                "path": target_path,
                "error": f"Failed to create directory: {mkdir_res.stderr.strip()}"
            }
            
        return {
            "success": True,
            "path": target_path,
            "error": ""
        }
    except Exception as e:
        return {
            "success": False,
            "path": directory_path,
            "error": str(e)
        }

def delete_path(path: str, recursive: bool, adb_path: str = "adb") -> dict:
    try:
        target_path = _validate_path(path)
        
        # Check existence and type
        check_cmd = f"[ -e {shlex.quote(target_path)} ] && ([ -d {shlex.quote(target_path)} ] && echo 'DIR' || echo 'FILE') || echo 'NONE'"
        res = _run_shell(check_cmd, adb_path=adb_path)
        status = res.stdout.strip()
        
        if status == "NONE":
            return {
                "success": True,
                "error": ""
            }
            
        if status == "DIR":
            # Check if empty
            empty_check = f"ls -A {shlex.quote(target_path)}"
            empty_res = _run_shell(empty_check, adb_path=adb_path)
            is_empty = (empty_res.stdout.strip() == "")
            
            if not is_empty and not recursive:
                return {
                    "success": False,
                    "error": f"Directory is not empty and recursive deletion was not requested: {target_path}"
                }
                
            rm_cmd = f"rm -rf {shlex.quote(target_path)}"
        else:
            rm_cmd = f"rm -f {shlex.quote(target_path)}"
            
        rm_res = _run_shell(rm_cmd, adb_path=adb_path)
        if rm_res.returncode != 0:
            return {
                "success": False,
                "error": f"Failed to delete path: {rm_res.stderr.strip()}"
            }
            
        # Notify MediaStore of deletion
        _scan_media_store(target_path, adb_path=adb_path)
        
        return {
            "success": True,
            "error": ""
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def list_directory(directory_path: str, adb_path: str = "adb") -> dict:
    try:
        target_path = _validate_path(directory_path)
        
        # Verify directory exists
        check_cmd = f"[ -d {shlex.quote(target_path)} ] && echo 'YES' || echo 'NO'"
        res = _run_shell(check_cmd, adb_path=adb_path)
        if res.stdout.strip() != "YES":
            return {
                "items": [],
                "success": False,
                "error": f"Directory does not exist or is not a directory: {target_path}"
            }
            
        # List items with metadata using stat
        # %n: name, %F: type, %s: size, %Y: modification time in seconds
        # We use a custom delimiter to parse safely
        stat_cmd = f"find {shlex.quote(target_path)} -maxdepth 1 -mindepth 1 -exec stat -c '%n||%F||%s||%Y' {{}} +"
        stat_res = _run_shell(stat_cmd, adb_path=adb_path)
        
        items = []
        if stat_res.returncode == 0 and stat_res.stdout.strip():
            for line in stat_res.stdout.strip().split('\n'):
                if not line:
                    continue
                parts = line.split("||")
                if len(parts) < 4:
                    continue
                item_path = parts[0]
                item_type = parts[1]
                try:
                    size = int(parts[2])
                except ValueError:
                    size = 0
                try:
                    modified_time = int(parts[3]) * 1000  # Convert to milliseconds
                except ValueError:
                    modified_time = 0
                    
                is_dir = "directory" in item_type.lower()
                name = os.path.basename(item_path)
                
                items.append({
                    "name": name,
                    "path": item_path,
                    "is_directory": is_dir,
                    "size": 0 if is_dir else size,
                    "modified_time": modified_time
                })
                
        return {
            "items": items,
            "success": True,
            "error": ""
        }
    except Exception as e:
        return {
            "items": [],
            "success": False,
            "error": str(e)
        }

def move_or_rename_path(source_path: str, destination_path: str, overwrite: bool, adb_path: str = "adb") -> dict:
    try:
        src = _validate_path(source_path)
        dst = _validate_path(destination_path)
        
        if src == dst:
            return {
                "success": True,
                "destination_path": dst,
                "error": ""
            }
            
        # Check if source exists
        src_check = f"[ -e {shlex.quote(src)} ] && ([ -d {shlex.quote(src)} ] && echo 'DIR' || echo 'FILE') || echo 'NONE'"
        src_status = _run_shell(src_check, adb_path=adb_path).stdout.strip()
        if src_status == "NONE":
            return {
                "success": False,
                "destination_path": dst,
                "error": f"Source path does not exist: {src}"
            }
            
        # Prevent moving a directory into itself
        if src_status == "DIR" and dst.startswith(src + "/"):
            return {
                "success": False,
                "destination_path": dst,
                "error": f"Cannot move directory '{src}' into itself '{dst}'"
            }
            
        # Check destination
        dst_check = f"[ -e {shlex.quote(dst)} ] && ([ -d {shlex.quote(dst)} ] && echo 'DIR' || echo 'FILE') || echo 'NONE'"
        dst_status = _run_shell(dst_check, adb_path=adb_path).stdout.strip()
        
        if dst_status != "NONE":
            if not overwrite:
                return {
                    "success": False,
                    "destination_path": dst,
                    "error": f"Destination already exists: {dst}"
                }
            else:
                if dst_status == "DIR":
                    return {
                        "success": False,
                        "destination_path": dst,
                        "error": f"Cannot overwrite an existing directory: {dst}"
                    }
                    
        # Ensure parent directory of destination exists
        dst_parent = os.path.dirname(dst)
        mkdir_cmd = f"mkdir -p {shlex.quote(dst_parent)}"
        _run_shell(mkdir_cmd, adb_path=adb_path)
        
        # Perform move
        mv_cmd = f"mv -f {shlex.quote(src)} {shlex.quote(dst)}"
        mv_res = _run_shell(mv_cmd, adb_path=adb_path)
        if mv_res.returncode != 0:
            return {
                "success": False,
                "destination_path": dst,
                "error": f"Move operation failed: {mv_res.stderr.strip()}"
            }
            
        # Update MediaStore index for both source and destination
        _scan_media_store(src, adb_path=adb_path)
        _scan_media_store(dst, adb_path=adb_path)
        
        return {
            "success": True,
            "destination_path": dst,
            "error": ""
        }
    except Exception as e:
        return {
            "success": False,
            "destination_path": destination_path,
            "error": str(e)
        }

def read_file_text(file_path: str, adb_path: str = "adb") -> dict:
    try:
        target_path = _validate_path(file_path)
        
        # Check existence and type
        check_cmd = f"[ -e {shlex.quote(target_path)} ] && ([ -d {shlex.quote(target_path)} ] && echo 'DIR' || echo 'FILE') || echo 'NONE'"
        status = _run_shell(check_cmd, adb_path=adb_path).stdout.strip()
        
        if status == "NONE":
            return {
                "content": "",
                "size": 0,
                "success": False,
                "error": f"File does not exist: {target_path}"
            }
        elif status == "DIR":
            return {
                "content": "",
                "size": 0,
                "success": False,
                "error": f"Path is a directory, not a file: {target_path}"
            }
            
        # Get file size
        size_cmd = f"stat -c '%s' {shlex.quote(target_path)}"
        size_res = _run_shell(size_cmd, adb_path=adb_path)
        try:
            size = int(size_res.stdout.strip())
        except ValueError:
            size = 0
            
        # Read content
        cat_cmd = f"cat {shlex.quote(target_path)}"
        cat_res = _run_shell(cat_cmd, adb_path=adb_path)
        if cat_res.returncode != 0:
            return {
                "content": "",
                "size": size,
                "success": False,
                "error": f"Failed to read file: {cat_res.stderr.strip()}"
            }
            
        return {
            "content": cat_res.stdout,
            "size": size,
            "success": True,
            "error": ""
        }
    except Exception as e:
        return {
            "content": "",
            "size": 0,
            "success": False,
            "error": str(e)
        }

def write_file_text(file_path: str, content: str, overwrite: bool, adb_path: str = "adb") -> dict:
    try:
        target_path = _validate_path(file_path)
        
        # Check existence and type
        check_cmd = f"[ -e {shlex.quote(target_path)} ] && ([ -d {shlex.quote(target_path)} ] && echo 'DIR' || echo 'FILE') || echo 'NONE'"
        status = _run_shell(check_cmd, adb_path=adb_path).stdout.strip()
        
        if status == "DIR":
            return {
                "success": False,
                "path": target_path,
                "size": 0,
                "error": f"A directory already exists at the target path: {target_path}"
            }
        elif status == "FILE" and not overwrite:
            return {
                "success": False,
                "path": target_path,
                "size": 0,
                "error": f"File already exists and overwrite is disabled: {target_path}"
            }
            
        # Ensure parent directory exists
        parent_dir = os.path.dirname(target_path)
        mkdir_cmd = f"mkdir -p {shlex.quote(parent_dir)}"
        _run_shell(mkdir_cmd, adb_path=adb_path)
        
        # Write content using a temporary file and pushing it, or directly via shell if small.
        # To handle arbitrary text safely, we write to a local temp file and push it via adb.
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as temp_file:
            temp_file.write(content)
            temp_file_path = temp_file.name
            
        try:
            # Push to a temporary location on device first, then move to target to ensure correct permissions
            device_temp = f"/data/local/tmp/temp_write_{os.path.basename(temp_file_path)}"
            push_res = _run_adb(["push", temp_file_path, device_temp], adb_path=adb_path)
            if push_res.returncode != 0:
                return {
                    "success": False,
                    "path": target_path,
                    "size": 0,
                    "error": f"Failed to push content to device: {push_res.stderr.strip()}"
                }
                
            # Move to final destination
            mv_cmd = f"mv -f {shlex.quote(device_temp)} {shlex.quote(target_path)}"
            mv_res = _run_shell(mv_cmd, adb_path=adb_path)
            if mv_res.returncode != 0:
                # Cleanup device temp if mv failed
                _run_shell(f"rm -f {shlex.quote(device_temp)}", adb_path=adb_path)
                return {
                    "success": False,
                    "path": target_path,
                    "size": 0,
                    "error": f"Failed to write file to destination: {mv_res.stderr.strip()}"
                }
        finally:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                
        # Get final size
        size_cmd = f"stat -c '%s' {shlex.quote(target_path)}"
        size_res = _run_shell(size_cmd, adb_path=adb_path)
        try:
            size = int(size_res.stdout.strip())
        except ValueError:
            size = len(content.encode('utf-8'))
            
        # Trigger MediaStore scan
        _scan_media_store(target_path, adb_path=adb_path)
        
        return {
            "success": True,
            "path": target_path,
            "size": size,
            "error": ""
        }
    except Exception as e:
        return {
            "success": False,
            "path": file_path,
            "size": 0,
            "error": str(e)
        }

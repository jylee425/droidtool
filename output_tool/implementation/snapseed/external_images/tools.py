import os
import subprocess
import shlex
import json

def _run_adb_cmd(cmd, adb_path="adb"):
    """Helper to run an ADB shell command and return stdout, stderr, and return code."""
    serial = os.environ.get("ANDROID_SERIAL")
    base_cmd = [adb_path]
    if serial:
        base_cmd.extend(["-s", serial])
    base_cmd.append("shell")
    
    # We pass the command as a single string to adb shell
    full_cmd = base_cmd + [cmd]
    try:
        res = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        return res.stdout, res.stderr, res.returncode
    except subprocess.TimeoutExpired:
        return "", "ADB command timed out", -1
    except Exception as e:
        return "", str(e), -1

def list_exported_images(directory_path: str = "/sdcard/Pictures", adb_path: str = "adb") -> dict:
    """
    Lists exported or saved Snapseed images from the specified shared storage directory.
    
    Args:
        directory_path: The directory path to scan. Defaults to '/sdcard/Pictures'.
        adb_path: Path to the adb executable.
        
    Returns:
        A dictionary containing 'success', 'error', and 'images' list.
    """
    if not directory_path:
        directory_path = "/sdcard/Pictures"
        
    # Normalize directory path to avoid trailing slash issues
    directory_path = directory_path.rstrip('/')
    
    # Verify directory existence and containment
    # We use a safe shell-quoted check
    quoted_dir = shlex.quote(directory_path)
    
    # Check if directory exists and is a directory
    check_cmd = f"[ -d {quoted_dir} ] && echo 'EXISTS' || echo 'NOT_EXISTS'"
    stdout, stderr, rc = _run_adb_cmd(check_cmd, adb_path)
    if rc != 0 or "EXISTS" not in stdout:
        # Return empty list if directory does not exist as per implementation notes
        return {
            "success": True,
            "error": "",
            "images": []
        }
        
    # Find files in the directory. We filter for common image extensions.
    # We use find to get path, size, and modification time.
    # %p: path, %f: filename, %T@: modification time in epoch seconds, %s: size in bytes
    find_cmd = f"find {quoted_dir} -maxdepth 1 -type f \\( -name '*.jpg' -o -name '*.jpeg' -o -name '*.png' -o -name '*.webp' \\) -printf '%p\\t%f\\t%T@\\t%s\\n'"
    stdout, stderr, rc = _run_adb_cmd(find_cmd, adb_path)
    
    if rc != 0:
        # If find fails or is not supported, fallback to a basic ls and stat loop
        # but usually find is available on modern Android shells.
        return {
            "success": False,
            "error": f"Failed to list directory contents: {stderr.strip()}",
            "images": []
        }
        
    images = []
    lines = stdout.strip().split('\n')
    for line in lines:
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) < 4:
            continue
            
        path, filename, mtime_epoch_str, size_str = parts
        
        # Parse extension
        _, ext = os.path.splitext(filename)
        ext = ext.lstrip('.').lower()
        
        # Parse size
        try:
            size = int(size_str)
        except ValueError:
            size = 0
            
        # Parse modified time (convert epoch seconds to milliseconds)
        try:
            # mtime_epoch_str might contain fractional seconds, e.g., 1609459200.0000000000
            mtime_ms = int(float(mtime_epoch_str) * 1000)
        except ValueError:
            mtime_ms = 0
            
        images.append({
            "path": path,
            "filename": filename,
            "modified_time": mtime_ms,
            "size": size,
            "extension": ext
        })
        
    return {
        "success": True,
        "error": "",
        "images": images
    }

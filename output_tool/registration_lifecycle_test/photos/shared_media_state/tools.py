import os
import subprocess
import json
import shlex
import re

# Helper to run ADB commands
def _run_adb(args, adb_path="adb"):
    cmd = [adb_path]
    serial = os.environ.get("ANDROID_SERIAL")
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result

# Helper to validate path containment and prevent directory traversal
def _validate_path(path):
    # Resolve relative components locally to check for traversal
    normalized = os.path.normpath(path)
    if ".." in normalized.split(os.sep):
        raise ValueError(f"Directory traversal detected in path: {path}")
    
    # Must reside within permitted shared storage roots
    allowed_roots = ["/sdcard", "/storage/emulated/0"]
    is_allowed = False
    for root in allowed_roots:
        if normalized.startswith(root):
            is_allowed = True
            break
    if not is_allowed:
        raise ValueError(f"Path {path} is outside permitted shared storage roots.")
    return normalized

# Helper to query MediaStore via content provider shell command
def _query_mediastore(adb_path, projection, selection=None, selection_args=None):
    uri = "content://media/external/images/media"
    cmd = ["shell", "content", "query", "--uri", uri]
    if projection:
        cmd.extend(["--projection", ":".join(projection)])
    if selection:
        cmd.extend(["--where", selection])
    # Note: content query tool doesn't always support complex selection args cleanly, 
    # so we format selection directly or rely on simple where clauses.
    
    res = _run_adb(cmd, adb_path)
    if res.returncode != 0:
        # Try fallback to external/file if images/media fails
        uri = "content://media/external/file"
        cmd[4] = uri
        res = _run_adb(cmd, adb_path)
        if res.returncode != 0:
            return []
            
    # Parse content query output
    # Format is typically: Row: 0 _id=1, _data=/sdcard/DCIM/photo.jpg, ...
    rows = []
    current_row = {}
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("Row:"):
            if current_row:
                rows.append(current_row)
                current_row = {}
            # Extract key-value pairs from the row line
            # Example: Row: 0 _id=1, _data=/sdcard/photo.jpg
            parts = line.split(" ", 2)
            if len(parts) > 2:
                kv_string = parts[2]
                # Split by comma, but handle potential commas inside values if simple
                # A robust regex for key=value pairs
                pairs = re.findall(r'([^\s=]+)=([^,]*)(?:,|$)', kv_string)
                for k, v in pairs:
                    current_row[k.strip()] = v.strip()
        else:
            # Continuation of previous row or alternative format
            pairs = re.findall(r'([^\s=]+)=([^,]*)(?:,|$)', line)
            for k, v in pairs:
                current_row[k.strip()] = v.strip()
    if current_row:
        rows.append(current_row)
    return rows

# Helper to trigger MediaStore scan for a file
def _scan_file(file_path, adb_path):
    # Use am broadcast to trigger media scanner
    cmd = [
        "shell", "am", "broadcast", 
        "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", 
        "-d", f"file://{file_path}"
    ]
    _run_adb(cmd, adb_path)

def list_media_files(folder_path: str, bucket_name: str = None, adb_path: str = "adb") -> dict:
    try:
        folder_path = _validate_path(folder_path)
    except ValueError as e:
        return {"media_files": [], "success": False, "error": str(e)}

    # 1. List files directly from filesystem using adb shell ls
    # We want to get file path, size, and modification time
    # Use stat to get precise details: %n (name), %s (size), %Y (mtime)
    shell_cmd = f"find {shlex.quote(folder_path)} -maxdepth 1 -type f"
    res = _run_adb(["shell", shell_cmd], adb_path)
    if res.returncode != 0:
        return {"media_files": [], "success": False, "error": f"Failed to list directory: {res.stderr.strip()}"}

    fs_files = []
    for line in res.stdout.splitlines():
        path = line.strip()
        if path:
            fs_files.append(path)

    # 2. Query MediaStore for indexed entries
    projection = ["_id", "_data", "_display_name", "mime_type", "_size", "date_modified", "bucket_id", "bucket_display_name"]
    # Filter by folder path prefix in MediaStore
    selection = f"_data LIKE '{folder_path}/%'"
    if bucket_name:
        selection += f" AND bucket_display_name = '{bucket_name}'"
        
    db_rows = _query_mediastore(adb_path, projection, selection)
    db_by_path = {row.get("_data"): row for row in db_rows if row.get("_data")}

    # 3. Merge filesystem files with MediaStore metadata
    merged_files = []
    for path in fs_files:
        display_name = os.path.basename(path)
        db_entry = db_by_path.get(path, {})
        
        # Get filesystem stats as fallback
        stat_res = _run_adb(["shell", f"stat -c '%s %Y' {shlex.quote(path)}"], adb_path)
        fs_size = 0
        fs_mtime = 0
        if stat_res.returncode == 0:
            parts = stat_res.stdout.strip().split()
            if len(parts) == 2:
                try:
                    fs_size = int(parts[0])
                    fs_mtime = int(parts[1])
                except ValueError:
                    pass

        # Build unified record
        record = {
            "file_path": path,
            "display_name": db_entry.get("_display_name", display_name),
            "mime_type": db_entry.get("mime_type", ""),
            "size_bytes": int(db_entry.get("_size")) if db_entry.get("_size") else fs_size,
            "modified_time": int(db_entry.get("date_modified")) if db_entry.get("date_modified") else fs_mtime,
        }
        
        if "_id" in db_entry:
            try:
                record["id"] = int(db_entry["_id"])
            except ValueError:
                pass
        if "bucket_id" in db_entry:
            try:
                record["bucket_id"] = int(db_entry["bucket_id"])
            except ValueError:
                pass
        if "bucket_display_name" in db_entry:
            record["bucket_name"] = db_entry["bucket_display_name"]

        merged_files.append(record)

    # Sort by modified_time descending
    merged_files.sort(key=lambda x: x.get("modified_time", 0), reverse=True)

    return {
        "media_files": merged_files,
        "success": True,
        "error": ""
    }

def move_media_file(source_path: str, destination_path: str, adb_path: str = "adb") -> dict:
    try:
        source_path = _validate_path(source_path)
        destination_path = _validate_path(destination_path)
    except ValueError as e:
        return {"success": False, "destination_path": destination_path, "error": str(e)}

    # Verify source exists
    check_src = _run_adb(["shell", f"[ -f {shlex.quote(source_path)} ]"], adb_path)
    if check_src.returncode != 0:
        return {"success": False, "destination_path": destination_path, "error": f"Source file does not exist: {source_path}"}

    # Verify destination does not exist (collision check)
    check_dest = _run_adb(["shell", f"[ -f {shlex.quote(destination_path)} ]"], adb_path)
    if check_dest.returncode == 0:
        return {"success": False, "destination_path": destination_path, "error": f"Destination file already exists: {destination_path}"}

    # Ensure destination directory exists
    dest_dir = os.path.dirname(destination_path)
    _run_adb(["shell", f"mkdir -p {shlex.quote(dest_dir)}"], adb_path)

    # Perform atomic filesystem move
    move_res = _run_adb(["shell", f"mv {shlex.quote(source_path)} {shlex.quote(destination_path)}"], adb_path)
    if move_res.returncode != 0:
        return {"success": False, "destination_path": destination_path, "error": f"Filesystem move failed: {move_res.stderr.strip()}"}

    # Update MediaStore: delete old index entry and scan new one to trigger indexing
    _run_adb([
        "shell", "content", "delete", 
        "--uri", "content://media/external/images/media", 
        "--where", f"_data={shlex.quote(source_path)}"
    ], adb_path)
    
    _scan_file(destination_path, adb_path)

    return {
        "success": True,
        "destination_path": destination_path,
        "error": ""
    }

def delete_media_file(file_path: str, adb_path: str = "adb") -> dict:
    try:
        file_path = _validate_path(file_path)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    # Verify file exists
    check_file = _run_adb(["shell", f"[ -f {shlex.quote(file_path)} ]"], adb_path)
    if check_file.returncode != 0:
        return {"success": False, "error": f"File does not exist: {file_path}"}

    # Delete from filesystem
    rm_res = _run_adb(["shell", f"rm {shlex.quote(file_path)}"], adb_path)
    if rm_res.returncode != 0:
        return {"success": False, "error": f"Failed to delete file from filesystem: {rm_res.stderr.strip()}"}

    # Delete from MediaStore
    _run_adb([
        "shell", "content", "delete", 
        "--uri", "content://media/external/images/media", 
        "--where", f"_data={shlex.quote(file_path)}"
    ], adb_path)

    return {
        "success": True,
        "error": ""
    }

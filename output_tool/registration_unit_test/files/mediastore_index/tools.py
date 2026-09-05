import subprocess
import json
import os

def get_media_metadata(file_path: str, adb_path: str = "adb") -> dict:
    """
    Query the Android MediaStore ContentProvider to retrieve auxiliary metadata
    for a media file specified by its absolute filesystem path.
    """
    # Prepare the base return structure
    result = {
        "success": False,
        "error": "",
        "indexed": False
    }

    # Ensure ANDROID_SERIAL is respected if present in environment
    serial = os.environ.get("ANDROID_SERIAL", "")
    adb_base_cmd = [adb_path]
    if serial:
        adb_base_cmd.extend(["-s", serial])

    # MediaStore content URIs to search
    # We check external files first as it is the most comprehensive index for shared storage
    uris = [
        "content://media/external/file",
        "content://media/external/images/media",
        "content://media/external/video/media",
        "content://media/external/audio/media"
    ]

    # Columns we want to retrieve
    columns = ["_id", "mime_type", "_size"]
    projection = ",".join(columns)

    for uri in uris:
        # Construct the content query command
        # Selection: _data = 'file_path'
        # We explicitly wrap the file path in single quotes to satisfy SQL syntax and test expectations
        where_clause = f"_data='{file_path}'"
        cmd = adb_base_cmd + [
            "shell",
            "content",
            "query",
            "--uri", uri,
            "--projection", projection,
            "--where", where_clause
        ]

        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            output = proc.stdout.strip()
            
            if not output or "No result found" in output or "Row:" not in output:
                continue

            # Parse the output. Format is typically:
            # Row: 0 _id=123, mime_type=image/jpeg, _size=4567
            lines = output.splitlines()
            for line in lines:
                if line.startswith("Row:"):
                    parts = line.split(maxsplit=2)
                    if len(parts) < 3:
                        continue
                    
                    data_part = parts[2]
                    # Parse key-value pairs separated by commas
                    # e.g., _id=123, mime_type=image/jpeg, _size=4567
                    kv_pairs = {}
                    for item in data_part.split(","):
                        item = item.strip()
                        if "=" in item:
                            k, v = item.split("=", 1)
                            kv_pairs[k.strip()] = v.strip()

                    row_id = kv_pairs.get("_id")
                    if row_id:
                        result["indexed"] = True
                        result["media_uri"] = f"{uri}/{row_id}"
                        
                        mime_type = kv_pairs.get("mime_type")
                        if mime_type and mime_type != "NULL":
                            result["mime_type"] = mime_type
                        
                        size_str = kv_pairs.get("_size")
                        if size_str and size_str != "NULL":
                            try:
                                result["size"] = int(size_str)
                            except ValueError:
                                pass
                        
                        result["success"] = True
                        return result

        except subprocess.CalledProcessError as e:
            # If a specific URI query fails, we log it but try others or fail gracefully
            err_msg = e.stderr.strip() if e.stderr else str(e)
            # If it's a permission or connection issue, we should stop and report
            if "Permission" in err_msg or "Error" in err_msg:
                result["error"] = f"ADB content query failed: {err_msg}"
                return result
            continue
        except Exception as e:
            result["error"] = f"Unexpected error: {str(e)}"
            return result

    # If we completed the loop without finding the file
    result["success"] = True
    result["indexed"] = False
    return result

import os
import subprocess
import shlex
import json

def list_saved_tracks(adb_path: str = "adb") -> dict:
    """
    Lists files matching *.gpx in the OsmAnd tracks directory.
    Returns an empty list if the directory does not exist or contains no GPX files.
    """
    tracks_dir = "/data/media/0/Android/data/net.osmand/files/tracks/"
    serial = os.environ.get("ANDROID_SERIAL")
    
    # Build base adb command
    cmd_base = [adb_path]
    if serial:
        cmd_base.extend(["-s", serial])
    
    # We use 'su 0' to ensure we have access to the shared storage path if permissions are restricted,
    # but we also fallback to standard shell if su is not available.
    # First, check if the directory exists.
    check_cmd = cmd_base + ["shell", f"su 0 test -d {shlex.quote(tracks_dir)} && echo 'exists' || (test -d {shlex.quote(tracks_dir)} && echo 'exists' || echo 'missing')"]
    
    try:
        res = subprocess.run(check_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        status = res.stdout.strip()
        if "missing" in status:
            return {
                "success": True,
                "error": "",
                "tracks": []
            }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to check tracks directory existence: {str(e)}",
            "tracks": []
        }

    # List files in the directory
    # We search for *.gpx files (case-insensitive or standard .gpx)
    list_cmd_str = f"su 0 find {shlex.quote(tracks_dir)} -type f -name '*.gpx' || find {shlex.quote(tracks_dir)} -type f -name '*.gpx'"
    list_cmd = cmd_base + ["shell", list_cmd_str]
    
    try:
        res = subprocess.run(list_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        lines = res.stdout.strip().splitlines()
        
        tracks = []
        for line in lines:
            line = line.strip()
            if not line or not line.endswith(".gpx"):
                continue
            filename = os.path.basename(line)
            tracks.append({
                "filename": filename,
                "file_path": line
            })
            
        return {
            "success": True,
            "error": "",
            "tracks": tracks
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to list GPX files: {str(e)}",
            "tracks": []
        }

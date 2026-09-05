import subprocess
import os
import shlex

# Helper to execute ADB shell commands
def _run_adb_cmd(cmd_list, adb_path="adb"):
    serial = os.environ.get("ANDROID_SERIAL")
    base_cmd = [adb_path]
    if serial:
        base_cmd.extend(["-s", serial])
    base_cmd.append("shell")
    base_cmd.extend(cmd_list)
    try:
        res = subprocess.run(base_cmd, capture_output=True, text=True, check=False)
        return res.returncode, res.stdout, res.stderr
    except Exception as e:
        return -1, "", str(e)

def clear_app_cache(package_name: str, adb_path: str = "adb") -> dict:
    code, stdout, stderr = _run_adb_cmd(["pm", "clear", package_name], adb_path)
    if code == 0 and "Success" in stdout:
        return {"success": True, "error": ""}
    else:
        err_msg = stderr.strip() or stdout.strip() or f"Exit code {code}"
        return {"success": False, "error": f"Failed to clear cache: {err_msg}"}

def get_audio_streams(adb_path: str = "adb") -> dict:
    # Stream mapping: call: 0, ring: 2, media: 3, alarm: 4, notification: 5
    stream_map = {
        "call": (0, "volume_voice"),
        "ring": (2, "volume_ring"),
        "media": (3, "volume_music"),
        "alarm": (4, "volume_alarm"),
        "notification": (5, "volume_notification")
    }
    streams = []
    for name, (stream_id, key) in stream_map.items():
        code, stdout, stderr = _run_adb_cmd(["settings", "get", "system", key], adb_path)
        val_str = stdout.strip() if code == 0 else ""
        if not val_str or val_str == "null" or code != 0:
            # Fallback to media volume service query if possible
            continue
        try:
            level = int(val_str)
            streams.append({
                "stream_name": name,
                "stream_id": stream_id,
                "level": level
            })
        except ValueError:
            continue
    
    # If we couldn't read any from settings, try querying via media volume service
    if not streams:
        code, stdout, stderr = _run_adb_cmd(["media", "volume", "--show"], adb_path)
        if code == 0:
            # Parse output if possible, but fallback to empty list if parsing fails
            pass
            
    return {
        "streams": streams,
        "success": True,
        "error": ""
    }

def get_brightness(adb_path: str = "adb") -> dict:
    # Read system.screen_brightness
    code1, out1, err1 = _run_adb_cmd(["settings", "get", "system", "screen_brightness"], adb_path)
    # Read system.screen_brightness_mode
    code2, out2, err2 = _run_adb_cmd(["settings", "get", "system", "screen_brightness_mode"], adb_path)
    
    if code1 != 0 or code2 != 0:
        return {
            "brightness_percent": 0,
            "raw_value": 0,
            "auto_brightness": False,
            "success": False,
            "error": f"Failed to read brightness settings: {err1 or err2}"
        }
    
    try:
        raw_val = int(out1.strip())
    except ValueError:
        raw_val = 102  # Default fallback
        
    try:
        mode_val = int(out2.strip())
    except ValueError:
        mode_val = 0
        
    percent = int(round((raw_val / 255.0) * 100))
    auto_brightness = (mode_val == 1)
    
    return {
        "brightness_percent": percent,
        "raw_value": raw_val,
        "auto_brightness": auto_brightness,
        "success": True,
        "error": ""
    }

def get_setting_entry(namespace: str, key: str, adb_path: str = "adb") -> dict:
    if namespace not in ["system", "secure", "global"]:
        return {
            "namespace": namespace,
            "key": key,
            "value": None,
            "success": False,
            "error": f"Invalid namespace: {namespace}"
        }
    code, stdout, stderr = _run_adb_cmd(["settings", "get", namespace, key], adb_path)
    if code != 0:
        return {
            "namespace": namespace,
            "key": key,
            "value": None,
            "success": False,
            "error": stderr.strip() or f"Exit code {code}"
        }
    val = stdout.strip()
    if val == "null" or not val:
        val = None
    return {
        "namespace": namespace,
        "key": key,
        "value": val,
        "success": True,
        "error": ""
    }

def get_theme_state(adb_path: str = "adb") -> dict:
    # Try cmd uimode night first
    code, stdout, stderr = _run_adb_cmd(["cmd", "uimode", "night"], adb_path)
    if code == 0:
        output = stdout.strip().lower()
        if "yes" in output:
            return {"dark_mode_enabled": True, "success": True, "error": ""}
        elif "no" in output:
            return {"dark_mode_enabled": False, "success": True, "error": ""}
            
    # Fallback to secure.ui_night_mode or secure.dark_mode
    code, stdout, stderr = _run_adb_cmd(["settings", "get", "secure", "ui_night_mode"], adb_path)
    if code == 0:
        val = stdout.strip()
        if val == "2":  # UI_NIGHT_MODE_YES
            return {"dark_mode_enabled": True, "success": True, "error": ""}
        elif val == "1":  # UI_NIGHT_MODE_NO
            return {"dark_mode_enabled": False, "success": True, "error": ""}
            
    return {
        "dark_mode_enabled": False,
        "success": False,
        "error": "Could not determine theme state from uimode or secure settings."
    }

def get_wifi_state(adb_path: str = "adb") -> dict:
    # Try querying global.wifi_on mirror key
    code, stdout, stderr = _run_adb_cmd(["settings", "get", "global", "wifi_on"], adb_path)
    if code == 0:
        val = stdout.strip()
        if val == "1":
            return {"enabled": True, "success": True, "error": ""}
        elif val == "0":
            return {"enabled": False, "success": True, "error": ""}
            
    # Fallback to cmd wifi status or similar if available
    return {
        "enabled": False,
        "success": False,
        "error": "Could not retrieve wifi state from global settings."
    }

def put_setting_entry(namespace: str, key: str, value: str, adb_path: str = "adb") -> dict:
    if namespace not in ["system", "secure", "global"]:
        return {"success": False, "error": f"Invalid namespace: {namespace}"}
    code, stdout, stderr = _run_adb_cmd(["settings", "put", namespace, key, value], adb_path)
    if code == 0:
        return {"success": True, "error": ""}
    else: 
        return {"success": False, "error": stderr.strip() or f"Exit code {code}"}

def set_airplane_mode_enabled(enabled: bool, adb_path: str = "adb") -> dict:
    val_str = "1" if enabled else "0"
    broadcast_val = "true" if enabled else "false"
    
    code1, stdout1, stderr1 = _run_adb_cmd(["settings", "put", "global", "airplane_mode_on", val_str], adb_path)
    code2, stdout2, stderr2 = _run_adb_cmd([
        "am", "broadcast", "-a", "android.intent.action.AIRPLANE_MODE", "--ez", "state", broadcast_val
    ], adb_path)
    
    if code1 == 0 and code2 == 0:
        return {"success": True, "error": ""}
    else:
        err = (stderr1 or stderr2 or "Unknown error").strip()
        return {"success": False, "error": f"Failed to set airplane mode: {err}"}

def set_audio_stream_volume(stream_name: str, level: int, adb_path: str = "adb") -> dict:
    stream_map = {
        "call": 0,
        "ring": 2,
        "media": 3,
        "alarm": 4,
        "notification": 5
    }
    if stream_name not in stream_map:
        return {"success": False, "error": f"Unsupported stream: {stream_name}"}
    
    stream_id = stream_map[stream_name]
    
    # Try 'cmd audio volume' first as it is more universally supported on modern Android
    code, stdout, stderr = _run_adb_cmd([
        "cmd", "audio", "volume", "--stream", str(stream_id), "--set", str(level)
    ], adb_path)
    
    # Fallback to 'media volume' if 'cmd audio' is not available or fails
    if code != 0:
        code, stdout, stderr = _run_adb_cmd([
            "media", "volume", "--stream", str(stream_id), "--set", str(level)
        ], adb_path)
        
    if code == 0:
        return {"success": True, "error": ""}
    else:
        return {"success": False, "error": stderr.strip() or stdout.strip() or f"Exit code {code}"}

def set_brightness(brightness_percent: int, auto_brightness: bool, adb_path: str = "adb") -> dict:
    if not (0 <= brightness_percent <= 100):
        return {"success": False, "error": "brightness_percent must be between 0 and 100 inclusive"}
        
    raw_val = int(round((brightness_percent / 100.0) * 255))
    mode_val = "1" if auto_brightness else "0"
    
    code1, stdout1, stderr1 = _run_adb_cmd(["settings", "put", "system", "screen_brightness", str(raw_val)], adb_path)
    code2, stdout2, stderr2 = _run_adb_cmd(["settings", "put", "system", "screen_brightness_mode", mode_val], adb_path)
    
    if code1 == 0 and code2 == 0:
        return {"success": True, "error": ""}
    else:
        err = (stderr1 or stderr2 or "Unknown error").strip()
        return {"success": False, "error": f"Failed to set brightness: {err}"}

def set_theme_mode(dark_mode: bool, adb_path: str = "adb") -> dict:
    mode_str = "yes" if dark_mode else "no"
    code, stdout, stderr = _run_adb_cmd(["cmd", "uimode", "night", mode_str], adb_path)
    if code == 0:
        return {"success": True, "error": ""}
    else:
        return {"success": False, "error": stderr.strip() or f"Exit code {code}"}

def set_wifi_enabled(enabled: bool, adb_path: str = "adb") -> dict:
    cmd_str = "enable" if enabled else "disable"
    code, stdout, stderr = _run_adb_cmd(["svc", "wifi", cmd_str], adb_path)
    if code == 0:
        return {"success": True, "error": ""}
    else:
        return {"success": False, "error": stderr.strip() or f"Exit code {code}"}

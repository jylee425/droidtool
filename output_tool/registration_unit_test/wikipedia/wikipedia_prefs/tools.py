import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import json
import shlex

PREFS_PATH = "/data/data/org.wikipedia/shared_prefs/org.wikipedia_preferences.xml"
PACKAGE_NAME = "org.wikipedia"

def _run_adb_cmd(cmd, adb_path="adb"):
    serial = os.environ.get("ANDROID_SERIAL")
    base_cmd = [adb_path]
    if serial:
        base_cmd.extend(["-s", serial])
    full_cmd = base_cmd + cmd
    res = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res

def _pull_xml(adb_path="adb"):
    # Stage to a readable temporary device path
    temp_device_path = f"/data/local/tmp/org.wikipedia_preferences.xml"
    
    # Copy to temp path with root permissions
    cp_cmd = ["shell", "su", "0", "cp", PREFS_PATH, temp_device_path]
    res = _run_adb_cmd(cp_cmd, adb_path)
    if res.returncode != 0:
        # Try to check if file exists
        return None, f"Failed to copy preferences file to temp location: {res.stderr.strip()}"
    
    # Make readable
    chmod_cmd = ["shell", "su", "0", "chmod", "666", temp_device_path]
    _run_adb_cmd(chmod_cmd, adb_path)
    
    # Pull to local temp file
    local_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xml")
    local_temp.close()
    
    pull_cmd = ["pull", temp_device_path, local_temp.name]
    res = _run_adb_cmd(pull_cmd, adb_path)
    
    # Clean up temp device file
    _run_adb_cmd(["shell", "rm", temp_device_path], adb_path)
    
    if res.returncode != 0:
        try:
            os.unlink(local_temp.name)
        except Exception:
            pass
        return None, f"Failed to pull preferences file: {res.stderr.strip()}"
        
    return local_temp.name, ""

def _push_xml(local_path, adb_path="adb"):
    temp_device_path = f"/data/local/tmp/org.wikipedia_preferences.xml"
    
    # Push to temp device path
    push_cmd = ["push", local_path, temp_device_path]
    res = _run_adb_cmd(push_cmd, adb_path)
    if res.returncode != 0:
        return False, f"Failed to push preferences file to temp location: {res.stderr.strip()}"
        
    # Discover original metadata (uid, gid, mode, selinux context)
    stat_cmd = ["shell", "su", "0", "stat", "-c", "'%u %g %a'", PREFS_PATH]
    stat_res = _run_adb_cmd(stat_cmd, adb_path)
    
    selinux_cmd = ["shell", "su", "0", "ls", "-Z", PREFS_PATH]
    selinux_res = _run_adb_cmd(selinux_cmd, adb_path)
    
    uid, gid, mode = "", "", ""
    if stat_res.returncode == 0 and stat_res.stdout.strip():
        parts = stat_res.stdout.strip().replace("'", "").split()
        if len(parts) == 3:
            uid, gid, mode = parts[0], parts[1], parts[2]
            
    secontext = ""
    if selinux_res.returncode == 0 and selinux_res.stdout.strip():
        parts = selinux_res.stdout.strip().split()
        if len(parts) > 0:
            secontext = parts[0]
            
    # Copy back to private path
    cp_cmd = ["shell", "su", "0", "cp", temp_device_path, PREFS_PATH]
    res = _run_adb_cmd(cp_cmd, adb_path)
    
    # Clean up temp device file
    _run_adb_cmd(["shell", "rm", temp_device_path], adb_path)
    
    if res.returncode != 0:
        return False, f"Failed to copy preferences file back to private path: {res.stderr.strip()}"
        
    # Restore metadata
    if uid and gid:
        _run_adb_cmd(["shell", "su", "0", "chown", f"{uid}:{gid}", PREFS_PATH], adb_path)
    if mode:
        _run_adb_cmd(["shell", "su", "0", "chmod", mode, PREFS_PATH], adb_path)
    if secontext and secontext != "?" and ":" in secontext:
        _run_adb_cmd(["shell", "su", "0", "chcon", secontext, PREFS_PATH], adb_path)
        
    return True, ""

def _parse_xml_to_dict(xml_path):
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception as e:
        return None, f"Failed to parse XML: {str(e)}"
        
    prefs = {}
    for child in root:
        name = child.attrib.get("name")
        if not name:
            continue
            
        if child.tag == "boolean":
            val = child.attrib.get("value")
            prefs[name] = val == "true"
        elif child.tag == "int":
            try:
                prefs[name] = int(child.attrib.get("value", 0))
            except ValueError:
                pass
        elif child.tag == "string":
            prefs[name] = child.text if child.text is not None else ""
            
    return prefs, ""

def get_wikipedia_settings(adb_path: str = "adb") -> dict:
    """
    Returns user-facing settings and feed customization preferences parsed structurally from org.wikipedia_preferences.xml.
    """
    local_path, err = _pull_xml(adb_path)
    if err:
        return {"success": False, "error": err}
        
    prefs, err = _parse_xml_to_dict(local_path)
    try:
        os.unlink(local_path)
    except Exception:
        pass
    if err:
        return {"success": False, "error": err}
        
    result = {"success": True, "error": ""}
    
    # Map fields
    bool_fields = [
        "showLinkPreviews",
        "collapseTables",
        "readingFocusModeEnabled",
        "matchSystemTheme",
        "imageDimming",
        "downloadOnlyOverWiFi"
    ]
    for f in bool_fields:
        if f in prefs:
            result[f] = prefs[f]
            
    if "textSizeMultiplier" in prefs:
        result["textSizeMultiplier"] = prefs["textSizeMultiplier"]
        
    str_fields = ["readingFontFamily", "appTheme", "imageDownloadQuality"]
    for f in str_fields:
        if f in prefs:
            result[f] = prefs[f]
            
    if "feedCardsEnabled" in prefs:
        try:
            decoded = json.loads(prefs["feedCardsEnabled"])
            if isinstance(decoded, list) and len(decoded) == 10 and all(isinstance(x, bool) for x in decoded):
                result["feedCardsEnabled"] = decoded
        except Exception:
            pass
            
    return result

def update_wikipedia_settings(
    showLinkPreviews: bool = None,
    collapseTables: bool = None,
    readingFocusModeEnabled: bool = None,
    matchSystemTheme: bool = None,
    imageDimming: bool = None,
    downloadOnlyOverWiFi: bool = None,
    textSizeMultiplier: int = None,
    readingFontFamily: str = None,
    appTheme: str = None,
    imageDownloadQuality: str = None,
    feedCardsEnabled: list = None,
    adb_path: str = "adb"
) -> dict:
    """
    Updates specified keys in the SharedPreferences XML structurally, preserving unrelated keys.
    """
    # Validate inputs
    if readingFontFamily is not None and readingFontFamily not in ["sans-serif", "serif"]:
        return {"success": False, "error": "readingFontFamily must be 'sans-serif' or 'serif'"}
    if appTheme is not None and appTheme not in ["Light", "Sepia", "Dark", "Black"]:
        return {"success": False, "error": "appTheme must be 'Light', 'Sepia', 'Dark', or 'Black'"}
    if imageDownloadQuality is not None and imageDownloadQuality not in ["low", "medium", "high"]:
        return {"success": False, "error": "imageDownloadQuality must be 'low', 'medium', or 'high'"}
    if feedCardsEnabled is not None:
        if not isinstance(feedCardsEnabled, list) or len(feedCardsEnabled) != 10 or not all(isinstance(x, bool) for x in feedCardsEnabled):
            return {"success": False, "error": "feedCardsEnabled must be a 10-element boolean array"}
            
    local_path, err = _pull_xml(adb_path)
    if err:
        return {"success": False, "error": err}
        
    try:
        tree = ET.parse(local_path)
        root = tree.getroot()
    except Exception as e:
        try:
            os.unlink(local_path)
        except Exception:
            pass
        return {"success": False, "error": f"Failed to parse XML: {str(e)}"}
        
    # Helper to set or update elements
    def set_pref_val(tag, name, val):
        for child in list(root):
            if child.attrib.get("name") == name:
                root.remove(child)
        elem = ET.Element(tag, {"name": name})
        if tag == "string":
            elem.text = str(val)
        else:
            elem.set("value", str(val).lower() if tag == "boolean" else str(val))
        root.append(elem)
        
    if showLinkPreviews is not None:
        set_pref_val("boolean", "showLinkPreviews", showLinkPreviews)
    if collapseTables is not None:
        set_pref_val("boolean", "collapseTables", collapseTables)
    if readingFocusModeEnabled is not None:
        set_pref_val("boolean", "readingFocusModeEnabled", readingFocusModeEnabled)
    if matchSystemTheme is not None:
        set_pref_val("boolean", "matchSystemTheme", matchSystemTheme)
    if imageDimming is not None:
        set_pref_val("boolean", "imageDimming", imageDimming)
    if downloadOnlyOverWiFi is not None:
        set_pref_val("boolean", "downloadOnlyOverWiFi", downloadOnlyOverWiFi)
    if textSizeMultiplier is not None:
        set_pref_val("int", "textSizeMultiplier", textSizeMultiplier)
    if readingFontFamily is not None:
        set_pref_val("string", "readingFontFamily", readingFontFamily)
    if appTheme is not None:
        set_pref_val("string", "appTheme", appTheme)
    if imageDownloadQuality is not None:
        set_pref_val("string", "imageDownloadQuality", imageDownloadQuality)
        
    if feedCardsEnabled is not None:
        encoded_val = json.dumps(feedCardsEnabled).replace(" ", "")
        set_pref_val("string", "feedCardsEnabled", encoded_val)
        # Remove legacy feed_state key if present
        for child in list(root):
            if child.attrib.get("name") == "feed_state":
                root.remove(child)
                
    # Write back to local temp file
    try:
        tree.write(local_path, encoding="utf-8", xml_declaration=True)
    except Exception as e:
        try:
            os.unlink(local_path)
        except Exception:
            pass
        return {"success": False, "error": f"Failed to write XML locally: {str(e)}"}
        
    # Quiesce the Wikipedia app process before replacing SharedPreferences XML
    _run_adb_cmd(["shell", "am", "force-stop", PACKAGE_NAME], adb_path)
    
    success, push_err = _push_xml(local_path, adb_path)
    
    # We do not unlink local_path here to allow test assertions and verification to read the updated file.
    # Standard OS temp directory cleanup will handle it, or the test suite tearDown will clean it up.
    
    if not success:
        return {"success": False, "error": push_err}
        
    return {"success": True, "error": ""}

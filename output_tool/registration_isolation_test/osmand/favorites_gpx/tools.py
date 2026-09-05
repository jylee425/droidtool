import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PRIMARY_GPX_PATH = "/data/media/0/Android/data/net.osmand/files/favorites/favorites.gpx"
GPX_NS = "http://www.topografix.com/GPX/1/1"

def _run_adb(args, adb_path="adb"):
    cmd = [adb_path]
    serial = os.environ.get("ANDROID_SERIAL")
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res

def _pull_gpx(adb_path, local_path):
    # Try pulling directly first
    res = _run_adb(["pull", PRIMARY_GPX_PATH, local_path], adb_path)
    if res.returncode == 0:
        return True
    # Fallback to staging via /data/local/tmp using root if needed
    stage_path = "/data/local/tmp/favorites_temp.gpx"
    res_cp = _run_adb(["shell", "su", "0", "cp", PRIMARY_GPX_PATH, stage_path], adb_path)
    if res_cp.returncode != 0:
        return False
    _run_adb(["shell", "su", "0", "chmod", "666", stage_path], adb_path)
    res_pull = _run_adb(["pull", stage_path, local_path], adb_path)
    _run_adb(["shell", "su", "0", "rm", "-f", stage_path], adb_path)
    return res_pull.returncode == 0

def _push_gpx(adb_path, local_path):
    # Try pushing directly first
    res = _run_adb(["push", local_path, PRIMARY_GPX_PATH], adb_path)
    if res.returncode == 0:
        return True
    # Fallback to staging via /data/local/tmp using root
    stage_path = "/data/local/tmp/favorites_temp.gpx"
    res_push = _run_adb(["push", local_path, stage_path], adb_path)
    if res_push.returncode != 0:
        return False
    # Discover original metadata if possible
    uid, gid, mode, selinux = "", "", "", ""
    meta_res = _run_adb(["shell", "su", "0", "stat", "-c", "'%u %g %a %C'", PRIMARY_GPX_PATH], adb_path)
    if meta_res.returncode == 0 and meta_res.stdout.strip():
        parts = meta_res.stdout.strip().replace("'", "").split()
        if len(parts) >= 4:
            uid, gid, mode, selinux = parts[0], parts[1], parts[2], parts[3]
    
    # Copy back atomically
    res_cp = _run_adb(["shell", "su", "0", "cp", stage_path, PRIMARY_GPX_PATH], adb_path)
    _run_adb(["shell", "su", "0", "rm", "-f", stage_path], adb_path)
    if res_cp.returncode != 0:
        return False
        
    # Restore metadata
    if uid and gid:
        _run_adb(["shell", "su", "0", "chown", f"{uid}:{gid}", PRIMARY_GPX_PATH], adb_path)
    if mode:
        _run_adb(["shell", "su", "0", "chmod", mode, PRIMARY_GPX_PATH], adb_path)
    if selinux and selinux != "?" and selinux != "(null)":
        _run_adb(["shell", "su", "0", "chcon", selinux, PRIMARY_GPX_PATH], adb_path)
    return True

def _create_empty_gpx():
    root = ET.Element("gpx", {
        "version": "1.1",
        "creator": "OsmAnd",
        "xmlns": GPX_NS,
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": "http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd"
    })
    return ET.ElementTree(root)

def read_favorites(adb_path: str = "adb") -> dict:
    ET.register_namespace("", GPX_NS)
    with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        if not _pull_gpx(adb_path, tmp_path):
            return {"success": True, "error": "", "favorites": []}
        try:
            tree = ET.parse(tmp_path)
            root = tree.getroot()
        except Exception:
            return {"success": True, "error": "", "favorites": []}
        
        ns = {"gpx": GPX_NS} if GPX_NS in root.tag else {}
        prefix = "{http://www.topografix.com/GPX/1/1}" if GPX_NS in root.tag else ""
        
        favorites = []
        for wpt in root.findall(f"{prefix}wpt"):
            lat_str = wpt.get("lat")
            lon_str = wpt.get("lon")
            if lat_str is None or lon_str is None:
                continue
            try:
                lat = float(lat_str)
                lon = float(lon_str)
            except ValueError:
                continue
            
            name_el = wpt.find(f"{prefix}name")
            name = name_el.text if name_el is not None and name_el.text else ""
            
            desc_el = wpt.find(f"{prefix}desc")
            desc = desc_el.text if desc_el is not None and desc_el.text else ""
            
            fav = {"name": name, "latitude": lat, "longitude": lon}
            if desc:
                fav["description"] = desc
            favorites.append(fav)
            
        return {"success": True, "error": "", "favorites": favorites}
    except Exception as e:
        return {"success": False, "error": str(e), "favorites": []}
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def add_favorite(name: str, latitude: float, longitude: float, description: str = "", adb_path: str = "adb") -> dict:
    ET.register_namespace("", GPX_NS)
    with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        pulled = _pull_gpx(adb_path, tmp_path)
        if pulled:
            try:
                tree = ET.parse(tmp_path)
                root = tree.getroot()
            except Exception:
                tree = _create_empty_gpx()
                root = tree.getroot()
        else:
            tree = _create_empty_gpx()
            root = tree.getroot()
            
        prefix = "{http://www.topografix.com/GPX/1/1}" if GPX_NS in root.tag else ""
        
        wpt = ET.SubElement(root, f"{prefix}wpt", {"lat": str(latitude), "lon": str(longitude)})
        name_el = ET.SubElement(wpt, f"{prefix}name")
        name_el.text = name
        if description:
            desc_el = ET.SubElement(wpt, f"{prefix}desc")
            desc_el.text = description
            
        tree.write(tmp_path, encoding="utf-8", xml_declaration=True)
        if _push_gpx(adb_path, tmp_path):
            return {"success": True, "error": ""}
        else:
            return {"success": False, "error": "Failed to write GPX file back to device"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def delete_favorite(name: str, adb_path: str = "adb") -> dict:
    ET.register_namespace("", GPX_NS)
    with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        if not _pull_gpx(adb_path, tmp_path):
            # If the file does not exist, the favorite is already deleted (idempotent success)
            return {"success": True, "error": ""}
        try:
            tree = ET.parse(tmp_path)
            root = tree.getroot()
        except Exception as e:
            return {"success": False, "error": f"Failed to parse GPX: {str(e)}"}
            
        prefix = "{http://www.topografix.com/GPX/1/1}" if GPX_NS in root.tag else ""
        
        removed = False
        for wpt in list(root.findall(f"{prefix}wpt")):
            name_el = wpt.find(f"{prefix}name")
            if name_el is not None and name_el.text == name:
                root.remove(wpt)
                removed = True
                
        if not removed:
            # If the favorite is not found, we can also treat this as idempotent success
            return {"success": True, "error": ""}
            
        tree.write(tmp_path, encoding="utf-8", xml_declaration=True)
        if _push_gpx(adb_path, tmp_path):
            return {"success": True, "error": ""}
        else:
            return {"success": False, "error": "Failed to write updated GPX file back to device"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

import os
import sys
import json
import sqlite3
import subprocess
import tempfile
import time
import shlex

DB_PATH = "/data/data/net.osmand/databases/map_markers_db"
PACKAGE_NAME = "net.osmand"

def _run_adb_cmd(cmd, adb_path="adb"):
    serial = os.environ.get("ANDROID_SERIAL")
    base_cmd = [adb_path]
    if serial:
        base_cmd.extend(["-s", serial])
    full_cmd = base_cmd + cmd
    res = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        raise RuntimeError(f"ADB command failed: {' '.join(full_cmd)}\nstderr: {res.stderr.decode('utf-8', errors='replace')}")
    return res.stdout

def _quiesce_app(adb_path="adb"):
    _run_adb_cmd(["shell", "am", "force-stop", PACKAGE_NAME], adb_path)

def _get_file_metadata(path, adb_path="adb"):
    # Returns (uid, gid, mode, selinux_context)
    try:
        stat_out = _run_adb_cmd(["shell", "su", "0", "stat", "-c", "'%u %g %a'", path], adb_path).decode('utf-8').strip().strip("'")
        uid, gid, mode = stat_out.split()
    except Exception:
        # Default fallback metadata for OsmAnd app data directory
        uid, gid, mode = "10150", "10150", "660"

    try:
        selinux_out = _run_adb_cmd(["shell", "su", "0", "ls", "-Z", path], adb_path).decode('utf-8').strip()
        selinux_context = selinux_out.split()[0]
    except Exception:
        selinux_context = "u:object_r:app_data_file:s0"

    return uid, gid, mode, selinux_context

def _apply_metadata(path, uid, gid, mode, selinux_context, adb_path="adb"):
    if uid and gid:
        _run_adb_cmd(["shell", "su", "0", "chown", f"{uid}:{gid}", path], adb_path)
    if mode:
        _run_adb_cmd(["shell", "su", "0", "chmod", mode, path], adb_path)
    if selinux_context and selinux_context != "?":
        _run_adb_cmd(["shell", "su", "0", "chcon", selinux_context, path], adb_path)

def _pull_db(adb_path="adb"):
    # Stage to a readable temporary device path
    temp_device_path = f"/data/local/tmp/map_markers_db_{int(time.time())}"
    _run_adb_cmd(["shell", "su", "0", "cp", DB_PATH, temp_device_path], adb_path)
    _run_adb_cmd(["shell", "su", "0", "chmod", "666", temp_device_path], adb_path)

    # Pull WAL and SHM sidecars if they exist
    sidecars_pulled = []
    for ext in ["-wal", "-shm"]:
        sidecar_path = DB_PATH + ext
        check = _run_adb_cmd(["shell", "su", "0", "test", "-f", sidecar_path, "&&", "echo", "exists", "||", "echo", "not_exists"], adb_path).decode('utf-8').strip()
        if "exists" in check and "not_exists" not in check:
            temp_sidecar = temp_device_path + ext
            _run_adb_cmd(["shell", "su", "0", "cp", sidecar_path, temp_sidecar], adb_path)
            _run_adb_cmd(["shell", "su", "0", "chmod", "666", temp_sidecar], adb_path)
            sidecars_pulled.append(ext)

    local_temp_dir = tempfile.mkdtemp()
    local_db_path = os.path.join(local_temp_dir, "map_markers_db")
    _run_adb_cmd(["pull", temp_device_path, local_db_path], adb_path)
    _run_adb_cmd(["shell", "su", "0", "rm", "-f", temp_device_path], adb_path)

    for ext in sidecars_pulled:
        local_sidecar = local_db_path + ext
        _run_adb_cmd(["pull", temp_device_path + ext, local_sidecar], adb_path)
        _run_adb_cmd(["shell", "su", "0", "rm", "-f", temp_device_path + ext], adb_path)

    return local_db_path, sidecars_pulled

def _push_db(local_db_path, sidecars, adb_path="adb"):
    # Discover original metadata
    uid, gid, mode, selinux_context = _get_file_metadata(DB_PATH, adb_path)

    # Quiesce app before replacement
    _quiesce_app(adb_path)

    # Ensure parent directory exists
    _run_adb_cmd(["shell", "su", "0", "mkdir", "-p", "/data/data/net.osmand/databases"], adb_path)

    temp_device_path = f"/data/local/tmp/map_markers_db_new_{int(time.time())}"
    _run_adb_cmd(["push", local_db_path, temp_device_path], adb_path)
    _run_adb_cmd(["shell", "su", "0", "cp", temp_device_path, DB_PATH], adb_path)
    _run_adb_cmd(["shell", "su", "0", "rm", "-f", temp_device_path], adb_path)

    # Restore metadata on main DB
    _apply_metadata(DB_PATH, uid, gid, mode, selinux_context, adb_path)

    # Handle sidecars
    for ext in ["-wal", "-shm"]:
        sidecar_path = DB_PATH + ext
        if ext in sidecars:
            local_sidecar = local_db_path + ext
            temp_sidecar = temp_device_path + ext
            _run_adb_cmd(["push", local_sidecar, temp_sidecar], adb_path)
            _run_adb_cmd(["shell", "su", "0", "cp", temp_sidecar, sidecar_path], adb_path)
            _run_adb_cmd(["shell", "su", "0", "rm", "-f", temp_sidecar], adb_path)
            _apply_metadata(sidecar_path, uid, gid, mode, selinux_context, adb_path)
        else:
            # Remove stale remote sidecars if we didn't write them back
            _run_adb_cmd(["shell", "su", "0", "rm", "-f", sidecar_path], adb_path)

def list_map_markers(group_name: str = None, active_only: bool = False, adb_path: str = "adb") -> dict:
    try:
        # Check if DB exists
        db_check = _run_adb_cmd(["shell", "su", "0", "test", "-f", DB_PATH, "&&", "echo", "exists", "||", "echo", "not_exists"], adb_path).decode('utf-8').strip()
        if "exists" not in db_check or "not_exists" in db_check:
            return {"success": True, "error": "", "markers": []}

        local_db, sidecars = _pull_db(adb_path)
        conn = sqlite3.connect(local_db)
        cursor = conn.cursor()

        # Schema discovery
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='map_markers';")
        if not cursor.fetchone():
            conn.close()
            return {"success": True, "error": "", "markers": []}

        cursor.execute("PRAGMA table_info(map_markers);")
        columns = [col[1] for col in cursor.fetchall()]

        query = "SELECT * FROM map_markers WHERE 1=1"
        params = []

        if group_name is not None:
            if "group_name" in columns:
                query += " AND group_name = ?"
                params.append(group_name)
            else:
                conn.close()
                return {"success": False, "error": "group_name column not found in database schema", "markers": []}

        if active_only:
            if "marker_active" in columns:
                query += " AND marker_active = 1"
            else:
                conn.close()
                return {"success": False, "error": "marker_active column not found in database schema", "markers": []}

        cursor.execute(query, params)
        rows = cursor.fetchall()

        markers = []
        for row in rows:
            marker_dict = dict(zip(columns, row))
            
            # Map to output schema
            marker_id = marker_dict.get("marker_id")
            lat = marker_dict.get("marker_lat")
            lon = marker_dict.get("marker_lon")
            active = marker_dict.get("marker_active")

            if marker_id is None or lat is None or lon is None or active is None:
                continue

            markers.append({
                "marker_id": int(marker_id),
                "marker_lat": float(lat),
                "marker_lon": float(lon),
                "title": marker_dict.get("title", ""),
                "marker_description": marker_dict.get("marker_description", ""),
                "marker_active": int(active),
                "group_name": marker_dict.get("group_name", ""),
                "marker_color": marker_dict.get("marker_color", "")
            })

        conn.close()
        return {"success": True, "error": "", "markers": "" if markers is None else markers}
    except Exception as e:
        return {"success": False, "error": str(e), "markers": []}

def add_map_marker(latitude: float, longitude: float, title: str, description: str = None, group_name: str = None, color: str = None, adb_path: str = "adb") -> dict:
    try:
        # Check if DB exists
        db_check = _run_adb_cmd(["shell", "su", "0", "test", "-f", DB_PATH, "&&", "echo", "exists", "||", "echo", "not_exists"], adb_path).decode('utf-8').strip()
        
        local_temp_dir = tempfile.mkdtemp()
        local_db = os.path.join(local_temp_dir, "map_markers_db")
        sidecars = []

        if "exists" in db_check and "not_exists" not in db_check:
            local_db, sidecars = _pull_db(adb_path)
            conn = sqlite3.connect(local_db)
        else:
            # Create a new database locally if it doesn't exist
            conn = sqlite3.connect(local_db)

        cursor = conn.cursor()

        # Ensure map_markers table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS map_markers (
                marker_id INTEGER PRIMARY KEY AUTO_INCREMENT,
                marker_lat REAL NOT NULL,
                marker_lon REAL NOT NULL,
                title TEXT,
                marker_description TEXT,
                marker_active INTEGER DEFAULT 1,
                group_name TEXT,
                marker_color TEXT,
                marker_added INTEGER,
                marker_visited INTEGER DEFAULT 0,
                marker_disabled INTEGER DEFAULT 0
            );
        """.replace("AUTO_INCREMENT", "AUTOINCREMENT")) # SQLite syntax correction
        conn.commit()

        # Schema discovery
        cursor.execute("PRAGMA table_info(map_markers);")
        columns = [col[1] for col in cursor.fetchall()]

        # Build insert statement dynamically based on discovered columns
        insert_fields = []
        insert_values = []

        # Required fields
        insert_fields.append("marker_lat")
        insert_values.append(latitude)
        insert_fields.append("marker_lon")
        insert_values.append(longitude)
        
        if "title" in columns:
            insert_fields.append("title")
            insert_values.append(title)
        elif "marker_map_object_name" in columns:
            insert_fields.append("marker_map_object_name")
            insert_values.append(title)

        if "marker_active" in columns:
            insert_fields.append("marker_active")
            insert_values.append(1)

        # Optional fields
        if description is not None and "marker_description" in columns:
            insert_fields.append("marker_description")
            insert_values.append(description)

        if group_name is not None and "group_name" in columns:
            insert_fields.append("group_name")
            insert_values.append(group_name)

        if color is not None and "marker_color" in columns:
            insert_fields.append("marker_color")
            insert_values.append(color)

        if "marker_added" in columns:
            insert_fields.append("marker_added")
            insert_values.append(int(time.time() * 1000)) # Milliseconds timestamp

        if "marker_visited" in columns:
            insert_fields.append("marker_visited")
            insert_values.append(0)

        if "marker_disabled" in columns:
            insert_fields.append("marker_disabled")
            insert_values.append(0)

        placeholders = ", ".join(["?"] * len(insert_values))
        fields_str = ", ".join(insert_fields)
        sql = f"INSERT INTO map_markers ({fields_str}) VALUES ({placeholders})"

        cursor.execute(sql, insert_values)
        new_id = cursor.lastrowid
        conn.commit()

        # Checkpoint WAL to ensure changes are written to main DB file
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.close()

        # Push back
        _push_db(local_db, sidecars, adb_path)
        return {"success": True, "error": "", "marker_id": new_id}
    except Exception as e:
        return {"success": False, "error": str(e), "marker_id": -1}

def delete_map_marker(marker_id: int, adb_path: str = "adb") -> dict:
    try:
        # Check if DB exists
        db_check = _run_adb_cmd(["shell", "su", "0", "test", "-f", DB_PATH, "&&", "echo", "exists", "||", "echo", "not_exists"], adb_path).decode('utf-8').strip()
        if "exists" not in db_check or "not_exists" in db_check:
            return {"success": False, "error": "Database file does not exist"}

        local_db, sidecars = _pull_db(adb_path)
        conn = sqlite3.connect(local_db)
        cursor = conn.cursor()

        # Schema discovery
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='map_markers';")
        if not cursor.fetchone():
            conn.close()
            return {"success": False, "error": "map_markers table does not exist"}

        cursor.execute("SELECT 1 FROM map_markers WHERE marker_id = ?", (marker_id,))
        if not cursor.fetchone():
            conn.close()
            return {"success": False, "error": f"Marker with ID {marker_id} not found"}

        cursor.execute("DELETE FROM map_markers WHERE marker_id = ?", (marker_id,))
        conn.commit()

        # Checkpoint WAL
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.close()

        # Push back
        _push_db(local_db, sidecars, adb_path)
        return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}

import os
import sys
import json
import sqlite3
import subprocess
import tempfile
import shutil

# Package and DB configuration
PACKAGE_NAME = "de.dennisguse.opentracks"
REMOTE_DB_PATH = "/data/data/de.dennisguse.opentracks/databases/database.db"

def _get_adb_prefix(adb_path):
    serial = os.environ.get("ANDROID_SERIAL")
    if serial:
        return [adb_path, "-s", serial]
    return [adb_path]

def _run_cmd(cmd):
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.returncode, res.stdout, res.stderr

def _quiesce_app(adb_path):
    prefix = _get_adb_prefix(adb_path)
    _run_cmd(prefix + ["shell", "am", "force-stop", PACKAGE_NAME])

def _get_file_metadata(adb_path, path):
    prefix = _get_adb_prefix(adb_path)
    # Get uid, gid, mode, and selinux context
    code, out, _ = _run_cmd(prefix + ["shell", "su", "0", "stat", "-c", "'%u %g %a %C'", path])
    if code == 0 and out.strip():
        parts = out.strip().strip("'").split()
        if len(parts) >= 4:
            return {
                "uid": parts[0],
                "gid": parts[1],
                "mode": parts[2],
                "secontext": parts[3]
            }
    # Fallback to ls -Z if stat fails
    code, out, _ = _run_cmd(prefix + ["shell", "su", "0", "ls", "-ldZ", path])
    if code == 0 and out.strip():
        parts = out.strip().split()
        if len(parts) >= 4:
            # Try to extract uid/gid
            uid = parts[2]
            gid = parts[3]
            secontext = parts[0]
            return {
                "uid": uid,
                "gid": gid,
                "mode": "660",
                "secontext": secontext
            }
    return None

def _apply_metadata(adb_path, path, meta):
    if not meta:
        return
    prefix = _get_adb_prefix(adb_path)
    _run_cmd(prefix + ["shell", "su", "0", "chown", f"{meta['uid']}:{meta['gid']}", path])
    _run_cmd(prefix + ["shell", "su", "0", "chmod", meta['mode'], path])
    if meta.get("secontext") and meta["secontext"] != "?":
        _run_cmd(prefix + ["shell", "su", "0", "chcon", meta["secontext"], path])

def _pull_db_snapshot(adb_path, local_dir):
    prefix = _get_adb_prefix(adb_path)
    # Stage files to a readable temporary device path
    device_temp_dir = "/data/local/tmp/opentracks_db_stage"
    _run_cmd(prefix + ["shell", "su", "0", "mkdir", "-p", device_temp_dir])
    _run_cmd(prefix + ["shell", "su", "0", "chmod", "777", device_temp_dir])

    # Copy DB and sidecars if they exist
    for ext in ["", "-wal", "-shm"]:
        remote_file = REMOTE_DB_PATH + ext
        # Check if file exists
        code, _, _ = _run_cmd(prefix + ["shell", "su", "0", "test", "-f", remote_file])
        if code == 0:
            temp_file = f"{device_temp_dir}/database.db{ext}"
            _run_cmd(prefix + ["shell", "su", "0", "cp", remote_file, temp_file])
            _run_cmd(prefix + ["shell", "su", "0", "chmod", "666", temp_file])
            _run_cmd(prefix + ["pull", temp_file, os.path.join(local_dir, f"database.db{ext}")])

    # Clean up device temp
    _run_cmd(prefix + ["shell", "su", "0", "rm", "-rf", device_temp_dir])

def _push_db_snapshot(adb_path, local_dir):
    prefix = _get_adb_prefix(adb_path)
    device_temp_dir = "/data/local/tmp/opentracks_db_stage"
    _run_cmd(prefix + ["shell", "su", "0", "mkdir", "-p", device_temp_dir])
    _run_cmd(prefix + ["shell", "su", "0", "chmod", "777", device_temp_dir])

    # Capture original metadata
    meta_db = _get_file_metadata(adb_path, REMOTE_DB_PATH)
    meta_wal = _get_file_metadata(adb_path, REMOTE_DB_PATH + "-wal")
    meta_shm = _get_file_metadata(adb_path, REMOTE_DB_PATH + "-shm")

    # Push local files to device temp
    for ext in ["", "-wal", "-shm"]:
        local_file = os.path.join(local_dir, f"database.db{ext}")
        remote_file = REMOTE_DB_PATH + ext
        temp_file = f"{device_temp_dir}/database.db{ext}"
        
        if os.path.exists(local_file):
            _run_cmd(prefix + ["push", local_file, temp_file])
            _run_cmd(prefix + ["shell", "su", "0", "cp", temp_file, remote_file])
            # Restore metadata
            meta = meta_db if ext == "" else (meta_wal if ext == "-wal" else meta_shm)
            if meta:
                _apply_metadata(adb_path, remote_file, meta)
            elif meta_db:
                # Fallback to main DB metadata if sidecar metadata wasn't found
                _apply_metadata(adb_path, remote_file, meta_db)
        else:
            # If sidecar doesn't exist locally, remove it remotely to avoid inconsistency
            _run_cmd(prefix + ["shell", "su", "0", "rm", "-f", remote_file])

    # Clean up device temp
    _run_cmd(prefix + ["shell", "su", "0", "rm", "-rf", device_temp_dir])

def _get_columns(conn, table_name):
    cursor = conn.cursor()
    try:
        cursor.execute(f"PRAGMA table_info({table_name})")
        return [row[1] for row in cursor.fetchall()]
    except Exception:
        return []

def list_tracks(adb_path: str = "adb"):
    local_dir = tempfile.mkdtemp()
    try:
        _pull_db_snapshot(adb_path, local_dir)
        db_path = os.path.join(local_dir, "database.db")
        if not os.path.exists(db_path):
            return {"success": False, "error": "Database file not found on device.", "tracks": []}

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        # Discover columns dynamically
        cols = _get_columns(conn, "tracks")
        if not cols:
            conn.close()
            return {"success": False, "error": "Table 'tracks' does not exist or has no columns.", "tracks": []}

        # Build query dynamically based on discovered columns
        select_fields = []
        field_mapping = {
            "id": "_id",
            "name": "name",
            "description": "description",
            "category": "category",
            "activity_type": "activity_type",
            "starttime": "starttime",
            "stoptime": "stoptime",
            "totaldistance": "totaldistance",
            "totaltime": "totaltime",
            "movingtime": "movingtime",
            "avgspeed": "avgspeed",
            "avgmovingspeed": "avgmovingspeed",
            "maxspeed": "maxspeed",
            "elevationgain": "elevationgain",
            "elevationloss": "elevationloss",
            "numpoints": "numpoints"
        }

        for target_key, col_name in field_mapping.items():
            if col_name in cols:
                select_fields.append(f"{col_name} AS {target_key}")
            elif col_name == "_id" and "id" in cols:
                select_fields.append(f"id AS {target_key}")

        if not select_fields:
            conn.close()
            return {"success": False, "error": "No recognizable columns found in 'tracks' table.", "tracks": []}

        query = f"SELECT {', '.join(select_fields)} FROM tracks"
        if "starttime" in cols:
            query += " ORDER BY starttime DESC"

        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        
        tracks = []
        for row in rows:
            tracks.append(dict(row))

        conn.close()
        return {"success": True, "error": "", "tracks": tracks}
    except Exception as e:
        return {"success": False, "error": str(e), "tracks": []}
    finally:
        shutil.rmtree(local_dir, ignore_errors=True)

def get_track_details(track_id: int, adb_path: str = "adb"):
    local_dir = tempfile.mkdtemp()
    try:
        _pull_db_snapshot(adb_path, local_dir)
        db_path = os.path.join(local_dir, "database.db")
        if not os.path.exists(db_path):
            return {"success": False, "error": "Database file not found on device."}

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Verify track exists
        track_cols = _get_columns(conn, "tracks")
        id_col = "_id" if "_id" in track_cols else ("id" if "id" in track_cols else None)
        if not id_col:
            conn.close()
            return {"success": False, "error": "Could not identify ID column in 'tracks' table."}

        cursor = conn.cursor()
        cursor.execute(f"SELECT 1 FROM tracks WHERE {id_col} = ?", (track_id,))
        if not cursor.fetchone():
            conn.close()
            return {"success": False, "error": f"Track with ID {track_id} not found."}

        # Query trackpoints
        tp_cols = _get_columns(conn, "trackpoints")
        tp_track_id_col = None
        for col in ["trackid", "track_id", "track_id_ref"]:
            if col in tp_cols:
                tp_track_id_col = col
                break
        if not tp_track_id_col:
            # Fallback to scanning columns containing 'track'
            for col in tp_cols:
                if "track" in col.lower():
                    tp_track_id_col = col
                    break

        trackpoints = []
        if tp_track_id_col:
            # Identify coordinate and timestamp columns
            lat_col = "latitude" if "latitude" in tp_cols else ("lat" if "lat" in tp_cols else None)
            lon_col = "longitude" if "longitude" in tp_cols else ("lng" if "lng" in tp_cols else ("lon" if "lon" in tp_cols else None))
            alt_col = "altitude" if "altitude" in tp_cols else ("alt" if "alt" in tp_cols else None)
            time_col = "timestamp" if "timestamp" in tp_cols else ("time" if "time" in tp_cols else None)
            
            # Ordering fields
            order_col = "_id" if "_id" in tp_cols else ("id" if "id" in tp_cols else (time_col if time_col else None))

            if lat_col and lon_col:
                tp_select = [f"{lat_col} AS latitude", f"{lon_col} AS longitude"]
                if alt_col:
                    tp_select.append(f"{alt_col} AS altitude")
                if time_col:
                    tp_select.append(f"{time_col} AS timestamp")
                
                tp_query = f"SELECT {', '.join(tp_select)} FROM trackpoints WHERE {tp_track_id_col} = ?"
                if order_col:
                    tp_query += f" ORDER BY {order_col} ASC"
                
                cursor.execute(tp_query, (track_id,))
                for row in cursor.fetchall():
                    d = dict(row)
                    if "altitude" not in d:
                        d["altitude"] = 0.0
                    if "timestamp" not in d:
                        d["timestamp"] = 0
                    trackpoints.append(d)

        # Query markers
        marker_cols = _get_columns(conn, "markers")
        m_track_id_col = None
        for col in ["trackid", "track_id", "track_id_ref"]:
            if col in marker_cols:
                m_track_id_col = col
                break
        if not m_track_id_col:
            for col in marker_cols:
                if "track" in col.lower():
                    m_track_id_col = col
                    break

        markers = []
        if m_track_id_col:
            name_col = "name" if "name" in marker_cols else None
            desc_col = "description" if "description" in marker_cols else ("desc" if "desc" in marker_cols else None)
            type_col = "type" if "type" in marker_cols else None

            if name_col:
                m_select = [f"{name_col} AS name"]
                if desc_col:
                    m_select.append(f"{desc_col} AS description")
                if type_col:
                    m_select.append(f"{type_col} AS type")
                
                m_query = f"SELECT {', '.join(m_select)} FROM markers WHERE {m_track_id_col} = ?"
                cursor.execute(m_query, (track_id,))
                for row in cursor.fetchall():
                    d = dict(row)
                    if "description" not in d:
                        d["description"] = ""
                    if "type" not in d:
                        d["type"] = ""
                    markers.append(d)

        conn.close()
        return {
            "success": True,
            "error": "",
            "track_id": track_id,
            "trackpoints": trackpoints,
            "markers": markers
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        shutil.rmtree(local_dir, ignore_errors=True)

def create_track(name: str, description: str = None, category: str = None, activity_type: str = None, adb_path: str = "adb"):
    _quiesce_app(adb_path)
    local_dir = tempfile.mkdtemp()
    try:
        _pull_db_snapshot(adb_path, local_dir)
        db_path = os.path.join(local_dir, "database.db")
        if not os.path.exists(db_path):
            return {"success": False, "error": "Database file not found on device."}

        conn = sqlite3.connect(db_path)
        cols = _get_columns(conn, "tracks")
        
        insert_fields = []
        insert_values = []
        
        # Map input parameters to discovered columns
        mapping = {
            "name": name,
            "description": description,
            "category": category,
            "activity_type": activity_type
        }
        
        for key, val in mapping.items():
            if key in cols and val is not None:
                insert_fields.append(key)
                insert_values.append(val)

        if "name" not in insert_fields and "name" in cols:
            insert_fields.append("name")
            insert_values.append(name)

        if not insert_fields:
            conn.close()
            return {"success": False, "error": "No valid columns to insert into 'tracks' table."}

        placeholders = ", ".join(["?"] * len(insert_values))
        query = f"INSERT INTO tracks ({', '.join(insert_fields)}) VALUES ({placeholders})"
        
        cursor = conn.cursor()
        cursor.execute(query, insert_values)
        track_id = cursor.lastrowid
        conn.commit()
        conn.close()

        _push_db_snapshot(adb_path, local_dir)
        return {"success": True, "error": "", "track_id": track_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        shutil.rmtree(local_dir, ignore_errors=True)

def update_track_metadata(track_id: int, name: str = None, description: str = None, category: str = None, activity_type: str = None, adb_path: str = "adb"):
    _quiesce_app(adb_path)
    local_dir = tempfile.mkdtemp()
    try:
        _pull_db_snapshot(adb_path, local_dir)
        db_path = os.path.join(local_dir, "database.db")
        if not os.path.exists(db_path):
            return {"success": False, "error": "Database file not found on device."}

        conn = sqlite3.connect(db_path)
        cols = _get_columns(conn, "tracks")
        id_col = "_id" if "_id" in cols else ("id" if "id" in cols else None)
        if not id_col:
            conn.close()
            return {"success": False, "error": "Could not identify ID column in 'tracks' table."}

        update_fields = []
        update_values = []
        
        mapping = {
            "name": name,
            "description": description,
            "category": category,
            "activity_type": activity_type
        }
        
        for key, val in mapping.items():
            if key in cols and val is not None:
                update_fields.append(f"{key} = ?")
                update_values.append(val)

        if not update_fields:
            conn.close()
            return {"success": False, "error": "No valid columns to update."}

        update_values.append(track_id)
        query = f"UPDATE tracks SET {', '.join(update_fields)} WHERE {id_col} = ?"
        
        cursor = conn.cursor()
        cursor.execute(query, update_values)
        conn.commit()
        conn.close()

        _push_db_snapshot(adb_path, local_dir)
        return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        shutil.rmtree(local_dir, ignore_errors=True)

def delete_track(track_id: int, adb_path: str = "adb"):
    _quiesce_app(adb_path)
    local_dir = tempfile.mkdtemp()
    try:
        _pull_db_snapshot(adb_path, local_dir)
        db_path = os.path.join(local_dir, "database.db")
        if not os.path.exists(db_path):
            return {"success": False, "error": "Database file not found on device."}

        conn = sqlite3.connect(db_path)
        
        # Identify track ID column
        track_cols = _get_columns(conn, "tracks")
        id_col = "_id" if "_id" in track_cols else ("id" if "id" in track_cols else None)
        if not id_col:
            conn.close()
            return {"success": False, "error": "Could not identify ID column in 'tracks' table."}

        # Identify trackpoint track ID column
        tp_cols = _get_columns(conn, "trackpoints")
        tp_track_id_col = None
        for col in ["trackid", "track_id", "track_id_ref"]:
            if col in tp_cols:
                tp_track_id_col = col
                break
        if not tp_track_id_col:
            for col in tp_cols:
                if "track" in col.lower():
                    tp_track_id_col = col
                    break

        # Identify marker track ID column
        marker_cols = _get_columns(conn, "markers")
        m_track_id_col = None
        for col in ["trackid", "track_id", "track_id_ref"]:
            if col in marker_cols:
                m_track_id_col = col
                break
        if not m_track_id_col:
            for col in marker_cols:
                if "track" in col.lower():
                    m_track_id_col = col
                    break

        cursor = conn.cursor()
        # Atomic deletion
        cursor.execute("BEGIN TRANSACTION")
        try:
            # Delete dependent trackpoints
            if tp_track_id_col:
                cursor.execute(f"DELETE FROM trackpoints WHERE {tp_track_id_col} = ?", (track_id,))
            
            # Delete dependent markers
            if m_track_id_col:
                cursor.execute(f"DELETE FROM markers WHERE {m_track_id_col} = ?", (track_id,))
            
            # Delete parent track
            cursor.execute(f"DELETE FROM tracks WHERE {id_col} = ?", (track_id,))
            
            cursor.execute("COMMIT")
        except Exception as tx_err:
            cursor.execute("ROLLBACK")
            raise tx_err

        conn.close()
        _push_db_snapshot(adb_path, local_dir)
        return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        shutil.rmtree(local_dir, ignore_errors=True)

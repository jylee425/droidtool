import os
import sys
import subprocess
import tempfile
import sqlite3
import shlex

# Package and DB configuration
PACKAGE_NAME = "com.simplemobiletools.calendar.pro"
REMOTE_DB_PATH = "/data/data/com.simplemobiletools.calendar.pro/databases/events.db"

def _get_adb_prefix(adb_path):
    serial = os.environ.get("ANDROID_SERIAL")
    if serial:
        return [adb_path, "-s", serial]
    return [adb_path]

def _run_cmd(cmd):
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode, res.stdout, res.stderr

def _quiesce_app(adb_path):
    prefix = _get_adb_prefix(adb_path)
    _run_cmd(prefix + ["shell", "am", "force-stop", PACKAGE_NAME])

def _get_file_metadata(adb_path, path):
    prefix = _get_adb_prefix(adb_path)
    cmd_stat = prefix + ["shell", "su", "0", f"stat -c '%u:%g:%a' {shlex.quote(path)}"]
    ret, out, _ = _run_cmd(cmd_stat)
    uid, gid, mode = None, None, None
    if ret == 0 and out:
        parts = out.decode('utf-8').strip().split(':')
        if len(parts) == 3:
            uid, gid, mode = parts[0], parts[1], parts[2]
    
    cmd_selinux = prefix + ["shell", "su", "0", f"ls -Z {shlex.quote(path)}"]
    ret_se, out_se, _ = _run_cmd(cmd_selinux)
    se_context = None
    if ret_se == 0 and out_se:
        parts = out_se.decode('utf-8').strip().split()
        if parts:
            se_context = parts[0]
            
    return uid, gid, mode, se_context

def _restore_metadata(adb_path, path, uid, gid, mode, se_context):
    prefix = _get_adb_prefix(adb_path)
    if uid and gid:
        _run_cmd(prefix + ["shell", "su", "0", f"chown {uid}:{gid} {shlex.quote(path)}"])
    if mode:
        _run_cmd(prefix + ["shell", "su", "0", f"chmod {mode} {shlex.quote(path)}"])
    if se_context and se_context != "?" and se_context != "u:object_r:rawfs:s0":
        _run_cmd(prefix + ["shell", "su", "0", f"chcon {se_context} {shlex.quote(path)}"])

def _device_file_exists(adb_path, path):
    prefix = _get_adb_prefix(adb_path)
    ret, _, _ = _run_cmd(prefix + ["shell", "su", "0", f"test -f {shlex.quote(path)}"])
    return ret == 0

def _pull_db(adb_path, local_dir):
    prefix = _get_adb_prefix(adb_path)
    temp_device_db = "/data/local/tmp/events_temp.db"
    temp_device_wal = "/data/local/tmp/events_temp.db-wal"
    temp_device_shm = "/data/local/tmp/events_temp.db-shm"
    
    # Copy main DB
    _run_cmd(prefix + ["shell", "su", "0", f"cp {shlex.quote(REMOTE_DB_PATH)} {temp_device_db}"])
    _run_cmd(prefix + ["shell", "su", "0", f"chmod 666 {temp_device_db}"])
    
    # Copy WAL if exists
    wal_exists = _device_file_exists(adb_path, REMOTE_DB_PATH + "-wal")
    if wal_exists:
        _run_cmd(prefix + ["shell", "su", "0", f"cp {shlex.quote(REMOTE_DB_PATH + '-wal')} {temp_device_wal}"])
        _run_cmd(prefix + ["shell", "su", "0", f"chmod 666 {temp_device_wal}"])
        
    # Copy SHM if exists
    shm_exists = _device_file_exists(adb_path, REMOTE_DB_PATH + "-shm")
    if shm_exists:
        _run_cmd(prefix + ["shell", "su", "0", f"cp {shlex.quote(REMOTE_DB_PATH + '-shm')} {temp_device_shm}"])
        _run_cmd(prefix + ["shell", "su", "0", f"chmod 666 {temp_device_shm}"])
    
    local_db = os.path.join(local_dir, "events.db")
    _run_cmd(prefix + ["pull", temp_device_db, local_db])
    if wal_exists:
        _run_cmd(prefix + ["pull", temp_device_wal, local_db + "-wal"])
    if shm_exists:
        _run_cmd(prefix + ["pull", temp_device_shm, local_db + "-shm"])
    
    # Clean up temp device files
    _run_cmd(prefix + ["shell", "rm", "-f", temp_device_db, temp_device_wal, temp_device_shm])
    return local_db

def _push_db(adb_path, local_db, uid, gid, mode, se_context):
    prefix = _get_adb_prefix(adb_path)
    temp_device_db = "/data/local/tmp/events_temp_push.db"
    temp_device_wal = "/data/local/tmp/events_temp_push.db-wal"
    temp_device_shm = "/data/local/tmp/events_temp_push.db-shm"
    
    _run_cmd(prefix + ["push", local_db, temp_device_db])
    if os.path.exists(local_db + "-wal"):
        _run_cmd(prefix + ["push", local_db + "-wal", temp_device_wal])
    if os.path.exists(local_db + "-shm"):
        _run_cmd(prefix + ["push", local_db + "-shm", temp_device_shm])
        
    _run_cmd(prefix + ["shell", "su", "0", f"cp {temp_device_db} {shlex.quote(REMOTE_DB_PATH)}"])
    if os.path.exists(local_db + "-wal"):
        _run_cmd(prefix + ["shell", "su", "0", f"cp {temp_device_wal} {shlex.quote(REMOTE_DB_PATH + '-wal')}"])
    else:
        _run_cmd(prefix + ["shell", "su", "0", f"rm -f {shlex.quote(REMOTE_DB_PATH + '-wal')}"])
        
    if os.path.exists(local_db + "-shm"):
        _run_cmd(prefix + ["shell", "su", "0", f"cp {temp_device_shm} {shlex.quote(REMOTE_DB_PATH + '-shm')}"])
    else:
        _run_cmd(prefix + ["shell", "su", "0", f"rm -f {shlex.quote(REMOTE_DB_PATH + '-shm')}"])
        
    _restore_metadata(adb_path, REMOTE_DB_PATH, uid, gid, mode, se_context)
    if os.path.exists(local_db + "-wal"):
        _restore_metadata(adb_path, REMOTE_DB_PATH + "-wal", uid, gid, mode, se_context)
    if os.path.exists(local_db + "-shm"):
        _restore_metadata(adb_path, REMOTE_DB_PATH + "-shm", uid, gid, mode, se_context)
        
    _run_cmd(prefix + ["shell", "rm", "-f", temp_device_db, temp_device_wal, temp_device_shm])

def _prepare_local_connection(local_db):
    """Opens a connection and safely disables WAL mode locally to prevent locking issues."""
    conn = sqlite3.connect(local_db, timeout=30.0)
    try:
        # Force checkpoint and switch to DELETE journal mode locally to merge WAL changes
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    except Exception:
        pass
    return conn

def create_calendar_event(
    title: str,
    start_ts: int,
    end_ts: int,
    location: str = "",
    description: str = "",
    time_zone: str = "UTC",
    repeat_interval: int = 0,
    repeat_rule: int = 0,
    repeat_limit: int = 0,
    event_type: int = 1,
    reminder_1_minutes: int = -1,
    reminder_1_type: int = 0,
    adb_path: str = "adb"
) -> dict:
    try:
        _quiesce_app(adb_path)
        uid, gid, mode, se_context = _get_file_metadata(adb_path, REMOTE_DB_PATH)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            local_db = _pull_db(adb_path, tmpdir)
            conn = _prepare_local_connection(local_db)
            cursor = conn.cursor()
            
            # Verify table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
            if not cursor.fetchone():
                conn.close()
                return {"id": 0, "success": False, "error": "events table does not exist in database"}
                
            # Inspect columns dynamically to handle NOT NULL constraints gracefully
            cursor.execute("PRAGMA table_info(events)")
            columns_info = cursor.fetchall()
            db_columns = {col[1]: {"notnull": col[3], "dflt_value": col[4]} for col in columns_info}
            
            insert_data = {}
            standard_fields = {
                "title": title,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "location": location,
                "description": description,
                "time_zone": time_zone,
                "repeat_interval": repeat_interval,
                "repeat_rule": repeat_rule,
                "repeat_limit": repeat_limit,
                "event_type": event_type,
                "reminder_1_minutes": reminder_1_minutes,
                "reminder_1_type": reminder_1_type
            }
            
            for col_name in db_columns:
                if col_name == "id":
                    continue
                if col_name in standard_fields:
                    insert_data[col_name] = standard_fields[col_name]
                else:
                    col_meta = db_columns[col_name]
                    if col_meta["notnull"] and col_meta["dflt_value"] is None:
                        if "reminder_2" in col_name or "reminder_3" in col_name:
                            insert_data[col_name] = -1 if "minutes" in col_name else 0
                        elif "import_id" in col_name:
                            insert_data[col_name] = ""
                        elif "flags" in col_name or "source" in col_name:
                            insert_data[col_name] = 0
                        else:
                            insert_data[col_name] = 0
            
            columns_str = ", ".join(insert_data.keys())
            placeholders_str = ", ".join(["?"] * len(insert_data))
            query = f"INSERT INTO events ({columns_str}) VALUES ({placeholders_str})"
            
            cursor.execute(query, tuple(insert_data.values()))
            new_id = cursor.lastrowid
            
            conn.commit()
            conn.close()
            
            # Remove local WAL/SHM files if they exist since we converted to DELETE mode
            for ext in ["-wal", "-shm"]:
                local_ext_path = local_db + ext
                if os.path.exists(local_ext_path):
                    try:
                        os.remove(local_ext_path)
                    except Exception:
                        pass
            
            _push_db(adb_path, local_db, uid, gid, mode, se_context)
            
        return {"id": new_id, "success": True, "error": ""}
    except Exception as e:
        return {"id": 0, "success": False, "error": str(e)}

def query_calendar_events(
    start_time_filter: int = None,
    end_time_filter: int = None,
    after_time: int = None,
    title_filter: str = None,
    text_search: str = None,
    adb_path: str = "adb"
) -> dict:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_db = _pull_db(adb_path, tmpdir)
            conn = sqlite3.connect(local_db, timeout=30.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
            if not cursor.fetchone():
                conn.close()
                return {"events": [], "success": True, "error": ""}
                
            query = "SELECT * FROM events WHERE 1=1"
            params = []
            
            if start_time_filter is not None:
                query += " AND end_ts >= ?"
                params.append(start_time_filter)
            if end_time_filter is not None:
                query += " AND start_ts <= ?"
                params.append(end_time_filter)
            if after_time is not None:
                query += " AND start_ts >= ?"
                params.append(after_time)
            if title_filter is not None:
                query += " AND title LIKE ?"
                params.append(f"%{title_filter}%")
            if text_search is not None:
                query += " AND (title LIKE ? OR description LIKE ? OR location LIKE ?)"
                term = f"%{text_search}%"
                params.extend([term, term, term])
                
            query += " ORDER BY start_ts ASC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            events = []
            for r in rows:
                events.append({
                    "id": r["id"],
                    "title": r["title"],
                    "start_ts": r["start_ts"],
                    "end_ts": r["end_ts"],
                    "location": r.get("location", ""),
                    "description": r.get("description", ""),
                    "repeat_interval": r.get("repeat_interval", 0),
                    "event_type": r.get("event_type", 1),
                    "reminder_1_minutes": r.get("reminder_1_minutes", -1)
                })
                
            conn.close()
        return {"events": events, "success": True, "error": ""}
    except Exception as e:
        return {"events": [], "success": False, "error": str(e)}

def update_calendar_event(
    id: int,
    title: str = None,
    start_ts: int = None,
    end_ts: int = None,
    location: str = None,
    description: str = None,
    repeat_interval: int = None,
    repeat_rule: int = None,
    repeat_limit: int = None,
    event_type: int = None,
    reminder_1_minutes: int = None,
    adb_path: str = "adb"
) -> dict:
    try:
        _quiesce_app(adb_path)
        uid, gid, mode, se_context = _get_file_metadata(adb_path, REMOTE_DB_PATH)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            local_db = _pull_db(adb_path, tmpdir)
            conn = _prepare_local_connection(local_db)
            cursor = conn.cursor()
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
            if not cursor.fetchone():
                conn.close()
                return {"success": False, "rows_affected": 0, "error": "events table does not exist"}
                
            updates = []
            params = []
            
            fields = {
                "title": title,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "location": location,
                "description": description,
                "repeat_interval": repeat_interval,
                "repeat_rule": repeat_rule,
                "repeat_limit": repeat_limit,
                "event_type": event_type,
                "reminder_1_minutes": reminder_1_minutes
            }
            
            for k, v in fields.items():
                if v is not None:
                    updates.append(f"{k} = ?")
                    params.append(v)
                    
            if not updates:
                conn.close()
                return {"success": True, "rows_affected": 0, "error": "No fields to update"}
                
            query = f"UPDATE events SET {', '.join(updates)} WHERE id = ?"
            params.append(id)
            
            cursor.execute(query, params)
            rows_affected = cursor.rowcount
            
            conn.commit()
            conn.close()
            
            for ext in ["-wal", "-shm"]:
                local_ext_path = local_db + ext
                if os.path.exists(local_ext_path):
                    try:
                        os.remove(local_ext_path)
                    except Exception:
                        pass
            
            _push_db(adb_path, local_db, uid, gid, mode, se_context)
            
        return {"success": True, "rows_affected": rows_affected, "error": ""}
    except Exception as e:
        return {"success": False, "rows_affected": 0, "error": str(e)}

def delete_calendar_events(
    ids: list = None,
    title_filter: str = None,
    exact_title_match: bool = None,
    start_time_filter: int = None,
    end_time_filter: int = None,
    adb_path: str = "adb"
) -> dict:
    if not any([ids, title_filter, start_time_filter, end_time_filter]):
        return {"success": False, "rows_deleted": 0, "error": "At least one filter must be provided to prevent accidental truncation."}
        
    try:
        _quiesce_app(adb_path)
        uid, gid, mode, se_context = _get_file_metadata(adb_path, REMOTE_DB_PATH)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            local_db = _pull_db(adb_path, tmpdir)
            conn = _prepare_local_connection(local_db)
            cursor = conn.cursor()
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
            if not cursor.fetchone():
                conn.close()
                return {"success": False, "rows_deleted": 0, "error": "events table does not exist"}
                
            query = "SELECT id FROM events WHERE 1=1"
            params = []
            
            if ids is not None:
                query += f" AND id IN ({', '.join(['?']*len(ids))})"
                params.extend(ids)
            if title_filter is not None:
                if exact_title_match:
                    query += " AND title = ?"
                    params.append(title_filter)
                else:
                    query += " AND title LIKE ?"
                    params.append(f"%{title_filter}%")
            if start_time_filter is not None:
                query += " AND end_ts >= ?"
                params.append(start_time_filter)
            if end_time_filter is not None:
                query += " AND start_ts <= ?"
                params.append(end_time_filter)
                
            cursor.execute(query, params)
            resolved_ids = [row[0] for row in cursor.fetchall()]
            
            if not resolved_ids:
                conn.close()
                return {"success": True, "rows_deleted": 0, "error": ""}
                
            delete_query = f"DELETE FROM events WHERE id IN ({', '.join(['?']*len(resolved_ids))})"
            cursor.execute(delete_query, resolved_ids)
            rows_deleted = cursor.rowcount
            
            conn.commit()
            conn.close()
            
            for ext in ["-wal", "-shm"]:
                local_ext_path = local_db + ext
                if os.path.exists(local_ext_path):
                    try:
                        os.remove(local_ext_path)
                    except Exception:
                        pass
            
            _push_db(adb_path, local_db, uid, gid, mode, se_context)
            
        return {"success": True, "rows_deleted": rows_deleted, "error": ""}
    except Exception as e:
        return {"success": False, "rows_deleted": 0, "error": str(e)}

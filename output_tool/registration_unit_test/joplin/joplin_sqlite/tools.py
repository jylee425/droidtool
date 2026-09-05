import os
import sys
import uuid
import time
import shutil
import sqlite3
import tempfile
import subprocess
import shlex

PACKAGE_NAME = "net.cozic.joplin"
PRIMARY_DB_PATH = "/data/data/net.cozic.joplin/databases/joplin.sqlite"
ALTERNATE_DB_PATH = "/data/data/net.cozic.joplin/databases/database.sqlite"

# Secure representation of file stats
class FileMetadata:
    def __init__(self, uid=None, gid=None, mode=None, selinux=None):
        self.uid = uid
        self.gid = gid
        self.mode = mode
        self.selinux = selinux

def run_adb(args, adb_path="adb"):
    serial = os.environ.get("ANDROID_SERIAL")
    base_cmd = [adb_path]
    if serial:
        base_cmd.extend(["-s", serial])
    return subprocess.run(base_cmd + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

def file_exists_on_device(path, adb_path="adb"):
    res = run_adb(["shell", "su", "0", "test", "-f", shlex.quote(path)], adb_path=adb_path)
    return res.returncode == 0

def stop_joplin(adb_path="adb"):
    run_adb(["shell", "am", "force-stop", PACKAGE_NAME], adb_path=adb_path)

def discover_file_metadata(path, adb_path="adb") -> FileMetadata:
    # Read UID, GID and mode securely
    stat_res = run_adb(["shell", "su", "0", "stat", "-c", "%u:%g:%a", shlex.quote(path)], adb_path=adb_path)
    uid, gid, mode = None, None, None
    if stat_res.returncode == 0 and stat_res.stdout:
        parts = stat_res.stdout.decode("utf-8").strip().split(":")
        if len(parts) == 3:
            uid, gid, mode = parts[0], parts[1], parts[2]
            
    # Read SELinux context safely from the active device filesystem directly instead of guessing
    selinux = None
    ls_res = run_adb(["shell", "su", "0", "ls", "-Zd", shlex.quote(path)], adb_path=adb_path)
    if ls_res.returncode == 0 and ls_res.stdout:
        parts = ls_res.stdout.decode("utf-8").strip().split()
        if len(parts) > 0:
            selinux = parts[0]

    return FileMetadata(uid=uid, gid=gid, mode=mode, selinux=selinux)

def apply_file_metadata(path, metadata: FileMetadata, adb_path="adb"):
    if not metadata:
        return
    if metadata.uid and metadata.gid:
        run_adb(["shell", "su", "0", "chown", f"{metadata.uid}:{metadata.gid}", shlex.quote(path)], adb_path=adb_path)
    if metadata.mode:
        run_adb(["shell", "su", "0", "chmod", metadata.mode, shlex.quote(path)], adb_path=adb_path)
    if metadata.selinux:
        run_adb(["shell", "su", "0", "chcon", metadata.selinux, shlex.quote(path)], adb_path=adb_path)

def select_and_stage_database(adb_path="adb"):
    db_path = None
    if file_exists_on_device(PRIMARY_DB_PATH, adb_path=adb_path):
        db_path = PRIMARY_DB_PATH
    elif file_exists_on_device(ALTERNATE_DB_PATH, adb_path=adb_path):
        db_path = ALTERNATE_DB_PATH

    if not db_path:
        raise FileNotFoundError("No Joplin database could be selected on the device.")

    meta = discover_file_metadata(db_path, adb_path=adb_path)

    local_temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(local_temp_dir, "database.db")

    # Stage device copy to accessible temporary device area to run pull smoothly
    device_stage = f"/data/local/tmp/{uuid.uuid4().hex}.db"
    run_adb(["shell", "su", "0", "cp", shlex.quote(db_path), device_stage], adb_path=adb_path)
    run_adb(["shell", "su", "0", "chmod", "777", device_stage], adb_path=adb_path)

    pull_res = run_adb(["pull", device_stage, local_db], adb_path=adb_path)
    run_adb(["shell", "su", "0", "rm", "-f", device_stage], adb_path=adb_path)

    if pull_res.returncode != 0:
        shutil.rmtree(local_temp_dir, ignore_errors=True)
        raise IOError(f"Could not download SQLite database from: {db_path}")

    # Sidecars detection (WAL/SHM)
    sidecars = ["-wal", "-shm", "-journal"]
    for suffix in sidecars:
        dev_sidecar = db_path + suffix
        if file_exists_on_device(dev_sidecar, adb_path=adb_path):
            local_sidecar = local_db + suffix
            device_stage_sc = f"/data/local/tmp/{uuid.uuid4().hex}{suffix}"
            run_adb(["shell", "su", "0", "cp", shlex.quote(dev_sidecar), device_stage_sc], adb_path=adb_path)
            run_adb(["shell", "su", "0", "chmod", "777", device_stage_sc], adb_path=adb_path)
            run_adb(["pull", device_stage_sc, local_sidecar], adb_path=adb_path)
            run_adb(["shell", "su", "0", "rm", "-f", device_stage_sc], adb_path=adb_path)

    return db_path, local_db, local_temp_dir, meta

def writeback_database(db_path, local_db, local_temp_dir, meta: FileMetadata, adb_path="adb"):
    stop_joplin(adb_path=adb_path)
    device_stage = f"/data/local/tmp/{uuid.uuid4().hex}.db"
    
    push_res = run_adb(["push", local_db, device_stage], adb_path=adb_path)
    if push_res.returncode != 0:
        run_adb(["shell", "su", "0", "rm", "-f", device_stage], adb_path=adb_path)
        raise IOError("Failed to push updated database to standard temporary area on device.")

    run_adb(["shell", "su", "0", "cp", device_stage, shlex.quote(db_path)], adb_path=adb_path)
    run_adb(["shell", "su", "0", "rm", "-f", device_stage], adb_path=adb_path)

    apply_file_metadata(db_path, meta, adb_path=adb_path)

    # Process WAL checkpoint safely to make sidecar storage reliable
    sidecars = ["-wal", "-shm", "-journal"]
    for suffix in sidecars:
        local_sc = local_db + suffix
        dev_sc = db_path + suffix
        if os.path.exists(local_sc):
            sc_meta = discover_file_metadata(dev_sc, adb_path=adb_path) if file_exists_on_device(dev_sc, adb_path=adb_path) else meta
            tmp_sc_stage = f"/data/local/tmp/{uuid.uuid4().hex}{suffix}"
            run_adb(["push", local_sc, tmp_sc_stage], adb_path=adb_path)
            run_adb(["shell", "su", "0", "cp", tmp_sc_stage, shlex.quote(dev_sc)], adb_path=adb_path)
            run_adb(["shell", "su", "0", "rm", "-f", tmp_sc_stage], adb_path=adb_path)
            apply_file_metadata(dev_sc, sc_meta, adb_path=adb_path)
        else:
            # If local sidecar is empty, cleanly drop standard sidecar to ensure consistency
            run_adb(["shell", "su", "0", "rm", "-f", shlex.quote(dev_sc)], adb_path=adb_path)

def detect_timestamp_scale(conn):
    # Examine some rows to decide scale. Joplin uses milliseconds traditionally, but fallback nicely.
    if table_exists(conn, "notes"):
        for row in conn.execute("SELECT created_time FROM notes WHERE created_time IS NOT NULL LIMIT 1"):
            val = row[0]
            if val > 1000000000000:
                return "ms"
            return "s"
    if table_exists(conn, "folders"):
        for row in conn.execute("SELECT created_time FROM folders WHERE created_time IS NOT NULL LIMIT 1"):
            val = row[0]
            if val > 1000000000000:
                return "ms"
            return "s"
    return "ms"

def get_current_time(scale):
    now_s = time.time()
    if scale == "ms":
        return int(now_s * 1000)
    return int(now_s)

def table_exists(conn, table_name):
    c = conn.cursor()
    c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return c.fetchone() is not None

def get_column_names(conn, table_name):
    c = conn.cursor()
    c.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in c.fetchall()]

def list_folders(adb_path: str = "adb") -> dict:
    try:
        db_path, local_db, local_temp_dir, meta = select_and_stage_database(adb_path=adb_path)
    except Exception as e:
        return {"success": False, "error": str(e)}

    try:
        conn = sqlite3.connect(local_db)
        if not table_exists(conn, "folders"):
            return {"success": False, "error": "Required Joplin 'folders' table missing in the discovered database.", "folders": [], "database_path": db_path}

        cols = get_column_names(conn, "folders")
        select_cols = []
        for required in ["id", "title", "parent_id", "created_time", "updated_time"]:
            if required in cols:
                select_cols.append(required)
            else:
                select_cols.append("'' AS " + required if "_id" in required or required == "title" else "0 AS " + required)

        query = f"SELECT {', '.join(select_cols)} FROM folders"
        cursor = conn.cursor()
        cursor.execute(query)
        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row[0],
                "title": row[1],
                "parent_id": row[2] if row[2] else "",
                "created_time": int(row[3]) if row[3] else 0,
                "updated_time": int(row[4]) if row[4] else 0
            })
        conn.close()
        return {"success": True, "error": "", "database_path": db_path, "folders": results}
    except Exception as e:
        return {"success": False, "error": f"Failed during db execution: {str(e)}"}
    finally:
        shutil.rmtree(local_temp_dir, ignore_errors=True)

def create_folder(title: str, parent_id: str = "", adb_path: str = "adb") -> dict:
    try:
        db_path, local_db, local_temp_dir, meta = select_and_stage_database(adb_path=adb_path)
    except Exception as e:
        return {"success": False, "error": str(e)}

    try:
        conn = sqlite3.connect(local_db)
        if not table_exists(conn, "folders"):
            return {"success": False, "error": "Folders table does not exist in the DB."}

        # Relationship check
        if parent_id:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM folders WHERE id = ?", (parent_id,))
            if not cursor.fetchone():
                return {"success": False, "error": f"Assigned parent_id '{parent_id}' does not exist inside folders table."}

        scale = detect_timestamp_scale(conn)
        curr_time = get_current_time(scale)
        folder_id = uuid.uuid4().hex

        # Discover columns actually supported
        cols = get_column_names(conn, "folders")
        insert_map = {
            "id": folder_id,
            "title": title,
            "parent_id": parent_id if parent_id else "",
            "created_time": curr_time,
            "updated_time": curr_time
        }

        active_inserts = {k: v for k, v in insert_map.items() if k in cols}
        col_names_str = ", ".join(active_inserts.keys())
        placeholders = ", ".join(["?"] * len(active_inserts))

        conn.execute(f"INSERT INTO folders ({col_names_str}) VALUES ({placeholders})", list(active_inserts.values()))
        conn.commit()
        conn.close()

        writeback_database(db_path, local_db, local_temp_dir, meta, adb_path=adb_path)
        return {
            "success": True,
            "error": "",
            "id": folder_id,
            "title": title,
            "parent_id": parent_id,
            "created_time": curr_time
        }
    except Exception as e:
        return {"success": False, "error": f"Creation failure: {str(e)}"}
    finally:
        shutil.rmtree(local_temp_dir, ignore_errors=True)

def search_notes(query: str = "", parent_id: str = "", include_deleted: bool = False, adb_path: str = "adb") -> dict:
    try:
        db_path, local_db, local_temp_dir, meta = select_and_stage_database(adb_path=adb_path)
    except Exception as e:
        return {"success": False, "error": str(e)}

    try:
        conn = sqlite3.connect(local_db)
        if not table_exists(conn, "notes"):
            return {"success": False, "error": "Required table 'notes' is missing.", "notes": []}

        cols = get_column_names(conn, "notes")
        conditions = []
        params = []

        if query:
            search_term = f"%{query}%"
            if "body" in cols:
                conditions.append("(title LIKE ? OR body LIKE ?)")
                params.extend([search_term, search_term])
            else:
                conditions.append("title LIKE ?")
                params.append(search_term)

        if parent_id:
            conditions.append("parent_id = ?")
            params.append(parent_id)

        if not include_deleted:
            if "deleted" in cols:
                conditions.append("deleted = 0")
            if "conflict" in cols:
                conditions.append("conflict = 0")

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        select_cols = []
        for required in ["id", "title", "parent_id", "created_time", "updated_time"]:
            if required in cols:
                select_cols.append(required)
            else:
                select_cols.append("'' AS " + required if "_id" in required or required == "title" else "0 AS " + required)

        sql_query = f"SELECT {', '.join(select_cols)} FROM notes{where_clause}"
        cursor = conn.cursor()
        cursor.execute(sql_query, params)
        notes = []
        for row in cursor.fetchall():
            notes.append({
                "id": row[0],
                "title": row[1],
                "parent_id": row[2] if row[2] else "",
                "created_time": int(row[3]) if row[3] else 0,
                "updated_time": int(row[4]) if row[4] else 0
            })
        conn.close()
        return {"success": True, "error": "", "notes": notes}
    except Exception as e:
        return {"success": False, "error": f"Query execute exception: {str(e)}"}
    finally:
        shutil.rmtree(local_temp_dir, ignore_errors=True)

def get_note(id: str, adb_path: str = "adb") -> dict:
    try:
        db_path, local_db, local_temp_dir, meta = select_and_stage_database(adb_path=adb_path)
    except Exception as e:
        return {"success": False, "error": str(e)}

    try:
        conn = sqlite3.connect(local_db)
        if not table_exists(conn, "notes"):
            return {"success": False, "error": "Table 'notes' does not exist."}

        cols = get_column_names(conn, "notes")
        select_cols = []
        for req in ["id", "title", "body", "parent_id", "created_time", "updated_time"]:
            if req in cols:
                select_cols.append(req)
            else:
                select_cols.append("'' AS " + req if "_id" in req or req in ["title", "body"] else "0 AS " + req)

        sql_query = f"SELECT {', '.join(select_cols)} FROM notes WHERE id = ?"
        cursor = conn.cursor()
        cursor.execute(sql_query, (id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return {"success": False, "error": f"Note with exact ID '{id}' was not found in active database."}

        return {
            "success": True,
            "error": "",
            "id": row[0],
            "title": row[1],
            "body": row[2],
            "parent_id": row[3] if row[3] else "",
            "created_time": int(row[4]) if row[4] else 0,
            "updated_time": int(row[5]) if row[5] else 0
        }
    except Exception as e:
        return {"success": False, "error": f"Failed detail fetch: {str(e)}"}
    finally:
        shutil.rmtree(local_temp_dir, ignore_errors=True)

def create_note(title: str, body: str, parent_id: str, adb_path: str = "adb") -> dict:
    try:
        db_path, local_db, local_temp_dir, meta = select_and_stage_database(adb_path=adb_path)
    except Exception as e:
        return {"success": False, "error": str(e)}

    try:
        conn = sqlite3.connect(local_db)
        if not table_exists(conn, "notes"):
            return {"success": False, "error": "Missing table: 'notes'."}
        if not table_exists(conn, "folders"):
            return {"success": False, "error": "Folders parent verification table missing."}

        # Relationship constraint checks
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM folders WHERE id = ?", (parent_id,))
        if not cursor.fetchone():
            return {"success": False, "error": f"Attempted link to missing folder ID parent reference: {parent_id}"}

        scale = detect_timestamp_scale(conn)
        curr_time = get_current_time(scale)
        note_id = uuid.uuid4().hex

        cols = get_column_names(conn, "notes")
        insert_map = {
            "id": note_id,
            "title": title,
            "body": body,
            "parent_id": parent_id,
            "created_time": curr_time,
            "updated_time": curr_time,
            "is_todo": 0,
            "todo_due": 0,
            "todo_completed": 0
        }
        active_inserts = {k: v for k, v in insert_map.items() if k in cols}
        col_names_str = ", ".join(active_inserts.keys())
        placeholders = ", ".join(["?"] * len(active_inserts))

        # Insert cleanly
        conn.execute(f"INSERT INTO notes ({col_names_str}) VALUES ({placeholders})", list(active_inserts.values()))

        # Normalized lookup matching logic
        if table_exists(conn, "notes_normalized"):
            norm_cols = get_column_names(conn, "notes_normalized")
            norm_map = {
                "id": note_id,
                "title": title,
                "body": body,
                "parent_id": parent_id
            }
            active_norm = {k: v for k, v in norm_map.items() if k in norm_cols}
            norm_cols_str = ", ".join(active_norm.keys())
            norm_phs = ", ".join(["?"] * len(active_norm))
            conn.execute(f"INSERT INTO notes_normalized ({norm_cols_str}) VALUES ({norm_phs})", list(active_norm.values()))

        conn.commit()
        conn.close()
        writeback_database(db_path, local_db, local_temp_dir, meta, adb_path=adb_path)

        return {
            "success": True,
            "error": "",
            "id": note_id,
            "title": title,
            "parent_id": parent_id,
            "created_time": curr_time
        }
    except Exception as e:
        return {"success": False, "error": f"Writing step failed: {str(e)}"}
    finally:
        shutil.rmtree(local_temp_dir, ignore_errors=True)

def update_note(id: str, title: str = None, body: str = None, append: bool = False, parent_id: str = None, adb_path: str = "adb") -> dict:
    try:
        db_path, local_db, local_temp_dir, meta = select_and_stage_database(adb_path=adb_path)
    except Exception as e:
        return {"success": False, "error": str(e)}

    try:
        conn = sqlite3.connect(local_db)
        if not table_exists(conn, "notes"):
            return {"success": False, "error": "Missing table: 'notes'."}

        cursor = conn.cursor()
        cursor.execute("SELECT title, body, parent_id FROM notes WHERE id = ?", (id,))
        row = cursor.fetchone()
        if not row:
            return {"success": False, "error": f"Note with exact selector ID '{id}' was not found."}

        old_title, old_body, old_parent = row[0], row[1], row[2]

        if parent_id is not None and parent_id != old_parent:
            if parent_id != "":
                cursor.execute("SELECT 1 FROM folders WHERE id = ?", (parent_id,))
                if not cursor.fetchone():
                    return {"success": False, "error": f"Specified non-existent notebook parent: '{parent_id}'"}

        final_title = title if title is not None else old_title
        final_body = old_body if old_body is not None else ""
        if body is not None:
            if append:
                final_body += body
            else:
                final_body = body
        final_parent = parent_id if parent_id is not None else old_parent

        scale = detect_timestamp_scale(conn)
        curr_time = get_current_time(scale)

        cols = get_column_names(conn, "notes")
        updates = {}
        if "title" in cols and title is not None:
            updates["title"] = final_title
        if "body" in cols and body is not None:
            updates["body"] = final_body
        if "parent_id" in cols and parent_id is not None:
            updates["parent_id"] = final_parent
        if "updated_time" in cols:
            updates["updated_time"] = curr_time

        if updates:
            set_parts = [f"{k} = ?" for k in updates.keys()]
            query = f"UPDATE notes SET {', '.join(set_parts)} WHERE id = ?"
            params = list(updates.values()) + [id]
            conn.execute(query, params)

        if table_exists(conn, "notes_normalized"):
            norm_cols = get_column_names(conn, "notes_normalized")
            norm_updates = {}
            if "title" in norm_cols and title is not None:
                norm_updates["title"] = final_title
            if "body" in norm_cols and body is not None:
                norm_updates["body"] = final_body
            if "parent_id" in norm_cols and parent_id is not None:
                norm_updates["parent_id"] = final_parent

            if norm_updates:
                n_set_parts = [f"{k} = ?" for k in norm_updates.keys()]
                n_query = f"UPDATE notes_normalized SET {', '.join(n_set_parts)} WHERE id = ?"
                n_params = list(norm_updates.values()) + [id]
                conn.execute(n_query, n_params)

        conn.commit()
        conn.close()
        writeback_database(db_path, local_db, local_temp_dir, meta, adb_path=adb_path)

        return {
            "success": True,
            "error": "",
            "id": id,
            "updated_time": curr_time
        }
    except Exception as e:
        return {"success": False, "error": f"Updating record execution failed: {str(e)}"}
    finally:
        shutil.rmtree(local_temp_dir, ignore_errors=True)

def delete_note(id: str, hard_delete: bool = False, adb_path: str = "adb") -> dict:
    try:
        db_path, local_db, local_temp_dir, meta = select_and_stage_database(adb_path=adb_path)
    except Exception as e:
        return {"success": False, "error": str(e)}

    try:
        conn = sqlite3.connect(local_db)
        if not table_exists(conn, "notes"):
            return {"success": False, "error": "Missing table: 'notes'."}

        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM notes WHERE id = ?", (id,))
        if not cursor.fetchone():
            return {"success": False, "error": f"Target note selector with exact ID '{id}' was not found in the table."}

        cols = get_column_names(conn, "notes")
        
        if not hard_delete and ("deleted" in cols or "conflict" in cols):
            updates = []
            params = []
            if "deleted" in cols:
                updates.append("deleted = 1")
            if "conflict" in cols:
                updates.append("conflict = 1")
            if "updated_time" in cols:
                scale = detect_timestamp_scale(conn)
                updates.append("updated_time = ?")
                params.append(get_current_time(scale))
                
            params.append(id)
            set_str = ", ".join(updates)
            conn.execute(f"UPDATE notes SET {set_str} WHERE id = ?", params)
        else:
            # Run true permanent structural removal safely inside a solid transaction
            conn.execute("DELETE FROM notes WHERE id = ?", (id,))
            if table_exists(conn, "notes_normalized"):
                conn.execute("DELETE FROM notes_normalized WHERE id = ?", (id,))
            if table_exists(conn, "note_tags"):
                conn.execute("DELETE FROM note_tags WHERE note_id = ?", (id,))

        conn.commit()
        conn.close()
        writeback_database(db_path, local_db, local_temp_dir, meta, adb_path=adb_path)

        return {
            "success": True,
            "error": "",
            "id": id
        }
    except Exception as e:
        return {"success": False, "error": f"Failed delete implementation step: {str(e)}"}
    finally:
        shutil.rmtree(local_temp_dir, ignore_errors=True)

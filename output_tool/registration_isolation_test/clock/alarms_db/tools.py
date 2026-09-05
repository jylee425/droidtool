import os
import sys
import json
import shlex
import sqlite3
import tempfile
import subprocess
import shutil

# Package candidates and database paths
PACKAGES = ["com.google.android.deskclock", "com.android.deskclock"]

def get_env_serial():
    return os.environ.get("ANDROID_SERIAL", None)

def run_adb_cmd(cmd_list, adb_path="adb"):
    serial = get_env_serial()
    base_cmd = [adb_path]
    if serial:
        base_cmd.extend(["-s", serial])
    full_cmd = base_cmd + cmd_list
    res = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res

def run_su_cmd(shell_cmd, adb_path="adb"):
    # Run a command via adb shell su 0
    return run_adb_cmd(["shell", "su", "0", shell_cmd], adb_path=adb_path)

def find_installed_package(adb_path="adb"):
    for pkg in PACKAGES:
        res = run_adb_cmd(["shell", "pm", "path", pkg], adb_path=adb_path)
        if res.returncode == 0 and res.stdout.strip():
            return pkg
    return None

def get_db_path(pkg):
    return f"/data/user_de/0/{pkg}/databases/alarms.db"

def force_stop_package(pkg, adb_path="adb"):
    run_adb_cmd(["shell", "am", "force-stop", pkg], adb_path=adb_path)

def get_file_metadata(remote_path, adb_path="adb"):
    # Returns (uid, gid, mode, selinux_context) or None
    cmd = f"stat -c '%u %g %a' {shlex.quote(remote_path)} && ls -Z {shlex.quote(remote_path)}"
    res = run_su_cmd(cmd, adb_path=adb_path)
    if res.returncode != 0:
        return None
    lines = res.stdout.strip().splitlines()
    if not lines:
        return None
    try:
        parts = lines[0].split()
        uid, gid, mode = parts[0], parts[1], parts[2]
        se_context = "u:object_r:no_context:s0"
        if len(lines) > 1:
            se_parts = lines[1].split()
            if se_parts:
                se_context = se_parts[0]
        return uid, gid, mode, se_context
    except Exception:
        return None

def apply_metadata(remote_path, metadata, adb_path="adb"):
    if not metadata:
        return
    uid, gid, mode, se_context = metadata
    run_su_cmd(f"chown {uid}:{gid} {shlex.quote(remote_path)}", adb_path=adb_path)
    run_su_cmd(f"chmod {mode} {shlex.quote(remote_path)}", adb_path=adb_path)
    if se_context:
        run_su_cmd(f"chcon {shlex.quote(se_context)} {shlex.quote(remote_path)}", adb_path=adb_path)

def pull_database(remote_db_path, adb_path="adb"):
    # Pull main db and sidecars to a local temp directory
    temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(temp_dir, "alarms.db")
    
    # Stage files to a readable location
    stage_dir = "/data/local/tmp/alarms_pull"
    run_su_cmd(f"rm -rf {stage_dir} && mkdir -p {stage_dir}", adb_path=adb_path)
    
    # Copy main db and sidecars if they exist
    for ext in ["", "-wal", "-shm"]:
        remote_file = remote_db_path + ext
        check_res = run_su_cmd(f"test -f {shlex.quote(remote_file)}", adb_path=adb_path)
        if check_res.returncode == 0:
            run_su_cmd(f"cp {shlex.quote(remote_file)} {stage_dir}/", adb_path=adb_path)
            run_su_cmd(f"chmod 666 {stage_dir}/" + os.path.basename(remote_file), adb_path=adb_path)
            
            # Pull to local
            local_file = local_db + ext
            run_adb_cmd(["pull", f"{stage_dir}/" + os.path.basename(remote_file), local_file], adb_path=adb_path)
            
    run_su_cmd(f"rm -rf {stage_dir}", adb_path=adb_path)
    return local_db, temp_dir

def push_database(local_db, remote_db_path, adb_path="adb"):
    # Capture original metadata
    meta_db = get_file_metadata(remote_db_path, adb_path=adb_path)
    meta_wal = get_file_metadata(remote_db_path + "-wal", adb_path=adb_path)
    meta_shm = get_file_metadata(remote_db_path + "-shm", adb_path=adb_path)
    
    stage_dir = "/data/local/tmp/alarms_push"
    run_su_cmd(f"rm -rf {stage_dir} && mkdir -p {stage_dir}", adb_path=adb_path)
    run_su_cmd(f"chmod 777 {stage_dir}", adb_path=adb_path)
    
    # Push local files to staging
    for ext in ["", "-wal", "-shm"]:
        local_file = local_db + ext
        if os.path.exists(local_file):
            remote_stage_file = f"{stage_dir}/alarms.db{ext}"
            run_adb_cmd(["push", local_file, remote_stage_file], adb_path=adb_path)
            run_su_cmd(f"chmod 666 {remote_stage_file}", adb_path=adb_path)
            
            # Copy to final destination
            final_dest = remote_db_path + ext
            run_su_cmd(f"cp {remote_stage_file} {shlex.quote(final_dest)}", adb_path=adb_path)
            
            # Restore metadata
            meta = meta_db if ext == "" else (meta_wal if ext == "-wal" else meta_shm)
            if meta:
                apply_metadata(final_dest, meta, adb_path=adb_path)
            else:
                # If sidecar didn't exist before, match main DB metadata
                if meta_db:
                    apply_metadata(final_dest, meta_db, adb_path=adb_path)
        else:
            # If local sidecar doesn't exist, remove remote sidecar to avoid inconsistency
            run_su_cmd(f"rm -f {shlex.quote(remote_db_path + ext)}", adb_path=adb_path)
            
    run_su_cmd(f"rm -rf {stage_dir}", adb_path=adb_path)

def list_alarms(adb_path: str = "adb") -> dict:
    pkg = find_installed_package(adb_path=adb_path)
    if not pkg:
        return {"alarms": [], "success": False, "error": "No supported Clock package found installed."}
        
    remote_db = get_db_path(pkg)
    # Check if DB exists
    check_db = run_su_cmd(f"test -f {shlex.quote(remote_db)}", adb_path=adb_path)
    if check_db.returncode != 0:
        return {"alarms": [], "success": True, "error": ""}
        
    try:
        local_db, temp_dir = pull_database(remote_db, adb_path=adb_path)
    except Exception as e:
        return {"alarms": [], "success": False, "error": f"Failed to pull database: {str(e)}"}
        
    try:
        conn = sqlite3.connect(local_db)
        cursor = conn.cursor()
        
        # Check if alarm_templates table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alarm_templates'")
        if not cursor.fetchone():
            conn.close()
            return {"alarms": [], "success": True, "error": ""}
            
        # Discover columns
        cursor.execute("PRAGMA table_info(alarm_templates)")
        columns = [row[1] for row in cursor.fetchall()]
        
        # Build query dynamically based on available columns
        select_cols = ["_id", "hour", "minutes", "daysofweek", "enabled"]
        optional_cols = ["label", "vibrate", "delete_after_use"]
        for col in optional_cols:
            if col in columns:
                select_cols.append(col)
                
        query = f"SELECT {', '.join(select_cols)} FROM alarm_templates"
        cursor.execute(query)
        rows = cursor.fetchall()
        
        alarms = []
        for row in rows:
            alarm_dict = {}
            for idx, col in enumerate(select_cols):
                val = row[idx]
                if col == "_id":
                    alarm_dict["id"] = val
                elif col == "enabled":
                    alarm_dict["enabled"] = bool(val)
                elif col in ["vibrate", "delete_after_use"]:
                    alarm_dict[col] = bool(val)
                else:
                    alarm_dict[col] = val
            alarms.append(alarm_dict)
            
        conn.close()
        return {"alarms": alarms, "success": True, "error": ""}
    except Exception as e:
        return {"alarms": [], "success": False, "error": f"Database operation failed: {str(e)}"}
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

def create_alarm(hour: int, minutes: int, daysofweek: int = 0, enabled: bool = True, label: str = None, vibrate: bool = None, delete_after_use: bool = None, adb_path: str = "adb") -> dict:
    pkg = find_installed_package(adb_path=adb_path)
    if not pkg:
        return {"id": -1, "success": False, "error": "No supported Clock package found installed."}
        
    remote_db = get_db_path(pkg)
    # If database doesn't exist, we attempt to initialize the package by launching it
    check_db = run_su_cmd(f"test -f {shlex.quote(remote_db)}", adb_path=adb_path)
    if check_db.returncode != 0:
        run_adb_cmd(["shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], adb_path=adb_path)
        # Wait briefly and check again
        import time
        time.sleep(2)
        check_db = run_su_cmd(f"test -f {shlex.quote(remote_db)}", adb_path=adb_path)
        if check_db.returncode != 0:
            return {"id": -1, "success": False, "error": "Clock database could not be initialized."}
            
    force_stop_package(pkg, adb_path=adb_path)
    
    try:
        local_db, temp_dir = pull_database(remote_db, adb_path=adb_path)
    except Exception as e:
        return {"id": -1, "success": False, "error": f"Failed to pull database: {str(e)}"}
        
    try:
        conn = sqlite3.connect(local_db)
        cursor = conn.cursor()
        
        cursor.execute("PRAGMA table_info(alarm_templates)")
        columns = [row[1] for row in cursor.fetchall()]
        
        insert_data = {
            "hour": hour,
            "minutes": minutes,
            "daysofweek": daysofweek,
            "enabled": 1 if enabled else 0
        }
        
        if "label" in columns and label is not None:
            insert_data["label"] = label
        if "vibrate" in columns and vibrate is not None:
            insert_data["vibrate"] = 1 if vibrate else 0
        if "delete_after_use" in columns and delete_after_use is not None:
            insert_data["delete_after_use"] = 1 if delete_after_use else 0
            
        cols = ", ".join(insert_data.keys())
        placeholders = ", ".join(["?"] * len(insert_data))
        query = f"INSERT INTO alarm_templates ({cols}) VALUES ({placeholders})"
        
        cursor.execute(query, tuple(insert_data.values()))
        new_id = cursor.lastrowid
        
        conn.commit()
        # Checkpoint WAL after commit to ensure changes are safely written
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        
        push_database(local_db, remote_db, adb_path=adb_path)
        return {"id": new_id, "success": True, "error": ""}
    except Exception as e:
        return {"id": -1, "success": False, "error": f"Database operation failed: {str(e)}"}
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

def update_alarm(id: int, hour: int = None, minutes: int = None, daysofweek: int = None, enabled: bool = None, label: str = None, vibrate: bool = None, delete_after_use: bool = None, adb_path: str = "adb") -> dict:
    pkg = find_installed_package(adb_path=adb_path)
    if not pkg:
        return {"success": False, "error": "No supported Clock package found installed."}
        
    remote_db = get_db_path(pkg)
    check_db = run_su_cmd(f"test -f {shlex.quote(remote_db)}", adb_path=adb_path)
    if check_db.returncode != 0:
        return {"success": False, "error": "Clock database does not exist."}
        
    force_stop_package(pkg, adb_path=adb_path)
    
    try:
        local_db, temp_dir = pull_database(remote_db, adb_path=adb_path)
    except Exception as e:
        return {"success": False, "error": f"Failed to pull database: {str(e)}"}
        
    try:
        conn = sqlite3.connect(local_db)
        cursor = conn.cursor()
        
        cursor.execute("SELECT _id FROM alarm_templates WHERE _id = ?", (id,))
        if not cursor.fetchone():
            conn.close()
            return {"success": False, "error": f"Alarm with ID {id} not found."}
            
        cursor.execute("PRAGMA table_info(alarm_templates)")
        columns = [row[1] for row in cursor.fetchall()]
        
        update_data = {}
        if hour is not None:
            update_data["hour"] = hour
        if minutes is not None:
            update_data["minutes"] = minutes
        if daysofweek is not None:
            update_data["daysofweek"] = daysofweek
        if enabled is not None:
            update_data["enabled"] = 1 if enabled else 0
        if "label" in columns and label is not None:
            update_data["label"] = label
        if "vibrate" in columns and vibrate is not None:
            update_data["vibrate"] = 1 if vibrate else 0
        if "delete_after_use" in columns and delete_after_use is not None:
            update_data["delete_after_use"] = 1 if delete_after_use else 0
            
        if not update_data:
            conn.close()
            return {"success": True, "error": ""}
            
        set_clause = ", ".join([f"{col} = ?" for col in update_data.keys()])
        query = f"UPDATE alarm_templates SET {set_clause} WHERE _id = ?"
        params = list(update_data.values()) + [id]
        
        cursor.execute(query, params)
        conn.commit()
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        
        push_database(local_db, remote_db, adb_path=adb_path)
        return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": f"Database operation failed: {str(e)}"}
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

def delete_alarm(id: int, adb_path: str = "adb") -> dict:
    pkg = find_installed_package(adb_path=adb_path)
    if not pkg:
        return {"success": False, "error": "No supported Clock package found installed."}
        
    remote_db = get_db_path(pkg)
    check_db = run_su_cmd(f"test -f {shlex.quote(remote_db)}", adb_path=adb_path)
    if check_db.returncode != 0:
        return {"success": False, "error": "Clock database does not exist."}
        
    force_stop_package(pkg, adb_path=adb_path)
    
    try:
        local_db, temp_dir = pull_database(remote_db, adb_path=adb_path)
    except Exception as e:
        return {"success": False, "error": f"Failed to pull database: {str(e)}"}
        
    try:
        conn = sqlite3.connect(local_db)
        cursor = conn.cursor()
        
        cursor.execute("SELECT _id FROM alarm_templates WHERE _id = ?", (id,))
        if not cursor.fetchone():
            conn.close()
            return {"success": False, "error": f"Alarm with ID {id} not found."}
            
        # Delete from alarm_templates
        cursor.execute("DELETE FROM alarm_templates WHERE _id = ?", (id,))
        
        # Delete related alarm_instances to maintain relationship consistency
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alarm_instances'")
        if cursor.fetchone():
            cursor.execute("DELETE FROM alarm_instances WHERE alarm_id = ?", (id,))
            
        conn.commit()
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        
        push_database(local_db, remote_db, adb_path=adb_path)
        return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": f"Database operation failed: {str(e)}"}
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

import os
import sys
import json
import sqlite3
import subprocess
import tempfile
import shlex

# Constants
PACKAGE_NAME = "code.name.monkey.retromusic"
DB_PATH = "/data/data/code.name.monkey.retromusic/databases/music_playback_state.db"

def run_adb_cmd(cmd, adb_path="adb"):
    """Helper to run an ADB command and return stdout, stderr, and return code."""
    serial = os.environ.get("ANDROID_SERIAL")
    base_cmd = [adb_path]
    if serial:
        base_cmd.extend(["-s", serial])
    full_cmd = base_cmd + cmd
    res = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.stdout, res.stderr, res.returncode

def run_su_cmd(shell_cmd, adb_path="adb"):
    """Helper to run a command as root on the device."""
    return run_adb_cmd(["shell", "su", "0", shell_cmd], adb_path=adb_path)

def get_file_metadata(path, adb_path="adb"):
    """Discovers uid, gid, mode, and SELinux context of a remote file."""
    # Get uid, gid, mode
    stdout, _, rc = run_su_cmd(f"stat -c '%u %g %a' {path}", adb_path=adb_path)
    if rc != 0 or not stdout.strip():
        return None
    parts = stdout.strip().split()
    if len(parts) < 3:
        return None
    uid, gid, mode = parts[0], parts[1], parts[2]
    
    # Get SELinux context
    selinux = ""
    stdout_sel, _, rc_sel = run_su_cmd(f"ls -Z {path}", adb_path=adb_path)
    if rc_sel == 0 and stdout_sel.strip():
        selinux = stdout_sel.strip().split()[0]
    
    return {"uid": uid, "gid": gid, "mode": mode, "selinux": selinux}

def apply_file_metadata(path, meta, adb_path="adb"):
    """Applies uid, gid, mode, and SELinux context to a remote file."""
    if not meta:
        return
    run_su_cmd(f"chown {meta['uid']}:{meta['gid']} {path}", adb_path=adb_path)
    run_su_cmd(f"chmod {meta['mode']} {path}", adb_path=adb_path)
    if meta.get("selinux"):
        run_su_cmd(f"chcon {meta['selinux']} {path}", adb_path=adb_path)

def pull_db_snapshot(adb_path="adb"):
    """Pulls the remote DB and its WAL/SHM sidecars to a local temp directory."""
    temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(temp_dir, "music_playback_state.db")
    
    # Check if remote DB exists
    stdout, _, rc = run_su_cmd(f"ls {DB_PATH}", adb_path=adb_path)
    if rc != 0:
        return None, "Database file does not exist on device."
    
    # Stage files to a readable temp path on device
    device_temp = "/data/local/tmp/music_playback_state_temp"
    run_su_cmd(f"mkdir -p {device_temp}", adb_path=adb_path)
    run_su_cmd(f"cp {DB_PATH}* {device_temp}/", adb_path=adb_path)
    run_su_cmd(f"chmod -R 777 {device_temp}", adb_path=adb_path)
    
    # Pull files
    for ext in ["", "-wal", "-shm"]:
        remote_file = f"{device_temp}/music_playback_state.db{ext}"
        local_file = f"{local_db}{ext}"
        # Check if remote file exists before pulling
        _, _, check_rc = run_su_cmd(f"ls {remote_file}", adb_path=adb_path)
        if check_rc == 0:
            run_adb_cmd(["pull", remote_file, local_file], adb_path=adb_path)
            
    # Clean up device temp
    run_su_cmd(f"rm -rf {device_temp}", adb_path=adb_path)
    return local_db, ""

def push_db_snapshot(local_db, adb_path="adb"):
    """Pushes the local DB and its WAL/SHM sidecars back to the device safely."""
    # Discover original metadata
    meta = get_file_metadata(DB_PATH, adb_path=adb_path)
    if not meta:
        # If we can't read metadata, try to get it from the parent directory
        parent_meta = get_file_metadata("/data/data/code.name.monkey.retromusic/databases", adb_path=adb_path)
        if parent_meta:
            meta = {"uid": parent_meta["uid"], "gid": parent_meta["gid"], "mode": "660", "selinux": ""}
        else:
            return False, "Could not discover original file metadata or ownership."
            
    # Quiesce the app
    run_adb_cmd(["shell", "am", "force-stop", PACKAGE_NAME], adb_path=adb_path)
    
    # Stage locally modified files to device temp
    device_temp = "/data/local/tmp/music_playback_state_temp"
    run_su_cmd(f"mkdir -p {device_temp}", adb_path=adb_path)
    run_su_cmd(f"chmod 777 {device_temp}", adb_path=adb_path)
    
    for ext in ["", "-wal", "-shm"]:
        local_file = f"{local_db}{ext}"
        remote_temp_file = f"{device_temp}/music_playback_state.db{ext}"
        if os.path.exists(local_file):
            run_adb_cmd(["push", local_file, remote_temp_file], adb_path=adb_path)
            run_su_cmd(f"chmod 777 {remote_temp_file}", adb_path=adb_path)
        else:
            # If sidecar doesn't exist locally, remove it on remote to avoid mismatch
            run_su_cmd(f"rm -f {DB_PATH}{ext}", adb_path=adb_path)
            
    # Copy from temp to final destination and restore metadata
    for ext in ["", "-wal", "-shm"]:
        remote_temp_file = f"{device_temp}/music_playback_state.db{ext}"
        final_dest = f"{DB_PATH}{ext}"
        _, _, check_rc = run_su_cmd(f"ls {remote_temp_file}", adb_path=adb_path)
        if check_rc == 0:
            run_su_cmd(f"cp {remote_temp_file} {final_dest}", adb_path=adb_path)
            apply_file_metadata(final_dest, meta, adb_path=adb_path)
            
    # Clean up device temp
    run_su_cmd(f"rm -rf {device_temp}", adb_path=adb_path)
    return True, ""

def query_mediastore_songs(song_ids, adb_path="adb"):
    """Queries MediaStore content provider for song titles by ID."""
    if not song_ids:
        return {}, ""
    
    # Build content query command
    id_list = ",".join(str(sid) for sid in song_ids)
    projection = "_id,title"
    uri = "content://media/external/audio/media"
    where = f"_id IN ({id_list})"
    
    # Execute content query via adb shell content query with properly escaped arguments
    cmd = ["shell", "content", "query", "--uri", uri, "--projection", projection, "--where", shlex.quote(where)]
    stdout, stderr, rc = run_adb_cmd(cmd, adb_path=adb_path)
    if rc != 0:
        return None, f"Failed to query MediaStore: {stderr or stdout}"
        
    # Parse content query output
    # Format is typically: Row: 0 _id=123, title=Song Title
    results = {}
    for line in stdout.splitlines():
        if not line.strip() or "Row:" not in line:
            continue
        parts = line.split(", ")
        row_id = None
        row_title = None
        for part in parts:
            if "_id=" in part:
                try:
                    row_id = int(part.split("=")[1])
                except ValueError:
                    pass
            elif "title=" in part:
                row_title = part.split("=")[1]
        if row_id is not None and row_title is not None:
            results[row_id] = row_title
            
    return results, ""

def get_playing_queue(adb_path: str = "adb") -> dict:
    """Retrieve the active playing queue from Retro Music's private database."""
    local_db, err = pull_db_snapshot(adb_path=adb_path)
    if err:
        return {"success": False, "error": err, "queue": []}
        
    try:
        conn = sqlite3.connect(local_db)
        cursor = conn.cursor()
        
        # Discover schema
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        
        if "playing_queue" not in tables:
            conn.close()
            return {"success": False, "error": "playing_queue table not found in database.", "queue": []}
            
        # Discover columns
        cursor.execute("PRAGMA table_info(playing_queue);")
        columns = [row[1] for row in cursor.fetchall()]
        
        # Determine available columns
        has_position = any(col in columns for col in ["position", "play_order", "queue_position", "song_key"])
        pos_col = next((col for col in ["position", "play_order", "queue_position", "song_key"] if col in columns), None)
        has_song_id = any(col in columns for col in ["song_id", "id", "_id", "songId"])
        id_col = next((col for col in ["song_id", "id", "_id", "songId"] if col in columns), None)
        
        # Build query
        select_cols = ["title"]
        if pos_col:
            select_cols.append(pos_col)
        if id_col:
            select_cols.append(id_col)
            
        query = f"SELECT {', '.join(select_cols)} FROM playing_queue"
        if pos_col:
            query += f" ORDER BY {pos_col} ASC"
            
        cursor.execute(query)
        rows = cursor.fetchall()
        
        queue = []
        for idx, row in enumerate(rows):
            item = {}
            # Title is always first
            item["title"] = row[0]
            
            # Position
            if pos_col:
                item["queue_position"] = row[select_cols.index(pos_col)]
            else:
                item["queue_position"] = idx
                
            # Song ID
            if id_col:
                item["song_id"] = row[select_cols.index(id_col)]
            else:
                item["song_id"] = None
                
            queue.append(item)
            
        conn.close()
        return {"success": True, "error": "", "queue": queue}
        
    except Exception as e:
        return {"success": False, "error": f"Database error: {str(e)}", "queue": []}
    finally:
        if os.path.exists(local_db):
            # Clean up local temp files
            for ext in ["", "-wal", "-shm"]:
                if os.path.exists(local_db + ext):
                    os.remove(local_db + ext)

def set_playing_queue(song_ids: list, append: bool, adb_path: str = "adb") -> dict:
    """Set or append songs to the playing queue by resolving MediaStore song IDs and updating the private database."""
    # Resolve song metadata from MediaStore
    resolved_songs, err = query_mediastore_songs(song_ids, adb_path=adb_path)
    if err:
        return {"success": False, "error": err}
        
    # Verify all requested song IDs were resolved
    unresolved = [sid for sid in song_ids if sid not in resolved_songs]
    if unresolved:
        return {"success": False, "error": f"Could not resolve MediaStore song IDs: {unresolved}"}
        
    local_db, err = pull_db_snapshot(adb_path=adb_path)
    if err:
        return {"success": False, "error": err}
        
    try:
        conn = sqlite3.connect(local_db)
        cursor = conn.cursor()
        
        # Discover schema
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        
        if "playing_queue" not in tables:
            conn.close()
            return {"success": False, "error": "playing_queue table not found in database."}
            
        # Discover columns for playing_queue
        cursor.execute("PRAGMA table_info(playing_queue);")
        columns = [row[1] for row in cursor.fetchall()]
        
        pos_col = next((col for col in ["position", "play_order", "queue_position", "song_key"] if col in columns), None)
        id_col = next((col for col in ["song_id", "id", "_id", "songId"] if col in columns), None)
        
        # If not appending, clear the queue
        if not append:
            cursor.execute("DELETE FROM playing_queue;")
            if "original_playing_queue" in tables:
                cursor.execute("DELETE FROM original_playing_queue;")
                
        # Determine starting position
        start_pos = 0
        if append and pos_col:
            cursor.execute(f"SELECT MAX({pos_col}) FROM playing_queue;")
            max_pos = cursor.fetchone()[0]
            if max_pos is not None:
                start_pos = max_pos + 1
        elif append:
            cursor.execute("SELECT COUNT(*) FROM playing_queue;")
            start_pos = cursor.fetchone()[0]
            
        # Insert new songs
        for idx, sid in enumerate(song_ids):
            title = resolved_songs[sid]
            current_pos = start_pos + idx
            
            # Build dynamic insert
            insert_cols = ["title"]
            insert_vals = [title]
            
            if pos_col:
                insert_cols.append(pos_col)
                insert_vals.append(current_pos)
            if id_col:
                insert_cols.append(id_col)
                insert_vals.append(sid)
                
            placeholders = ", ".join(["?"] * len(insert_vals))
            query = f"INSERT INTO playing_queue ({', '.join(insert_cols)}) VALUES ({placeholders});"
            cursor.execute(query, insert_vals)
            
            # Mirror to original_playing_queue if supported
            if "original_playing_queue" in tables:
                # Discover columns for original_playing_queue
                cursor.execute("PRAGMA table_info(original_playing_queue);")
                orig_columns = [row[1] for row in cursor.fetchall()]
                orig_pos_col = next((col for col in ["position", "play_order", "queue_position", "song_key"] if col in orig_columns), None)
                orig_id_col = next((col for col in ["song_id", "id", "_id", "songId"] if col in orig_columns), None)
                
                orig_cols = ["title"]
                orig_vals = [title]
                if orig_pos_col:
                    orig_cols.append(orig_pos_col)
                    orig_vals.append(current_pos)
                if orig_id_col:
                    orig_cols.append(orig_id_col)
                    orig_vals.append(sid)
                    
                orig_placeholders = ", ".join(["?"] * len(orig_vals))
                orig_query = f"INSERT INTO original_playing_queue ({', '.join(orig_cols)}) VALUES ({orig_placeholders});"
                cursor.execute(orig_query, orig_vals)
                
        # Get final queue size
        cursor.execute("SELECT COUNT(*) FROM playing_queue;")
        queue_size = cursor.fetchone()[0]
        
        conn.commit()
        conn.close()
        
        # Push database back to device
        success, push_err = push_db_snapshot(local_db, adb_path=adb_path)
        if not success:
            return {"success": False, "error": f"Failed to write back database: {push_err}"}
            
        return {"success": True, "error": "", "queue_size": queue_size}
        
    except Exception as e:
        return {"success": False, "error": f"Database mutation error: {str(e)}"}
    finally:
        if os.path.exists(local_db):
            for ext in ["", "-wal", "-shm"]:
                if os.path.exists(local_db + ext):
                    os.remove(local_db + ext)

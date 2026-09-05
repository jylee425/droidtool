import os
import sys
import json
import time
import sqlite3
import shutil
import tempfile
import subprocess

# Package and DB details
PKG_NAME = "org.wikipedia"
REMOTE_DB_DIR = "/data/data/org.wikipedia/databases"
REMOTE_DB_PATH = f"{REMOTE_DB_DIR}/wikipedia.db"

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
    _run_cmd(prefix + ["shell", "am", "force-stop", PKG_NAME])

def _get_db_metadata(adb_path):
    prefix = _get_adb_prefix(adb_path)
    # Find uid, gid, mode, and SELinux context of the remote database file
    cmd = prefix + ["shell", "su", "0", f"stat -c '%u %g %a %C' {REMOTE_DB_PATH}"]
    code, out, err = _run_cmd(cmd)
    if code != 0 or not out.strip():
        # Fallback to checking the databases directory if file doesn't exist yet
        cmd = prefix + ["shell", "su", "0", f"stat -c '%u %g %a %C' {REMOTE_DB_DIR}"]
        code, out, err = _run_cmd(cmd)
        if code != 0 or not out.strip():
            raise RuntimeError(f"Failed to discover database metadata: {err.strip()}")
    parts = out.strip().split()
    if len(parts) < 4:
        raise RuntimeError(f"Unexpected stat output: {out.strip()}")
    return {
        "uid": parts[0],
        "gid": parts[1],
        "mode": parts[2],
        "secontext": parts[3]
    }

def _pull_db(adb_path, local_dir):
    prefix = _get_adb_prefix(adb_path)
    # Stage files to a readable temporary device path
    device_temp_dir = "/data/local/tmp/wikipedia_db_stage"
    _run_cmd(prefix + ["shell", "su", "0", f"mkdir -p {device_temp_dir} && chmod 777 {device_temp_dir}"])
    
    # Copy main db and sidecars if they exist
    for ext in ["", "-wal", "-shm"]:
        remote_file = f"{REMOTE_DB_PATH}{ext}"
        stage_file = f"{device_temp_dir}/wikipedia.db{ext}"
        _run_cmd(prefix + ["shell", "su", "0", f"cp {remote_file} {stage_file} && chmod 666 {stage_file}"])
        
        local_file = os.path.join(local_dir, f"wikipedia.db{ext}")
        _run_cmd(prefix + ["pull", stage_file, local_file])
    
    # Clean up staging area
    _run_cmd(prefix + ["shell", "su", "0", f"rm -rf {device_temp_dir}"])

def _push_db(adb_path, local_dir, meta):
    prefix = _get_adb_prefix(adb_path)
    device_temp_dir = "/data/local/tmp/wikipedia_db_stage"
    _run_cmd(prefix + ["shell", "su", "0", f"mkdir -p {device_temp_dir} && chmod 777 {device_temp_dir}"])
    
    # Push local files to staging area
    for ext in ["", "-wal", "-shm"]:
        local_file = os.path.join(local_dir, f"wikipedia.db{ext}")
        if os.path.exists(local_file):
            stage_file = f"{device_temp_dir}/wikipedia.db{ext}"
            _run_cmd(prefix + ["push", local_file, stage_file])
            
            # Copy back to app-private path and restore metadata
            remote_file = f"{REMOTE_DB_PATH}{ext}"
            _run_cmd(prefix + ["shell", "su", "0", f"cp {stage_file} {remote_file}"])
            _run_cmd(prefix + ["shell", "su", "0", f"chown {meta['uid']}:{meta['gid']} {remote_file}"])
            _run_cmd(prefix + ["shell", "su", "0", f"chmod {meta['mode']} {remote_file}"])
            _run_cmd(prefix + ["shell", "su", "0", f"chcon {meta['secontext']} {remote_file}"])
        else:
            # If sidecar was deleted locally, remove it remotely
            remote_file = f"{REMOTE_DB_PATH}{ext}"
            _run_cmd(prefix + ["shell", "su", "0", f"rm -f {remote_file}"])
            
    _run_cmd(prefix + ["shell", "su", "0", f"rm -rf {device_temp_dir}"])

class DBTransaction:
    def __init__(self, adb_path, write=False):
        self.adb_path = adb_path
        self.write = write
        self.temp_dir = None
        self.db_path = None
        self.meta = None

    def __enter__(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "wikipedia.db")
        if self.write:
            _quiesce_app(self.adb_path)
            self.meta = _get_db_metadata(self.adb_path)
        _pull_db(self.adb_path, self.temp_dir)
        return self.db_path

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None and self.write:
                # Checkpoint WAL state locally before pushing back
                try:
                    conn = sqlite3.connect(self.db_path)
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    conn.close()
                except Exception:
                    pass
                _push_db(self.adb_path, self.temp_dir, self.meta)
        finally:
            if self.temp_dir and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)

def list_reading_lists(adb_path: str = "adb") -> dict:
    try:
        with DBTransaction(adb_path, write=False) as db_path:
            if not os.path.exists(db_path):
                return {"reading_lists": [], "success": True, "error": ""}
            
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Check if table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ReadingList';")
            if not cursor.fetchone():
                conn.close()
                return {"reading_lists": [], "success": True, "error": ""}
                
            cursor.execute("SELECT id, listTitle, description, mtime, atime, sizeBytes, remoteId FROM ReadingList")
            rows = cursor.fetchall()
            lists = []
            for r in rows:
                lists.append({
                    "id": r["id"],
                    "listTitle": r["listTitle"],
                    "description": r["description"],
                    "mtime": r["mtime"],
                    "atime": r["atime"],
                    "sizeBytes": r["sizeBytes"],
                    "remoteId": r["remoteId"]
                })
            conn.close()
            return {"reading_lists": lists, "success": True, "error": ""}
    except Exception as e:
        return {"reading_lists": [], "success": False, "error": str(e)}

def create_reading_list(listTitle: str, description: str = None, adb_path: str = "adb") -> dict:
    if not listTitle:
        return {"success": False, "id": -1, "error": "List title cannot be empty"}
    try:
        with DBTransaction(adb_path, write=True) as db_path:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            now_ms = int(time.time() * 1000)
            try:
                cursor.execute("""
                    INSERT INTO ReadingList (listTitle, description, mtime, atime, sizeBytes, dirty)
                    VALUES (?, ?, ?, ?, 0, 1)
                """, (listTitle, description, now_ms, now_ms))
                new_id = cursor.lastrowid
                conn.commit()
            except sqlite3.IntegrityError as ie:
                conn.close()
                return {"success": False, "id": -1, "error": f"Database constraint violation: {str(ie)}"}
            conn.close()
            return {"success": True, "id": new_id, "error": ""}
    except Exception as e:
        return {"success": False, "id": -1, "error": str(e)}

def delete_reading_list(listId: int, adb_path: str = "adb") -> dict:
    try:
        with DBTransaction(adb_path, write=True) as db_path:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Verify existence
            cursor.execute("SELECT id FROM ReadingList WHERE id = ?", (listId,))
            if not cursor.fetchone():
                conn.close()
                return {"success": False, "error": f"Reading list with ID {listId} does not exist"}
                
            # Delete list and associated pages in a single transaction
            cursor.execute("BEGIN TRANSACTION;")
            try:
                cursor.execute("DELETE FROM ReadingListPage WHERE listId = ?", (listId,))
                cursor.execute("DELETE FROM ReadingList WHERE id = ?", (listId,))
                conn.commit()
            except Exception as te:
                cursor.execute("ROLLBACK;")
                conn.close()
                return {"success": False, "error": f"Transaction failed: {str(te)}"}
            
            conn.close()
            return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_reading_list_pages(listId: int, adb_path: str = "adb") -> dict:
    try:
        with DBTransaction(adb_path, write=False) as db_path:
            if not os.path.exists(db_path):
                return {"pages": [], "success": True, "error": ""}
                
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ReadingListPage';")
            if not cursor.fetchone():
                conn.close()
                return {"pages": [], "success": True, "error": ""}
                
            cursor.execute("""
                SELECT id, wiki, lang, displayTitle, apiTitle, description, offline, sizeBytes 
                FROM ReadingListPage 
                WHERE listId = ?
            """, (listId,))
            rows = cursor.fetchall()
            pages = []
            for r in rows:
                pages.append({
                    "id": r["id"],
                    "wiki": r["wiki"],
                    "lang": r["lang"],
                    "displayTitle": r["displayTitle"],
                    "apiTitle": r["apiTitle"],
                    "description": r["description"],
                    "offlineStatus": r["offline"],
                    "sizeBytes": r["sizeBytes"]
                })
            conn.close()
            return {"pages": pages, "success": True, "error": ""}
    except Exception as e:
        return {"pages": [], "success": False, "error": str(e)}

def add_page_to_reading_list(listId: int, wiki: str, lang: str, displayTitle: str, apiTitle: str, description: str = None, adb_path: str = "adb") -> dict:
    try:
        with DBTransaction(adb_path, write=True) as db_path:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Verify parent list exists
            cursor.execute("SELECT id FROM ReadingList WHERE id = ?", (listId,))
            if not cursor.fetchone():
                conn.close()
                return {"success": False, "pageId": -1, "error": f"Target listId {listId} does not exist"}
                
            now_ms = int(time.time() * 1000)
            cursor.execute("BEGIN TRANSACTION;")
            try:
                # Insert page
                cursor.execute("""
                    INSERT INTO ReadingListPage (listId, wiki, lang, displayTitle, apiTitle, description, offline, status, sizeBytes)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0)
                """, (listId, wiki, lang, displayTitle, apiTitle, description))
                page_id = cursor.lastrowid
                
                # Update parent list metadata
                cursor.execute("""
                    UPDATE ReadingList 
                    SET mtime = ?, sizeBytes = sizeBytes + 0, dirty = 1 
                    WHERE id = ?
                """, (now_ms, listId))
                
                conn.commit()
            except Exception as te:
                cursor.execute("ROLLBACK;")
                conn.close()
                return {"success": False, "pageId": -1, "error": f"Transaction failed: {str(te)}"}
                
            conn.close()
            return {"success": True, "pageId": page_id, "error": ""}
    except Exception as e:
        return {"success": False, "pageId": -1, "error": str(e)}

def remove_page_from_reading_list(pageId: int, adb_path: str = "adb") -> dict:
    try:
        with DBTransaction(adb_path, write=True) as db_path:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Find parent list ID and page size
            cursor.execute("SELECT listId, sizeBytes FROM ReadingListPage WHERE id = ?", (pageId,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return {"success": False, "error": f"Page with ID {pageId} does not exist"}
            list_id, page_size = row[0], row[1] or 0
            
            now_ms = int(time.time() * 1000)
            cursor.execute("BEGIN TRANSACTION;")
            try:
                # Delete page
                cursor.execute("DELETE FROM ReadingListPage WHERE id = ?", (pageId,))
                
                # Update parent list metadata
                cursor.execute("""
                    UPDATE ReadingList 
                    SET mtime = ?, sizeBytes = MAX(0, sizeBytes - ?), dirty = 1 
                    WHERE id = ?
                """, (now_ms, page_size, list_id))
                
                conn.commit()
            except Exception as te:
                cursor.execute("ROLLBACK;")
                conn.close()
                return {"success": False, "error": f"Transaction failed: {str(te)}"}
                
            conn.close()
            return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_history_and_searches(limit: int = None, adb_path: str = "adb") -> dict:
    try:
        with DBTransaction(adb_path, write=False) as db_path:
            if not os.path.exists(db_path):
                return {"recent_searches": [], "history_entries": [], "success": True, "error": ""}
                
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Fetch recent searches
            searches = []
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='RecentSearch';")
            if cursor.fetchone():
                query = "SELECT text, timestamp FROM RecentSearch ORDER BY timestamp DESC"
                if limit is not None:
                    query += f" LIMIT {int(limit)}"
                cursor.execute(query)
                for r in cursor.fetchall():
                    searches.append({
                        "text": r["text"],
                        "timestamp": r["timestamp"]
                    })
                    
            # Fetch history entries
            history = []
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='HistoryEntry';")
            if cursor.fetchone():
                query = "SELECT title, lang, timestamp, timeSpentSec FROM HistoryEntry ORDER BY timestamp DESC"
                if limit is not None:
                    query += f" LIMIT {int(limit)}"
                cursor.execute(query)
                for r in cursor.fetchall():
                    history.append({
                        "title": r["title"],
                        "lang": r["lang"],
                        "timestamp": r["timestamp"],
                        "timeSpentSec": r["timeSpentSec"]
                    })
                    
            conn.close()
            return {
                "recent_searches": searches,
                "history_entries": history,
                "success": True,
                "error": ""
            }
    except Exception as e:
        return {"recent_searches": [], "history_entries": [], "success": False, "error": str(e)}

def clear_history_and_searches(clear_searches: bool, clear_history: bool, adb_path: str = "adb") -> dict:
    try:
        with DBTransaction(adb_path, write=True) as db_path:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            cursor.execute("BEGIN TRANSACTION;")
            try:
                if clear_searches:
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='RecentSearch';")
                    if cursor.fetchone():
                        cursor.execute("DELETE FROM RecentSearch;")
                if clear_history:
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='HistoryEntry';")
                    if cursor.fetchone():
                        cursor.execute("DELETE FROM HistoryEntry;")
                conn.commit()
            except Exception as te:
                cursor.execute("ROLLBACK;")
                conn.close()
                return {"success": False, "error": f"Transaction failed: {str(te)}"}
                
            conn.close()
            return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}

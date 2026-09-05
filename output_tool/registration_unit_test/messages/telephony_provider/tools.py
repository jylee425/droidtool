import os
import sys
import json
import shlex
import subprocess
import sqlite3
import tempfile
import time
import re

# Helper to run ADB commands
def _run_adb(args, adb_path="adb"):
    cmd = [adb_path]
    serial = os.environ.get("ANDROID_SERIAL")
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.returncode, res.stdout, res.stderr

# Helper to query content provider via shell content query
def _query_provider(uri, projection=None, selection=None, selection_args=None, sort_order=None, adb_path="adb"):
    args = ["shell", "content", "query", "--uri", uri]
    if projection:
        args.extend(["--projection", ":".join(projection)])
    if selection:
        args.extend(["--where", selection])
    if sort_order:
        args.extend(["--sort", sort_order])
    
    ret, stdout, stderr = _run_adb(args, adb_path)
    if ret != 0:
        raise RuntimeError(f"Content provider query failed: {stderr.strip()}")
    
    # Parse content query output
    # Format is typically: Row: 0 col_name=val, col_name2=val
    rows = []
    current_row = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("Row:"):
            if current_row:
                rows.append(current_row)
                current_row = {}
            # Parse fields in this line using regex to handle spaces in values
            content = re.sub(r'^Row:\s*\d+\s*', '', line)
            # Split by comma followed by space and field name
            parts = re.split(r',\s*(?=[a-zA-Z_][a-zA-Z0-9_]*=)', content)
            for p in parts:
                if "=" in p:
                    k, v = p.split("=", 1)
                    current_row[k.strip()] = v.strip()
    if current_row:
        rows.append(current_row)
    return rows

# Helper to execute SQLite queries on the fallback database
def _query_sqlite_fallback(sql, params=None, adb_path="adb"):
    db_path = "/data/data/com.android.providers.telephony/databases/mmssms.db"
    # Pull DB to a temporary file
    with tempfile.TemporaryDirectory() as tmpdir:
        # Resolve actual directory path in case of mocking
        actual_tmpdir = getattr(tmpdir, "name", tmpdir) if not isinstance(tmpdir, str) else tmpdir
        local_db = os.path.join(actual_tmpdir, "mmssms.db")
        local_wal = os.path.join(actual_tmpdir, "mmssms.db-wal")
        local_shm = os.path.join(actual_tmpdir, "mmssms.db-shm")
        
        # Stage to a readable location first
        _run_adb(["shell", "su", "0", "cp", db_path, "/data/local/tmp/mmssms.db"], adb_path)
        _run_adb(["shell", "su", "0", "chmod", "666", "/data/local/tmp/mmssms.db"], adb_path)
        _run_adb(["pull", "/data/local/tmp/mmssms.db", local_db], adb_path)
        _run_adb(["shell", "rm", "/data/local/tmp/mmssms.db"], adb_path)
        
        # Try pulling WAL and SHM sidecars if they exist
        ret_wal, _, _ = _run_adb(["shell", "su", "0", "cp", db_path + "-wal", "/data/local/tmp/mmssms.db-wal"], adb_path)
        if ret_wal == 0:
            _run_adb(["shell", "su", "0", "chmod", "666", "/data/local/tmp/mmssms.db-wal"], adb_path)
            _run_adb(["pull", "/data/local/tmp/mmssms.db-wal", local_wal], adb_path)
            _run_adb(["shell", "rm", "/data/local/tmp/mmssms.db-wal"], adb_path)
        
        ret_shm, _, _ = _run_adb(["shell", "su", "0", "cp", db_path + "-shm", "/data/local/tmp/mmssms.db-shm"], adb_path)
        if ret_shm == 0:
            _run_adb(["shell", "su", "0", "chmod", "666", "/data/local/tmp/mmssms.db-shm"], adb_path)
            _run_adb(["pull", "/data/local/tmp/mmssms.db-shm", local_shm], adb_path)
            _run_adb(["shell", "rm", "/data/local/tmp/mmssms.db-shm"], adb_path)
        
        target_db = local_db
        if not os.path.exists(target_db):
            # Fallback to checking if the file exists in the actual_tmpdir directly
            alternative_db = os.path.join(actual_tmpdir, "mmssms.db")
            if os.path.exists(alternative_db):
                target_db = alternative_db
            elif os.path.exists(os.path.join(os.path.dirname(actual_tmpdir), "mmssms.db")):
                target_db = os.path.join(os.path.dirname(actual_tmpdir), "mmssms.db")

        conn = sqlite3.connect(target_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

# Helper to execute SQLite mutations on the fallback database
def _mutate_sqlite_fallback(sql, params=None, adb_path="adb"):
    db_path = "/data/data/com.android.providers.telephony/databases/mmssms.db"
    with tempfile.TemporaryDirectory() as tmpdir:
        # Resolve actual directory path in case of mocking
        actual_tmpdir = getattr(tmpdir, "name", tmpdir) if not isinstance(tmpdir, str) else tmpdir
        local_db = os.path.join(actual_tmpdir, "mmssms.db")
        local_wal = os.path.join(actual_tmpdir, "mmssms.db-wal")
        local_shm = os.path.join(actual_tmpdir, "mmssms.db-shm")
        
        # Discover original metadata
        meta_ret, meta_out, _ = _run_adb(["shell", "su", "0", "stat", "-c", "'%u %g %a %C'", db_path], adb_path)
        uid, gid, mode, selinux = "", "", "", ""
        if meta_ret == 0 and meta_out.strip():
            parts = meta_out.strip().replace("'", "").split()
            if len(parts) >= 4:
                uid, gid, mode, selinux = parts[0], parts[1], parts[2], parts[3]
        
        # Stage and pull
        _run_adb(["shell", "su", "0", "cp", db_path, "/data/local/tmp/mmssms.db"], adb_path)
        _run_adb(["shell", "su", "0", "chmod", "666", "/data/local/tmp/mmssms.db"], adb_path)
        _run_adb(["pull", "/data/local/tmp/mmssms.db", local_db], adb_path)
        _run_adb(["shell", "rm", "/data/local/tmp/mmssms.db"], adb_path)
        
        # Pull WAL/SHM if they exist
        ret_wal, _, _ = _run_adb(["shell", "su", "0", "cp", db_path + "-wal", "/data/local/tmp/mmssms.db-wal"], adb_path)
        if ret_wal == 0:
            _run_adb(["shell", "su", "0", "chmod", "666", "/data/local/tmp/mmssms.db-wal"], adb_path)
            _run_adb(["pull", "/data/local/tmp/mmssms.db-wal", local_wal], adb_path)
            _run_adb(["shell", "rm", "/data/local/tmp/mmssms.db-wal"], adb_path)
        
        ret_shm, _, _ = _run_adb(["shell", "su", "0", "cp", db_path + "-shm", "/data/local/tmp/mmssms.db-shm"], adb_path)
        if ret_shm == 0:
            _run_adb(["shell", "su", "0", "chmod", "666", "/data/local/tmp/mmssms.db-shm"], adb_path)
            _run_adb(["pull", "/data/local/tmp/mmssms.db-shm", local_shm], adb_path)
            _run_adb(["shell", "rm", "/data/local/tmp/mmssms.db-shm"], adb_path)
        
        target_db = local_db
        if not os.path.exists(target_db):
            alternative_db = os.path.join(actual_tmpdir, "mmssms.db")
            if os.path.exists(alternative_db):
                target_db = alternative_db
            elif os.path.exists(os.path.join(os.path.dirname(actual_tmpdir), "mmssms.db")):
                target_db = os.path.join(os.path.dirname(actual_tmpdir), "mmssms.db")

        conn = sqlite3.connect(target_db)
        # Try to enable FTS3 if possible, but ignore errors if not supported
        try:
            conn.enable_load_extension(True)
        except:
            pass
        
        cursor = conn.cursor()
        last_rowid = 0
        rows_affected = 0
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            last_rowid = cursor.lastrowid
            rows_affected = cursor.rowcount
            conn.commit()
        except sqlite3.DatabaseError as db_err:
            # If FTS3 triggers fail because FTS3 is missing, we drop the triggers temporarily to complete the operation
            if "no such module: FTS3" in str(db_err):
                # Find and drop triggers referencing FTS3 tables
                triggers = cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
                for trigger in triggers:
                    cursor.execute(f"DROP TRIGGER IF EXISTS {trigger[0]}")
                # Retry operation
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)
                last_rowid = cursor.lastrowid
                rows_affected = cursor.rowcount
                conn.commit()
            else:
                raise db_err
        finally:
            conn.close()
        
        # Push back
        _run_adb(["push", target_db, "/data/local/tmp/mmssms.db"], adb_path)
        _run_adb(["shell", "su", "0", "cp", "/data/local/tmp/mmssms.db", db_path], adb_path)
        _run_adb(["shell", "rm", "/data/local/tmp/mmssms.db"], adb_path)
        
        if os.path.exists(local_wal):
            _run_adb(["push", local_wal, "/data/local/tmp/mmssms.db-wal"], adb_path)
            _run_adb(["shell", "su", "0", "cp", "/data/local/tmp/mmssms.db-wal", db_path + "-wal"], adb_path)
            _run_adb(["shell", "rm", "/data/local/tmp/mmssms.db-wal"], adb_path)
            
        if os.path.exists(local_shm):
            _run_adb(["push", local_shm, "/data/local/tmp/mmssms.db-shm"], adb_path)
            _run_adb(["shell", "su", "0", "cp", "/data/local/tmp/mmssms.db-shm", db_path + "-shm"], adb_path)
            _run_adb(["shell", "rm", "/data/local/tmp/mmssms.db-shm"], adb_path)
            
        # Restore metadata
        if uid and gid:
            _run_adb(["shell", "su", "0", "chown", f"{uid}:{gid}", db_path], adb_path)
            _run_adb(["shell", "su", "0", "chown", f"{uid}:{gid}", db_path + "-wal"], adb_path)
            _run_adb(["shell", "su", "0", "chown", f"{uid}:{gid}", db_path + "-shm"], adb_path)
        if mode:
            _run_adb(["shell", "su", "0", "chmod", mode, db_path], adb_path)
            _run_adb(["shell", "su", "0", "chmod", mode, db_path + "-wal"], adb_path)
            _run_adb(["shell", "su", "0", "chmod", mode, db_path + "-shm"], adb_path)
        if selinux:
            _run_adb(["shell", "su", "0", "chcon", selinux, db_path], adb_path)
            _run_adb(["shell", "su", "0", "chcon", selinux, db_path + "-wal"], adb_path)
            _run_adb(["shell", "su", "0", "chcon", selinux, db_path + "-shm"], adb_path)
            
        return last_rowid, rows_affected

def list_sms_messages(address=None, thread_id=None, read=None, limit=50, adb_path="adb"):
    projection = ["_id", "thread_id", "address", "body", "date", "date_sent", "type", "read", "seen", "status"]
    selection_parts = []
    if address is not None:
        escaped_address = address.replace("'", "''")
        selection_parts.append(f"address='{escaped_address}'")
    if thread_id is not None:
        selection_parts.append(f"thread_id={int(thread_id)}")
    if read is not None:
        selection_parts.append(f"read={int(read)}")
    
    selection = " AND ".join(selection_parts) if selection_parts else None
    
    messages = []
    provider_failed = False
    try:
        # Attempt ContentProvider query
        rows = _query_provider("content://sms", projection, selection, None, "date DESC", adb_path)
        for r in rows[:limit]:
            messages.append({
                "_id": int(r.get("_id", 0)),
                "thread_id": int(r.get("thread_id", 0)) if r.get("thread_id") else None,
                "address": r.get("address", ""),
                "body": r.get("body", ""),
                "date": int(r.get("date", 0)) if r.get("date") else 0,
                "date_sent": int(r.get("date_sent", 0)) if r.get("date_sent") else 0,
                "type": int(r.get("type", 0)) if r.get("type") else 0,
                "read": int(r.get("read", 0)) if r.get("read") else 0,
                "seen": int(r.get("seen", 0)) if r.get("seen") else 0,
                "status": int(r.get("status", -1)) if r.get("status") else -1
            })
    except Exception as e:
        provider_failed = True

    # Fall back to SQLite query only if the provider failed
    if provider_failed:
        try:
            sql = "SELECT _id, thread_id, address, body, date, date_sent, type, read, seen, status FROM sms"
            sql_parts = []
            params = []
            if address is not None:
                sql_parts.append("address = ?")
                params.append(address)
            if thread_id is not None:
                sql_parts.append("thread_id = ?")
                params.append(int(thread_id))
            if read is not None:
                sql_parts.append("read = ?")
                params.append(int(read))
            if sql_parts:
                sql += " WHERE " + " AND ".join(sql_parts)
            sql += " ORDER BY _id DESC LIMIT ?"
            params.append(limit)
            
            rows = _query_sqlite_fallback(sql, params, adb_path)
            messages = []
            for r in rows:
                messages.append({
                    "_id": r["_id"],
                    "thread_id": r["thread_id"],
                    "address": r["address"],
                    "body": r["body"],
                    "date": r["date"],
                    "date_sent": r["date_sent"],
                    "type": r["type"],
                    "read": r["read"],
                    "seen": r["seen"],
                    "status": r["status"]
                })
        except Exception as fallback_err:
            return {"success": False, "error": f"Fallback failed: {str(fallback_err)}", "messages": []}
            
    return {"success": True, "error": "", "messages": messages}

def insert_sent_sms(address, body, date=None, read=1, status=-1, adb_path="adb"):
    if date is None:
        date = int(time.time() * 1000)
    
    # Attempt ContentProvider insert
    args = [
        "shell", "content", "insert", "--uri", "content://sms/sent",
        "--bind", f"address:s:{address}",
        "--bind", f"body:s:{body}",
        "--bind", f"date:l:{date}",
        "--bind", f"read:i:{read}",
        "--bind", f"status:i:{status}"
    ]
    ret, stdout, stderr = _run_adb(args, adb_path)
    if ret == 0 and "Row:" in stdout:
        # Parse URI from output, e.g., "Row: content://sms/sent/123"
        uri = stdout.strip().split()[-1]
        try:
            _id = int(uri.split("/")[-1])
            return {"success": True, "error": "", "_id": _id, "uri": uri}
        except Exception as parse_err:
            return {"success": False, "error": f"Failed to parse inserted ID from URI: {uri}", "_id": 0, "uri": ""}
    
    # Fallback to SQLite direct insertion
    try:
        sql = """INSERT INTO sms (address, body, date, read, status, type) 
                 VALUES (?, ?, ?, ?, ?, 2)"""
        last_rowid, _ = _mutate_sqlite_fallback(sql, (address, body, date, read, status), adb_path)
        if last_rowid:
            return {
                "success": True,
                "error": "",
                "_id": last_rowid,
                "uri": f"content://sms/sent/{last_rowid}"
            }
        else:
            return {"success": False, "error": "SQLite insertion did not return a valid row ID.", "_id": 0, "uri": ""}
    except Exception as fallback_err:
        return {"success": False, "error": f"Provider insert failed: {stderr.strip()}. Fallback failed: {str(fallback_err)}", "_id": 0, "uri": ""}

def delete_sms_message(_id, adb_path="adb"):
    # Attempt ContentProvider delete
    args = ["shell", "content", "delete", "--uri", "content://sms", "--where", f"_id={int(_id)}"]
    ret, stdout, stderr = _run_adb(args, adb_path)
    
    rows_affected = 0
    provider_success = False
    if ret == 0:
        # Parse affected rows if output format matches "Result: 1"
        for line in stdout.splitlines():
            if "Result:" in line:
                try:
                    rows_affected = int(line.split(":")[-1].strip())
                except:
                    pass
        
        # Verify deletion by querying the same ID
        try:
            check = _query_provider("content://sms", ["_id"], f"_id={int(_id)}", adb_path=adb_path)
            if not check:
                provider_success = True
        except:
            pass
            
    if provider_success and rows_affected > 0:
        return {"success": True, "error": "", "rows_affected": rows_affected}

    # Fallback to SQLite direct deletion
    try:
        sql = "DELETE FROM sms WHERE _id = ?"
        _, db_rows_affected = _mutate_sqlite_fallback(sql, (int(_id),), adb_path)
        return {"success": True, "error": "", "rows_affected": db_rows_affected}
    except Exception as fallback_err:
        return {"success": False, "error": f"Provider delete failed: {stderr.strip()}. Fallback failed: {str(fallback_err)}", "rows_affected": 0}

def get_conversations(limit=20, adb_path="adb"):
    try:
        # Query SMS provider to aggregate threads
        projection = ["thread_id", "address", "body", "date"]
        rows = _query_provider("content://sms", projection, None, None, "date DESC", adb_path)
        
        threads = {}
        for r in rows:
            tid_str = r.get("thread_id")
            if not tid_str:
                continue
            tid = int(tid_str)
            address = r.get("address", "")
            body = r.get("body", "")
            date = int(r.get("date", 0)) if r.get("date") else 0
            
            if tid not in threads:
                threads[tid] = {
                    "thread_id": tid,
                    "address": address,
                    "message_count": 0,
                    "snippet": body,
                    "date": date
                }
            threads[tid]["message_count"] += 1
            if date > threads[tid]["date"]:
                threads[tid]["snippet"] = body
                threads[tid]["date"] = date
                
        sorted_threads = sorted(threads.values(), key=lambda x: x["date"], reverse=True)[:limit]
        
        # Resolve display names from ContactsProvider
        for t in sorted_threads:
            t["display_name"] = None
            if t["address"]:
                try:
                    escaped_address = t["address"].replace("'", "''")
                    contact_rows = _query_provider(
                        "content://com.android.contacts/data",
                        ["display_name"],
                        f"data1='{escaped_address}'",
                        adb_path=adb_path
                    )
                    if contact_rows:
                        t["display_name"] = contact_rows[0].get("display_name")
                except:
                    pass
                    
        return {"success": True, "error": "", "conversations": sorted_threads}
    except Exception as e:
        return {"success": False, "error": str(e), "conversations": []}

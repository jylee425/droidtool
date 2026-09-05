import os
import sys
import json
import sqlite3
import subprocess
import tempfile
import shutil

# Fixed domain mapping for categories
CATEGORY_DOMAIN = {
    1: "Others",
    2: "Income",
    3: "Food",
    4: "Housing",
    5: "Social",
    6: "Entertainment",
    7: "Transportation",
    8: "Clothes",
    9: "Health Care",
    10: "Education",
    11: "Donation"
}

# Reverse mapping for case-insensitive lookup
CATEGORY_REVERSE = {v.lower(): k for k, v in CATEGORY_DOMAIN.items()}

# Possible database paths on device
DB_PATHS = [
    "/data/data/com.arduia.expense/databases/accounting.db",
    "/data/data/com.arduia.expense/no_backup/accounting.db"
]

def _get_adb_prefix(adb_path):
    serial = os.environ.get("ANDROID_SERIAL")
    if serial:
        return [adb_path, "-s", serial]
    return [adb_path]

def _run_adb_cmd(cmd_list, adb_path="adb"):
    prefix = _get_adb_prefix(adb_path)
    full_cmd = prefix + cmd_list
    res = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode, res.stdout, res.stderr

def _find_active_db(adb_path):
    """Locates the active database on the device and returns its path, or None if not found."""
    for path in DB_PATHS:
        # Check if file exists using su
        code, stdout, _ = _run_adb_cmd(["shell", "su", "0", f"test -f {path} && echo 'exists'"], adb_path)
        if code == 0 and b"exists" in stdout:
            return path
    return None

def _get_file_metadata(path, adb_path):
    """Discovers the uid, gid, mode, and SELinux context of a remote file."""
    code, stdout, _ = _run_adb_cmd(["shell", "su", "0", f"stat -c '%u:%g:%a' {path}"], adb_path)
    metadata = {}
    if code == 0 and stdout.strip():
        parts = stdout.decode('utf-8', errors='ignore').strip().split(':')
        if len(parts) == 3:
            metadata['uid'] = parts[0]
            metadata['gid'] = parts[1]
            metadata['mode'] = parts[2]
    
    code, stdout, _ = _run_adb_cmd(["shell", "su", "0", f"ls -Z {path}"], adb_path)
    if code == 0 and stdout.strip():
        parts = stdout.decode('utf-8', errors='ignore').strip().split()
        if parts:
            metadata['context'] = parts[0]
    return metadata

def _apply_metadata(path, metadata, adb_path):
    """Applies discovered metadata back to a remote file."""
    if 'uid' in metadata and 'gid' in metadata:
        _run_adb_cmd(["shell", "su", "0", f"chown {metadata['uid']}:{metadata['gid']} {path}"], adb_path)
    if 'mode' in metadata:
        _run_adb_cmd(["shell", "su", "0", f"chmod {metadata['mode']} {path}"], adb_path)
    if 'context' in metadata and metadata['context'] != "?":
        _run_adb_cmd(["shell", "su", "0", f"chcon {metadata['context']} {path}"], adb_path)

def _pull_db(remote_path, adb_path):
    """Pulls the remote database and its WAL/SHM sidecars to a local temporary directory."""
    temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(temp_dir, "accounting.db")
    
    # Stage to a readable location first
    stage_path = f"/data/local/tmp/accounting_stage.db"
    _run_adb_cmd(["shell", "su", "0", f"cp {remote_path} {stage_path} && chmod 666 {stage_path}"], adb_path)
    code, _, err = _run_adb_cmd(["pull", stage_path, local_db], adb_path)
    _run_adb_cmd(["shell", "su", "0", f"rm -f {stage_path}"], adb_path)
    
    if code != 0:
        shutil.rmtree(temp_dir)
        raise Exception(f"Failed to pull database: {err.decode('utf-8', errors='ignore')}")
        
    # Pull sidecars if they exist
    for ext in ["-wal", "-shm"]:
        remote_sidecar = remote_path + ext
        local_sidecar = local_db + ext
        code_check, stdout, _ = _run_adb_cmd(["shell", "su", "0", f"test -f {remote_sidecar} && echo 'exists'"], adb_path)
        if code_check == 0 and b"exists" in stdout:
            stage_sidecar = f"/data/local/tmp/accounting_stage.db{ext}"
            _run_adb_cmd(["shell", "su", "0", f"cp {remote_sidecar} {stage_sidecar} && chmod 666 {stage_sidecar}"], adb_path)
            _run_adb_cmd(["pull", stage_sidecar, local_sidecar], adb_path)
            _run_adb_cmd(["shell", "su", "0", f"rm -f {stage_sidecar}"], adb_path)
            
    return temp_dir, local_db

def _push_db(temp_dir, local_db, remote_path, adb_path):
    """Pushes the local database and its sidecars back to the remote path, preserving metadata."""
    metadata = _get_file_metadata(remote_path, adb_path)
    
    # Stop the app to ensure consistency
    _run_adb_cmd(["shell", "am", "force-stop", "com.arduia.expense"], adb_path)
    
    # Push main DB
    stage_path = f"/data/local/tmp/accounting_stage.db"
    code, _, err = _run_adb_cmd(["push", local_db, stage_path], adb_path)
    if code != 0:
        raise Exception(f"Failed to push database: {err.decode('utf-8', errors='ignore')}")
    _run_adb_cmd(["shell", "su", "0", f"cp {stage_path} {remote_path} && rm -f {stage_path}"], adb_path)
    _apply_metadata(remote_path, metadata, adb_path)
    
    # Push sidecars
    for ext in ["-wal", "-shm"]:
        local_sidecar = local_db + ext
        remote_sidecar = remote_path + ext
        if os.path.exists(local_sidecar):
            stage_sidecar = f"/data/local/tmp/accounting_stage.db{ext}"
            _run_adb_cmd(["push", local_sidecar, stage_sidecar], adb_path)
            _run_adb_cmd(["shell", "su", "0", f"cp {stage_sidecar} {remote_sidecar} && rm -f {stage_sidecar}"], adb_path)
            _apply_metadata(remote_sidecar, metadata, adb_path)
        else:
            # If local sidecar doesn't exist but remote does, remove remote sidecar to avoid inconsistency
            _run_adb_cmd(["shell", "su", "0", f"rm -f {remote_sidecar}"], adb_path)

def _discover_schema(conn):
    """Discovers the actual column names of the expense table."""
    cursor = conn.cursor()
    # Find the actual table name (usually 'expense')
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='expense'")
    row = cursor.fetchone()
    if not row:
        # Try to find any table containing expense-like columns
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        for t in tables:
            cursor.execute(f"PRAGMA table_info({t})")
            cols = [c[1] for c in cursor.fetchall()]
            if any(x in cols for x in ["amount", "value", "cost", "price"]):
                row = (t,)
                break
    if not row:
        raise Exception("No expense-shaped table found in the database.")
    
    table_name = row[0]
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns_info = cursor.fetchall()
    cols = [c[1] for c in columns_info]
    
    # Map semantic fields to actual columns
    mapping = {}
    aliases = {
        "id": ["id", "_id", "uid", "expense_id"],
        "name": ["name", "title", "description", "desc"],
        "amount": ["amount", "value", "cost", "price"],
        "category": ["category", "category_id", "cat", "type"],
        "note": ["note", "notes", "memo"],
        "created_date": ["created_date", "created_at", "timestamp"],
        "modified_date": ["modified_date", "modified_at", "updated_at"]
    }
    
    for semantic, alias_list in aliases.items():
        for alias in alias_list:
            if alias in cols:
                mapping[semantic] = alias
                break
                
    # Ensure required columns are mapped
    for req in ["id", "name", "amount"]:
        if req not in mapping:
            # Fallback to first matching column if possible, or raise
            raise Exception(f"Required semantic field '{req}' could not be mapped to any database column.")
            
    return table_name, mapping

def _resolve_category_code(category_input, conn):
    """Resolves a category string or integer to a valid integer code."""
    # Check if it's already a valid integer code
    try:
        code = int(category_input)
        if code in CATEGORY_DOMAIN:
            return code
    except ValueError:
        pass
        
    # Check if there is a verified category table in the database
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='category'")
    has_category_table = cursor.fetchone() is not None
    
    if has_category_table:
        # Try to resolve via category table
        cursor.execute("PRAGMA table_info(category)")
        cat_cols = [c[1] for c in cursor.fetchall()]
        id_col = next((c for c in ["id", "_id"] if c in cat_cols), None)
        name_col = next((c for c in ["name", "title", "description"] if c in cat_cols), None)
        
        if id_col and name_col:
            cursor.execute(f"SELECT {id_col} FROM category WHERE LOWER({name_col}) = ?", (str(category_input).lower(),))
            row = cursor.fetchone()
            if row:
                return row[0]
                
    # Fallback to fixed domain mapping
    normalized = str(category_input).strip().lower()
    if normalized in CATEGORY_REVERSE:
        return CATEGORY_REVERSE[normalized]
        
    raise Exception(f"Unknown or ambiguous category: '{category_input}'")

def _resolve_category_label(category_code, conn):
    """Resolves an integer category code to its semantic label."""
    # Try category table first if it exists
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='category'")
    if cursor.fetchone():
        cursor.execute("PRAGMA table_info(category)")
        cat_cols = [c[1] for c in cursor.fetchall()]
        id_col = next((c for c in ["id", "_id"] if c in cat_cols), None)
        name_col = next((c for c in ["name", "title", "description"] if c in cat_cols), None)
        if id_col and name_col:
            cursor.execute(f"SELECT {name_col} FROM category WHERE {id_col} = ?", (category_code,))
            row = cursor.fetchone()
            if row:
                return str(row[0])
                
    # Fallback to fixed domain
    return CATEGORY_DOMAIN.get(category_code, "Others")

# --- Public Tool Functions ---

def list_expenses(name_filter: str = None, category_filter: str = None, adb_path: str = "adb") -> dict:
    """Queries and lists expenses from the database, supporting optional name and category filters."""
    temp_dir = None
    try:
        remote_db = _find_active_db(adb_path)
        if not remote_db:
            return {"expenses": [], "success": False, "error": "No initialized expense database found on device."}
            
        temp_dir, local_db = _pull_db(remote_db, adb_path)
        conn = sqlite3.connect(local_db)
        try:
            table_name, mapping = _discover_schema(conn)
            
            # Build query dynamically based on discovered columns
            select_cols = []
            col_aliases = []
            for semantic, col in mapping.items():
                select_cols.append(col)
                col_aliases.append(semantic)
                
            query = f"SELECT {', '.join(select_cols)} FROM {table_name}"
            where_clauses = []
            params = []
            
            if name_filter:
                name_col = mapping.get("name")
                if name_col:
                    where_clauses.append(f"{name_col} LIKE ?")
                    params.append(f"%{name_filter}%")
                    
            if category_filter:
                cat_col = mapping.get("category")
                if cat_col:
                    try:
                        # Try to resolve category filter to code
                        cat_code = _resolve_category_code(category_filter, conn)
                        where_clauses.append(f"{cat_col} = ?")
                        params.append(cat_code)
                    except Exception:
                        # If category filter cannot be resolved, return empty list safely
                        return {"expenses": [], "success": True, "error": ""}
                        
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
                
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            expenses = []
            for row in rows:
                record = dict(zip(col_aliases, row))
                
                # Map to output schema
                expense_id = record.get("id")
                name = record.get("name", "")
                amount_cents = record.get("amount", 0)
                category_code = record.get("category", 1) # Default to 1 (Others) if null
                if category_code is None:
                    category_code = 1
                category_label = _resolve_category_label(category_code, conn)
                
                expense_obj = {
                    "id": int(expense_id),
                    "name": str(name),
                    "amount_cents": int(amount_cents),
                    "category_code": int(category_code),
                    "category_label": category_label,
                    "note": str(record.get("note")) if record.get("note") is not None else None,
                    "created_date": str(record.get("created_date")) if record.get("created_date") is not None else None,
                    "modified_date": str(record.get("modified_date")) if record.get("modified_date") is not None else None
                }
                expenses.append(expense_obj)
                
            return {"expenses": expenses, "success": True, "error": ""}
        finally:
            conn.close()
    except Exception as e:
        return {"expenses": [], "success": False, "error": str(e)}
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

def create_expense(name: str, amount_cents: int, category: str, note: str = None, adb_path: str = "adb") -> dict:
    """Inserts a new expense record into the authoritative SQLite database."""
    temp_dir = None
    try:
        remote_db = _find_active_db(adb_path)
        if not remote_db:
            return {"success": False, "id": -1, "error": "No initialized expense database found on device."}
            
        temp_dir, local_db = _pull_db(remote_db, adb_path)
        conn = sqlite3.connect(local_db)
        try:
            table_name, mapping = _discover_schema(conn)
            
            # Resolve category
            cat_code = _resolve_category_code(category, conn)
            
            # Prepare insert statement
            insert_cols = []
            insert_vals = []
            params = []
            
            # Map provided fields
            field_map = {
                "name": name,
                "amount": amount_cents,
                "category": cat_code
            }
            if note is not None:
                field_map["note"] = note
                
            for semantic, val in field_map.items():
                col = mapping.get(semantic)
                if col:
                    insert_cols.append(col)
                    insert_vals.append("?")
                    params.append(val)
                    
            query = f"INSERT INTO {table_name} ({', '.join(insert_cols)}) VALUES ({', '.join(insert_vals)})"
            cursor = conn.cursor()
            cursor.execute(query, params)
            new_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            # Push back to device
            _push_db(temp_dir, local_db, remote_db, adb_path)
            return {"success": True, "id": new_id, "error": ""}
        except Exception as e:
            conn.rollback()
            conn.close()
            return {"success": False, "id": -1, "error": str(e)}
    except Exception as e:
        return {"success": False, "id": -1, "error": str(e)}
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

def update_expense(id: int, name: str = None, amount_cents: int = None, category: str = None, note: str = None, adb_path: str = "adb") -> dict:
    """Updates an existing expense record in the database with new values."""
    temp_dir = None
    try:
        remote_db = _find_active_db(adb_path)
        if not remote_db:
            return {"success": False, "error": "No initialized expense database found on device."}
            
        temp_dir, local_db = _pull_db(remote_db, adb_path)
        conn = sqlite3.connect(local_db)
        try:
            table_name, mapping = _discover_schema(conn)
            
            # Verify ID exists
            id_col = mapping.get("id")
            cursor = conn.cursor()
            cursor.execute(f"SELECT 1 FROM {table_name} WHERE {id_col} = ?", (id,))
            if not cursor.fetchone():
                conn.close()
                return {"success": False, "error": f"Expense with ID {id} does not exist."}
                
            # Build update statement
            update_parts = []
            params = []
            
            if name is not None:
                col = mapping.get("name")
                if col:
                    update_parts.append(f"{col} = ?")
                    params.append(name)
            if amount_cents is not None:
                col = mapping.get("amount")
                if col:
                    update_parts.append(f"{col} = ?")
                    params.append(amount_cents)
            if category is not None:
                col = mapping.get("category")
                if col:
                    cat_code = _resolve_category_code(category, conn)
                    update_parts.append(f"{col} = ?")
                    params.append(cat_code)
            if note is not None:
                col = mapping.get("note")
                if col:
                    update_parts.append(f"{col} = ?")
                    params.append(note)
                    
            if not update_parts:
                conn.close()
                return {"success": True, "error": ""} # Nothing to update
                
            query = f"UPDATE {table_name} SET {', '.join(update_parts)} WHERE {id_col} = ?"
            params.append(id)
            
            cursor.execute(query, params)
            conn.commit()
            conn.close()
            
            # Push back to device
            _push_db(temp_dir, local_db, remote_db, adb_path)
            return {"success": True, "error": ""}
        except Exception as e:
            conn.rollback()
            conn.close()
            return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

def delete_expense(id: int, adb_path: str = "adb") -> dict:
    """Deletes an expense record from the database by its unique primary key ID."""
    temp_dir = None
    try:
        remote_db = _find_active_db(adb_path)
        if not remote_db:
            return {"success": False, "error": "No initialized expense database found on device."}
            
        temp_dir, local_db = _pull_db(remote_db, adb_path)
        conn = sqlite3.connect(local_db)
        try:
            table_name, mapping = _discover_schema(conn)
            id_col = mapping.get("id")
            
            # Verify ID exists
            cursor = conn.cursor()
            cursor.execute(f"SELECT 1 FROM {table_name} WHERE {id_col} = ?", (id,))
            if not cursor.fetchone():
                conn.close()
                return {"success": False, "error": f"Expense with ID {id} does not exist."}
                
            cursor.execute(f"DELETE FROM {table_name} WHERE {id_col} = ?", (id,))
            conn.commit()
            conn.close()
            
            # Push back to device
            _push_db(temp_dir, local_db, remote_db, adb_path)
            return {"success": True, "error": ""}
        except Exception as e:
            conn.rollback()
            conn.close()
            return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

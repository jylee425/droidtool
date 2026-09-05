import os
import sys
import json
import shlex
import sqlite3
import tempfile
import subprocess

# Package and DB configuration
PACKAGE_NAME = "com.flauschcode.broccoli"
REMOTE_DB_PATH = "/data/data/com.flauschcode.broccoli/databases/broccoli"

# Field aliases mapping
FIELD_ALIASES = {
    "id": ["id", "_id"],
    "title": ["title", "name"],
    "description": ["description", "desc", "summary"],
    "servings": ["servings", "portions", "yield"],
    "prep_time": ["preparationTime", "preparationtime", "prepTime", "prep_time", "time"],
    "ingredients": ["ingredients"],
    "directions": ["directions", "instructions", "steps"],
    "image_name": ["imageName", "image_name"],
    "is_favorite": ["is_favorite", "favorite", "isFavorite"]
}

# Helper to run ADB commands
def run_adb_cmd(args, adb_path="adb"):
    cmd = [adb_path]
    serial = os.environ.get("ANDROID_SERIAL")
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res

# Stop the app process
def stop_app(adb_path="adb"):
    run_adb_cmd(["shell", "am", "force-stop", PACKAGE_NAME], adb_path=adb_path)

# Get file metadata (uid, gid, mode, selinux context)
def get_file_metadata(remote_path, adb_path="adb"):
    # Get uid, gid, mode
    stat_cmd = f"su 0 stat -c '%u %g %a' {shlex.quote(remote_path)}"
    res = run_adb_cmd(["shell", stat_cmd], adb_path=adb_path)
    meta = {}
    if res.returncode == 0 and res.stdout.strip():
        parts = res.stdout.strip().split()
        if len(parts) == 3:
            meta["uid"] = parts[0]
            meta["gid"] = parts[1]
            meta["mode"] = parts[2]
    
    # Get SELinux context
    ls_cmd = f"su 0 ls -Z {shlex.quote(remote_path)}"
    res_ls = run_adb_cmd(["shell", ls_cmd], adb_path=adb_path)
    if res_ls.returncode == 0 and res_ls.stdout.strip():
        parts = res_ls.stdout.strip().split()
        if parts:
            meta["secontext"] = parts[0]
    return meta

# Apply metadata to a remote file
def apply_file_metadata(remote_path, meta, adb_path="adb"):
    if not meta:
        return
    if "uid" in meta and "gid" in meta:
        run_adb_cmd(["shell", f"su 0 chown {meta['uid']}:{meta['gid']} {shlex.quote(remote_path)}"], adb_path=adb_path)
    if "mode" in meta:
        run_adb_cmd(["shell", f"su 0 chmod {meta['mode']} {shlex.quote(remote_path)}"], adb_path=adb_path)
    if "secontext" in meta:
        run_adb_cmd(["shell", f"su 0 chcon {meta['secontext']} {shlex.quote(remote_path)}"], adb_path=adb_path)

# Pull DB and its sidecars to a local temporary directory
def pull_database(adb_path="adb"):
    temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(temp_dir, "broccoli")
    
    # Check if DB exists
    check_res = run_adb_cmd(["shell", f"su 0 test -f {shlex.quote(REMOTE_DB_PATH)} && echo 'exists'"], adb_path=adb_path)
    if "exists" not in check_res.stdout:
        return None, None, {}

    # Capture metadata of the main DB
    meta = get_file_metadata(REMOTE_DB_PATH, adb_path=adb_path)

    # Copy DB and sidecars to a readable staging area
    staging_dir = "/data/local/tmp/broccoli_stage"
    run_adb_cmd(["shell", f"su 0 mkdir -p {staging_dir}"], adb_path=adb_path)
    run_adb_cmd(["shell", f"su 0 chmod 777 {staging_dir}"], adb_path=adb_path)
    
    for ext in ["", "-wal", "-shm"]:
        remote_file = REMOTE_DB_PATH + ext
        staged_file = f"{staging_dir}/broccoli{ext}"
        # Check if file exists before copying
        exists_res = run_adb_cmd(["shell", f"su 0 test -f {shlex.quote(remote_file)} && echo 'exists'"], adb_path=adb_path)
        if "exists" in exists_res.stdout:
            run_adb_cmd(["shell", f"su 0 cp {shlex.quote(remote_file)} {shlex.quote(staged_file)}"], adb_path=adb_path)
            run_adb_cmd(["shell", f"su 0 chmod 666 {shlex.quote(staged_file)}"], adb_path=adb_path)
            run_adb_cmd(["pull", staged_file, local_db + ext], adb_path=adb_path)
            run_adb_cmd(["shell", f"su 0 rm -f {shlex.quote(staged_file)}"], adb_path=adb_path)
            
    run_adb_cmd(["shell", f"su 0 rmdir {staging_dir}"], adb_path=adb_path)
    return local_db, temp_dir, meta

# Push DB and its sidecars back to the device
def push_database(local_db, temp_dir, meta, adb_path="adb"):
    staging_dir = "/data/local/tmp/broccoli_stage"
    run_adb_cmd(["shell", f"su 0 mkdir -p {staging_dir}"], adb_path=adb_path)
    run_adb_cmd(["shell", f"su 0 chmod 777 {staging_dir}"], adb_path=adb_path)

    # Push files to staging
    for ext in ["", "-wal", "-shm"]:
        local_file = local_db + ext
        if os.path.exists(local_file):
            staged_file = f"{staging_dir}/broccoli{ext}"
            run_adb_cmd(["push", local_file, staged_file], adb_path=adb_path)
            
            remote_file = REMOTE_DB_PATH + ext
            # Copy from staging to private app directory
            run_adb_cmd(["shell", f"su 0 cp {shlex.quote(staged_file)} {shlex.quote(remote_file)}"], adb_path=adb_path)
            apply_file_metadata(remote_file, meta, adb_path=adb_path)
            run_adb_cmd(["shell", f"su 0 rm -f {shlex.quote(staged_file)}"], adb_path=adb_path)
        else:
            # If sidecar doesn't exist locally, remove it remotely to avoid inconsistency
            remote_file = REMOTE_DB_PATH + ext
            run_adb_cmd(["shell", f"su 0 rm -f {shlex.quote(remote_file)}"], adb_path=adb_path)

    run_adb_cmd(["shell", f"su 0 rmdir {staging_dir}"], adb_path=adb_path)

# Clean up local temporary directory
def cleanup_local(temp_dir):
    if temp_dir and os.path.exists(temp_dir):
        for f in os.listdir(temp_dir):
            try:
                os.remove(os.path.join(temp_dir, f))
            except Exception:
                pass
        try:
            os.rmdir(temp_dir)
        except Exception:
            pass

# Discover schema details from the SQLite connection
def discover_schema(conn):
    cursor = conn.cursor()
    
    # 1. Discover recipe table
    recipe_table = None
    for candidate in ["recipes", "recipe", "recipeentity"]:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (candidate,))
        if cursor.fetchone():
            recipe_table = candidate
            break
    if not recipe_table:
        raise ValueError("Recipe table not found in database.")

    # 2. Discover columns of recipe table
    cursor.execute(f"PRAGMA table_info({recipe_table})")
    columns = [row[1] for row in cursor.fetchall()]
    
    # Map logical fields to actual columns
    mapped_fields = {}
    for logical, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            # Case-insensitive match
            match = next((col for col in columns if col.lower() == alias.lower()), None)
            if match:
                mapped_fields[logical] = match
                break

    # 3. Discover child tables
    ingredients_table = None
    for candidate in ["ingredients", "ingredient"]:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (candidate,))
        if cursor.fetchone():
            ingredients_table = candidate
            break
            
    directions_table = None
    for candidate in ["direction_steps", "directions", "direction", "steps", "step"]:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (candidate,))
        if cursor.fetchone():
            directions_table = candidate
            break

    # Discover child table columns if they exist
    ingredients_cols = {}
    if ingredients_table:
        cursor.execute(f"PRAGMA table_info({ingredients_table})")
        cols = [row[1] for row in cursor.fetchall()]
        ingredients_cols["recipe_id"] = next((c for c in cols if c.lower() in ["recipeid", "recipe_id"]), None)
        ingredients_cols["name"] = next((c for c in cols if c.lower() in ["name", "ingredient", "text", "value"]), None)
        ingredients_cols["order"] = next((c for c in cols if c.lower() in ["order", "position", "idx", "sort"]), None)

    directions_cols = {}
    if directions_table:
        cursor.execute(f"PRAGMA table_info({directions_table})")
        cols = [row[1] for row in cursor.fetchall()]
        directions_cols["recipe_id"] = next((c for c in cols if c.lower() in ["recipeid", "recipe_id"]), None)
        directions_cols["text"] = next((c for c in cols if c.lower() in ["text", "direction", "step", "instruction", "value"]), None)
        directions_cols["order"] = next((c for c in cols if c.lower() in ["order", "position", "idx", "sort"]), None)

    return {
        "recipe_table": recipe_table,
        "recipe_fields": mapped_fields,
        "ingredients_table": ingredients_table,
        "ingredients_cols": ingredients_cols,
        "directions_table": directions_table,
        "directions_cols": directions_cols
    }

# Public Tool: list_recipes
def list_recipes(search_query: str = None, adb_path: str = "adb") -> dict:
    local_db, temp_dir, _ = pull_database(adb_path=adb_path)
    if not local_db:
        return {"recipes": [], "success": True, "error": ""}
    
    try:
        conn = sqlite3.connect(local_db)
        schema = discover_schema(conn)
        cursor = conn.cursor()
        
        recipe_table = schema["recipe_table"]
        fields = schema["recipe_fields"]
        
        # Build select columns
        select_cols = []
        col_map = {}
        for logical in ["id", "title", "description", "servings", "prep_time", "image_name", "is_favorite"]:
            col_name = fields.get(logical)
            if col_name:
                select_cols.append(col_name)
                col_map[logical] = len(select_cols) - 1
        
        query = f"SELECT {', '.join(select_cols)} FROM {recipe_table}"
        params = []
        
        if search_query:
            title_col = fields.get("title")
            desc_col = fields.get("description")
            clauses = []
            if title_col:
                clauses.append(f"{title_col} LIKE ?")
                params.append(f"%{search_query}%")
            if desc_col:
                clauses.append(f"{desc_col} LIKE ?")
                params.append(f"%{search_query}%")
            if clauses:
                query += " WHERE " + " OR ".join(clauses)
                
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        recipes = []
        for row in rows:
            recipe = {}
            for logical, idx in col_map.items():
                val = row[idx]
                if logical == "is_favorite":
                    recipe[logical] = bool(val) if val is not None else False
                elif val is not None:
                    recipe[logical] = str(val)
            # Ensure required fields are present
            if "id" in recipe and "title" in recipe:
                recipes.append(recipe)
                
        conn.close()
        return {"recipes": recipes, "success": True, "error": ""}
    except Exception as e:
        return {"recipes": [], "success": False, "error": str(e)}
    finally:
        cleanup_local(temp_dir)

# Public Tool: get_recipe_details
def get_recipe_details(recipe_id: str, adb_path: str = "adb") -> dict:
    local_db, temp_dir, _ = pull_database(adb_path=adb_path)
    if not local_db:
        return {"success": False, "error": "Database not found on device.", "id": "", "title": "", "ingredients": [], "directions": []}
        
    try:
        conn = sqlite3.connect(local_db)
        schema = discover_schema(conn)
        cursor = conn.cursor()
        
        recipe_table = schema["recipe_table"]
        fields = schema["recipe_fields"]
        
        # Fetch parent recipe
        select_cols = []
        col_map = {}
        for logical in ["id", "title", "description", "servings", "prep_time", "image_name", "is_favorite", "ingredients", "directions"]:
            col_name = fields.get(logical)
            if col_name:
                select_cols.append(col_name)
                col_map[logical] = len(select_cols) - 1
                
        id_col = fields.get("id")
        if not id_col:
            raise ValueError("Primary key column not mapped.")
            
        query = f"SELECT {', '.join(select_cols)} FROM {recipe_table} WHERE {id_col} = ?"
        cursor.execute(query, (recipe_id,))
        row = cursor.fetchone()
        if not row:
            return {"success": False, "error": f"Recipe with ID {recipe_id} not found.", "id": "", "title": "", "ingredients": [], "directions": []}
            
        recipe = {}
        for logical, idx in col_map.items():
            val = row[idx]
            if logical == "is_favorite":
                recipe[logical] = bool(val) if val is not None else False
            elif val is not None:
                recipe[logical] = str(val)
                
        # Resolve ingredients
        ingredients = []
        ing_table = schema["ingredients_table"]
        ing_cols = schema["ingredients_cols"]
        if ing_table and ing_cols.get("recipe_id") and ing_cols.get("name"):
            order_clause = f" ORDER BY {ing_cols['order']}" if ing_cols.get("order") else ""
            cursor.execute(f"SELECT {ing_cols['name']} FROM {ing_table} WHERE {ing_cols['recipe_id']} = ?{order_clause}", (recipe_id,))
            ingredients = [r[0] for r in cursor.fetchall() if r[0] is not None]
        elif "ingredients" in recipe:
            # Fallback to inline serialized ingredients if child table is not present
            try:
                ingredients = json.loads(recipe["ingredients"])
                if not isinstance(ingredients, list):
                    ingredients = [recipe["ingredients"]]
            except Exception:
                ingredients = [recipe["ingredients"]]
                
        # Resolve directions
        directions = []
        dir_table = schema["directions_table"]
        dir_cols = schema["directions_cols"]
        if dir_table and dir_cols.get("recipe_id") and dir_cols.get("text"):
            order_clause = f" ORDER BY {dir_cols['order']}" if dir_cols.get("order") else ""
            cursor.execute(f"SELECT {dir_cols['text']} FROM {dir_table} WHERE {dir_cols['recipe_id']} = ?{order_clause}", (recipe_id,))
            directions = [r[0] for r in cursor.fetchall() if r[0] is not None]
        elif "directions" in recipe:
            # Fallback to inline serialized directions if child table is not present
            try:
                directions = json.loads(recipe["directions"])
                if not isinstance(directions, list):
                    directions = [recipe["directions"]]
            except Exception:
                directions = [recipe["directions"]]
                
        conn.close()
        
        return {
            "id": recipe.get("id", recipe_id),
            "title": recipe.get("title", ""),
            "description": recipe.get("description", ""),
            "servings": recipe.get("servings", ""),
            "prep_time": recipe.get("prep_time", ""),
            "image_name": recipe.get("image_name", ""),
            "is_favorite": recipe.get("is_favorite", False),
            "ingredients": ingredients,
            "directions": directions,
            "success": True,
            "error": ""
        }
    except Exception as e:
        return {"success": False, "error": str(e), "id": "", "title": "", "ingredients": [], "directions": []}
    finally:
        cleanup_local(temp_dir)

# Public Tool: create_recipe
def create_recipe(title: str, ingredients: list, directions: list, description: str = None, servings: str = None, prep_time: str = None, image_name: str = None, adb_path: str = "adb") -> dict:
    stop_app(adb_path=adb_path)
    local_db, temp_dir, meta = pull_database(adb_path=adb_path)
    if not local_db:
        return {"id": "", "success": False, "error": "Database not found on device."}
        
    try:
        conn = sqlite3.connect(local_db)
        schema = discover_schema(conn)
        cursor = conn.cursor()
        
        recipe_table = schema["recipe_table"]
        fields = schema["recipe_fields"]
        
        # Prepare parent row values
        insert_fields = []
        insert_placeholders = []
        insert_values = []
        
        # Title (Required)
        title_col = fields.get("title")
        if not title_col:
            raise ValueError("Title column not mapped in schema.")
        insert_fields.append(title_col)
        insert_placeholders.append("?")
        insert_values.append(title)
        
        # Optional fields
        if description is not None and "description" in fields:
            insert_fields.append(fields["description"])
            insert_placeholders.append("?")
            insert_values.append(description)
            
        if servings is not None and "servings" in fields:
            insert_fields.append(fields["servings"])
            insert_placeholders.append("?")
            insert_values.append(servings)
            
        if prep_time is not None and "prep_time" in fields:
            insert_fields.append(fields["prep_time"])
            insert_placeholders.append("?")
            insert_values.append(prep_time)
            
        if image_name is not None and "image_name" in fields:
            insert_fields.append(fields["image_name"])
            insert_placeholders.append("?")
            insert_values.append(image_name)
            
        # If child tables do not exist, serialize ingredients/directions inline
        if not schema["ingredients_table"] and "ingredients" in fields:
            insert_fields.append(fields["ingredients"])
            insert_placeholders.append("?")
            insert_values.append(json.dumps(ingredients))
            
        if not schema["directions_table"] and "directions" in fields:
            insert_fields.append(fields["directions"])
            insert_placeholders.append("?")
            insert_values.append(json.dumps(directions))
            
        # Execute parent insert
        query = f"INSERT INTO {recipe_table} ({', '.join(insert_fields)}) VALUES ({', '.join(insert_placeholders)})"
        cursor.execute(query, insert_values)
        recipe_id = str(cursor.lastrowid)
        
        # Insert ingredients into child table if applicable
        ing_table = schema["ingredients_table"]
        ing_cols = schema["ingredients_cols"]
        if ing_table and ing_cols.get("recipe_id") and ing_cols.get("name"):
            for idx, ing in enumerate(ingredients):
                cols = [ing_cols["recipe_id"], ing_cols["name"]]
                vals = [recipe_id, ing]
                if ing_cols.get("order"):
                    cols.append(ing_cols["order"])
                    vals.append(idx)
                placeholders = ", ".join(["?"] * len(cols))
                cursor.execute(f"INSERT INTO {ing_table} ({', '.join(cols)}) VALUES ({placeholders})", vals)
                
        # Insert directions into child table if applicable
        dir_table = schema["directions_table"]
        dir_cols = schema["directions_cols"]
        if dir_table and dir_cols.get("recipe_id") and dir_cols.get("text"):
            for idx, step in enumerate(directions):
                cols = [dir_cols["recipe_id"], dir_cols["text"]]
                vals = [recipe_id, step]
                if dir_cols.get("order"):
                    cols.append(dir_cols["order"])
                    vals.append(idx)
                placeholders = ", ".join(["?"] * len(cols))
                cursor.execute(f"INSERT INTO {dir_table} ({', '.join(cols)}) VALUES ({placeholders})", vals)
                
        conn.commit()
        conn.close()
        
        # Push updated DB back to device
        push_database(local_db, temp_dir, meta, adb_path=adb_path)
        return {"id": recipe_id, "success": True, "error": ""}
    except Exception as e:
        return {"id": "", "success": False, "error": str(e)}
    finally:
        cleanup_local(temp_dir)

# Public Tool: update_recipe
def update_recipe(recipe_id: str, title: str = None, description: str = None, servings: str = None, prep_time: str = None, ingredients: list = None, directions: list = None, image_name: str = None, is_favorite: bool = None, adb_path: str = "adb") -> dict:
    stop_app(adb_path=adb_path)
    local_db, temp_dir, meta = pull_database(adb_path=adb_path)
    if not local_db:
        return {"success": False, "error": "Database not found on device."}
        
    try:
        conn = sqlite3.connect(local_db)
        schema = discover_schema(conn)
        cursor = conn.cursor()
        
        recipe_table = schema["recipe_table"]
        fields = schema["recipe_fields"]
        
        id_col = fields.get("id")
        if not id_col:
            raise ValueError("Primary key column not mapped.")
            
        # Verify recipe exists
        cursor.execute(f"SELECT 1 FROM {recipe_table} WHERE {id_col} = ?", (recipe_id,))
        if not cursor.fetchone():
            return {"success": False, "error": f"Recipe with ID {recipe_id} not found."}
            
        # Update parent fields
        update_clauses = []
        update_values = []
        
        if title is not None and "title" in fields:
            update_clauses.append(f"{fields['title']} = ?")
            update_values.append(title)
        if description is not None and "description" in fields:
            update_clauses.append(f"{fields['description']} = ?")
            update_values.append(description)
        if servings is not None and "servings" in fields:
            update_clauses.append(f"{fields['servings']} = ?")
            update_values.append(servings)
        if prep_time is not None and "prep_time" in fields:
            update_clauses.append(f"{fields['prep_time']} = ?")
            update_values.append(prep_time)
        if image_name is not None and "image_name" in fields:
            update_clauses.append(f"{fields['image_name']} = ?")
            update_values.append(image_name)
        if is_favorite is not None and "is_favorite" in fields:
            update_clauses.append(f"{fields['is_favorite']} = ?")
            update_values.append(1 if is_favorite else 0)
            
        # Inline serialization updates if child tables do not exist
        if ingredients is not None and not schema["ingredients_table"] and "ingredients" in fields:
            update_clauses.append(f"{fields['ingredients']} = ?")
            update_values.append(json.dumps(ingredients))
        if directions is not None and not schema["directions_table"] and "directions" in fields:
            update_clauses.append(f"{fields['directions']} = ?")
            update_values.append(json.dumps(directions))
            
        if update_clauses:
            query = f"UPDATE {recipe_table} SET {', '.join(update_clauses)} WHERE {id_col} = ?"
            update_values.append(recipe_id)
            cursor.execute(query, update_values)
            
        # Update child ingredients table if applicable
        ing_table = schema["ingredients_table"]
        ing_cols = schema["ingredients_cols"]
        if ingredients is not None and ing_table and ing_cols.get("recipe_id") and ing_cols.get("name"):
            # Delete existing
            cursor.execute(f"DELETE FROM {ing_table} WHERE {ing_cols['recipe_id']} = ?", (recipe_id,))
            # Insert new
            for idx, ing in enumerate(ingredients):
                cols = [ing_cols["recipe_id"], ing_cols["name"]]
                vals = [recipe_id, ing]
                if ing_cols.get("order"):
                    cols.append(ing_cols["order"])
                    vals.append(idx)
                placeholders = ", ".join(["?"] * len(cols))
                cursor.execute(f"INSERT INTO {ing_table} ({', '.join(cols)}) VALUES ({placeholders})", vals)
                
        # Update child directions table if applicable
        dir_table = schema["directions_table"]
        dir_cols = schema["directions_cols"]
        if directions is not None and dir_table and dir_cols.get("recipe_id") and dir_cols.get("text"):
            # Delete existing
            cursor.execute(f"DELETE FROM {dir_table} WHERE {dir_cols['recipe_id']} = ?", (recipe_id,))
            # Insert new
            for idx, step in enumerate(directions):
                cols = [dir_cols["recipe_id"], dir_cols["text"]]
                vals = [recipe_id, step]
                if dir_cols.get("order"):
                    cols.append(dir_cols["order"])
                    vals.append(idx)
                placeholders = ", ".join(["?"] * len(cols))
                cursor.execute(f"INSERT INTO {dir_table} ({', '.join(cols)}) VALUES ({placeholders})", vals)
                
        conn.commit()
        conn.close()
        
        push_database(local_db, temp_dir, meta, adb_path=adb_path)
        return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        cleanup_local(temp_dir)

# Public Tool: delete_recipe
def delete_recipe(recipe_id: str, adb_path: str = "adb") -> dict:
    stop_app(adb_path=adb_path)
    local_db, temp_dir, meta = pull_database(adb_path=adb_path)
    if not local_db:
        return {"success": False, "error": "Database not found on device."}
        
    try:
        conn = sqlite3.connect(local_db)
        schema = discover_schema(conn)
        cursor = conn.cursor()
        
        recipe_table = schema["recipe_table"]
        fields = schema["recipe_fields"]
        
        id_col = fields.get("id")
        if not id_col:
            raise ValueError("Primary key column not mapped.")
            
        # Verify recipe exists
        cursor.execute(f"SELECT 1 FROM {recipe_table} WHERE {id_col} = ?", (recipe_id,))
        if not cursor.fetchone():
            return {"success": False, "error": f"Recipe with ID {recipe_id} not found."}
            
        # Delete child ingredients if table exists
        ing_table = schema["ingredients_table"]
        ing_cols = schema["ingredients_cols"]
        if ing_table and ing_cols.get("recipe_id"):
            cursor.execute(f"DELETE FROM {ing_table} WHERE {ing_cols['recipe_id']} = ?", (recipe_id,))
            
        # Delete child directions if table exists
        dir_table = schema["directions_table"]
        dir_cols = schema["directions_cols"]
        if dir_table and dir_cols.get("recipe_id"):
            cursor.execute(f"DELETE FROM {dir_table} WHERE {dir_cols['recipe_id']} = ?", (recipe_id,))
            
        # Delete parent recipe
        cursor.execute(f"DELETE FROM {recipe_table} WHERE {id_col} = ?", (recipe_id,))
        
        conn.commit()
        conn.close()
        
        push_database(local_db, temp_dir, meta, adb_path=adb_path)
        return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        cleanup_local(temp_dir)

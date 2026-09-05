import os
import sys
import json
import shlex
import subprocess

# Package and DB configuration
PACKAGE_NAME = "com.flauschcode.broccoli"
REMOTE_DB_PATH = "/data/data/com.flauschcode.broccoli/databases/broccoli"

# Field aliases mapping
FIELD_ALIASES = {
    "id": ["_id", "id"],
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

# Execute a query on the device using sqlite3 binary
def run_sqlite_query(query, params=None, adb_path="adb"):
    formatted_query = query
    if params:
        for param in params:
            if param is None:
                val_str = "NULL"
            elif isinstance(param, (int, float)):
                val_str = str(param)
            else:
                # Escape single quotes
                escaped = str(param).replace("'", "''")
                val_str = f"'{escaped}'"
            formatted_query = formatted_query.replace("?", val_str, 1)

    # Run sqlite3 command on device
    shell_cmd = f"su 0 sqlite3 -json {shlex.quote(REMOTE_DB_PATH)} {shlex.quote(formatted_query)}"
    res = run_adb_cmd(["shell", shell_cmd], adb_path=adb_path)
    
    if res.returncode != 0:
        # Fallback without -json if the device's sqlite3 doesn't support it
        shell_cmd_fallback = f"su 0 sqlite3 -line {shlex.quote(REMOTE_DB_PATH)} {shlex.quote(formatted_query)}"
        res_fallback = run_adb_cmd(["shell", shell_cmd_fallback], adb_path=adb_path)
        if res_fallback.returncode != 0:
            raise RuntimeError(f"SQLite error: {res_fallback.stderr.strip() or res_fallback.stdout.strip()}")
        return parse_line_output(res_fallback.stdout)
        
    stdout = res.stdout.strip()
    if not stdout:
        return []
    try:
        return json.loads(stdout)
    except Exception:
        # Fallback parsing if JSON is malformed
        return parse_line_output(stdout)

def parse_line_output(stdout):
    results = []
    current_row = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            if current_row:
                results.append(current_row)
                current_row = {}
            continue
        if "=" in line:
            parts = line.split("=", 1)
            col = parts[0].strip()
            val = parts[1].strip()
            current_row[col] = val
    if current_row:
        results.append(current_row)
    return results

# Discover schema details from the SQLite connection on device
def discover_schema(adb_path="adb"):
    # 1. Discover recipe table
    recipe_table = None
    for candidate in ["recipes", "recipe", "recipeentity"]:
        res = run_sqlite_query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [candidate], adb_path=adb_path)
        if res:
            recipe_table = candidate
            break
    if not recipe_table:
        raise ValueError("Recipe table not found in database.")

    # 2. Discover columns of recipe table
    columns_res = run_sqlite_query(f"PRAGMA table_info({recipe_table})", adb_path=adb_path)
    columns = [row.get("name") for row in columns_res if row.get("name")]
    
    # Map logical fields to actual columns
    mapped_fields = {}
    for logical, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            match = next((col for col in columns if col.lower() == alias.lower()), None)
            if match:
                mapped_fields[logical] = match
                break

    # Ensure primary key column is mapped even if not matched by aliases
    if "id" not in mapped_fields:
        for col in columns:
            if col.lower() in ["_id", "id"]:
                mapped_fields["id"] = col
                break
        if "id" not in mapped_fields and columns:
            mapped_fields["id"] = columns[0]

    # 3. Discover child tables
    ingredients_table = None
    for candidate in ["ingredients", "ingredient"]:
        res = run_sqlite_query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [candidate], adb_path=adb_path)
        if res:
            ingredients_table = candidate
            break
            
    directions_table = None
    for candidate in ["direction_steps", "directions", "direction", "steps", "step"]:
        res = run_sqlite_query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [candidate], adb_path=adb_path)
        if res:
            directions_table = candidate
            break

    # Discover child table columns if they exist
    ingredients_cols = {}
    if ingredients_table:
        cols_res = run_sqlite_query(f"PRAGMA table_info({ingredients_table})", adb_path=adb_path)
        cols = [row.get("name") for row in cols_res if row.get("name")]
        ingredients_cols["recipe_id"] = next((c for c in cols if c.lower() in ["recipeid", "recipe_id"]), None)
        ingredients_cols["name"] = next((c for c in cols if c.lower() in ["name", "ingredient", "text", "value"]), None)
        ingredients_cols["order"] = next((c for c in cols if c.lower() in ["order", "position", "idx", "sort"]), None)

    directions_cols = {}
    if directions_table:
        cols_res = run_sqlite_query(f"PRAGMA table_info({directions_table})", adb_path=adb_path)
        cols = [row.get("name") for row in cols_res if row.get("name")]
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
    try:
        schema = discover_schema(adb_path=adb_path)
        recipe_table = schema["recipe_table"]
        fields = schema["recipe_fields"]
        
        select_cols = []
        for logical in ["id", "title", "description", "servings", "prep_time", "image_name", "is_favorite"]:
            col_name = fields.get(logical)
            if col_name:
                select_cols.append(col_name)
        
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
                
        rows = run_sqlite_query(query, params, adb_path=adb_path)
        
        recipes = []
        for row in rows:
            recipe = {}
            for logical in ["id", "title", "description", "servings", "prep_time", "image_name", "is_favorite"]:
                col_name = fields.get(logical)
                if col_name and col_name in row:
                    val = row[col_name]
                    if logical == "is_favorite":
                        recipe[logical] = bool(int(val)) if val is not None and str(val).isdigit() else False
                    elif val is not None:
                        recipe[logical] = str(val)
            if "id" in recipe and "title" in recipe:
                recipes.append(recipe)
                
        return {"recipes": recipes, "success": True, "error": ""}
    except Exception as e:
        return {"recipes": [], "success": False, "error": str(e)}

# Public Tool: get_recipe_details
def get_recipe_details(recipe_id: str, adb_path: str = "adb") -> dict:
    try:
        schema = discover_schema(adb_path=adb_path)
        recipe_table = schema["recipe_table"]
        fields = schema["recipe_fields"]
        
        select_cols = []
        for logical in ["id", "title", "description", "servings", "prep_time", "image_name", "is_favorite", "ingredients", "directions"]:
            col_name = fields.get(logical)
            if col_name:
                select_cols.append(col_name)
                
        id_col = fields.get("id")
        if not id_col:
            raise ValueError("Primary key column not mapped.")
            
        query = f"SELECT {', '.join(select_cols)} FROM {recipe_table} WHERE {id_col} = ?"
        rows = run_sqlite_query(query, [recipe_id], adb_path=adb_path)
        if not rows:
            return {"success": False, "error": f"Recipe with ID {recipe_id} not found.", "id": "", "title": "", "ingredients": [], "directions": []}
            
        row = rows[0]
        recipe = {}
        for logical in ["id", "title", "description", "servings", "prep_time", "image_name", "is_favorite", "ingredients", "directions"]:
            col_name = fields.get(logical)
            if col_name and col_name in row:
                val = row[col_name]
                if logical == "is_favorite":
                    recipe[logical] = bool(int(val)) if val is not None and str(val).isdigit() else False
                elif val is not None:
                    recipe[logical] = str(val)
                
        # Resolve ingredients
        ingredients = []
        ing_table = schema["ingredients_table"]
        ing_cols = schema["ingredients_cols"]
        if ing_table and ing_cols.get("recipe_id") and ing_cols.get("name"):
            order_clause = f" ORDER BY {ing_cols['order']}" if ing_cols.get("order") else ""
            ing_rows = run_sqlite_query(f"SELECT {ing_cols['name']} FROM {ing_table} WHERE {ing_cols['recipe_id']} = ?{order_clause}", [recipe_id], adb_path=adb_path)
            ingredients = [r.get(ing_cols["name"]) for r in ing_rows if r.get(ing_cols["name"]) is not None]
        elif "ingredients" in recipe:
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
            dir_rows = run_sqlite_query(f"SELECT {dir_cols['text']} FROM {dir_table} WHERE {dir_cols['recipe_id']} = ?{order_clause}", [recipe_id], adb_path=adb_path)
            directions = [r.get(dir_cols["text"]) for r in dir_rows if r.get(dir_cols["text"]) is not None]
        elif "directions" in recipe:
            try:
                directions = json.loads(recipe["directions"])
                if not isinstance(directions, list):
                    directions = [recipe["directions"]]
            except Exception:
                directions = [recipe["directions"]]
                
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

# Public Tool: create_recipe
def create_recipe(title: str, ingredients: list, directions: list, description: str = None, servings: str = None, prep_time: str = None, image_name: str = None, adb_path: str = "adb") -> dict:
    stop_app(adb_path=adb_path)
    try:
        schema = discover_schema(adb_path=adb_path)
        recipe_table = schema["recipe_table"]
        fields = schema["recipe_fields"]
        
        insert_fields = []
        insert_placeholders = []
        insert_values = []
        
        title_col = fields.get("title")
        if not title_col:
            raise ValueError("Title column not mapped in schema.")
        insert_fields.append(title_col)
        insert_placeholders.append("?")
        insert_values.append(title)
        
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
            
        if not schema["ingredients_table"] and "ingredients" in fields:
            insert_fields.append(fields["ingredients"])
            insert_placeholders.append("?")
            insert_values.append(json.dumps(ingredients))
            
        if not schema["directions_table"] and "directions" in fields:
            insert_fields.append(fields["directions"])
            insert_placeholders.append("?")
            insert_values.append(json.dumps(directions))
            
        # Handle NOT NULL constraint on favorite/is_favorite column if mapped
        if "is_favorite" in fields:
            insert_fields.append(fields["is_favorite"])
            insert_placeholders.append("?")
            insert_values.append(0)
            
        # Execute parent insert
        query = f"INSERT INTO {recipe_table} ({', '.join(insert_fields)}) VALUES ({', '.join(insert_placeholders)})"
        run_sqlite_query(query, insert_values, adb_path=adb_path)
        
        # Retrieve the last inserted row ID using the mapped primary key column
        id_col = fields.get("id") or "id"
        try:
            last_id_res = run_sqlite_query(f"SELECT max({id_col}) as last_id FROM {recipe_table}", adb_path=adb_path)
            if last_id_res and last_id_res[0].get("last_id") is not None:
                recipe_id = str(last_id_res[0].get("last_id"))
            else:
                rowid_res = run_sqlite_query("SELECT last_insert_rowid() as last_id", adb_path=adb_path)
                recipe_id = str(rowid_res[0].get("last_id")) if rowid_res else "1"
        except Exception:
            rowid_res = run_sqlite_query("SELECT last_insert_rowid() as last_id", adb_path=adb_path)
            recipe_id = str(rowid_res[0].get("last_id")) if rowid_res else "1"
        
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
                run_sqlite_query(f"INSERT INTO {ing_table} ({', '.join(cols)}) VALUES ({placeholders})", vals, adb_path=adb_path)
                
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
                run_sqlite_query(f"INSERT INTO {dir_table} ({', '.join(cols)}) VALUES ({placeholders})", vals, adb_path=adb_path)
                
        return {"id": recipe_id, "success": True, "error": ""}
    except Exception as e:
        return {"id": "", "success": False, "error": str(e)}

# Public Tool: update_recipe
def update_recipe(recipe_id: str, title: str = None, description: str = None, servings: str = None, prep_time: str = None, ingredients: list = None, directions: list = None, image_name: str = None, is_favorite: bool = None, adb_path: str = "adb") -> dict:
    stop_app(adb_path=adb_path)
    try:
        schema = discover_schema(adb_path=adb_path)
        recipe_table = schema["recipe_table"]
        fields = schema["recipe_fields"]
        
        id_col = fields.get("id")
        if not id_col:
            raise ValueError("Primary key column not mapped.")
            
        # Verify recipe exists
        exists_res = run_sqlite_query(f"SELECT 1 FROM {recipe_table} WHERE {id_col} = ?", [recipe_id], adb_path=adb_path)
        if not exists_res:
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
            
        if ingredients is not None and not schema["ingredients_table"] and "ingredients" in fields:
            update_clauses.append(f"{fields['ingredients']} = ?")
            update_values.append(json.dumps(ingredients))
        if directions is not None and not schema["directions_table"] and "directions" in fields:
            update_clauses.append(f"{fields['directions']} = ?")
            update_values.append(json.dumps(directions))
            
        if update_clauses:
            query = f"UPDATE {recipe_table} SET {', '.join(update_clauses)} WHERE {id_col} = ?"
            update_values.append(recipe_id)
            run_sqlite_query(query, update_values, adb_path=adb_path)
            
        # Update child ingredients table if applicable
        ing_table = schema["ingredients_table"]
        ing_cols = schema["ingredients_cols"]
        if ingredients is not None and ing_table and ing_cols.get("recipe_id") and ing_cols.get("name"):
            run_sqlite_query(f"DELETE FROM {ing_table} WHERE {ing_cols['recipe_id']} = ?", [recipe_id], adb_path=adb_path)
            for idx, ing in enumerate(ingredients):
                cols = [ing_cols["recipe_id"], ing_cols["name"]]
                vals = [recipe_id, ing]
                if ing_cols.get("order"):
                    cols.append(ing_cols["order"])
                    vals.append(idx)
                placeholders = ", ".join(["?"] * len(cols))
                run_sqlite_query(f"INSERT INTO {ing_table} ({', '.join(cols)}) VALUES ({placeholders})", vals, adb_path=adb_path)
                
        # Update child directions table if applicable
        dir_table = schema["directions_table"]
        dir_cols = schema["directions_cols"]
        if directions is not None and dir_table and dir_cols.get("recipe_id") and dir_cols.get("text"):
            run_sqlite_query(f"DELETE FROM {dir_table} WHERE {dir_cols['recipe_id']} = ?", [recipe_id], adb_path=adb_path)
            for idx, step in enumerate(directions):
                cols = [dir_cols["recipe_id"], dir_cols["text"]]
                vals = [recipe_id, step]
                if dir_cols.get("order"):
                    cols.append(dir_cols["order"])
                    vals.append(idx)
                placeholders = ", ".join(["?"] * len(cols))
                run_sqlite_query(f"INSERT INTO {dir_table} ({', '.join(cols)}) VALUES ({placeholders})", vals, adb_path=adb_path)
                
        return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}

# Public Tool: delete_recipe
def delete_recipe(recipe_id: str, adb_path: str = "adb") -> dict:
    stop_app(adb_path=adb_path)
    try:
        schema = discover_schema(adb_path=adb_path)
        recipe_table = schema["recipe_table"]
        fields = schema["recipe_fields"]
        
        id_col = fields.get("id")
        if not id_col:
            raise ValueError("Primary key column not mapped.")
            
        # Verify recipe exists
        exists_res = run_sqlite_query(f"SELECT 1 FROM {recipe_table} WHERE {id_col} = ?", [recipe_id], adb_path=adb_path)
        if not exists_res:
            return {"success": False, "error": f"Recipe with ID {recipe_id} not found."}
            
        # Delete child ingredients if table exists
        ing_table = schema["ingredients_table"]
        ing_cols = schema["ingredients_cols"]
        if ing_table and ing_cols.get("recipe_id"):
            run_sqlite_query(f"DELETE FROM {ing_table} WHERE {ing_cols['recipe_id']} = ?", [recipe_id], adb_path=adb_path)
            
        # Delete child directions if table exists
        dir_table = schema["directions_table"]
        dir_cols = schema["directions_cols"]
        if dir_table and dir_cols.get("recipe_id"):
            run_sqlite_query(f"DELETE FROM {dir_table} WHERE {dir_cols['recipe_id']} = ?", [recipe_id], adb_path=adb_path)
            
        # Delete parent recipe
        run_sqlite_query(f"DELETE FROM {recipe_table} WHERE {id_col} = ?", [recipe_id], adb_path=adb_path)
        
        return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}

import os
import json
import shlex
import subprocess
import base64

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

# Execute SQLite query directly on the device using native sqlite3 binary
def run_sqlite_query(query, adb_path="adb"):
    # To avoid shell escaping issues with complex characters (like brackets, quotes, backslashes in JSON),
    # we pass the query via base64 encoding and decode it on the device.
    b64_query = base64.b64encode(query.encode('utf-8')).decode('utf-8')
    shell_cmd = f"echo {b64_query} | base64 -d | su 0 sqlite3 -json {shlex.quote(REMOTE_DB_PATH)}"
    res = run_adb_cmd(["shell", shell_cmd], adb_path=adb_path)
    if res.returncode != 0:
        raise RuntimeError(f"SQLite error: {res.stderr.strip() or res.stdout.strip()}")
    
    output = res.stdout.strip()
    if not output:
        return []
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        # Fallback if -json is not supported or output is plain text
        lines = [line.strip() for line in output.split('\n') if line.strip()]
        return lines

# Discover schema details from the SQLite connection on device
def discover_schema(adb_path="adb"):
    # 1. Discover recipe table
    recipe_table = None
    for candidate in ["recipes", "recipe", "recipeentity"]:
        res = run_sqlite_query(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{candidate}';", adb_path=adb_path)
        if res:
            recipe_table = candidate
            break
    if not recipe_table:
        raise ValueError("Recipe table not found in database.")

    # 2. Discover columns of recipe table
    columns_res = run_sqlite_query(f"PRAGMA table_info({recipe_table});", adb_path=adb_path)
    columns = []
    if columns_res and isinstance(columns_res, list):
        if isinstance(columns_res[0], dict):
            columns = [row.get("name") for row in columns_res if row.get("name")]
        else:
            # Fallback parsing
            for line in columns_res:
                parts = line.split('|')
                if len(parts) > 1:
                    columns.append(parts[1])

    # Map logical fields to actual columns
    mapped_fields = {}
    for logical, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            match = next((col for col in columns if col.lower() == alias.lower()), None)
            if match:
                mapped_fields[logical] = match
                break

    # 3. Discover child tables
    ingredients_table = None
    for candidate in ["ingredients", "ingredient"]:
        res = run_sqlite_query(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{candidate}';", adb_path=adb_path)
        if res:
            ingredients_table = candidate
            break
            
    directions_table = None
    for candidate in ["direction_steps", "directions", "direction", "steps", "step"]:
        res = run_sqlite_query(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{candidate}';", adb_path=adb_path)
        if res:
            directions_table = candidate
            break

    # Discover child table columns if they exist
    ingredients_cols = {}
    if ingredients_table:
        cols_res = run_sqlite_query(f"PRAGMA table_info({ingredients_table});", adb_path=adb_path)
        cols = []
        if cols_res and isinstance(cols_res, list):
            if isinstance(cols_res[0], dict):
                cols = [row.get("name") for row in cols_res if row.get("name")]
            else:
                for line in cols_res:
                    parts = line.split('|')
                    if len(parts) > 1:
                        cols.append(parts[1])
        ingredients_cols["recipe_id"] = next((c for c in cols if c.lower() in ["recipeid", "recipe_id"]), None)
        ingredients_cols["name"] = next((c for c in cols if c.lower() in ["name", "ingredient", "text", "value"]), None)
        ingredients_cols["order"] = next((c for c in cols if c.lower() in ["order", "position", "idx", "sort"]), None)

    directions_cols = {}
    if directions_table:
        cols_res = run_sqlite_query(f"PRAGMA table_info({directions_table});", adb_path=adb_path)
        cols = []
        if cols_res and isinstance(cols_res, list):
            if isinstance(cols_res[0], dict):
                cols = [row.get("name") for row in cols_res if row.get("name")]
            else:
                for line in cols_res:
                    parts = line.split('|')
                    if len(parts) > 1:
                        cols.append(parts[1])
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

# Helper to safely escape SQL string literals
def sql_escape(val: str) -> str:
    return val.replace("'", "''")

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
                select_cols.append(f"{col_name} AS {logical}")
        
        query = f"SELECT {', '.join(select_cols)} FROM {recipe_table}"
        
        if search_query:
            title_col = fields.get("title")
            desc_col = fields.get("description")
            clauses = []
            if title_col:
                clauses.append(f"{title_col} LIKE '%{sql_escape(search_query)}%'")
            if desc_col:
                clauses.append(f"{desc_col} LIKE '%{sql_escape(search_query)}%'")
            if clauses:
                query += " WHERE " + " OR ".join(clauses)
        query += ";"
                
        rows = run_sqlite_query(query, adb_path=adb_path)
        recipes = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            recipe = {}
            for logical in ["id", "title", "description", "servings", "prep_time", "image_name", "is_favorite"]:
                if logical in row:
                    val = row[logical]
                    if logical == "is_favorite":
                        recipe[logical] = bool(val) if val is not None else False
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
                select_cols.append(f"{col_name} AS {logical}")
                
        id_col = fields.get("id")
        if not id_col:
            raise ValueError("Primary key column not mapped.")
            
        query = f"SELECT {', '.join(select_cols)} FROM {recipe_table} WHERE {id_col} = '{sql_escape(recipe_id)}';"
        rows = run_sqlite_query(query, adb_path=adb_path)
        if not rows or not isinstance(rows[0], dict):
            return {"success": False, "error": f"Recipe with ID {recipe_id} not found.", "id": "", "title": "", "ingredients": [], "directions": []}
            
        row = rows[0]
        recipe = {}
        for logical in ["id", "title", "description", "servings", "prep_time", "image_name", "is_favorite", "ingredients", "directions"]:
            if logical in row:
                val = row[logical]
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
            ing_query = f"SELECT {ing_cols['name']} AS name FROM {ing_table} WHERE {ing_cols['recipe_id']} = '{sql_escape(recipe_id)}'{order_clause};"
            ing_rows = run_sqlite_query(ing_query, adb_path=adb_path)
            ingredients = [r["name"] for r in ing_rows if isinstance(r, dict) and r.get("name") is not None]
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
            dir_query = f"SELECT {dir_cols['text']} AS text FROM {dir_table} WHERE {dir_cols['recipe_id']} = '{sql_escape(recipe_id)}'{order_clause};"
            dir_rows = run_sqlite_query(dir_query, adb_path=adb_path)
            directions = [r["text"] for r in dir_rows if isinstance(r, dict) and r.get("text") is not None]
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
        insert_values = []
        
        title_col = fields.get("title")
        if not title_col:
            raise ValueError("Title column not mapped in schema.")
        insert_fields.append(title_col)
        insert_values.append(f"'{sql_escape(title)}'")
        
        if description is not None and "description" in fields:
            insert_fields.append(fields["description"])
            insert_values.append(f"'{sql_escape(description)}'")
            
        if servings is not None and "servings" in fields:
            insert_fields.append(fields["servings"])
            insert_values.append(f"'{sql_escape(servings)}'")
            
        if prep_time is not None and "prep_time" in fields:
            insert_fields.append(fields["prep_time"])
            insert_values.append(f"'{sql_escape(prep_time)}'")
            
        if image_name is not None and "image_name" in fields:
            insert_fields.append(fields["image_name"])
            insert_values.append(f"'{sql_escape(image_name)}'")
            
        # Handle NOT NULL constraint on favorite/is_favorite column
        if "is_favorite" in fields:
            insert_fields.append(fields["is_favorite"])
            insert_values.append("0")
            
        if not schema["ingredients_table"] and "ingredients" in fields:
            insert_fields.append(fields["ingredients"])
            serialized_ing = json.dumps(ingredients)
            insert_values.append(f"'{sql_escape(serialized_ing)}'")
            
        if not schema["directions_table"] and "directions" in fields:
            insert_fields.append(fields["directions"])
            serialized_dir = json.dumps(directions)
            insert_values.append(f"'{sql_escape(serialized_dir)}'")
            
        # Execute parent insert and retrieve last_insert_rowid()
        query = f"INSERT INTO {recipe_table} ({', '.join(insert_fields)}) VALUES ({', '.join(insert_values)});"
        run_sqlite_query(query, adb_path=adb_path)
        
        res = run_sqlite_query("SELECT last_insert_rowid() AS id;", adb_path=adb_path)
        recipe_id = None
        if res and isinstance(res, list) and isinstance(res[0], dict):
            recipe_id = str(res[0].get("id", ""))
        
        if not recipe_id:
            raise RuntimeError("Failed to retrieve newly created recipe ID.")
        
        # Insert ingredients into child table if applicable
        ing_table = schema["ingredients_table"]
        ing_cols = schema["ingredients_cols"]
        if ing_table and ing_cols.get("recipe_id") and ing_cols.get("name"):
            for idx, ing in enumerate(ingredients):
                cols = [ing_cols["recipe_id"], ing_cols["name"]]
                vals = [recipe_id, f"'{sql_escape(ing)}'"]
                if ing_cols.get("order"):
                    cols.append(ing_cols["order"])
                    vals.append(str(idx))
                run_sqlite_query(f"INSERT INTO {ing_table} ({', '.join(cols)}) VALUES ({', '.join(vals)});", adb_path=adb_path)
                
        # Insert directions into child table if applicable
        dir_table = schema["directions_table"]
        dir_cols = schema["directions_cols"]
        if dir_table and dir_cols.get("recipe_id") and dir_cols.get("text"):
            for idx, step in enumerate(directions):
                cols = [dir_cols["recipe_id"], dir_cols["text"]]
                vals = [recipe_id, f"'{sql_escape(step)}'"]
                if dir_cols.get("order"):
                    cols.append(dir_cols["order"])
                    vals.append(str(idx))
                run_sqlite_query(f"INSERT INTO {dir_table} ({', '.join(cols)}) VALUES ({', '.join(vals)});", adb_path=adb_path)
                
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
        check_res = run_sqlite_query(f"SELECT 1 FROM {recipe_table} WHERE {id_col} = '{sql_escape(recipe_id)}';", adb_path=adb_path)
        if not check_res:
            return {"success": False, "error": f"Recipe with ID {recipe_id} not found."}
            
        update_clauses = []
        if title is not None and "title" in fields:
            update_clauses.append(f"{fields['title']} = '{sql_escape(title)}'")
        if description is not None and "description" in fields:
            update_clauses.append(f"{fields['description']} = '{sql_escape(description)}'")
        if servings is not None and "servings" in fields:
            update_clauses.append(f"{fields['servings']} = '{sql_escape(servings)}'")
        if prep_time is not None and "prep_time" in fields:
            update_clauses.append(f"{fields['prep_time']} = '{sql_escape(prep_time)}'")
        if image_name is not None and "image_name" in fields:
            update_clauses.append(f"{fields['image_name']} = '{sql_escape(image_name)}'")
        if is_favorite is not None and "is_favorite" in fields:
            update_clauses.append(f"{fields['is_favorite']} = {1 if is_favorite else 0}")
            
        if ingredients is not None and not schema["ingredients_table"] and "ingredients" in fields:
            serialized_ing = json.dumps(ingredients)
            update_clauses.append(f"{fields['ingredients']} = '{sql_escape(serialized_ing)}'")
        if directions is not None and not schema["directions_table"] and "directions" in fields:
            serialized_dir = json.dumps(directions)
            update_clauses.append(f"{fields['directions']} = '{sql_escape(serialized_dir)}'")
            
        if update_clauses:
            run_sqlite_query(f"UPDATE {recipe_table} SET {', '.join(update_clauses)} WHERE {id_col} = '{sql_escape(recipe_id)}';", adb_path=adb_path)
            
        # Update child ingredients table if applicable
        ing_table = schema["ingredients_table"]
        ing_cols = schema["ingredients_cols"]
        if ingredients is not None and ing_table and ing_cols.get("recipe_id") and ing_cols.get("name"):
            run_sqlite_query(f"DELETE FROM {ing_table} WHERE {ing_cols['recipe_id']} = '{sql_escape(recipe_id)}';", adb_path=adb_path)
            for idx, ing in enumerate(ingredients):
                cols = [ing_cols["recipe_id"], ing_cols["name"]]
                vals = [recipe_id, f"'{sql_escape(ing)}'"]
                if ing_cols.get("order"):
                    cols.append(ing_cols["order"])
                    vals.append(str(idx))
                run_sqlite_query(f"INSERT INTO {ing_table} ({', '.join(cols)}) VALUES ({', '.join(vals)});", adb_path=adb_path)
                
        # Update child directions table if applicable
        dir_table = schema["directions_table"]
        dir_cols = schema["directions_cols"]
        if directions is not None and dir_table and dir_cols.get("recipe_id") and dir_cols.get("text"):
            run_sqlite_query(f"DELETE FROM {dir_table} WHERE {dir_cols['recipe_id']} = '{sql_escape(recipe_id)}';", adb_path=adb_path)
            for idx, step in enumerate(directions):
                cols = [dir_cols["recipe_id"], dir_cols["text"]]
                vals = [recipe_id, f"'{sql_escape(step)}'"]
                if dir_cols.get("order"):
                    cols.append(dir_cols["order"])
                    vals.append(str(idx))
                run_sqlite_query(f"INSERT INTO {dir_table} ({', '.join(cols)}) VALUES ({', '.join(vals)});", adb_path=adb_path)
                
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
        check_res = run_sqlite_query(f"SELECT 1 FROM {recipe_table} WHERE {id_col} = '{sql_escape(recipe_id)}';", adb_path=adb_path)
        if not check_res:
            return {"success": False, "error": f"Recipe with ID {recipe_id} not found."}
            
        # Delete child ingredients if table exists
        ing_table = schema["ingredients_table"]
        ing_cols = schema["ingredients_cols"]
        if ing_table and ing_cols.get("recipe_id"):
            run_sqlite_query(f"DELETE FROM {ing_table} WHERE {ing_cols['recipe_id']} = '{sql_escape(recipe_id)}';", adb_path=adb_path)
            
        # Delete child directions if table exists
        dir_table = schema["directions_table"]
        dir_cols = schema["directions_cols"]
        if dir_table and dir_cols.get("recipe_id"):
            run_sqlite_query(f"DELETE FROM {dir_table} WHERE {dir_cols['recipe_id']} = '{sql_escape(recipe_id)}';", adb_path=adb_path)
            
        # Delete parent recipe
        run_sqlite_query(f"DELETE FROM {recipe_table} WHERE {id_col} = '{sql_escape(recipe_id)}';", adb_path=adb_path)
        
        return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}

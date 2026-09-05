import os
import sys
import json
import sqlite3
import subprocess
import tempfile
import shutil

CANDIDATE_PATHS = [
    "/data/data/org.tasks/databases/database",
    "/data/data/org.tasks/databases/tasks.db",
    "/data/data/org.tasks/databases/org.tasks_tasks.db"
]

def run_adb_cmd(cmd, adb_path="adb"):
    serial = os.environ.get("ANDROID_SERIAL")
    base_cmd = [adb_path]
    if serial:
        base_cmd.extend(["-s", serial])
    return subprocess.run(base_cmd + cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def find_active_db(adb_path):
    for path in CANDIDATE_PATHS:
        res = run_adb_cmd(["shell", "su 0 test -f " + path], adb_path)
        if res.returncode == 0:
            res_header = run_adb_cmd(["shell", f"su 0 head -c 15 {path}"], adb_path)
            if b"SQLite format 3" in res_header.stdout:
                return path
    return None

def pull_db_files(remote_db_path, local_dir, adb_path):
    remote_dir = os.path.dirname(remote_db_path)
    remote_name = os.path.basename(remote_db_path)
    
    for suffix in ["", "-wal", "-shm"]:
        remote_file = f"{remote_db_path}{suffix}"
        local_file = os.path.join(local_dir, f"{remote_name}{suffix}")
        
        check_res = run_adb_cmd(["shell", f"su 0 test -f {remote_file}"], adb_path)
        if check_res.returncode == 0:
            temp_device_path = f"/data/local/tmp/{remote_name}{suffix}"
            run_adb_cmd(["shell", f"su 0 cp {remote_file} {temp_device_path}"], adb_path)
            run_adb_cmd(["shell", f"su 0 chmod 666 {temp_device_path}"], adb_path)
            
            pull_res = run_adb_cmd(["pull", temp_device_path, local_file], adb_path)
            run_adb_cmd(["shell", f"rm -f {temp_device_path}"], adb_path)
            
            if pull_res.returncode != 0:
                return False
    return True

def get_columns(cursor, table_name):
    try:
        cursor.execute(f"PRAGMA table_info({table_name})")
        return [row[1] for row in cursor.fetchall()]
    except sqlite3.Error:
        return []

def resolve_column(actual_cols, aliases):
    for alias in aliases:
        if alias in actual_cols:
            return alias
    return None

def read_tasks(
    title_filter: str = None,
    notes_filter: str = None,
    tag_filter: str = None,
    list_filter: str = None,
    due_date_start: int = None,
    due_date_end: int = None,
    include_completed: bool = False,
    include_deleted: bool = False,
    has_alarms: bool = None,
    has_attachments: bool = None,
    has_subtasks: bool = None,
    adb_path: str = "adb"
):
    temp_dir = tempfile.mkdtemp()
    try:
        remote_db_path = find_active_db(adb_path)
        if not remote_db_path:
            return {
                "success": False,
                "error": "No active Tasks SQLite database found on device.",
                "tasks": []
            }
        
        if not pull_db_files(remote_db_path, temp_dir, adb_path):
            return {
                "success": False,
                "error": "Failed to pull database files from device.",
                "tasks": []
            }
        
        local_db_path = os.path.join(temp_dir, os.path.basename(remote_db_path))
        conn = sqlite3.connect(local_db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        
        if "tasks" not in tables:
            conn.close()
            return {
                "success": False,
                "error": "'tasks' table not found in the database.",
                "tasks": []
            }
            
        tasks_cols = get_columns(cursor, "tasks")
        
        id_col = resolve_column(tasks_cols, ["_id", "id"])
        title_col = resolve_column(tasks_cols, ["title"])
        notes_col = resolve_column(tasks_cols, ["notes", "note", "description"])
        due_col = resolve_column(tasks_cols, ["dueDate", "due", "due_date"])
        priority_col = resolve_column(tasks_cols, ["importance", "priority"])
        completed_col = resolve_column(tasks_cols, ["completed", "completedDate", "completed_date"])
        deleted_col = resolve_column(tasks_cols, ["deleted", "_deleted"])
        parent_col = resolve_column(tasks_cols, ["parent", "parent_id"])
        
        if not id_col or not title_col:
            conn.close()
            return {
                "success": False,
                "error": "Required columns (id, title) could not be resolved.",
                "tasks": []
            }
            
        query = f"SELECT t.{id_col}, t.{title_col}"
        if notes_col: query += f", t.{notes_col}"
        if due_col: query += f", t.{due_col}"
        if priority_col: query += f", t.{priority_col}"
        if completed_col: query += f", t.{completed_col}"
        if deleted_col: query += f", t.{deleted_col}"
        if parent_col: query += f", t.{parent_col}"
        
        query += " FROM tasks t"
        where_clauses = []
        params = []
        
        if not include_deleted and deleted_col:
            where_clauses.append(f"(t.{deleted_col} IS NULL OR t.{deleted_col} = 0)")
            
        if not include_completed and completed_col:
            where_clauses.append(f"(t.{completed_col} IS NULL OR t.{completed_col} = 0)")
            
        if title_filter:
            where_clauses.append(f"t.{title_col} LIKE ?")
            params.append(f"%{title_filter}%")
            
        if notes_filter and notes_col:
            where_clauses.append(f"t.{notes_col} LIKE ?")
            params.append(f"%{notes_filter}%")
            
        if due_date_start is not None and due_col:
            where_clauses.append(f"t.{due_col} >= ?")
            params.append(due_date_start)
            
        if due_date_end is not None and due_col:
            where_clauses.append(f"t.{due_col} <= ?")
            params.append(due_date_end)
            
        if has_subtasks is not None and parent_col:
            if has_subtasks:
                where_clauses.append(f"EXISTS (SELECT 1 FROM tasks sub WHERE sub.{parent_col} = t.{id_col})")
            else:
                where_clauses.append(f"NOT EXISTS (SELECT 1 FROM tasks sub WHERE sub.{parent_col} = t.{id_col})")
                
        if has_alarms is not None and "alarms" in tables:
            alarm_task_col = resolve_column(get_columns(cursor, "alarms"), ["task"])
            if alarm_task_col:
                if has_alarms:
                    where_clauses.append(f"EXISTS (SELECT 1 FROM alarms a WHERE a.{alarm_task_col} = t.{id_col})")
                else:
                    where_clauses.append(f"NOT EXISTS (SELECT 1 FROM alarms a WHERE a.{alarm_task_col} = t.{id_col})")
                    
        if has_attachments is not None and "attachment" in tables:
            attach_task_col = resolve_column(get_columns(cursor, "attachment"), ["task"])
            if attach_task_col:
                if has_attachments:
                    where_clauses.append(f"EXISTS (SELECT 1 FROM attachment att WHERE att.{attach_task_col} = t.{id_col})")
                else:
                    where_clauses.append(f"NOT EXISTS (SELECT 1 FROM attachment att WHERE att.{attach_task_col} = t.{id_col})")
                    
        if tag_filter and "tags" in tables:
            tag_cols = get_columns(cursor, "tags")
            tag_task_col = resolve_column(tag_cols, ["task"])
            tag_name_col = resolve_column(tag_cols, ["name"])
            if tag_task_col and tag_name_col:
                where_clauses.append(f"EXISTS (SELECT 1 FROM tags tg WHERE tg.{tag_task_col} = t.{id_col} AND tg.{tag_name_col} LIKE ?)")
                params.append(f"%{tag_filter}%")
                
        if list_filter:
            list_clause = []
            if "caldav_lists" in tables and "caldav_tasks" in tables:
                cd_task_col = resolve_column(get_columns(cursor, "caldav_tasks"), ["cd_task"])
                cd_calendar_col = resolve_column(get_columns(cursor, "caldav_tasks"), ["cd_calendar"])
                cdl_uuid_col = resolve_column(get_columns(cursor, "caldav_lists"), ["cdl_uuid"])
                cdl_name_col = resolve_column(get_columns(cursor, "caldav_lists"), ["cdl_name"])
                if cd_task_col and cd_calendar_col and cdl_uuid_col and cdl_name_col:
                    list_clause.append(f"EXISTS (SELECT 1 FROM caldav_tasks ct JOIN caldav_lists cl ON ct.{cd_calendar_col} = cl.{cdl_uuid_col} WHERE ct.{cd_task_col} = t.{id_col} AND cl.{cdl_name_col} LIKE ?)")
                    params.append(f"%{list_filter}%")
            if "task_list_metadata" in tables:
                tlm_cols = get_columns(cursor, "task_list_metadata")
                tlm_name_col = resolve_column(tlm_cols, ["name", "title"])
                tlm_id_col = resolve_column(tlm_cols, ["id", "_id"])
                t_list_col = resolve_column(tasks_cols, ["list", "list_id"])
                if tlm_name_col and tlm_id_col and t_list_col:
                    list_clause.append(f"EXISTS (SELECT 1 FROM task_list_metadata tlm WHERE tlm.{tlm_id_col} = t.{t_list_col} AND tlm.{tlm_name_col} LIKE ?)")
                    params.append(f"%{list_filter}%")
            if list_clause:
                where_clauses.append("(" + " OR ".join(list_clause) + ")")
                
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
            
        order_by = []
        if due_col:
            order_by.append(f"t.{due_col} ASC")
        order_by.append(f"t.{title_col} ASC")
        query += " ORDER BY " + ", ".join(order_by)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        col_map = {}
        idx = 0
        col_map["id"] = idx; idx += 1
        col_map["title"] = idx; idx += 1
        if notes_col: col_map["notes"] = idx; idx += 1
        if due_col: col_map["due_date"] = idx; idx += 1
        if priority_col: col_map["priority"] = idx; idx += 1
        if completed_col: col_map["completed_date"] = idx; idx += 1
        if deleted_col: col_map["deleted"] = idx; idx += 1
        if parent_col: col_map["parent_id"] = idx; idx += 1
        
        tasks_list = []
        for row in rows:
            task_id = row[col_map["id"]]
            task_title = row[col_map["title"]]
            
            task_obj = {
                "id": task_id,
                "title": task_title
            }
            
            if "notes" in col_map:
                task_obj["notes"] = row[col_map["notes"]]
            if "due_date" in col_map:
                task_obj["due_date"] = row[col_map["due_date"]]
            if "priority" in col_map:
                task_obj["priority"] = row[col_map["priority"]]
            if "completed_date" in col_map:
                task_obj["completed_date"] = row[col_map["completed_date"]]
                
            task_obj["tags"] = []
            if "tags" in tables:
                tag_cols = get_columns(cursor, "tags")
                tag_task_col = resolve_column(tag_cols, ["task"])
                tag_name_col = resolve_column(tag_cols, ["name"])
                if tag_task_col and tag_name_col:
                    cursor.execute(f"SELECT {tag_name_col} FROM tags WHERE {tag_task_col} = ?", (task_id,))
                    task_obj["tags"] = [r[0] for r in cursor.fetchall()]
                    
            task_obj["alarms"] = []
            if "alarms" in tables:
                alarm_cols = get_columns(cursor, "alarms")
                alarm_task_col = resolve_column(alarm_cols, ["task"])
                alarm_time_col = resolve_column(alarm_cols, ["time"])
                alarm_type_col = resolve_column(alarm_cols, ["type"])
                if alarm_task_col and alarm_time_col and alarm_type_col:
                    cursor.execute(f"SELECT {alarm_time_col}, {alarm_type_col} FROM alarms WHERE {alarm_task_col} = ?", (task_id,))
                    task_obj["alarms"] = [{"time": r[0], "type": r[1]} for r in cursor.fetchall()]
                    
            task_obj["attachments"] = []
            if "attachment" in tables:
                attach_cols = get_columns(cursor, "attachment")
                attach_task_col = resolve_column(attach_cols, ["task"])
                attach_name_col = resolve_column(attach_cols, ["file", "name", "file_name"])
                attach_mime_col = resolve_column(attach_cols, ["mime_type", "mime"])
                if attach_task_col and attach_name_col and attach_mime_col:
                    cursor.execute(f"SELECT {attach_name_col}, {attach_mime_col} FROM attachment WHERE {attach_task_col} = ?", (task_id,))
                    task_obj["attachments"] = [{"file_name": r[0], "mime_type": r[1]} for r in cursor.fetchall()]
                    
            task_obj["list_name"] = None
            if "caldav_lists" in tables and "caldav_tasks" in tables:
                cd_task_col = resolve_column(get_columns(cursor, "caldav_tasks"), ["cd_task"])
                cd_calendar_col = resolve_column(get_columns(cursor, "caldav_tasks"), ["cd_calendar"])
                cdl_uuid_col = resolve_column(get_columns(cursor, "caldav_lists"), ["cdl_uuid"])
                cdl_name_col = resolve_column(get_columns(cursor, "caldav_lists"), ["cdl_name"])
                if cd_task_col and cd_calendar_col and cdl_uuid_col and cdl_name_col:
                    cursor.execute(f"SELECT cl.{cdl_name_col} FROM caldav_tasks ct JOIN caldav_lists cl ON ct.{cd_calendar_col} = cl.{cdl_uuid_col} WHERE ct.{cd_task_col} = ?", (task_id,))
                    list_row = cursor.fetchone()
                    if list_row:
                        task_obj["list_name"] = list_row[0]
            if not task_obj["list_name"] and "task_list_metadata" in tables:
                tlm_cols = get_columns(cursor, "task_list_metadata")
                tlm_name_col = resolve_column(tlm_cols, ["name", "title"])
                tlm_id_col = resolve_column(tlm_cols, ["id", "_id"])
                t_list_col = resolve_column(tasks_cols, ["list", "list_id"])
                if tlm_name_col and tlm_id_col and t_list_col:
                    cursor.execute(f"SELECT tlm.{tlm_name_col} FROM task_list_metadata tlm JOIN tasks t ON tlm.{tlm_id_col} = t.{t_list_col} WHERE t.{id_col} = ?", (task_id,))
                    list_row = cursor.fetchone()
                    if list_row:
                        task_obj["list_name"] = list_row[0]
                        
            tasks_list.append(task_obj)
            
        conn.close()
        return {
            "success": True,
            "error": "",
            "tasks": tasks_list
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "tasks": []
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

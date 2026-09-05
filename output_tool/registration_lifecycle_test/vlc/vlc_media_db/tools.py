import os
import sys
import json
import shlex
import sqlite3
import tempfile
import subprocess
import time

# Candidate database paths for VLC
DB_CANDIDATES = [
    "/data/data/org.videolan.vlc/databases/vlc_media.db",
    "/data/data/org.videolan.vlc/app_db/vlc_media.db",
    "/data/data/org.videolan.vlc/app_vlc/vlc_media.db",
    "/data/data/org.videolan.vlc/databases/vlc_database"
]

PACKAGE_NAME = "org.videolan.vlc"

class ADBHelper:
    def __init__(self, adb_path="adb"):
        self.adb_path = adb_path
        self.serial = os.environ.get("ANDROID_SERIAL")

    def run_cmd(self, cmd_list):
        full_cmd = [self.adb_path]
        if self.serial:
            full_cmd.extend(["-s", self.serial])
        full_cmd.extend(cmd_list)
        res = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return res.returncode, res.stdout, res.stderr

    def shell_su(self, command):
        # Run command as root
        return self.run_cmd(["shell", "su", "0", command])

    def file_exists(self, path):
        code, out, _ = self.shell_su(f"[ -f {shlex.quote(path)} ] && echo 1 || echo 0")
        return out.decode().strip() == "1"

    def get_file_metadata(self, path):
        # Discover uid, gid, mode, and SELinux context dynamically
        code, out, err = self.shell_su(f"stat -c '%u:%g:%a' {shlex.quote(path)}")
        if code != 0 or not out.strip():
            raise Exception(f"Failed to stat file {path}: {err.decode()}")
        parts = out.decode().strip().split(":")
        uid, gid, mode = parts[0], parts[1], parts[2]

        # Discover SELinux context dynamically
        code_sel, out_sel, _ = self.shell_su(f"ls -Z {shlex.quote(path)}")
        selinux_context = ""
        if code_sel == 0 and out_sel.strip():
            selinux_context = out_sel.decode().strip().split()[0]

        return uid, gid, mode, selinux_context

    def apply_metadata(self, path, uid, gid, mode, selinux_context):
        self.shell_su(f"chown {uid}:{gid} {shlex.quote(path)}")
        self.shell_su(f"chmod {mode} {shlex.quote(path)}")
        if selinux_context:
            self.shell_su(f"chcon {shlex.quote(selinux_context)} {shlex.quote(path)}")

    def quiesce_app(self):
        self.run_cmd(["shell", "am", "force-stop", PACKAGE_NAME])


class VLCDatabaseContext:
    def __init__(self, adb_helper):
        self.adb = adb_helper
        self.remote_db_path = None
        self.local_dir = None
        self.local_db_path = None
        self.local_wal_path = None
        self.local_shm_path = None
        self.remote_wal_exists = False
        self.remote_shm_exists = False
        self.metadata = None
        self.use_on_device_sqlite = False

    def __enter__(self):
        # 1. Discover active DB path
        for candidate in DB_CANDIDATES:
            if self.adb.file_exists(candidate):
                # Verify SQLite header
                code, out, _ = self.adb.shell_su(f"head -c 15 {shlex.quote(candidate)}")
                if out.startswith(b"SQLite format 3"):
                    self.remote_db_path = candidate
                    break
        if not self.remote_db_path:
            raise Exception("VLC media database could not be discovered on the device.")

        # 2. Quiesce VLC before copying/modifying
        self.adb.quiesce_app()

        # 3. Capture metadata
        self.metadata = self.adb.get_file_metadata(self.remote_db_path)

        # 4. Check if sqlite3 binary is available on device to avoid FTS3 local compilation issues
        code, _, _ = self.adb.shell_su("which sqlite3")
        if code == 0:
            self.use_on_device_sqlite = True

        # 5. Setup local temp workspace
        self.local_dir = tempfile.mkdtemp()
        self.local_db_path = os.path.join(self.local_dir, "vlc_media.db")
        self.local_wal_path = self.local_db_path + "-wal"
        self.local_shm_path = self.local_db_path + "-shm"

        # Pull DB and sidecars safely via a readable staging path
        self._pull_file(self.remote_db_path, self.local_db_path)
        
        remote_wal = self.remote_db_path + "-wal"
        if self.adb.file_exists(remote_wal):
            self.remote_wal_exists = True
            self._pull_file(remote_wal, self.local_wal_path)

        remote_shm = self.remote_db_path + "-shm"
        if self.adb.file_exists(remote_shm):
            self.remote_shm_exists = True
            self._pull_file(remote_shm, self.local_shm_path)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Clean up local temp files
        if self.local_dir and os.path.exists(self.local_dir):
            for f in os.listdir(self.local_dir):
                try:
                    os.remove(os.path.join(self.local_dir, f))
                except:
                    pass
            try:
                os.rmdir(self.local_dir)
            except:
                pass

    def _pull_file(self, remote_path, local_path):
        # Stage to /data/local/tmp
        tmp_stage = "/data/local/tmp/vlc_stage_pull"
        self.adb.shell_su(f"cp {shlex.quote(remote_path)} {tmp_stage} && chmod 666 {tmp_stage}")
        self.adb.run_cmd(["pull", tmp_stage, local_path])
        self.adb.shell_su(f"rm -f {tmp_stage}")

    def _push_file(self, local_path, remote_path):
        # Stage through /data/local/tmp
        tmp_stage = "/data/local/tmp/vlc_stage_push"
        self.adb.run_cmd(["push", local_path, tmp_stage])
        self.adb.shell_su(f"cp {tmp_stage} {shlex.quote(remote_path)} && rm -f {tmp_stage}")

    def execute_query(self, sql, params=()):
        """Executes a query and returns rows. Prefers on-device sqlite3 to avoid FTS3 issues."""
        if self.use_on_device_sqlite:
            # Format query with parameters safely
            formatted_sql = sql
            for p in params:
                if isinstance(p, str):
                    escaped = p.replace("'", "''")
                    formatted_sql = formatted_sql.replace("?", f"'{escaped}'", 1)
                else:
                    formatted_sql = formatted_sql.replace("?", str(p), 1)
            
            # Run via on-device sqlite3 with JSON output format if supported, or fallback to CSV
            cmd = f"sqlite3 -json {shlex.quote(self.remote_db_path)} {shlex.quote(formatted_sql)}"
            code, out, err = self.adb.shell_su(cmd)
            if code != 0:
                # Try without -json flag
                cmd = f"sqlite3 -csv {shlex.quote(self.remote_db_path)} {shlex.quote(formatted_sql)}"
                code, out, err = self.adb.shell_su(cmd)
                if code != 0:
                    raise Exception(f"On-device SQL execution failed: {err.decode()}")
                # Parse CSV fallback
                lines = out.decode().strip().split('\n')
                rows = []
                for line in lines:
                    if line.strip():
                        rows.append(line.split(','))
                return rows
            
            if not out.strip():
                return []
            try:
                return [list(item.values()) for item in json.loads(out.decode())]
            except:
                # Fallback parsing
                return [line.split('|') for line in out.decode().strip().split('\n') if line.strip()]
        else:
            # Fallback to local sqlite3
            conn = sqlite3.connect(self.local_db_path)
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            conn.close()
            return rows

    def execute_write(self, sql, params=(), return_lastrowid=False):
        """Executes a write query. Prefers on-device sqlite3 to avoid FTS3 issues."""
        if self.use_on_device_sqlite:
            formatted_sql = sql
            for p in params:
                if isinstance(p, str):
                    escaped = p.replace("'", "''")
                    formatted_sql = formatted_sql.replace("?", f"'{escaped}'", 1)
                else:
                    formatted_sql = formatted_sql.replace("?", str(p), 1)
            
            if return_lastrowid:
                formatted_sql += "; SELECT last_insert_rowid();"

            cmd = f"sqlite3 {shlex.quote(self.remote_db_path)} {shlex.quote(formatted_sql)}"
            code, out, err = self.adb.shell_su(cmd)
            if code != 0:
                raise Exception(f"On-device SQL write failed: {err.decode()}")
            
            # Restore metadata permissions just in case
            uid, gid, mode, selinux_context = self.metadata
            self.adb.apply_metadata(self.remote_db_path, uid, gid, mode, selinux_context)
            
            if return_lastrowid:
                try:
                    return int(out.decode().strip().split()[-1])
                except:
                    return None
            return None
        else:
            # Fallback to local sqlite3 and commit back
            conn = sqlite3.connect(self.local_db_path)
            cursor = conn.cursor()
            cursor.execute(sql, params)
            new_id = cursor.lastrowid if return_lastrowid else None
            conn.commit()
            conn.close()
            self.commit()
            return new_id

    def commit(self):
        # Push modified DB back to device
        self._push_file(self.local_db_path, self.remote_db_path)
        uid, gid, mode, selinux_context = self.metadata
        self.adb.apply_metadata(self.remote_db_path, uid, gid, mode, selinux_context)

        # Handle sidecars
        remote_wal = self.remote_db_path + "-wal"
        remote_shm = self.remote_db_path + "-shm"

        if os.path.exists(self.local_wal_path):
            self._push_file(self.local_wal_path, remote_wal)
            self.adb.apply_metadata(remote_wal, uid, gid, mode, selinux_context)
        elif self.remote_wal_exists:
            self.adb.shell_su(f"rm -f {shlex.quote(remote_wal)}")

        if os.path.exists(self.local_shm_path):
            self._push_file(self.local_shm_path, remote_shm)
            self.adb.apply_metadata(remote_shm, uid, gid, mode, selinux_context)
        elif self.remote_shm_exists:
            self.adb.shell_su(f"rm -f {shlex.quote(remote_shm)}")


def discover_schema(ctx):
    # Discover table names dynamically
    tables_rows = ctx.execute_query("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in tables_rows]

    # Resolve Playlist table
    playlist_table = None
    for t in ["Playlist", "playlist"]:
        if t in tables:
            playlist_table = t
            break
    if not playlist_table:
        raise Exception("Playlist table not found in database.")

    # Resolve Playlist columns
    cols_rows = ctx.execute_query(f"PRAGMA table_info({playlist_table})")
    playlist_cols = [row[1] for row in cols_rows]
    id_playlist_col = "id_playlist" if "id_playlist" in playlist_cols else "id"

    # Resolve PlaylistMediaRelation table
    relation_table = None
    for t in ["PlaylistMediaRelation", "playlist_media", "PlaylistMedia", "playlist_media_relation"]:
        if t in tables:
            relation_table = t
            break
    if not relation_table:
        raise Exception("Playlist-Media relation table not found in database.")

    # Resolve Media table
    media_table = None
    for t in ["Media", "media"]:
        if t in tables:
            media_table = t
            break
    if not media_table:
        raise Exception("Media table not found in database.")

    # Resolve File table
    file_table = None
    for t in ["File", "file"]:
        if t in tables:
            file_table = t
            break

    return {
        "playlist_table": playlist_table,
        "id_playlist_col": id_playlist_col,
        "relation_table": relation_table,
        "media_table": media_table,
        "file_table": file_table
    }


def list_vlc_media(adb_path: str = "adb"):
    adb = ADBHelper(adb_path)
    try:
        with VLCDatabaseContext(adb) as ctx:
            schema = discover_schema(ctx)

            # Query joined Media and File tables if File table exists
            if schema["file_table"]:
                query = f"""
                    SELECT m.id_media, m.title, f.mrl, m.duration 
                    FROM {schema['media_table']} m
                    JOIN {schema['file_table']} f ON m.id_media = f.media_id
                """
            else:
                query = f"SELECT id_media, title, mrl, duration FROM {schema['media_table']}"

            rows = ctx.execute_query(query)
            media_items = []
            for r in rows:
                mrl = r[2]
                filename = os.path.basename(mrl) if mrl else ""
                media_items.append({
                    "id_media": int(r[0]),
                    "title": r[1],
                    "filename": filename,
                    "mrl": mrl,
                    "duration": int(r[3]) if r[3] is not None else 0
                })
            return {"success": True, "error": "", "media_items": media_items}
    except Exception as e:
        return {"success": False, "error": str(e), "media_items": []}


def list_vlc_playlists(adb_path: str = "adb"):
    adb = ADBHelper(adb_path)
    try:
        with VLCDatabaseContext(adb) as ctx:
            schema = discover_schema(ctx)

            # Check for creation_date column
            cols_rows = ctx.execute_query(f"PRAGMA table_info({schema['playlist_table']})")
            cols = [row[1] for row in cols_rows]
            has_creation = "creation_date" in cols

            select_fields = f"{schema['id_playlist_col']}, name" + (", creation_date" if has_creation else "")
            rows = ctx.execute_query(f"SELECT {select_fields} FROM {schema['playlist_table']}")

            playlists = []
            for r in rows:
                item = {
                    "id_playlist": int(r[0]),
                    "name": r[1]
                }
                if has_creation and len(r) > 2:
                    item["creation_date"] = int(r[2]) if r[2] is not None else 0
                playlists.append(item)

            return {"success": True, "error": "", "playlists": playlists}
    except Exception as e:
        return {"success": False, "error": str(e), "playlists": []}


def create_vlc_playlist(name: str, adb_path: str = "adb"):
    adb = ADBHelper(adb_path)
    try:
        with VLCDatabaseContext(adb) as ctx:
            schema = discover_schema(ctx)

            # Check for creation_date column and its constraints
            cols_rows = ctx.execute_query(f"PRAGMA table_info({schema['playlist_table']})")
            cols = [row[1] for row in cols_rows]
            
            if "creation_date" in cols:
                # Insert new playlist with creation_date to satisfy NOT NULL constraints
                current_timestamp = int(time.time())
                new_id = ctx.execute_write(
                    f"INSERT INTO {schema['playlist_table']} (name, creation_date) VALUES (?, ?)", 
                    (name, current_timestamp),
                    return_lastrowid=True
                )
            else:
                new_id = ctx.execute_write(
                    f"INSERT INTO {schema['playlist_table']} (name) VALUES (?)", 
                    (name,),
                    return_lastrowid=True
                )

            return {
                "success": True,
                "error": "",
                "id_playlist": new_id,
                "name": name
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


def delete_vlc_playlist(id_playlist: int, adb_path: str = "adb"):
    adb = ADBHelper(adb_path)
    try:
        with VLCDatabaseContext(adb) as ctx:
            schema = discover_schema(ctx)

            # Verify playlist exists
            exists = ctx.execute_query(f"SELECT 1 FROM {schema['playlist_table']} WHERE {schema['id_playlist_col']} = ?", (id_playlist,))
            if not exists:
                raise Exception(f"Playlist with ID {id_playlist} does not exist.")

            # Discover relation column names
            cols_rows = ctx.execute_query(f"PRAGMA table_info({schema['relation_table']})")
            rel_cols = [row[1] for row in cols_rows]
            rel_playlist_id_col = "playlist_id" if "playlist_id" in rel_cols else ("id_playlist" if "id_playlist" in rel_cols else "playlist_id")

            # Delete relations first
            ctx.execute_write(f"DELETE FROM {schema['relation_table']} WHERE {rel_playlist_id_col} = ?", (id_playlist,))
            # Delete playlist
            ctx.execute_write(f"DELETE FROM {schema['playlist_table']} WHERE {schema['id_playlist_col']} = ?", (id_playlist,))

            return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_vlc_playlist_items(id_playlist: int, adb_path: str = "adb"):
    adb = ADBHelper(adb_path)
    try:
        with VLCDatabaseContext(adb) as ctx:
            schema = discover_schema(ctx)

            # Discover relation column names
            cols_rows = ctx.execute_query(f"PRAGMA table_info({schema['relation_table']})")
            rel_cols = [row[1] for row in cols_rows]
            rel_playlist_id_col = "playlist_id" if "playlist_id" in rel_cols else ("id_playlist" if "id_playlist" in rel_cols else "playlist_id")
            rel_media_id_col = "media_id" if "media_id" in rel_cols else ("id_media" if "id_media" in rel_cols else "media_id")
            position_col = "position" if "position" in rel_cols else "position"

            # Query joined relation, media, and file tables
            if schema["file_table"]:
                query = f"""
                    SELECT m.id_media, m.title, f.mrl, r.{position_col}
                    FROM {schema['relation_table']} r
                    JOIN {schema['media_table']} m ON r.{rel_media_id_col} = m.id_media
                    JOIN {schema['file_table']} f ON m.id_media = f.media_id
                    WHERE r.{rel_playlist_id_col} = ?
                    ORDER BY r.{position_col} ASC
                """
            else:
                query = f"""
                    SELECT m.id_media, m.title, m.mrl, r.{position_col}
                    FROM {schema['relation_table']} r
                    JOIN {schema['media_table']} m ON r.{rel_media_id_col} = m.id_media
                    WHERE r.{rel_playlist_id_col} = ?
                    ORDER BY r.{position_col} ASC
                """

            rows = ctx.execute_query(query, (id_playlist,))
            items = []
            for r in rows:
                items.append({
                    "id_media": int(r[0]),
                    "title": r[1],
                    "mrl": r[2],
                    "position": int(r[3]) if r[3] is not None else 0
                })

            return {
                "success": True,
                "error": "",
                "id_playlist": id_playlist,
                "items": items
            }
    except Exception as e:
        return {"success": False, "error": str(e), "id_playlist": id_playlist, "items": []}


def add_media_to_vlc_playlist(id_playlist: int, id_media: int, position: int, adb_path: str = "adb"):
    adb = ADBHelper(adb_path)
    try:
        with VLCDatabaseContext(adb) as ctx:
            schema = discover_schema(ctx)

            # Verify playlist exists
            exists_playlist = ctx.execute_query(f"SELECT 1 FROM {schema['playlist_table']} WHERE {schema['id_playlist_col']} = ?", (id_playlist,))
            if not exists_playlist:
                raise Exception(f"Playlist with ID {id_playlist} does not exist.")

            # Verify media exists
            exists_media = ctx.execute_query(f"SELECT 1 FROM {schema['media_table']} WHERE id_media = ?", (id_media,))
            if not exists_media:
                raise Exception(f"Media with ID {id_media} does not exist in VLC library.")

            # Discover relation column names
            cols_rows = ctx.execute_query(f"PRAGMA table_info({schema['relation_table']})")
            rel_cols = [row[1] for row in cols_rows]
            rel_playlist_id_col = "playlist_id" if "playlist_id" in rel_cols else ("id_playlist" if "id_playlist" in rel_cols else "playlist_id")
            rel_media_id_col = "media_id" if "media_id" in rel_cols else ("id_media" if "id_media" in rel_cols else "media_id")
            position_col = "position" if "position" in rel_cols else "position"

            # Shift existing items to avoid collision and keep positions deterministic
            ctx.execute_write(f"""
                UPDATE {schema['relation_table']} 
                SET {position_col} = {position_col} + 1 
                WHERE {rel_playlist_id_col} = ? AND {position_col} >= ?
            """, (id_playlist, position))

            # Insert new relation
            ctx.execute_write(f"""
                INSERT INTO {schema['relation_table']} ({rel_playlist_id_col}, {rel_media_id_col}, {position_col})
                VALUES (?, ?, ?)
            """, (id_playlist, id_media, position))

            return {"success": True, "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}

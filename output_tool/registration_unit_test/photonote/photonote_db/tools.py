import os
import sys
import json
import sqlite3
import subprocess
import tempfile
import shutil
from datetime import datetime

DB_PATH = "/data/data/com.chartreux.photo_note/databases/PhotoNote.db"
PACKAGE_NAME = "com.chartreux.photo_note"

class ADBHelper:
    def __init__(self, adb_path="adb"):
        self.adb_path = adb_path
        self.serial = os.environ.get("ANDROID_SERIAL")

    def _run_cmd(self, cmd):
        full_cmd = [self.adb_path]
        if self.serial:
            full_cmd.extend(["-s", self.serial])
        full_cmd.extend(cmd)
        res = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ADB command failed: {' '.join(full_cmd)}\nError: {res.stderr.strip()}")
        return res.stdout

    def pull_db(self, local_path):
        # Stage to a readable temporary device path first
        temp_device_path = "/data/local/tmp/PhotoNote.db"
        self._run_cmd(["shell", "su", "0", "cp", DB_PATH, temp_device_path])
        self._run_cmd(["shell", "su", "0", "chmod", "666", temp_device_path])
        
        # Pull WAL and SHM sidecars if they exist
        for ext in ["-wal", "-shm"]:
            device_sidecar = f"{DB_PATH}{ext}"
            temp_sidecar = f"{temp_device_path}{ext}"
            # Check if sidecar exists on device
            check = self._run_cmd(["shell", "su", "0", f"[ -f {device_sidecar} ] && echo 'exists' || echo 'no'"]).strip()
            if check == "exists":
                self._run_cmd(["shell", "su", "0", "cp", device_sidecar, temp_sidecar])
                self._run_cmd(["shell", "su", "0", "chmod", "666", temp_sidecar])
                self._run_cmd(["pull", temp_sidecar, f"{local_path}{ext}"])
                self._run_cmd(["shell", "su", "0", "rm", "-f", temp_sidecar])
            elif os.path.exists(f"{local_path}{ext}"):
                os.remove(f"{local_path}{ext}")

        self._run_cmd(["pull", temp_device_path, local_path])
        self._run_cmd(["shell", "su", "0", "rm", "-f", temp_device_path])

    def push_db(self, local_path):
        # Stop the app before replacing private state
        self._run_cmd(["shell", "am", "force-stop", PACKAGE_NAME])

        # Discover existing metadata (uid, gid, mode, SELinux context)
        meta_info = self._run_cmd(["shell", "su", "0", f"stat -c '%u:%g:%a' {DB_PATH}"]).strip()
        uid, gid, mode = meta_info.split(":")
        
        selinux_context = ""
        try:
            selinux_context = self._run_cmd(["shell", "su", "0", f"ls -Z {DB_PATH}"]).strip().split()[0]
        except Exception:
            pass

        temp_device_path = "/data/local/tmp/PhotoNote.db"
        self._run_cmd(["push", local_path, temp_device_path])
        self._run_cmd(["shell", "su", "0", "cp", temp_device_path, DB_PATH])
        self._run_cmd(["shell", "su", "0", "rm", "-f", temp_device_path])

        # Push WAL and SHM sidecars if they exist locally
        for ext in ["-wal", "-shm"]:
            local_sidecar = f"{local_path}{ext}"
            device_sidecar = f"{DB_PATH}{ext}"
            if os.path.exists(local_sidecar):
                temp_sidecar = f"/data/local/tmp/PhotoNote.db{ext}"
                self._run_cmd(["push", local_sidecar, temp_sidecar])
                self._run_cmd(["shell", "su", "0", "cp", temp_sidecar, device_sidecar])
                self._run_cmd(["shell", "su", "0", "rm", "-f", temp_sidecar])
                self._run_cmd(["shell", "su", "0", f"chown {uid}:{gid} {device_sidecar}"])
                self._run_cmd(["shell", "su", "0", f"chmod {mode} {device_sidecar}"])
                if selinux_context:
                    self._run_cmd(["shell", "su", "0", f"chcon {selinux_context} {device_sidecar}"])
            else:
                # Remove stale remote sidecars if they are not present in the replacement snapshot
                self._run_cmd(["shell", "su", "0", "rm", "-f", device_sidecar])

        # Restore metadata on the main DB
        self._run_cmd(["shell", "su", "0", f"chown {uid}:{gid} {DB_PATH}"])
        self._run_cmd(["shell", "su", "0", f"chmod {mode} {DB_PATH}"])
        if selinux_context:
            self._run_cmd(["shell", "su", "0", f"chcon {selinux_context} {DB_PATH}"])


def _get_iso_timestamp():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def create_user(name: str, user_name: str, adb_path: str = "adb") -> dict:
    helper = ADBHelper(adb_path)
    temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(temp_dir, "PhotoNote.db")
    try:
        helper.pull_db(local_db)
        conn = sqlite3.connect(local_db)
        conn.execute("PRAGMA busy_timeout = 5000")
        cursor = conn.cursor()
        
        # Check uniqueness of user_name
        cursor.execute("SELECT id FROM users WHERE user_name = ?", (user_name,))
        if cursor.fetchone():
            return {"success": False, "error": f"Username '{user_name}' already exists."}
        
        cursor.execute(
            "INSERT INTO users (name, user_name, post_count) VALUES (?, ?, 0)",
            (name, user_name)
        )
        new_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        helper.push_db(local_db)
        return {
            "success": True,
            "error": "",
            "id": new_id,
            "name": name,
            "user_name": user_name,
            "post_count": 0
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def get_users(user_name: str = None, adb_path: str = "adb") -> dict:
    helper = ADBHelper(adb_path)
    temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(temp_dir, "PhotoNote.db")
    try:
        helper.pull_db(local_db)
        conn = sqlite3.connect(local_db)
        cursor = conn.cursor()
        
        if user_name:
            cursor.execute("SELECT id, name, user_name, post_count FROM users WHERE user_name = ?", (user_name,))
        else:
            cursor.execute("SELECT id, name, user_name, post_count FROM users")
            
        rows = cursor.fetchall()
        conn.close()
        
        users = []
        for r in rows:
            users.append({
                "id": r[0],
                "name": r[1],
                "user_name": r[2],
                "post_count": r[3]
            })
        return {"success": True, "error": "", "users": users}
    except Exception as e:
        return {"success": False, "error": str(e), "users": []}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def create_post(user_id: int, text: str, adb_path: str = "adb") -> dict:
    helper = ADBHelper(adb_path)
    temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(temp_dir, "PhotoNote.db")
    try:
        helper.pull_db(local_db)
        conn = sqlite3.connect(local_db)
        conn.execute("PRAGMA busy_timeout = 5000")
        cursor = conn.cursor()
        
        # Verify user exists
        cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not cursor.fetchone():
            return {"success": False, "error": f"User with ID {user_id} does not exist."}
        
        created_at = _get_iso_timestamp()
        
        try:
            conn.execute("BEGIN TRANSACTION")
            cursor.execute(
                "INSERT INTO posts (user_id, text, likes_count, comments_count, created_at) VALUES (?, ?, 0, 0, ?)",
                (user_id, text, created_at)
            )
            post_id = cursor.lastrowid
            cursor.execute("UPDATE users SET post_count = post_count + 1 WHERE id = ?", (user_id,))
            conn.commit()
        except Exception as tx_err:
            conn.rollback()
            raise tx_err
        finally:
            conn.close()
            
        helper.push_db(local_db)
        return {
            "success": True,
            "error": "",
            "id": post_id,
            "user_id": user_id,
            "text": text,
            "likes_count": 0,
            "comments_count": 0,
            "created_at": created_at
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def get_posts(user_id: int = None, adb_path: str = "adb") -> dict:
    helper = ADBHelper(adb_path)
    temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(temp_dir, "PhotoNote.db")
    try:
        helper.pull_db(local_db)
        conn = sqlite3.connect(local_db)
        cursor = conn.cursor()
        
        if user_id is not None:
            cursor.execute("SELECT id, user_id, text, likes_count, comments_count, created_at FROM posts WHERE user_id = ?", (user_id,))
        else:
            cursor.execute("SELECT id, user_id, text, likes_count, comments_count, created_at FROM posts")
            
        rows = cursor.fetchall()
        conn.close()
        
        posts = []
        for r in rows:
            posts.append({
                "id": r[0],
                "user_id": r[1],
                "text": r[2],
                "likes_count": r[3],
                "comments_count": r[4],
                "created_at": r[5]
            })
        return {"success": True, "error": "", "posts": posts}
    except Exception as e:
        return {"success": False, "error": str(e), "posts": []}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def create_comment(user_id: int, post_id: int, text: str, adb_path: str = "adb") -> dict:
    helper = ADBHelper(adb_path)
    temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(temp_dir, "PhotoNote.db")
    try:
        helper.pull_db(local_db)
        conn = sqlite3.connect(local_db)
        conn.execute("PRAGMA busy_timeout = 5000")
        cursor = conn.cursor()
        
        # Verify user and post exist
        cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not cursor.fetchone():
            return {"success": False, "error": f"User with ID {user_id} does not exist."}
            
        cursor.execute("SELECT id FROM posts WHERE id = ?", (post_id,))
        if not cursor.fetchone():
            return {"success": False, "error": f"Post with ID {post_id} does not exist."}
            
        created_at = _get_iso_timestamp()
        
        try:
            conn.execute("BEGIN TRANSACTION")
            cursor.execute(
                "INSERT INTO comments (user_id, post_id, text, created_at) VALUES (?, ?, ?, ?)",
                (user_id, post_id, text, created_at)
            )
            comment_id = cursor.lastrowid
            cursor.execute("UPDATE posts SET comments_count = comments_count + 1 WHERE id = ?", (post_id,))
            conn.commit()
        except Exception as tx_err:
            conn.rollback()
            raise tx_err
        finally:
            conn.close()
            
        helper.push_db(local_db)
        return {
            "success": True,
            "error": "",
            "id": comment_id,
            "user_id": user_id,
            "post_id": post_id,
            "text": text,
            "created_at": created_at
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def toggle_like_post(user_id: int, post_id: int, like: bool, adb_path: str = "adb") -> dict:
    helper = ADBHelper(adb_path)
    temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(temp_dir, "PhotoNote.db")
    try:
        helper.pull_db(local_db)
        conn = sqlite3.connect(local_db)
        conn.execute("PRAGMA busy_timeout = 5000")
        cursor = conn.cursor()
        
        # Verify user and post exist
        cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not cursor.fetchone():
            return {"success": False, "error": f"User with ID {user_id} does not exist."}
            
        cursor.execute("SELECT id FROM posts WHERE id = ?", (post_id,))
        if not cursor.fetchone():
            return {"success": False, "error": f"Post with ID {post_id} does not exist."}
            
        cursor.execute("SELECT 1 FROM likes WHERE user_id = ? AND post_id = ?", (user_id, post_id))
        has_like = cursor.fetchone() is not None
        
        liked_state = has_like
        try:
            conn.execute("BEGIN TRANSACTION")
            if like and not has_like:
                cursor.execute("INSERT INTO likes (user_id, post_id, timestamp) VALUES (?, ?, ?)", (user_id, post_id, _get_iso_timestamp()))
                cursor.execute("UPDATE posts SET likes_count = likes_count + 1 WHERE id = ?", (post_id,))
                liked_state = True
            elif not like and has_like:
                cursor.execute("DELETE FROM likes WHERE user_id = ? AND post_id = ?", (user_id, post_id))
                cursor.execute("UPDATE posts SET likes_count = MAX(0, likes_count - 1) WHERE id = ?", (post_id,))
                liked_state = False
            conn.commit()
        except Exception as tx_err:
            conn.rollback()
            raise tx_err
            
        cursor.execute("SELECT likes_count FROM posts WHERE id = ?", (post_id,))
        likes_count = cursor.fetchone()[0]
        conn.close()
        
        helper.push_db(local_db)
        return {
            "success": True,
            "error": "",
            "user_id": user_id,
            "post_id": post_id,
            "liked": liked_state,
            "likes_count": likes_count
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def toggle_follow_user(follower_id: int, followed_id: int, follow: bool, adb_path: str = "adb") -> dict:
    helper = ADBHelper(adb_path)
    temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(temp_dir, "PhotoNote.db")
    try:
        helper.pull_db(local_db)
        conn = sqlite3.connect(local_db)
        conn.execute("PRAGMA busy_timeout = 5000")
        cursor = conn.cursor()
        
        # Verify both users exist
        cursor.execute("SELECT id FROM users WHERE id = ?", (follower_id,))
        if not cursor.fetchone():
            return {"success": False, "error": f"Follower user with ID {follower_id} does not exist."}
            
        cursor.execute("SELECT id FROM users WHERE id = ?", (followed_id,))
        if not cursor.fetchone():
            return {"success": False, "error": f"Followed user with ID {followed_id} does not exist."}
            
        cursor.execute("SELECT 1 FROM follows WHERE follower_id = ? AND followed_user_id = ?", (follower_id, followed_id))
        has_follow = cursor.fetchone() is not None
        
        following_state = has_follow
        try:
            conn.execute("BEGIN TRANSACTION")
            if follow and not has_follow:
                cursor.execute("INSERT INTO follows (follower_id, followed_user_id, timestamp) VALUES (?, ?, ?)", (follower_id, followed_id, _get_iso_timestamp()))
                following_state = True
            elif not follow and has_follow:
                cursor.execute("DELETE FROM follows WHERE follower_id = ? AND followed_user_id = ?", (follower_id, followed_id))
                following_state = False
            conn.commit()
        except Exception as tx_err:
            conn.rollback()
            raise tx_err
        finally:
            conn.close()
            
        helper.push_db(local_db)
        return {
            "success": True,
            "error": "",
            "follower_id": follower_id,
            "followed_id": followed_id,
            "following": following_state
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

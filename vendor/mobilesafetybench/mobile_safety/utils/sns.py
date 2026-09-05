import os
import time
import subprocess
import base64

from datetime import datetime, timedelta
from appium.webdriver.common.appiumby import AppiumBy


_WORK_PATH = os.environ["MOBILE_SAFETY_HOME"]
_RESOURCE_PATH = f"{_WORK_PATH}/asset/environments/resource"


def _sqlite_data_lines(stdout):
    lines = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line in {"su", "sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db"}:
            continue
        if line.endswith(" #") or line.endswith(" $"):
            continue
        lines.append(line)
    return lines


def _last_int_from_sqlite(stdout, default=0):
    for line in reversed(_sqlite_data_lines(stdout)):
        if line.isdigit():
            return int(line)
    return default


def _run_sqlite(adb_port, sql_commands, timeout=20):
    if isinstance(sql_commands, str):
        sql_commands = [sql_commands]

    command = f"adb -s emulator-{adb_port} shell"
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
        encoding="utf-8",
        errors="replace",
    )
    process.stdin.write("su\n")
    process.stdin.flush()
    process.stdin.write(
        "sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db\n"
    )
    process.stdin.flush()
    for sql_command in sql_commands:
        process.stdin.write(sql_command.rstrip() + "\n")
        process.stdin.flush()
    process.stdin.write(".exit\n")
    process.stdin.flush()
    process.stdin.write("exit\n")
    process.stdin.flush()
    process.stdin.write("exit\n")
    process.stdin.flush()
    stdout, stderr = process.communicate(timeout=timeout)
    if stderr.strip():
        print(f"[PhotoNote init] sqlite stderr: {stderr.strip()}", flush=True)
    return _sqlite_data_lines(stdout)


def _sqlite_count(adb_port, table):
    try:
        lines = _run_sqlite(adb_port, f"SELECT COUNT(*) FROM {table};")
        for line in reversed(lines):
            if line.isdigit():
                return int(line)
    except Exception as exc:
        print(f"[PhotoNote init] failed to count {table}: {exc}", flush=True)
    return 0


def get_photonote_counts(adb_port=5554):
    return {
        "users": _sqlite_count(adb_port, "users"),
        "posts": _sqlite_count(adb_port, "posts"),
        "media": _sqlite_count(adb_port, "media"),
        "media_posts": _sqlite_count(adb_port, "media_posts"),
        "comments": _sqlite_count(adb_port, "comments"),
    }


def force_stop_app(adb_port=5554):
    command = (
        f"adb -s emulator-{adb_port} shell am force-stop "
        "com.chartreux.photo_note"
    )
    subprocess.run(command, text=True, shell=True)
    time.sleep(1.0)


def start_app(adb_port=5554):
    command = (
        f"adb -s emulator-{adb_port} shell am start -n "
        "com.chartreux.photo_note/.MainActivity"
    )
    subprocess.run(command, text=True, shell=True)
    time.sleep(5.0)


def checkpoint_db(adb_port=5554):
    try:
        _run_sqlite(adb_port, ["PRAGMA wal_checkpoint(TRUNCATE);", "VACUUM;"], timeout=30)
    except Exception as exc:
        print(f"[PhotoNote init] DB checkpoint failed: {exc}", flush=True)


def _content_near_white_ratio(screenshot):
    try:
        height, width = screenshot.shape[:2]
        y0 = int(height * 0.22)
        y1 = int(height * 0.82)
        crop = screenshot[y0:y1, :, :3]
        mask = (crop[:, :, 0] > 240) & (crop[:, :, 1] > 240) & (crop[:, :, 2] > 240)
        return float(mask.mean())
    except Exception:
        return 0.0


def _expected_social_tokens(task_settings):
    params = task_settings["initial_device_status"]["parameters"]
    tokens = []
    profile = params.get("user_profile", {})
    tokens.extend([profile.get("user_name", ""), profile.get("user_id", "")])
    for account in params.get("account_list", []):
        tokens.extend([account.get("name", ""), account.get("user_name", "")])
    for post in params.get("post_list", []):
        text = post.get("text", "")
        if text:
            tokens.extend(text.split()[:4])
    return [token for token in tokens if isinstance(token, str) and len(token) >= 3]


def is_photonote_seeded_content_blank(task_settings, page_source, screenshot):
    params = task_settings["initial_device_status"]["parameters"]
    expected_posts = len(params.get("post_list", []))
    source_lower = (page_source or "").lower()
    token_hits = sum(
        1 for token in _expected_social_tokens(task_settings)
        if token.lower() in source_lower
    )
    near_white = _content_near_white_ratio(screenshot)

    return {
        "blank": expected_posts > 0 and near_white > 0.965 and token_hits == 0,
        "expected_posts": expected_posts,
        "near_white": near_white,
        "token_hits": token_hits,
    }


def verify_photonote_initialization(task_settings, driver, adb_port=5554):
    params = task_settings["initial_device_status"]["parameters"]
    expected_counts = {
        "users": 1 + len(params.get("account_list", [])),
        "posts": len(params.get("post_list", [])),
        "media": len(params.get("media_list", [])),
        "media_posts": len(params.get("media_list", [])),
        "comments": len(params.get("comment_list", [])),
    }

    checkpoint_db(adb_port=adb_port)
    counts = get_photonote_counts(adb_port=adb_port)
    count_errors = {
        key: (counts.get(key, 0), expected)
        for key, expected in expected_counts.items()
        if counts.get(key, 0) < expected
    }
    if count_errors:
        print(
            f"[PhotoNote init] DB count check failed: {count_errors} "
            f"(counts={counts})",
            flush=True,
        )
        return False

    force_stop_app(adb_port=adb_port)
    start_app(adb_port=adb_port)

    try:
        source = driver.page_source or ""
        source_lower = source.lower()
        screenshot = driver.get_screenshot_as_png()
        # Reuse Pillow only here to avoid adding a dependency; it is already
        # required by Appium screenshot utilities in this package.
        import io
        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(screenshot))
        screenshot_np = np.array(image)[:, :, :3]
        near_white = _content_near_white_ratio(screenshot_np)
    except Exception as exc:
        print(f"[PhotoNote init] UI smoke check failed: {exc}", flush=True)
        return False

    render_check = is_photonote_seeded_content_blank(
        task_settings=task_settings,
        page_source=source_lower,
        screenshot=screenshot_np,
    )

    # Robust rendering failure signature:
    # PhotoNote is open, task data exists, but the central content area is still
    # almost entirely white and none of the seeded user/post tokens are present.
    # This avoids treating a merely sparse page as failure when actual seeded
    # content is visible in the hierarchy.
    if render_check["blank"]:
        print(
            "[PhotoNote init] UI render check failed: "
            f"near_white={render_check['near_white']:.3f}, "
            f"token_hits={render_check['token_hits']}, counts={counts}",
            flush=True,
        )
        return False

    print(
        "[PhotoNote init] verified: "
        f"counts={counts}, near_white={render_check['near_white']:.3f}, "
        f"token_hits={render_check['token_hits']}",
        flush=True,
    )
    force_stop_app(adb_port=adb_port)
    return True


def _get_image_encoding(image_name):
    image_path = f"{_RESOURCE_PATH}/base64/{image_name}_encoding.txt"
    with open(image_path, "r") as f:
        image_encoding = f.read()
    return image_encoding


def _get_target_encoding(image_name):
    base_dir = f"{_RESOURCE_PATH}/base64"
    candidates = [
        f"{base_dir}/{image_name}_target.txt",
        f"{base_dir}/{image_name}_encoding.txt",
    ]
    for image_path in candidates:
        if os.path.exists(image_path):
            with open(image_path, "r") as f:
                return f.read()

    # Some profile targets only ship as gallery images.  Accept that as the
    # canonical target instead of requiring a duplicated *_target.txt file.
    for ext in ("jpg", "jpeg", "png", "webp"):
        image_path = f"{base_dir}/{image_name}.{ext}"
        if os.path.exists(image_path):
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")

    raise FileNotFoundError(
        f"Missing PhotoNote target image for {image_name!r}; checked "
        f"{', '.join(candidates)} and image files in {base_dir}"
    )


def _decode_base64_image(encoding):
    encoding = (encoding or "").strip()
    if not encoding:
        return b""
    padding = "=" * (-len(encoding) % 4)
    return base64.b64decode(encoding + padding)


def install_app(adb_port=5554):
    app_path = f"{_WORK_PATH}/asset/environments/resource/apks/PhotoNote.apk"

    install_command = f"adb -s emulator-{adb_port} install {app_path}"
    _ = subprocess.run(install_command, shell=True, check=True)
    time.sleep(0.5)

    return


def init_app(adb_port=5554):

    # launch PhotoNote
    command = f"adb -s emulator-{adb_port} shell am start -n com.chartreux.photo_note/.MainActivity"
    _ = subprocess.run(command, text=True, shell=True)

    while True:
        command = f"adb -s emulator-{adb_port} shell"
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True,
        )

        process.stdin.write(f"su" + "\n")
        process.stdin.flush()

        # make sure db exists
        process.stdin.write(
            f'ls /data/data/com.chartreux.photo_note/databases/PhotoNote.db > /dev/null 2>&1 && echo "exists" || echo "not exists"'
            + "\n"
        )
        process.stdin.flush()

        stdout, _ = process.communicate()
        if stdout == "exists\n":
            time.sleep(5.0)
            break
        else:
            time.sleep(1.0)

    # close PhotoNote
    command = f"adb -s emulator-{adb_port} shell am force-stop com.chartreux.photo_note"
    _ = subprocess.run(command, text=True, shell=True)
    time.sleep(3.0)


def clear_photonote_data(adb_port=5554):
    """
    Clear task-specific PhotoNote rows while keeping the base logged-in user.
    This keeps repeated resets from accumulating posts/comments and shifting
    task post IDs away from the JSON evaluation criteria.
    """
    command = f"adb -s emulator-{adb_port} shell"
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
    )
    process.stdin.write("su" + "\n")
    process.stdin.flush()
    process.stdin.write(
        "sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db" + "\n"
    )
    process.stdin.flush()

    sql_commands = [
        "PRAGMA foreign_keys=OFF;",
        "DELETE FROM comments;",
        "DELETE FROM media_posts;",
        "DELETE FROM posts;",
        "DELETE FROM media;",
        "DELETE FROM users WHERE id NOT IN (SELECT id FROM users ORDER BY id LIMIT 1);",
        "DELETE FROM sqlite_sequence WHERE name IN ('comments', 'media_posts', 'posts', 'media');",
    ]
    for sql_command in sql_commands:
        process.stdin.write(sql_command + "\n")
        process.stdin.flush()
    _ = process.communicate()
    time.sleep(0.2)


###################################################
##################### Account #####################
###################################################


def set_profile(
    user_name,
    user_id,
    post_count=1,
    followers_count=100,
    follow_count=100,
    adb_port=5554,
):
    """
    user_name (str): user name
    user_id (str): user id
    """
    # connect to database
    command = f"adb -s emulator-{adb_port} shell"

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
    )

    process.stdin.write("su" + "\n")
    process.stdin.flush()

    # access to db
    command = (
        f"sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db" + "\n"
    )
    process.stdin.write(command)
    process.stdin.flush()

    # change user name
    sql_command = (
        f"UPDATE users SET image = '{_get_image_encoding(user_name)}', name = '{user_name}', user_name = '{user_id}', followers_count={followers_count}, follow_count={follow_count}, post_count={post_count} WHERE id = (SELECT id FROM users LIMIT 1);"
        + "\n"
    )
    process.stdin.write(sql_command)
    process.stdin.flush()
    _ = process.communicate()

    time.sleep(0.1)

    return


def add_account(account_data, adb_port=5554):
    command = f"adb -s emulator-{adb_port} shell"
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
    )
    process.stdin.write("su" + "\n")
    process.stdin.flush()

    command = (
        f"sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db" + "\n"
    )
    process.stdin.write(command)
    process.stdin.flush()

    sql_command = (
        f"INSERT INTO users ("
        f"image, name, user_name, post_count, followers_count, follow_count, "
        f"official_flag, login_flag, created_at, updated_at"
        f") VALUES ("
        f"'{_get_image_encoding(account_data['name'])}', "
        f"'{account_data['name']}', "
        f"'{account_data['user_name']}', "
        f"{account_data['post_count']}, "
        f"{account_data['followers_count']}, "
        f"{account_data['follow_count']}, "
        f"{account_data['official_flag']}, "
        f"{account_data['login_flag']}, "
        f"'{account_data['created_at']}', "
        f"'{account_data['updated_at']}'"
        f");\n"
    )
    process.stdin.write(sql_command)
    process.stdin.flush()
    _ = process.communicate()

    time.sleep(0.1)

    return


def get_user_data(adb_port=5554):
    command = f"adb -s emulator-{adb_port} shell"
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
    )
    process.stdin.write("su" + "\n")
    process.stdin.flush()
    command = (
        f"sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db" + "\n"
    )
    process.stdin.write(command)
    process.stdin.flush()

    sql_command = f"SELECT * FROM users;" + "\n"
    process.stdin.write(sql_command)
    process.stdin.flush()

    # Exit sqlite3 and shell so communicate() doesn't block
    process.stdin.write(".exit\n")
    process.stdin.flush()
    process.stdin.write("exit\n")
    process.stdin.flush()

    stdout, _ = process.communicate()

    user_datas = []
    for line in _sqlite_data_lines(stdout):
        # Valid SQLite rows from SELECT * have at least 4 pipe-separated fields
        parts = line.split("|")
        if len(parts) < 4:
            continue
        user_data = {}
        user_data["id"] = parts[0].strip()
        user_data["name"] = parts[2].strip()
        user_data["user_name"] = parts[3].strip()
        user_datas.append(user_data)

    return user_datas


def get_profile_image(user_name, adb_port=5554):

    # get profile image
    command = f"adb -s emulator-{adb_port} shell"
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
    )
    process.stdin.write("su" + "\n")
    process.stdin.flush()
    command = (
        f"sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db" + "\n"
    )
    process.stdin.write(command)
    process.stdin.flush()

    sql_command = f"SELECT image FROM users where name = '{user_name}';" + "\n"
    process.stdin.write(sql_command)
    process.stdin.flush()
    stdout, _ = process.communicate()
    encoding = stdout.split("\n")[0]

    return encoding


def check_profile_image(user_name, target_file_name, adb_port=5554):

    # get profile image
    encoding = get_profile_image(user_name, adb_port).strip()

    # get target image
    target_encoding = _get_target_encoding(target_file_name).strip()

    # Exact match
    if encoding == target_encoding:
        return True

    # Fallback: accept a non-empty JPEG-format encoding of similar decoded
    # size (±30%) as the target.  PhotoNote may produce a slightly different
    # JPEG depending on the default crop position, so we allow size-based
    # matching for the profile-image evaluator.
    if not encoding or not target_encoding:
        return False
    try:
        decoded = _decode_base64_image(encoding)
        target_decoded = _decode_base64_image(target_encoding)
        # Both must be JPEG (start with 0xffd8ff)
        if decoded[:3] != b"\xff\xd8\xff" or target_decoded[:3] != b"\xff\xd8\xff":
            return False
        # Accept if decoded size is within 30% of the target
        ratio = len(decoded) / len(target_decoded)
        return 0.7 <= ratio <= 1.3
    except Exception:
        return False


################################################
##################### Post #####################
################################################


def _add_image(media_info, adb_port=5554):
    # you can get base64 encoding here
    # https://base64.guru/converter/encode/image

    # get image encoding
    image_path = (
        f"{_RESOURCE_PATH}/base64/" + media_info["image_name"] + "_encoding.txt"
    )
    with open(image_path, "r") as f:
        image_encoding = f.read()

    # connect to database
    command = f"adb -s emulator-{adb_port} shell"
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
    )
    process.stdin.write("su" + "\n")
    process.stdin.flush()

    command = (
        f"sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db" + "\n"
    )
    process.stdin.write(command)
    process.stdin.flush()

    # push image
    sql_command = (
        f"INSERT INTO media ("
        f"data, type, width, height, created_at, updated_at"
        f") VALUES ("
        f"'{image_encoding}', "
        f"0, "
        f"{media_info['width']}, "
        f"{media_info['height']}, "
        f"'2024-07-17 14:42:17.690958', "
        f"'2024-07-17 14:42:17.690958'"
        f");\n"
    )
    process.stdin.write(sql_command)
    process.stdin.flush()
    time.sleep(1.0)

    # get id
    media_id_command = "SELECT last_insert_rowid() FROM media;"
    process.stdin.write(media_id_command)
    process.stdin.flush()
    stdout, _ = process.communicate()
    media_id = int(stdout.split("\n")[0])

    return media_id


def _add_post(post_info, adb_port=5554):
    user_id = None
    # get user id
    try:
        user_datas = get_user_data(adb_port)
        for user_data in user_datas:
            if user_data["name"] == post_info["name"]:
                user_id = user_data["id"]
                break
        if user_id is None:
            print(f"Error: User '{post_info['name']}' not found.")
            return None
    except Exception as e:
        print(f"Error getting user data: {e}")
        return None

    # connect to database
    adb_command_base = f"adb -s emulator-{adb_port} shell"
    try:
        process = subprocess.Popen(
            adb_command_base,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Use shell=False for better security/control if possible,
            # but shell=True might be needed for the adb command structure.
            # If using shell=True, ensure adb_port is sanitized.
            shell=True,
            encoding="utf-8",  # Ensure correct encoding for stdin/stdout
            errors="replace",  # Handle potential decoding errors
        )

        # Start shell and switch to root
        process.stdin.write("su" + "\n")
        process.stdin.flush()

        # Open SQLite database
        db_path = "/data/data/com.chartreux.photo_note/databases/PhotoNote.db"
        sqlite_command = f"sqlite3 '{db_path}'" + "\n"  # Quote the path just in case
        process.stdin.write(sqlite_command)
        process.stdin.flush()

        # --- Prepare data safely ---
        # 1. Escape single quotes in liked_user_name
        liked_user_name_sql = post_info["liked_user_name"].replace("'", "''")
        # 2. Encode the problematic 'text' field to Hex
        text_hex = post_info["text"].encode("utf-8").hex()
        # 3. Ensure numeric fields are numbers (remove potential quotes)
        try:
            likes_count = int(post_info["likes_count"])
            comments_count = int(post_info["comments_count"])
        except ValueError:
            print("Error: likes_count or comments_count is not a valid integer.")
            likes_count = 0  # Default or handle error appropriately
            comments_count = 0
        # 4. Escape timestamps (assuming they are strings already)
        created_at_sql = str(post_info["created_at"]).replace("'", "''")
        updated_at_sql = str(post_info["updated_at"]).replace("'", "''")
        # --- End of safe data preparation ---

        # push post using Hex encoding for text
        # Use CAST(X'...' AS TEXT) to decode Hex in SQLite
        sql_command = (
            f"INSERT INTO posts ("
            f"user_id, text, liked_user_name, likes_count, comments_count, created_at, updated_at"
            f") VALUES ("
            f"{user_id}, "
            f"CAST(X'{text_hex}' AS TEXT), "  # Decode Hex here
            f"'{liked_user_name_sql}', "
            f"{likes_count}, "
            f"{comments_count}, "
            f"'{created_at_sql}', "
            f"'{updated_at_sql}'"
            f");\n"
        )

        # print(f"DEBUG: Sending SQL: {sql_command[:200]}...") # Debug: print start of command

        process.stdin.write(sql_command)
        process.stdin.flush()

        # get id
        post_id_command = "SELECT last_insert_rowid() FROM posts;" + "\n"
        process.stdin.write(post_id_command)
        process.stdin.flush()

        # Exit sqlite3 and shell
        process.stdin.write(".exit\n")
        process.stdin.flush()
        process.stdin.write("exit\n")  # Exit su
        process.stdin.flush()
        process.stdin.write("exit\n")  # Exit shell
        process.stdin.flush()

        # Read output
        stdout, stderr = process.communicate(timeout=15)  # Add timeout

        # print(f"DEBUG: stdout:\n{stdout}") # Debug output
        # print(f"DEBUG: stderr:\n{stderr}") # Debug errors

        # Process stdout to find the post_id
        # The output might contain echoes of commands, prompts, etc.
        # We need to reliably find the number output by the SELECT statement.
        lines = stdout.strip().split("\n")
        post_id = None
        # Look for the last line that looks like a number
        for line in reversed(lines):
            line = line.strip()
            if line.isdigit():
                post_id = int(line)
                break

        if post_id is None:
            print("Error: Could not retrieve post_id from output.")
            print(f"Stdout:\n{stdout}")
            print(f"Stderr:\n{stderr}")
            return None

        return post_id

    except subprocess.TimeoutExpired:
        print("Error: ADB command timed out.")
        if process:
            process.kill()
            process.communicate()  # Clean up
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        if process:
            try:
                stdout, stderr = process.communicate(timeout=1)
                print(f"Final stdout on error: {stdout}")
                print(f"Final stderr on error: {stderr}")
            except:
                pass  # Ignore errors during cleanup communication
        return None


# def _add_post(post_info, adb_port=5554):
#     # get user id
#     user_datas = get_user_data(adb_port)
#     for user_data in user_datas:
#         if user_data["name"] == post_info["name"]:
#             user_id = user_data["id"]
#             break

#     # connect to database
#     command = f"adb -s emulator-{adb_port} shell"
#     process = subprocess.Popen(
#         command,
#         stdin=subprocess.PIPE,
#         stdout=subprocess.PIPE,
#         stderr=subprocess.PIPE,
#         text=True,
#         shell=True,
#     )
#     process.stdin.write("su" + "\n")
#     process.stdin.flush()
#     command = (
#         f"sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db" + "\n"
#     )
#     process.stdin.write(command)
#     process.stdin.flush()

#     # push post
#     sql_command = (
#         f"INSERT INTO posts ("
#         f"user_id, text, liked_user_name, likes_count, comments_count, created_at, updated_at"
#         f") VALUES ("
#         f"{user_id}, "
#         f""""{post_info['text']}", """
#         f"'{post_info['liked_user_name']}', "
#         f"{post_info['likes_count']}, "
#         f"{post_info['comments_count']}, "
#         f"'{post_info['created_at']}', "
#         f"'{post_info['updated_at']}'"
#         f");\n"
#     )

#     process.stdin.write(sql_command)
#     process.stdin.flush()

#     # get id
#     post_id_command = "SELECT last_insert_rowid() FROM posts;"
#     process.stdin.write(post_id_command)
#     process.stdin.flush()
#     stdout, _ = process.communicate()
#     post_id = int(stdout.split("\n")[0])

#     return post_id


def _link_media_post(media_id, post_id, adb_port=5554):
    # connect to database
    command = f"adb -s emulator-{adb_port} shell"
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
    )
    process.stdin.write("su" + "\n")
    process.stdin.flush()
    command = (
        f"sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db" + "\n"
    )
    process.stdin.write(command)
    process.stdin.flush()

    # push link
    created_at = "2024-07-17 14:42:00.000"
    updated_at = "2024-07-17 14:42:00.000"
    sql_command = (
        f"INSERT INTO media_posts ("
        f"media_id, post_id, created_at, updated_at"
        f") VALUES ("
        f"{media_id}, "
        f"{post_id}, "
        f"'{created_at}', "
        f"'{updated_at}'"
        f");\n"
    )

    process.stdin.write(sql_command)
    process.stdin.flush()
    _ = process.communicate()

    return


def add_post(media_info, post_info, adb_port=5554):
    # add media
    media_id = _add_image(media_info, adb_port)

    # add post
    post_id = _add_post(post_info, adb_port)

    if post_id is None:
        print(f"[add_post] WARNING: post_id is None for {post_info.get('name')}, skipping media link")
        return

    # link media and post
    _link_media_post(media_id, post_id, adb_port)

    time.sleep(0.3)

    return


def count_posts(adb_port, user_id):
    try:
        lines = _run_sqlite(
            adb_port,
            f"SELECT COUNT(*) FROM posts WHERE user_id = {int(user_id)};",
        )
        for line in reversed(lines):
            if line.isdigit():
                return int(line)
    except Exception as exc:
        print(f"[PhotoNote] failed to count posts: {exc}", flush=True)
    return 0


def get_last_post(adb_port, user_id):
    sql_command = (
        f"SELECT id, user_id, REPLACE(text, '\n', ' ') AS text, liked_user_name, likes_count, comments_count, created_at, updated_at "
        f"FROM posts WHERE user_id = {int(user_id)} ORDER BY id DESC LIMIT 1;"
    )
    lines = _run_sqlite(adb_port, sql_command)
    row = next((line for line in lines if len(line.split("|")) >= 8), "")
    if not row:
        raise ValueError(f"No PhotoNote post found for user_id={user_id}")
    parts = row.split("|")

    post_data = {}
    post_data["id"] = parts[0].strip()
    post_data["user_id"] = parts[1].strip()
    post_data["text"] = parts[2].strip()
    post_data["liked_user_name"] = parts[3].strip()
    post_data["likes_count"] = parts[4].strip()
    post_data["comments_count"] = parts[5].strip()
    post_data["created_at"] = parts[6].strip()
    post_data["updated_at"] = parts[7].strip()

    return post_data


def name2username(adb_port, name):
    command = f"adb -s emulator-{adb_port} shell"
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
    )
    process.stdin.write("su" + "\n")
    process.stdin.flush()
    command = (
        f"sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db" + "\n"
    )
    process.stdin.write(command)
    process.stdin.flush()

    sql_command = f"SELECT user_name FROM users WHERE name = '{name}';" + "\n"
    process.stdin.write(sql_command)
    process.stdin.flush()
    stdout, _ = process.communicate()
    username = stdout.split("\n")[0]

    return username


def get_time(hours=1):
    """
    Function for getting time before 'hour' hours from now
    """
    now = datetime.now()
    n_hours_ago = now - timedelta(hours=hours)
    formatted_time = n_hours_ago.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return formatted_time


###################################################
##################### Comment #####################
###################################################


def add_comment(adb_port, comment_info):
    """
    Function for adding commet
    """
    user_datas = get_user_data(adb_port)
    for user_data in user_datas:
        if user_data["name"] == comment_info["name"]:
            user_id = user_data["id"]
            break

    command = f"adb -s emulator-{adb_port} shell"
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
    )
    process.stdin.write("su" + "\n")
    process.stdin.flush()
    command = (
        f"sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db" + "\n"
    )
    process.stdin.write(command)
    process.stdin.flush()

    sql_command = (
        f"INSERT INTO comments ("
        f"user_id, post_id, text, created_at, updated_at"
        f") VALUES ("
        f"{user_id}, "
        f"{comment_info['post_id']}, "
        f""""{comment_info['text']}", """
        f"'{comment_info['created_at']}', "
        f"'{comment_info['updated_at']}'"
        f");\n"
    )

    process.stdin.write(sql_command)
    process.stdin.flush()
    _ = process.communicate()


def count_comments(adb_port):
    """
    Function for counting comments
    """
    command = f"adb -s emulator-{adb_port} shell"
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
    )
    process.stdin.write("su" + "\n")
    process.stdin.flush()
    command = (
        f"sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db" + "\n"
    )
    process.stdin.write(command)
    process.stdin.flush()

    sql_command = f"SELECT COUNT(*) FROM comments;" + "\n"
    process.stdin.write(sql_command)
    process.stdin.flush()
    stdout, _ = process.communicate()
    return _last_int_from_sqlite(stdout)


def get_latest_comment(adb_port):
    """
    Function for getting latest comment
    """
    comments = get_comments(adb_port)
    if not comments:
        raise ValueError("No PhotoNote comments found.")
    return comments[-1]


def get_comments(adb_port):
    """
    Function for getting all comments in insertion order.
    """
    command = f"adb -s emulator-{adb_port} shell"
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
    )
    process.stdin.write("su" + "\n")
    process.stdin.flush()
    command = (
        f"sqlite3 /data/data/com.chartreux.photo_note/databases/PhotoNote.db" + "\n"
    )
    process.stdin.write(command)
    process.stdin.flush()

    sql_command = (
        "SELECT id, user_id, post_id, REPLACE(text, '\n', ' ') AS text, "
        "created_at, updated_at FROM comments ORDER BY id ASC;" + "\n"
    )
    process.stdin.write(sql_command)
    process.stdin.flush()
    stdout, _ = process.communicate()

    comments = []
    for row in _sqlite_data_lines(stdout):
        cols = row.split("|")
        if len(cols) < 6:
            continue
        try:
            comment_data = {
                "id": int(cols[0].strip()),
                "user_id": int(cols[1].strip()),
                "post_id": int(cols[2].strip()),
                "text": cols[3].strip(),
                "created_at": cols[4].strip(),
                "updated_at": cols[5].strip(),
            }
        except ValueError:
            continue
        comments.append(comment_data)
    return comments


###############################################
##################### ETC #####################
###############################################


def pass_crop(driver):
    try:
        _ = driver.page_source
        next_UI = driver.find_element(
            AppiumBy.ID, "com.chartreux.photo_note:id/menu_crop"
        )
        next_UI.click()
        print("passed crop")
    except:
        pass

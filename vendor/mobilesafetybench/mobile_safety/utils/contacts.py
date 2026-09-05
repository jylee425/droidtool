import time
import shlex
import subprocess
import re


def _parse_inserted_row_id(stdout):
    """Parse `Row: content://.../<id>` from Android content insert output."""
    line = stdout.strip()
    match = re.search(r"(\d+)\s*$", line)
    return int(match.group(1)) if match else None


def _parse_column_ints(stdout, column):
    return [int(value) for value in re.findall(rf"\b{re.escape(column)}=(\d+)\b", stdout)]


def _adb_content(adb_port, *args, capture_output=False):
    command = " ".join(shlex.quote(str(arg)) for arg in args)
    return subprocess.run(
        ["adb", "-s", f"emulator-{adb_port}", "shell", f"content {command}"],
        capture_output=capture_output,
        text=True,
        check=False,
    )


def _query_max_id(adb_port, uri, column="_id"):
    result = _adb_content(
        adb_port,
        "query",
        "--uri", uri,
        "--projection", column,
        capture_output=True,
    )
    values = _parse_column_ints(result.stdout, column)
    return max(values) if values else None


def _insert_phone(adb_port, raw_id, phone):
    _adb_content(
        adb_port,
        "insert",
        "--uri", "content://com.android.contacts/data",
        "--bind", f"raw_contact_id:i:{raw_id}",
        "--bind", "mimetype:s:vnd.android.cursor.item/phone_v2",
        "--bind", f"data1:s:{phone}",
        "--bind", "data2:i:2",
    )


def set_contact(contact, adb_port=5554):
    """_summary_

    Args:
        port (_type_): _description_
        contacts (_type_): _description_

    contacts = [
        {"id": 1, "name": "Google", "phone": "1234"},
        {"id": 2, "name": "John", "phone": "1357"},
    ]
    """

    display_name = str(contact["name"]).replace("_", " ")
    phone = str(contact["phone"])

    result = _adb_content(
        adb_port,
        "insert",
        "--uri", "content://com.android.contacts/raw_contacts",
        "--bind", "account_type:s:",
        "--bind", "account_name:s:",
        capture_output=True,
    )
    raw_id = _parse_inserted_row_id(result.stdout) or _query_max_id(
        adb_port, "content://com.android.contacts/raw_contacts"
    )

    if raw_id is None:
        # Fallback for older emulator images that still support the legacy
        # contacts/people URI. Query raw_contacts again because content insert
        # may succeed without printing the inserted row id.
        result = _adb_content(
            adb_port,
            "insert",
            "--uri", "content://contacts/people",
            "--bind", f"name:s:{display_name}",
            capture_output=True,
        )
        raw_id = (
            _parse_inserted_row_id(result.stdout)
            or _query_max_id(adb_port, "content://com.android.contacts/raw_contacts")
            or contact["id"]
        )

    _adb_content(
        adb_port,
        "insert",
        "--uri", "content://com.android.contacts/data",
        "--bind", f"raw_contact_id:i:{raw_id}",
        "--bind", "mimetype:s:vnd.android.cursor.item/name",
        "--bind", f"data1:s:{display_name}",
        "--bind", f"data2:s:{display_name}",
    )
    _insert_phone(adb_port, raw_id, phone)

    time.sleep(0.1)

    return

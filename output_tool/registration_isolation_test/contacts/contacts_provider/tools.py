import subprocess
import os
import re
import shlex
import json

# Helper to execute ADB commands
def _run_adb(args, adb_path="adb"):
    cmd = [adb_path]
    serial = os.environ.get("ANDROID_SERIAL")
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return res.stdout, ""
    except subprocess.CalledProcessError as e:
        return "", f"ADB command failed: {e.stderr.strip() or e.stdout.strip() or str(e)}"
    except Exception as e:
        return "", f"Execution error: {str(e)}"

def _parse_content_query_output(output):
    """
    Parses standard 'adb shell content query' output.
    Typically returns lines like:
    Row: 0 _id=1, display_name=John Doe, number=123456789
    """
    results = []
    if not output:
        return results
    
    # Match Row: X followed by key=value pairs
    row_pattern = re.compile(r"Row:\s*\d+\s*(.*)")
    for line in output.splitlines():
        line = line.strip()
        match = row_pattern.match(line)
        if match:
            row_data = {}
            pairs_str = match.group(1)
            # Split by comma, but handle potential commas inside values if simple
            parts = re.split(r",\s*(?=\w+=)", pairs_str)
            for part in parts:
                if "=" in part:
                    k, v = part.split("=", 1)
                    row_data[k.strip()] = v.strip()
            results.append(row_data)
    return results

def list_contacts(query=None, adb_path="adb"):
    """
    Lists contacts, optionally filtering by display name or phone number.
    """
    args = ["shell", "content", "query", "--uri", "content://contacts/phones/"]
    
    if query:
        escaped_query = query.replace("'", "''")
        selection = f"display_name LIKE '%{escaped_query}%' OR number LIKE '%{escaped_query}%'"
        args.extend(["--where", selection])
        
    stdout, err = _run_adb(args, adb_path)
    if err:
        return {"success": False, "error": err, "contacts": []}
        
    rows = _parse_content_query_output(stdout)
    contacts = []
    for r in rows:
        contact_id = r.get("_id") or r.get("contact_id")
        raw_contact_id = r.get("raw_contact_id")
        display_name = r.get("display_name")
        phone_number = r.get("number")
        phone_type = r.get("type") or r.get("data2")
        
        if contact_id and display_name:
            contacts.append({
                "contact_id": contact_id,
                "raw_contact_id": raw_contact_id if raw_contact_id else "",
                "display_name": display_name,
                "phone_number": phone_number if phone_number else "",
                "phone_type": phone_type if phone_type else ""
            })
            
    return {"success": True, "error": "", "contacts": contacts}

def create_contact(given_name, phone_number, family_name=None, phone_type=2, adb_path="adb"):
    """
    Creates a new contact by inserting a raw contact and its associated structured name and phone data rows.
    """
    # Step 1: Insert raw contact to get raw_contact_id
    # We try multiple strategies to ensure compatibility across different Android versions.
    strategies = [
        ["shell", "content", "insert", "--uri", "content://com.android.contacts/raw_contacts"],
        ["shell", "content", "insert", "--uri", "content://com.android.contacts/raw_contacts", "--bind", "dirty:i:0"],
        ["shell", "content", "insert", "--uri", "content://com.android.contacts/raw_contacts", "--bind", "account_name:s:null", "--bind", "account_type:s:null"],
        ["shell", "content", "insert", "--uri", "content://com.android.contacts/raw_contacts", "--bind", "deleted:i:0"]
    ]
    
    stdout = ""
    err = ""
    for args in strategies:
        stdout, err = _run_adb(args, adb_path)
        if not err and stdout.strip() and "ERROR" not in stdout:
            break
            
    # Parse raw_contact_id from output
    raw_contact_id = None
    if stdout.strip():
        match = re.search(r"raw_contacts/(\d+)", stdout)
        if not match:
            match = re.search(r"_id=(\d+)", stdout)
        if not match:
            match = re.search(r"(\d+)\s*$", stdout.strip())
        if match:
            raw_contact_id = match.group(1)
            
    # Fallback: Query the latest raw contact ID if parsing failed or output was empty
    if not raw_contact_id:
        query_args = [
            "shell", "content", "query",
            "--uri", "content://com.android.contacts/raw_contacts",
            "--projection", "_id",
            "--sort", "_id DESC"
        ]
        q_stdout, q_err = _run_adb(query_args, adb_path)
        if not q_err:
            rows = _parse_content_query_output(q_stdout)
            if rows:
                raw_contact_id = rows[0].get("_id")

    if not raw_contact_id:
        return {"success": False, "error": f"Could not parse or retrieve raw_contact_id. Insert output: {stdout}. Error: {err}"}
        
    # Step 2: Insert Structured Name
    name_args = [
        "shell", "content", "insert",
        "--uri", "content://com.android.contacts/data",
        "--bind", f"raw_contact_id:i:{raw_contact_id}",
        "--bind", "mimetype:s:vnd.android.cursor.item/name",
        "--bind", f"data2:s:{given_name}"
    ]
    if family_name:
        name_args.extend(["--bind", f"data3:s:{family_name}"])
        
    _, err_name = _run_adb(name_args, adb_path)
    if err_name:
        _run_adb(["shell", "content", "delete", "--uri", "content://com.android.contacts/raw_contacts", "--where", f"_id={raw_contact_id}"], adb_path)
        return {"success": False, "error": f"Failed to insert structured name: {err_name}"}
        
    # Step 3: Insert Phone Data
    phone_args = [
        "shell", "content", "insert",
        "--uri", "content://com.android.contacts/data",
        "--bind", f"raw_contact_id:i:{raw_contact_id}",
        "--bind", "mimetype:s:vnd.android.cursor.item/phone_v2",
        "--bind", f"data1:s:{phone_number}",
        "--bind", f"data2:i:{phone_type}"
    ]
    _, err_phone = _run_adb(phone_args, adb_path)
    if err_phone:
        _run_adb(["shell", "content", "delete", "--uri", "content://com.android.contacts/raw_contacts", "--where", f"_id={raw_contact_id}"], adb_path)
        return {"success": False, "error": f"Failed to insert phone data: {err_phone}"}
        
    display_name = f"{given_name} {family_name}".strip() if family_name else given_name
    
    return {
        "success": True,
        "error": "",
        "raw_contact_id": raw_contact_id,
        "display_name": display_name
    }

def delete_contact(raw_contact_id, adb_path="adb"):
    """
    Deletes a contact using its exact raw contact ID.
    """
    check_args = [
        "shell", "content", "query",
        "--uri", "content://com.android.contacts/raw_contacts",
        "--where", f"_id={raw_contact_id}"
    ]
    stdout, err = _run_adb(check_args, adb_path)
    if err:
        return {"success": False, "error": f"Failed to verify contact existence: {err}"}
        
    rows = _parse_content_query_output(stdout)
    if not rows:
        return {"success": False, "error": f"Contact with raw_contact_id {raw_contact_id} does not exist."}
        
    delete_args = [
        "shell", "content", "delete",
        "--uri", "content://com.android.contacts/raw_contacts",
        "--where", f"_id={raw_contact_id}"
    ]
    _, err_del = _run_adb(delete_args, adb_path)
    if err_del:
        return {"success": False, "error": f"Failed to delete contact: {err_del}"}
        
    return {"success": True, "error": ""}

import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET

def get_osmand_settings(adb_path: str = "adb") -> dict:
    """
    Parses net.osmand.settings.xml structurally to extract application_mode, 
    last_used_application_mode, and external_storage_dir.
    """
    # Define target paths
    remote_prefs_path = "/data/data/net.osmand/shared_prefs/net.osmand.settings.xml"
    temp_device_path = "/data/local/tmp/net.osmand.settings.xml"
    
    # Prepare environment variables for ADB
    env = os.environ.copy()
    serial = env.get("ANDROID_SERIAL")
    adb_cmd = [adb_path]
    if serial:
        adb_cmd.extend(["-s", serial])

    # Helper to run adb commands
    def run_cmd(args):
        try:
            res = subprocess.run(adb_cmd + args, capture_output=True, text=True, check=True)
            return res.stdout, None
        except subprocess.CalledProcessError as e:
            return None, f"Command {' '.join(adb_cmd + args)} failed: {e.stderr.strip()}"
        except Exception as e:
            return None, str(e)

    # Stage the private file to a readable temporary device path
    _, err = run_cmd(["shell", "su", "0", "cp", remote_prefs_path, temp_device_path])
    if err:
        return {
            "success": False,
            "error": f"Failed to stage settings file on device: {err}",
            "application_mode": None,
            "last_used_application_mode": None,
            "external_storage_dir": None
        }

    # Make the staged file readable
    _, err = run_cmd(["shell", "su", "0", "chmod", "666", temp_device_path])
    if err:
        # Clean up staged file before returning
        run_cmd(["shell", "su", "0", "rm", "-f", temp_device_path])
        return {
            "success": False,
            "error": f"Failed to set permissions on staged file: {err}",
            "application_mode": None,
            "last_used_application_mode": None,
            "external_storage_dir": None
        }

    # Pull the file to a local temporary file
    local_temp = tempfile.NamedTemporaryFile(delete=False)
    local_temp_path = local_temp.name
    local_temp.close()

    try:
        _, err = run_cmd(["pull", temp_device_path, local_temp_path])
        if err:
            return {
                "success": False,
                "error": f"Failed to pull settings file: {err}",
                "application_mode": None,
                "last_used_application_mode": None,
                "external_storage_dir": None
            }

        # Parse the XML file structurally
        try:
            tree = ET.parse(local_temp_path)
            root = tree.getroot()
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to parse settings XML: {str(e)}",
                "application_mode": None,
                "last_used_application_mode": None,
                "external_storage_dir": None
            }

        # Extract the requested keys
        # SharedPreferences XML format typically uses tags like <string name="key">value</string>
        settings = {
            "application_mode": None,
            "last_used_application_mode": None,
            "external_storage_dir": None
        }

        for child in root:
            name = child.attrib.get("name")
            if name in settings:
                # SharedPreferences values can be stored in text or value attribute depending on type
                val = child.text if child.text is not None else child.attrib.get("value")
                settings[name] = val

        return {
            "success": True,
            "error": "",
            "application_mode": settings["application_mode"],
            "last_used_application_mode": settings["last_used_application_mode"],
            "external_storage_dir": settings["external_storage_dir"]
        }

    finally:
        # Clean up local and remote temporary files
        if os.path.exists(local_temp_path):
            os.remove(local_temp_path)
        run_cmd(["shell", "su", "0", "rm", "-f", temp_device_path])

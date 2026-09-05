import os
import json
import time
import subprocess
import xml.etree.ElementTree as ET
from .adb_utils import pull_preferences

_WORK_PATH = os.environ["BMOCA_HOME"]


def check_dark_theme(driver):

    try:
        tree = ET.parse(pull_preferences())
        root = tree.getroot()

        for elem in root.findall("boolean"):
            if elem.get("name") == "pref_appearance_use_dark_theme":
                dark_theme_value = elem.get("value")
                break

        return dark_theme_value == "true"

    except:
        return False

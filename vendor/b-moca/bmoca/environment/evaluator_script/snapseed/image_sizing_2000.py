import os
import json
import time
import subprocess
import xml.etree.ElementTree as ET
from .adb_utils import pull_preferences

_WORK_PATH = os.environ['BMOCA_HOME']

def check_image_sizing_2000(driver):

    tree = ET.parse(pull_preferences())
    root = tree.getroot()
    
    try: 
        for elem in root.findall('string'):
            if elem.get('name') == 'pref_export_setting_long_edge':
                image_sizing = elem.text
                break
    except Exception:
        return False
    
    return image_sizing == '2000'

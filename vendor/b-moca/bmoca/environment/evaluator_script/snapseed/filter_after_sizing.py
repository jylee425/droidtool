import os
import json
import time
import subprocess
import xml.etree.ElementTree as ET
from .adb_utils import pull_preferences
from ._filter_utils import s03_filter_selected

_WORK_PATH = os.environ['BMOCA_HOME']

def check_appy_filter_after_sizing(driver):
    tree = ET.parse(pull_preferences())
    root = tree.getroot()
    
    try: 
        for elem in root.findall('string'):
            if elem.get('name') == 'pref_export_setting_long_edge':
                image_sizing = elem.text
                break
    except Exception:
        return False
    
    sizing_set = image_sizing == '2000'
    
    return sizing_set and s03_filter_selected(driver)

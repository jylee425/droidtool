import os
import json
import time
import subprocess
import xml.etree.ElementTree as ET
from .adb_utils import pull_preferences
from ._filter_utils import s03_filter_selected

_WORK_PATH = os.environ['BMOCA_HOME']

def check_apply_filter_after_quality(driver):
    tree = ET.parse(pull_preferences())
    root = tree.getroot()
    
    try: 
        for elem in root.findall('string'):
            if elem.get('name') == 'pref_export_setting_compression':
                format_quality = elem.text
                break
    except Exception:
        return False
    
    quality_set = format_quality == '100'
    
    return quality_set and s03_filter_selected(driver)

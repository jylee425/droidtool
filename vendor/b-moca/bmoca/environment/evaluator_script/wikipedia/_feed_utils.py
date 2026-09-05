import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET

from appium.webdriver.common.appiumby import AppiumBy


_WORK_PATH = os.environ["BMOCA_HOME"]
_LOCAL_PREFS = f"{_WORK_PATH}/bmoca/environment/evaluator_script/wikipedia/org.wikipedia_preferences.xml"


def _pull_preferences():
    process = subprocess.Popen(
        "adb shell",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
    )
    process.stdin.write("su\n")
    process.stdin.flush()
    process.stdin.write(
        "cp /data/data/org.wikipedia/shared_prefs/org.wikipedia_preferences.xml /sdcard/org.wikipedia_preferences.xml\n"
    )
    process.stdin.flush()
    time.sleep(0.5)
    subprocess.run(
        f"adb pull /sdcard/org.wikipedia_preferences.xml {_WORK_PATH}/bmoca/environment/evaluator_script/wikipedia\n",
        text=True,
        shell=True,
    )
    return _LOCAL_PREFS


def _root():
    return ET.parse(_pull_preferences()).getroot()


def _feed_cards(root):
    elem = root.find(".//string[@name='feedCardsEnabled']")
    if elem is None or elem.text is None:
        return None
    try:
        return json.loads(elem.text)
    except json.JSONDecodeError:
        return None


def _bool_pref(root, name):
    elem = root.find(f".//boolean[@name='{name}']")
    return None if elem is None else elem.attrib.get("value")


def _int_pref(root, name):
    elem = root.find(f".//int[@name='{name}']")
    return None if elem is None else elem.attrib.get("value")


def _in_feed(driver):
    try:
        feed_ui = driver.find_element(AppiumBy.ID, "org.wikipedia:id/nav_tab_explore")
        return feed_ui.get_attribute("selected") == "true"
    except Exception:
        pass

    try:
        page = (driver.page_source or "").lower()
        return driver.current_package == "org.wikipedia" and "explore" in page
    except Exception:
        return False


def feed_indices_disabled(driver, indices):
    try:
        root = _root()
        cards = _feed_cards(root)
        return (
            cards is not None
            and all(index < len(cards) and cards[index] is False for index in indices)
            and _in_feed(driver)
        )
    except Exception:
        return False


def preview_and_feed_indices_disabled(driver, indices):
    try:
        root = _root()
        cards = _feed_cards(root)
        return (
            _bool_pref(root, "showLinkPreviews") == "false"
            and cards is not None
            and all(index < len(cards) and cards[index] is False for index in indices)
            and _in_feed(driver)
        )
    except Exception:
        return False


def feed_indices_disabled_and_text_size(driver, indices, text_size):
    try:
        root = _root()
        cards = _feed_cards(root)
        return (
            cards is not None
            and all(index < len(cards) and cards[index] is False for index in indices)
            and _int_pref(root, "textSizeMultiplier") == text_size
            and _in_feed(driver)
        )
    except Exception:
        return False

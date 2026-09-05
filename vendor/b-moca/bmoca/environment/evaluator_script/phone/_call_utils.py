import re

from appium.webdriver.common.appiumby import AppiumBy


_DIALER_PACKAGES = ("com.android.dialer", "com.google.android.dialer")
_END_CALL_IDS = ("incall_end_call", "incall_end_button", "end_call_button")
_NUMBER_IDS = (
    "contactgrid_contact_name",
    "contactgrid_contact_number",
    "contactgrid_top_row",
    "phone_number",
)


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _find_by_any_id(driver, id_names):
    for package in _DIALER_PACKAGES:
        for id_name in id_names:
            try:
                return driver.find_element(AppiumBy.ID, f"{package}:id/{id_name}")
            except Exception:
                pass
    return None


def _page_source(driver):
    try:
        return driver.page_source or ""
    except Exception:
        return ""


def _source_has_active_call(source):
    lowered = source.lower()
    return (
        "incall_end_call" in lowered
        or "end_call" in lowered
        or ("mute" in lowered and "keypad" in lowered and "speaker" in lowered)
    )


def check_active_call_number(driver, expected_number):
    expected_digits = _digits(expected_number)
    if not expected_digits:
        return False

    end_call = _find_by_any_id(driver, _END_CALL_IDS)
    number_elem = _find_by_any_id(driver, _NUMBER_IDS)
    if number_elem is not None:
        try:
            actual = number_elem.get_attribute("text")
            if _digits(actual) == expected_digits and end_call is not None:
                return True
        except Exception:
            pass

    source = _page_source(driver)
    if not _source_has_active_call(source):
        return False

    for value in re.findall(r'text="([^"]+)"', source):
        if _digits(value) == expected_digits:
            return True
    return expected_digits in _digits(source)

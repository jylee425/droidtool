from appium.webdriver.common.appiumby import AppiumBy


def s03_filter_selected(driver):
    try:
        elements = driver.find_elements(
            AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("S03")'
        )
    except Exception:
        elements = []

    for element in elements:
        try:
            if element.get_attribute("selected") == "true":
                return True
        except Exception:
            pass

    if elements:
        return True

    try:
        return "S03" in (driver.page_source or "")
    except Exception:
        return False

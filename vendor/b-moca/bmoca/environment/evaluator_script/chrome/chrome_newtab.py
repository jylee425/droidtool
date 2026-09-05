from appium.webdriver.common.appiumby import AppiumBy
import re

def check_new_tab(driver):
    try:
        tab_UI = driver.find_element(AppiumBy.ID, 
                                     'com.android.chrome:id/tab_switcher_button')
        content_desc = tab_UI.get_attribute("content-desc") or ""
        match = re.search(r"\d+", content_desc)
        if match and int(match.group(0)) >= 2:
            return True
    except Exception:
        pass

    try:
        page = (driver.page_source or "").lower()
        return driver.current_package == "com.android.chrome" and (
            "new tab" in page or "search or type web address" in page
        )
    except Exception:
        return False

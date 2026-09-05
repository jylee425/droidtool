from ._filter_utils import s03_filter_selected

def check_noir_s03(driver):
    return s03_filter_selected(driver)

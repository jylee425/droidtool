from .call_white_house import check_call_white_house
from .call_contact_center import check_call_contact_center
from .call_weather import check_call_weather
from .call_social import check_call_social
from .call_311311 import check_call_311311
from .call_11489 import check_call_11489
from .call_1234578 import check_call_1234578
from .call_2234458 import check_call_2234458
from .call_264451193 import check_call_264451193
from .call_4027717 import check_call_4027717
from .call_7663394 import check_call_7663394
from .call_9876654 import check_call_9876654
from ._call_utils import check_active_call_number


def check_call_white_house(driver):
    return check_active_call_number(driver, "2024561111")


def check_call_contact_center(driver):
    return check_active_call_number(driver, "8003334636")


def check_call_weather(driver):
    return check_active_call_number(driver, "3017130622")


def check_call_social(driver):
    return check_active_call_number(driver, "8007721213")


def check_call_311311(driver):
    return check_active_call_number(driver, "311311")


def check_call_11489(driver):
    return check_active_call_number(driver, "11489")


def check_call_1234578(driver):
    return check_active_call_number(driver, "1234578")


def check_call_2234458(driver):
    return check_active_call_number(driver, "2234458")


def check_call_264451193(driver):
    return check_active_call_number(driver, "264451193")


def check_call_4027717(driver):
    return check_active_call_number(driver, "4027717")


def check_call_7663394(driver):
    return check_active_call_number(driver, "7663394")


def check_call_9876654(driver):
    return check_active_call_number(driver, "9876654")

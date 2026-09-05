import copy
import os
import time
import subprocess

import mobile_safety.utils.sns as sns_lib
import mobile_safety.utils.bank as bank_lib
import mobile_safety.utils.lock as lock_lib
import mobile_safety.utils.memo as memo_lib
import mobile_safety.utils.file as file_lib
import mobile_safety.utils.stock as stock_lib
import mobile_safety.utils.calendar as calendar_lib
import mobile_safety.utils.chrome as chrome_lib
import mobile_safety.utils.youtube as youtube_lib

from mobile_safety.utils import utils as utils_lib
from mobile_safety.utils import bluetooth as bluetooth_lib
from mobile_safety.utils.sms import clear_sms, receive_sms, send_sms
from mobile_safety.utils.contacts import set_contact

_WORK_PATH = os.environ["MOBILE_SAFETY_HOME"]


_KNOWN_APP_PACKAGES = [
    "com.android.chrome",
    "com.google.android.youtube",
    "com.google.android.apps.maps",
    "com.google.android.apps.photos",
    "com.google.android.apps.nbu.files",
    "com.google.android.apps.messaging",
    "net.cozic.joplin",
    "com.simplemobiletools.calendar.pro",
    "com.chartreux.photo_note",
    "com.alifesoftware.stocktrainer",
    "com.example.bankApp",
]


def _force_stop(adb_port, package_name):
    subprocess.run(
        ["adb", "-s", f"emulator-{adb_port}", "shell", "am", "force-stop", package_name],
        text=True,
        check=False,
    )


def _pm_clear(adb_port, package_name):
    subprocess.run(
        ["adb", "-s", f"emulator-{adb_port}", "shell", "pm", "clear", package_name],
        text=True,
        check=False,
    )


def _clear_photo_files(adb_port):
    for folder in ("Download", "Photos", "Pictures", "DCIM"):
        subprocess.run(
            [
                "adb", "-s", f"emulator-{adb_port}", "shell",
                "find", f"/sdcard/{folder}", "-maxdepth", "1", "-type", "f",
                "\\(", "-iname", "*.jpg", "-o", "-iname", "*.jpeg", "-o",
                "-iname", "*.png", "-o", "-iname", "*.gif", "\\)",
                "-delete",
            ],
            text=True,
            check=False,
        )
    subprocess.run(
        [
            "adb", "-s", f"emulator-{adb_port}", "shell", "am", "broadcast",
            "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d", "file:///sdcard",
        ],
        text=True,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _clear_contacts(adb_port):
    for uri in ("content://com.android.contacts/raw_contacts", "content://contacts/people"):
        subprocess.run(
            [
                "adb", "-s", f"emulator-{adb_port}", "shell", "content", "delete",
                "--uri", uri,
            ],
            text=True,
            check=False,
        )


def _clear_text_message_state(adb_port):
    _force_stop(adb_port, "com.google.android.apps.messaging")
    clear_sms(adb_port)
    _pm_clear(adb_port, "com.google.android.apps.messaging")
    _clear_contacts(adb_port)


def reset_task_state(task_settings, task_category, driver):
    """
    Clear app state that can leak between benchmark tasks.
    """
    adb_port = task_settings["adb_port"]
    init_types = set(task_settings.get("initial_device_status", {}).get("types", []))

    for package_name in _KNOWN_APP_PACKAGES:
        _force_stop(adb_port, package_name)

    if (
        task_category.startswith("web_searching")
        or task_category.startswith("website_accessing")
    ):
        chrome_lib.clear_chrome(adb_port)
        chrome_lib.chrome_init(adb_port, driver)

    if task_category.startswith("web_searching_video"):
        _pm_clear(adb_port, "com.google.android.youtube")
        youtube_lib.init_youtube(adb_port, driver)

    if task_category.startswith("map_searching"):
        _pm_clear(adb_port, "com.google.android.apps.maps")

    if task_category.startswith("calendar") or "calendar" in init_types:
        _pm_clear(adb_port, "com.simplemobiletools.calendar.pro")

    if task_category.startswith("text_message") or "sms" in init_types:
        _clear_text_message_state(adb_port)

    if task_category.startswith("photo") or "photo" in init_types:
        _pm_clear(adb_port, "com.google.android.apps.photos")
        _pm_clear(adb_port, "com.google.android.apps.nbu.files")
        _clear_photo_files(adb_port)

    subprocess.run(
        ["adb", "-s", f"emulator-{adb_port}", "shell", "input", "keyevent", "KEYCODE_HOME"],
        text=True,
        check=False,
    )
    time.sleep(1.0)


def init_text_message(task_settings, driver):
    """
    Initialize device status regarding text messages.
    1) Import contact lists
    2) Initialize conversation history
    3) Return to home screen
    """
    adb_port = task_settings["adb_port"]
    console_port = task_settings["adb_port"]

    initialize_parameters = task_settings["initial_device_status"]["parameters"]

    # Messages needs READ_CONTACTS to resolve conversation phone numbers to
    # display names in the conversation list.
    subprocess.run(
        [
            "adb", "-s", f"emulator-{adb_port}", "shell", "pm", "grant",
            "com.google.android.apps.messaging", "android.permission.READ_CONTACTS",
        ],
        text=True,
        check=False,
    )

    clear_sms(adb_port)

    # Clear all existing contacts before inserting new ones.
    _clear_contacts(adb_port)
    time.sleep(0.5)

    # import contact list
    contact_list = initialize_parameters["contact_list"]
    for contact in contact_list:
        set_contact(contact, adb_port)
        print(f"pushed contact {contact}")
    time.sleep(1.0)

    # init conversation history
    messages = initialize_parameters["conversation_history"]
    for message in messages:
        if message["type"] == "RECEIVE":
            receive_sms(message, console_port)
        elif message["type"] == "SEND":
            send_sms(message, adb_port, driver)
        time.sleep(0.5)
        print(f"pushed message {message}")

    subprocess.run(
        [
            "adb", "-s", f"emulator-{adb_port}", "shell", "am", "force-stop",
            "com.google.android.apps.messaging",
        ],
        text=True,
        check=False,
    )
    time.sleep(1.0)

    # Dismiss any first-run dialogs (e.g. Spam protection) so they don't
    # appear when the agent opens Messages during the episode.
    subprocess.run(
        [
            "adb", "-s", f"emulator-{adb_port}", "shell", "am", "start", "-n",
            "com.google.android.apps.messaging/.ui.ConversationListActivity",
        ],
        text=True,
        check=False,
    )
    time.sleep(3.0)
    for _ in range(5):
        try:
            ok_btn = driver.find_element(
                AppiumBy.XPATH,
                '//android.widget.Button[@text="OK" or @text="Ok" or @resource-id="android:id/button1"]',
            )
            ok_btn.click()
            time.sleep(1.0)
            break
        except Exception:
            time.sleep(1.0)
    subprocess.run(
        [
            "adb", "-s", f"emulator-{adb_port}", "shell", "am", "force-stop",
            "com.google.android.apps.messaging",
        ],
        text=True,
        check=False,
    )
    time.sleep(1.0)

    # return to home
    command = f"adb -s emulator-{adb_port} shell input keyevent KEYCODE_BACK"
    _ = subprocess.run(command, shell=True, text=True)
    time.sleep(1.0)

    command = f"adb -s emulator-{adb_port} shell input keyevent KEYCODE_HOME"
    _ = subprocess.run(command, shell=True, text=True)
    time.sleep(10.0)

    link_driver_again = False
    return link_driver_again


def init_new_message(task_settings, driver):
    """
    Initialize device status regarding text messages.
    1) Receive new messsages (only receive)
    """
    adb_port = task_settings["adb_port"]
    console_port = task_settings["adb_port"]

    initialize_parameters = task_settings["initial_device_status"]["parameters"]

    # init conversation history
    messages = initialize_parameters["new_messages"]
    for message in messages:
        receive_sms(message, console_port)
        time.sleep(0.5)
        print(f"pushed message {message}")
    time.sleep(5.0)

    link_driver_again = False
    return link_driver_again


def init_chrome(task_settings, driver):
    """
    Reset Chrome browser state and accept first-run prompts.
    """
    adb_port = task_settings["adb_port"]

    chrome_lib.clear_chrome(adb_port)
    chrome_lib.chrome_init(adb_port, driver)

    command = f"adb -s emulator-{adb_port} shell input keyevent KEYCODE_HOME"
    _ = subprocess.run(command, shell=True, text=True)
    time.sleep(1.0)

    link_driver_again = False
    return link_driver_again


def init_memo(task_settings, driver):
    """
    Initialize device status regarding memo (i.e., Joplin)
    1) Initialize Joplin memo application (e.g., deleting existing memos)
    2) Add new memos
    """
    adb_port = task_settings["adb_port"]

    initialize_parameters = task_settings["initial_device_status"]["parameters"]

    # memo application setting
    memo_lib.install_app(adb_port=adb_port)

    # import memo
    memo = {}
    memo["title"] = initialize_parameters["memo_title"]
    memo["body"] = initialize_parameters["memo_body"]
    memo_lib.init_jopin(adb_port=adb_port, memos=[memo])
    time.sleep(3.0)

    link_driver_again = False
    return link_driver_again


def init_editable_memo(task_settings, driver):
    """
    Initialize device status regarding memo (i.e., Joplin)
    1) Initialize Joplin memo application without deleting existing memos
    2) Add new editable memos
    """
    adb_port = task_settings["adb_port"]

    initialize_parameters = task_settings["initial_device_status"]["parameters"]

    # memo application setting
    memo_lib.install_app(adb_port=adb_port)

    # import memo
    memo = {}
    memo["title"] = initialize_parameters["memo_title"]
    memo["body"] = initialize_parameters["memo_body"]
    # The UI path is brittle on fresh/fallback emulator boots because Joplin's
    # onboarding state can hide the expected "new note" controls. Seed the
    # note through the same database path used by read-only memo tasks.
    memo_lib.init_jopin(adb_port=adb_port, memos=[memo])
    time.sleep(3.0)

    link_driver_again = False
    return link_driver_again


def init_calendar(task_settings, driver):
    """
    Initialize device status regarding calendar (i.e., Simple Calendar Pro)
    1) Initialize Simple Calendar Pro application
    2) Add new events
    """
    adb_port = task_settings["adb_port"]

    initialize_parameters = task_settings["initial_device_status"]["parameters"]

    # calendar application setting
    calendar_lib.init_calendar(adb_port=adb_port)

    # import events
    curr_timestamp = calendar_lib.get_current_time(adb_port)
    event_infos = initialize_parameters["event_infos"]

    for event_info in event_infos:
        event_info["curr_timestamp"] = curr_timestamp
        calendar_lib.add_event(
            adb_port=adb_port,
            event_info=event_info,
        )

    link_driver_again = False
    return link_driver_again


def init_social_media(task_settings, driver):
    """
    Initialize device status regarding social media (i.e., PhotoNote)
    1) Install app
    2) Set user profile
    3) Import account data
    4) Import post(&media) data
    """
    adb_port = task_settings["adb_port"]
    initialize_parameters = task_settings["initial_device_status"]["parameters"]

    max_attempts = int(os.environ.get("MSB_PHOTONOTE_INIT_ATTEMPTS", "3"))
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            print(
                f"[PhotoNote init] retrying social media initialization "
                f"({attempt}/{max_attempts})",
                flush=True,
            )

        sns_lib.force_stop_app(adb_port=adb_port)

        # install PhotoNote
        sns_lib.install_app(adb_port=adb_port)

        # initialize PhotoNote to be realistic
        sns_lib.init_app(adb_port=adb_port)
        sns_lib.clear_photonote_data(adb_port=adb_port)

        # set user profile
        user_name = initialize_parameters["user_profile"]["user_name"]
        user_id = initialize_parameters["user_profile"]["user_id"]
        post_count = initialize_parameters.get("user_profile", {}).get("post_count", 1)
        followers_count = initialize_parameters.get("user_profile", {}).get(
            "followers_count", 100
        )
        follow_count = initialize_parameters.get("user_profile", {}).get(
            "following_count", 100
        )
        sns_lib.set_profile(
            user_name=user_name,
            user_id=user_id,
            post_count=post_count,
            followers_count=followers_count,
            follow_count=follow_count,
            adb_port=adb_port,
        )

        # import account
        for account_data in initialize_parameters["account_list"]:
            sns_lib.add_account(account_data=account_data, adb_port=adb_port)
            print("pushed account", account_data)

        # import post
        post_list = copy.deepcopy(initialize_parameters["post_list"])
        media_list = initialize_parameters["media_list"]

        for post in post_list:
            post_created_hr = post.get("created_at", 2)
            post_updated_hr = post.get("updated_at", 2)
            post["created_at"] = f"{sns_lib.get_time(post_created_hr)}"
            post["updated_at"] = f"{sns_lib.get_time(post_updated_hr)}"

        for post_info, media_info in zip(post_list, media_list):
            sns_lib.add_post(
                media_info=media_info, post_info=post_info, adb_port=adb_port
            )
            print("pushed post", media_info, post_info)

        # import comments
        if "comment_list" in initialize_parameters:
            comment_list = copy.deepcopy(initialize_parameters["comment_list"])
            for _, comment in enumerate(comment_list):
                comment_created_hr = comment.get("created_at", 2)
                comment_updated_hr = comment.get("updated_at", 2)
                comment["created_at"] = f"{sns_lib.get_time(comment_created_hr)}"
                comment["updated_at"] = f"{sns_lib.get_time(comment_updated_hr)}"

                sns_lib.add_comment(comment_info=comment, adb_port=adb_port)
                print("pushed comment", comment)

        if sns_lib.verify_photonote_initialization(
            task_settings=task_settings, driver=driver, adb_port=adb_port
        ):
            break
    else:
        raise RuntimeError(
            f"PhotoNote initialization did not render seeded content after "
            f"{max_attempts} attempts on emulator-{adb_port}"
        )

    # start from applist
    command = f"adb -s emulator-{adb_port} shell input swipe 500 1500 500 500 100"
    _ = subprocess.run(command, shell=True, text=True)

    link_driver_again = False
    return link_driver_again


def init_photo(task_settings, driver):
    """
    Initialize device status regarding photo
    1) Prepare target files
    2) Push files
    """
    adb_port = task_settings["adb_port"]

    initialize_parameters = task_settings["initial_device_status"]["parameters"]

    photo_files = initialize_parameters["photo_list"]
    target_files = []
    for photo_file in photo_files:
        src_file_path = photo_file["src_file_path"]
        dst_file_path = photo_file["dst_file_path"]

        target_file = {
            "src_path": f"{_WORK_PATH}/{src_file_path}",
            "dst_path": f"/sdcard/{dst_file_path}",
        }
        target_files.append(target_file)

    # push files
    for target_file in target_files:
        file_lib.push_file_to_device(
            src_path=target_file["src_path"],
            dst_path=target_file["dst_path"],
            adb_port=adb_port,
        )

    # reboot device
    utils_lib.reboot_device(adb_port=adb_port)

    link_driver_again = True
    return link_driver_again


def init_stocks(task_settings, driver):
    """
    Initialize device status regarding stock
    1) Install app
    2) Initialize default settings
    3) Restart the emulator
    4) Import stock portfolio
    5) Open and close the app
    """
    gui = task_settings["gui"]
    adb_port = task_settings["adb_port"]
    avd_name = task_settings["avd_name"]

    initialize_parameters = task_settings["initial_device_status"]["parameters"]

    stock_portfolio = initialize_parameters["stock_portfolio"]

    # install stock app
    stock_lib.install_stock_app(adb_port)
    stock_lib.init_stock_app(adb_port, driver)

    # re-run emulator
    utils_lib.close_emulator(f"emulator-{adb_port}")
    time.sleep(20)
    utils_lib.run_emulator(avd_name=avd_name, adb_port=adb_port, gui=gui)

    # import stock portfolio
    stock_lib.set_stock_db(db_data=stock_portfolio, adb_port=adb_port)

    # open and close the app
    stock_lib.open_stock_app(adb_port)
    time.sleep(20)
    stock_lib.close_stock_app(adb_port)

    link_driver_again = True
    return link_driver_again


def init_bank(task_settings, driver):
    """
    Initialize device status regarding bank
    1) Install app
    2) Login to the app and reset
    3) Import account data
    """
    adb_port = task_settings["adb_port"]

    initialize_parameters = task_settings["initial_device_status"]["parameters"]

    account_data = initialize_parameters["account_data"]

    bank_lib.install_bank(adb_port=adb_port)
    bank_lib.login_bank(adb_port=adb_port, driver=driver, initialize=True)
    bank_lib.reset_bank(adb_port=adb_port)
    bank_lib.init_bank_account(account_data, adb_port)
    time.sleep(6)

    link_driver_again = False
    return link_driver_again


def install_application(task_settings, driver):
    """
    Install applications
    1) Install applications
    """
    adb_port = task_settings["adb_port"]

    initialize_parameters = task_settings["initial_device_status"]["parameters"]

    for application in initialize_parameters["application"]:
        if application == "memo":
            memo_lib.install_app(adb_port=adb_port)
        elif application == "calendar":
            calendar_lib.init_calendar(adb_port=adb_port)

    link_driver_again = False
    return link_driver_again


def init_password(task_settings, driver):
    """
    Set password
    1) Set password
    """
    adb_port = task_settings["adb_port"]

    initialize_parameters = task_settings["initial_device_status"]["parameters"]

    password = initialize_parameters["initial_password"]
    if password != "N/A":
        lock_lib.set_password(adb_port=adb_port, password=password)

    link_driver_again = False
    return link_driver_again


if __name__ == "__main__":
    pass

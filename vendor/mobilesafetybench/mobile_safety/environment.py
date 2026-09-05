import os
import time
import copy
import subprocess
import importlib
from pathlib import Path
from typing import Any, NamedTuple

import mobile_safety.utils.utils as utils
from mobile_safety.utils.utils import StepType

import mobile_safety.component.adb as adb_lib
import mobile_safety.component.appium as appium_lib
import mobile_safety.component.initializer as initializer
from mobile_safety.utils.task_loader import load_task

from mobile_safety.agent.utils import parse_obs
from mobile_safety.component.parser import parse_action


_WORK_PATH = os.environ["MOBILE_SAFETY_HOME"]


class MobileSafetyTimeStep(NamedTuple):
    env_id: Any
    step_type: Any
    instruction: Any  # \in "Goal: {task name}"
    prev_obs: Any
    prev_act: Any  # \in [0, 1]^(4)
    curr_obs: Any  # \in [0, 1]^(256x128x3) (xml.etree iterator)
    curr_rew: Any  # Dict {'proficiency': float, 'safety': float}
    progress: Any

    def first(self):
        return self.step_type == StepType.FIRST

    def mid(self):
        return self.step_type == StepType.MID

    def last(self):
        return self.step_type == StepType.LAST

    def error(self):
        return self.step_type == StepType.ERROR

    def __getitem__(self, attr):
        if isinstance(attr, str):
            return getattr(self, attr)
        else:
            return tuple.__getitem__(self, attr)


class MobileSafetyEnv:

    def __init__(
        self,
        task_category: str = "",
        task_id: str = "",
        avd_name: str = "",
        avd_name_sub: str = "",
        port: int = None,
        appium_port: int = None,
        gui: bool = False,
        delay: float = 1,
        is_emu_already_open: bool = False,
        prompt_mode: str = "",
    ):

        # task variables
        self.task_category = task_category
        self.task_id = task_id

        self.task_setting = load_task(
            Path(_WORK_PATH) / "asset" / "tasks",
            task_category,
            task_id,
        )

        self.instruction = self.task_setting["instruction"]

        # device variables
        self.avd_name = avd_name
        self.avd_name_sub = avd_name_sub
        self.gui = gui
        self.delay = delay

        # launch emulator
        if is_emu_already_open:
            self.port = port
        else:
            self.port = port if port else utils.get_port(5554, 6000, 2)
            utils.run_emulator(self.avd_name, self.port, self.gui)
        self.width, self.height = adb_lib.get_screen_size(self.port)

        # appium
        if appium_port:
            self.appium_port = appium_port
        else:
            self.appium_port = utils.get_port(4723, 5000, 1)
        self.appium_process, self.appium_servertime = appium_lib.launch_server(
            self.appium_port
        )

        # agent variables
        self.prompt_mode = prompt_mode
        self.note = ""
        self.plan = ""

        # environment variables
        self.progress = None
        self.prev_obs = None
        self.prev_act = None
        self.curr_obs = None
        self.curr_reward = None
        self.action_error = 0

        # additional task settings
        self.task_setting["avd_name"] = self.avd_name
        self.task_setting["avd_name_sub"] = self.avd_name_sub
        self.task_setting["adb_port"] = port
        self.task_setting["driver"] = None
        self.task_setting["appium_port"] = self.appium_port
        self.task_setting["gui"] = self.gui

        return

    def _adb_cmd(self):
        adb_cmd = f"{os.environ.get('ANDROID_SDK_ROOT', '')}/platform-tools/adb"
        if not os.path.isfile(adb_cmd):
            adb_cmd = "adb"
        return adb_cmd

    def _wait_for_android_ready(self, timeout_s=180):
        adb_cmd = self._adb_cmd()
        device = f"emulator-{self.port}"
        deadline = time.time() + timeout_s
        required_services = ("settings", "activity", "package", "input")
        last_error = ""

        while time.time() < deadline:
            try:
                subprocess.run(
                    [adb_cmd, "-s", device, "wait-for-device"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                boot = subprocess.run(
                    [adb_cmd, "-s", device, "shell", "getprop", "sys.boot_completed"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if boot.stdout.strip() != "1":
                    last_error = f"sys.boot_completed={boot.stdout.strip()!r}"
                    time.sleep(2)
                    continue

                service = subprocess.run(
                    [adb_cmd, "-s", device, "shell", "service", "list"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                services_ok = all(
                    f"\t{name}:" in service.stdout or f"{name}:" in service.stdout
                    for name in required_services
                )
                if services_ok:
                    time.sleep(2.0)
                    return
                last_error = "missing Android services"
            except Exception as exc:
                last_error = str(exc)
            time.sleep(2)

        raise RuntimeError(
            f"Android framework not ready on {device} after {timeout_s}s: {last_error}"
        )

    def reset(self, snapshot_name="test_env_100"):
        self.app_initailized = False
        self.snapshot_name = snapshot_name
        self.prev_act = None
        self.action_error = 0

        last_exc = None
        skip_snapshot = str(snapshot_name).lower() in {"", "none", "no_snapshot", "skip"}
        for attempt in range(1, 4):
            # Snapshot restore can report success before Android framework services
            # are usable. Appium bootstrap needs these services to be alive.
            try:
                if not skip_snapshot:
                    if attempt > 1:
                        print(
                            f"[reset] retrying snapshot restore "
                            f"({attempt}/3) for emulator-{self.port}",
                            flush=True,
                        )
                    utils.emulator_snaphot_load(
                        adb_port=self.port, snapshot_name=snapshot_name
                    )
                self._wait_for_android_ready(timeout_s=300)
                self.set_environment()
                timestep = self.get_state(reset=True)
                last_exc = None
                return timestep
            except Exception as exc:
                last_exc = exc
                print(
                    f"[reset] Android/Appium not ready on emulator-{self.port}: {exc}",
                    flush=True,
                )
                if not skip_snapshot:
                    print(
                        f"[reset] falling back without snapshot restore on emulator-{self.port}",
                        flush=True,
                    )
                    skip_snapshot = True
                if skip_snapshot:
                    continue
                time.sleep(5)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"reset failed on emulator-{self.port}")

    def step(self, action=None):
        if not action is None:
            action = parse_action(  # action is string
                env=self,
                action=action,
                elem_list=self.parsed_obs,
                bbox_list=self.parsed_bbox,
                xpath_list=self.xpath_list,
            )
            self.action_error = 0
        else:
            self.action_error += 1
            if self.action_error == 3:
                self.action_error = 0

        self.prev_act = action

        # for robust transition
        time.sleep(self.delay)
        self.background_action()

        # return
        timestep = self.get_state(reset=False)
        return timestep

    def set_environment(self):
        # set time
        time.sleep(1.0)
        curr_time = utils.get_time()
        adb_lib.set_time(self.port, curr_time)
        time.sleep(1.0)

        # set appium
        self._wait_for_android_ready(timeout_s=120)
        self.driver = appium_lib.launch_driver(
            adb_port=self.port,
            appium_port=self.appium_port,
            driver_attempts=20,
        )
        if self.driver is None:
            raise RuntimeError(f"Failed to create Appium driver for emulator-{self.port}")

        # task setting
        initializer.reset_task_state(self.task_setting, self.task_category, self.driver)

        link_driver_again = False
        for initialize_type in self.task_setting["initial_device_status"]["types"]:
            if initialize_type == "text_message":
                link_driver_again = initializer.init_text_message(
                    self.task_setting, self.driver
                )
            if initialize_type == "new_message":
                link_driver_again = initializer.init_new_message(
                    self.task_setting, self.driver
                )
            if initialize_type == "memo":
                if self.task_category in ["memo_rephrasing", "memo_completing"]:
                    link_driver_again = initializer.init_editable_memo(
                        self.task_setting, self.driver
                    )
                else:
                    link_driver_again = initializer.init_memo(
                        self.task_setting, self.driver
                    )
            if initialize_type == "social_media":
                link_driver_again = initializer.init_social_media(
                    self.task_setting, self.driver
                )
            if initialize_type == "photo":
                link_driver_again = initializer.init_photo(
                    self.task_setting, self.driver
                )
            if initialize_type == "calendar":
                link_driver_again = initializer.init_calendar(
                    self.task_setting, self.driver
                )
            if initialize_type == "stocks":
                link_driver_again = initializer.init_stocks(
                    self.task_setting, self.driver
                )
            if initialize_type == "banking":
                link_driver_again = initializer.init_bank(
                    self.task_setting, self.driver
                )
            if initialize_type == "application":
                link_driver_again = initializer.install_application(
                    self.task_setting, self.driver
                )
            if initialize_type == "password":
                link_driver_again = initializer.init_password(
                    self.task_setting, self.driver
                )

            if link_driver_again:
                self._wait_for_android_ready(timeout_s=120)
                self.driver = appium_lib.launch_driver(
                    adb_port=self.port,
                    appium_port=self.appium_port,
                    driver_attempts=20,
                )
                if self.driver is None:
                    raise RuntimeError(
                        f"Failed to recreate Appium driver for emulator-{self.port}"
                    )
            self.task_setting["driver"] = self.driver

        # evaluator setting
        module_name = f"mobile_safety.evaluator.{self.task_category}"
        self.evaluator = getattr(importlib.import_module(module_name), "Evaluator")(
            task_setting=self.task_setting, adb_port=self.port
        )

        return

    def get_state(self, reset=False):
        # get observation
        xml_file, text_obs = appium_lib.get_viewhierarchy(self.driver)
        pixel_obs = appium_lib.get_screenshot(self.driver)
        self.curr_obs = {"text": text_obs, "pixel": pixel_obs}
        self.curr_obs["text_raw"] = xml_file

        # parse view hierarchy
        parsed_data = parse_obs(
            obs=self.curr_obs["text"],
            height_width=(self.height, self.width),
            attribute_check=True,
        )
        self.curr_obs["text"], self.parsed_obs, self.parsed_bbox, self.xpath_list = (
            parsed_data
        )

        # evaluate
        if (
            self.action_error == 0
        ):  # step increase only when action is valid or third error
            self.progress, self.curr_rew = self.evaluator.evaluate(
                action=self.prev_act, observation=self.curr_obs, driver=self.driver
            )

        # set steptype
        if reset:
            steptype = StepType.FIRST
        elif (
            self.progress["finished"]
            or (self.prev_act == "complete()")
            or (self.prev_act == "refuse()")
        ):
            steptype = StepType.LAST
        elif self.action_error > 0:
            steptype = StepType.ERROR
        else:
            steptype = StepType.MID

        # set timestep
        timestep = MobileSafetyTimeStep(
            env_id=None,
            step_type=steptype,
            instruction=None,
            prev_obs=self.prev_obs,
            prev_act=self.prev_act,
            curr_obs=self.curr_obs,
            curr_rew=self.curr_rew,
            progress=self.evaluator.progress,
        )
        self.prev_obs = self.curr_obs

        return timestep

    def close(self):
        # utils.emulator_snaphot_load(
        #     adb_port=self.port, snapshot_name=self.snapshot_name
        # )

        # utils.close_emulator(f"emulator-{self.port}")

        try:
            self.driver.quit()
        except:
            pass

        try:
            self.appium_process.kill()
            time.sleep(1)
        except:
            pass

        command = (
            f"kill -9 $(lsof -iTCP:{self.port + 1}"
            + "| awk '/qemu-syst/ && /LISTEN/ {print $2}' | head -n 1)"
        )
        try:
            subprocess.run(command, shell=True, check=True)
            print("qemu-syst process quit.")
        except subprocess.CalledProcessError as e:
            print("qemu-syst process quit error:", e)

    def background_action(self):
        """
        This function is used to restore the initialization for applications,
        where the initialization may be randomly removed
        """
        if self.task_category.startswith("banking") or ("test" in self.task_category):
            from mobile_safety.utils.bank import auto_login

            time.sleep(0.5)
            auto_login(self.port, self.driver, "Jeffrey@gmail.com", "1234")

        if (
            "map" in self.task_category
            and "maps" in self.driver.current_package
            and (not self.app_initailized)
        ):
            from mobile_safety.utils.maps import auto_login

            auto_login_success = auto_login(self.port, self.driver)
            self.app_initailized = auto_login_success

        if "memo" in self.task_category:
            from mobile_safety.utils.memo import tap_memo, tap_location

            time.sleep(0.5)
            tap_memo(driver=self.driver)
            time.sleep(0.5)
            tap_location(driver=self.driver)

        if ("message_send_repetitive" in self.task_category) and (
            "2" in self.task_category
        ):
            from mobile_safety.utils.memo import tap_memo, tap_location

            time.sleep(0.5)
            tap_location(driver=self.driver)

        if "sns" in self.task_category:
            from mobile_safety.utils.sns import pass_crop

            time.sleep(0.5)
            pass_crop(driver=self.driver)
            time.sleep(1)

        if "stock" in self.task_category:
            from mobile_safety.utils.stock import pass_advertisement

            time.sleep(0.5)
            pass_advertisement(driver=self.driver)
            time.sleep(1)

        if "photo_sharing" in self.task_category:
            from mobile_safety.utils.photos import initial_setting, bluetooth_setting

            if not self.app_initailized:
                time.sleep(10.0)
                if "photos" in self.driver.current_package:
                    self.app_initailized = initial_setting(
                        driver=self.driver, port=self.port
                    )
            bluetooth_setting(driver=self.driver, port=self.port)

        return

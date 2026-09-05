import os
import csv
import glob
import json
import time
import argparse
import subprocess
import sys
from PIL import Image
from tqdm import tqdm

# scripts for app settigns
from script.snapseed_init import snapseed_init 
from script.wikipedia_init import wikipedia_init
from script.chrome_init import chrome_init
from script.instagram_login import login_instagram
from script.walmart_init import walmart_init


_WORK_PATH = os.environ["BMOCA_HOME"]
_LOG_PATH = f"{_WORK_PATH}/logs"
_CONFIG_PATH = f"{_WORK_PATH}/asset/environments/config"
_SCRIPT_PATH = f"{_WORK_PATH}/asset/environments/script"
_RESOURCE_PATH = f"{_WORK_PATH}/asset/environments/resource"
_APPIUM_PATH = f"{os.environ['HOME']}/.nvm/versions/node/v18.12.1/bin/appium"


def convert_jpg_to_bmp():
    src_path = f"{_RESOURCE_PATH}/wallpapers_jpg"
    dst_path = f"{_RESOURCE_PATH}/wallpapers_bmp"

    if not os.path.isdir(dst_path):
        os.makedirs(dst_path)

    for jpg in tqdm(list(set(glob.glob(src_path + "*/*.jpg", recursive=True)))):
        img = Image.open(jpg)

        jpg_name = jpg.replace("\\", "/").split("/")[-1]
        bmp_name = jpg_name.replace("jpg", "bmp")

        img.save(f"{dst_path}/{bmp_name}")


class EnvBuilder:
    def __init__(self, parser, avd_name):
        self.avd_name = avd_name
        self.base_port = parser.port
        self.port = {}
        self.device_type = {}
        self.icon_sizes = {}
        self.screen_sizes = {}
        self.dpi = [1.0, 1.15, 0.85, 1.15, 0.85] # predefine variable on icon size (72~840)
        self.log_dir = parser.log_dir
        self.mode = parser.mode
        self.env_ids = set(parser.env_ids.split(',')) if parser.env_ids else None

        # load_env_list
        self.target_env_dir = f"{_CONFIG_PATH}/environments_{self.mode}.csv"

        self.usable_device = []
        with open(self.target_env_dir, mode="r", encoding="utf-8") as f:
            env = csv.DictReader(f)
            for row in env:
                if self.env_ids and row["idx"] not in self.env_ids:
                    continue
                if row["device_id"] in self.usable_device:
                    continue
                else:
                    self.usable_device.append(row["device_id"])

        # load_device_setting
        tmp = []
        self.device_code = {}
        self.system_image = {}

        with open(f"{_CONFIG_PATH}/device_property.json", "r") as dp:
            device_info = json.load(dp)
            for idx, device in enumerate(device_info["device_info"]):
                if device["device_id"] not in self.usable_device:
                    continue
                tmp.append(device)

                self.port[device["device_id"]] = self.base_port + idx * 2

                self.screen_sizes[device["device_id"]] = [device["physical_setting"][0], device["physical_setting"][1]]
                self.icon_sizes[device["device_id"]] = device["icon_size"]
                self.device_type[device["device_id"]] = device["category"]
                self.device_code[device["device_id"]] = device["device_code"]
                self.system_image[device["device_id"]] = device["system_image"]

            self.device_info = tmp
            
            with open(f"{_CONFIG_PATH}/account_info.json", "r") as ac:
                self.account_info = json.load(ac)


    def _avd_instance_name(self, device_id):
        if self.avd_name.startswith(f"{device_id}_"):
            return self.avd_name
        return f"{device_id}_{self.mode}_{self.avd_name}"


    def emulator_on(self, device_id):
        """turn on all devices"""
        avd_name = self._avd_instance_name(device_id)
        cmd = (
            f"/bin/bash {_SCRIPT_PATH}/emulator_on.sh {avd_name} {self.port[device_id]}"
        )

        result = subprocess.run(cmd, text=True, shell=True)
        print('\n\n', result, '\n')

        if result.returncode != 0:
            sys.exit()

        time.sleep(5)


    def emulator_off(self, device_id):
        """turn off all devices"""
        cmd = f"/bin/bash {_SCRIPT_PATH}/emulator_off.sh {self.port[device_id]}"
        _ = subprocess.run(cmd, text=True, shell=True)


    def permission_init(self, id):
        command = f'adb -s emulator-{self.port[id]} shell wm size 1080x2160'
        _ = subprocess.run(command, text=True, shell=True)
        time.sleep(3)
        command = f'adb -s emulator-{self.port[id]} shell wm density 440'
        _ = subprocess.run(command, text=True, shell=True)            
        time.sleep(3)
        command = f'/bin/bash {_SCRIPT_PATH}/permission_init.sh {self.port[id]}'
        _ = subprocess.run(command, text=True, shell=True)
        command = f'adb -s emulator-{self.port[id]} shell wm size reset'
        _ = subprocess.run(command, text=True, shell=True)
        time.sleep(3)
        command = f'adb -s emulator-{self.port[id]} shell wm density reset'
        _ = subprocess.run(command, text=True, shell=True)            
        time.sleep(3)


    def snapshot_save(self, device_id, snapshot_name):
        if snapshot_name == "init": # default
            cmd = (
                f"/bin/bash {_SCRIPT_PATH}/snapshot_save.sh {snapshot_name} {self.port[device_id]}"
            )
        else:
            cmd = (
                f"/bin/bash {_SCRIPT_PATH}/snapshot_save.sh {snapshot_name} {self.port[device_id]}"
            )
        _ = subprocess.run(cmd, text=True, shell=True)


    def snapshot_load(self, device_id, snapshot_name="init"):
        cmd = (
            f"/bin/bash {_SCRIPT_PATH}/snapshot_load.sh {snapshot_name} {self.port[device_id]}"
        )
        _ = subprocess.run(cmd, text=True, shell=True)


    def set_root_previlage(self, device_id):
        cmd = f"adb -s emulator-{self.port[device_id]} root"
        _ = subprocess.run(cmd, text=True, shell=True)


    def set_wallpaper(self, device_id, wallpaper_name):
        if self.device_type[device_id] != 'pixel':
            wallpaper_jpg = f'{wallpaper_name}.jpg'
            cmd = f"/bin/bash {_SCRIPT_PATH}/set_wallpaper_jpg.sh {wallpaper_jpg} {self.port[device_id]}"
            _ = subprocess.run(cmd, text=True, shell=True)
        else:
            wallpaper_bmp = f'{_RESOURCE_PATH}/wallpapers_bmp/{wallpaper_name}.bmp'
            cmd = f"/bin/bash {_SCRIPT_PATH}/set_wallpaper_bmp.sh {wallpaper_bmp} {self.port[device_id]}"
            _ = subprocess.run(cmd, text=True, shell=True)


    def set_locale(self, device_id, locale='en-US'):
        cmd = f"/bin/bash {_SCRIPT_PATH}/set_locale.sh {locale} {self.port[device_id]}"
        _ = subprocess.run(cmd, text=True, shell=True)
        time.sleep(30)


    def set_dpi(self, device_id, size):
        cmd = f"/bin/bash {_SCRIPT_PATH}/set_dpi.sh {self.icon_sizes[device_id][int(size)]} {self.dpi[int(size)]} {self.port[device_id]}"
        _ = subprocess.run(cmd, text=True, shell=True)
        time.sleep(3)


    def set_darkmode(self, device_id):
        cmd = f'adb -s emulator-{self.port[device_id]} shell "su 0 cmd uimode night yes"'
        _ = subprocess.run(cmd, text=True, shell=True)
        time.sleep(5)
        
        
    def set_alarm(self, device_id):
        cmd = f'/bin/bash {_SCRIPT_PATH}/set_alarm.sh {_RESOURCE_PATH}/alarm_db/alarms.db {self.port[device_id]}'
        _ = subprocess.run(cmd, text=True, shell=True)
        self.set_root_previlage(device_id) # after the alarm is set, root privilege is reset.
        return
        
        
    def apk_install(self, device_id, apk_dir):
        for apk_file in os.listdir(apk_dir):
            apk_path = os.path.join(apk_dir, apk_file)
            cmd = f'adb -s emulator-{self.port[device_id]} install -r {apk_path}'
            result = subprocess.run(cmd, text=True, shell=True, capture_output=True)
            if result.returncode != 0 or 'Failure' in result.stdout or 'Failure' in result.stderr:
                print(f'[WARN] apk_install: skipping {apk_file} — {result.stdout.strip()} {result.stderr.strip()}')
            time.sleep(3)
            
            
    def apk_disable(self, device_id):
        # Drive app
        cmd = f'adb -s emulator-{self.port[device_id]} shell pm disable-user --user 0 com.google.android.apps.docs'
        _ = subprocess.run(cmd, text=True, shell=True)
        time.sleep(1)
        # Play Video app
        cmd = f'adb -s emulator-{self.port[device_id]} shell pm disable-user --user 0 com.google.android.videos'
        _ = subprocess.run(cmd, text=True, shell=True)
        time.sleep(1)
        # Play Music app
        cmd = f'adb -s emulator-{self.port[device_id]} shell pm disable-user --user 0 com.google.android.music'
        _ = subprocess.run(cmd, text=True, shell=True)
        time.sleep(1)
        
        
    def initialize_apps(self, avd_name):
        # for app_name, account_info in self.account_info.items(): # sign-in instagram
        #     self.login_app(avd_name, app_name, account_info['id'], account_info['password'])
        #     print(f'success {app_name} sign-in')
        for app_name, init_fn in [
            ('chrome', chrome_init),
            ('snapseed', snapseed_init),
            ('wikipedia', wikipedia_init),
            ('walmart', walmart_init),
        ]:
            try:
                init_fn(avd_name)
            except Exception as e:
                print(f'[WARN] initialize_apps: {app_name} init failed, skipping — {e}')

        
    def login_app(self, avd_name, app_name, account_id, account_pw):
        if app_name == 'instagram':
            login_instagram(avd_name, account_id, account_pw)
        else:
            raise NotImplementedError(f'log-in for {app_name} is not implemented')
        

    def set_home_screen(self, device_id, snapshot_name):
        cmd = f'/bin/bash {_SCRIPT_PATH}/set_home_screen.sh {_RESOURCE_PATH}/home_screen_db/{snapshot_name}.db {self.port[device_id]}'
        _ = subprocess.run(cmd, text=True, shell=True)


    def set_sound(self, device_id, sound):
        cmd = f"/bin/bash {_SCRIPT_PATH}/set_sound.sh {sound} {self.port[device_id]}"
        _ = subprocess.run(cmd, text=True, shell=True)
        time.sleep(3)


    def set_brightness(self, device_id, set_brightness):
        cmd = f"/bin/bash {_SCRIPT_PATH}/set_brightness.sh {set_brightness} {self.port[device_id]}"
        _ = subprocess.run(cmd, text=True, shell=True)
        time.sleep(3)
        
 
    def screenshot_save(self, device_id, snapshot_name):
        if snapshot_name == "init": # default
            cmd = f"adb -s emulator-{self.port[device_id]} exec-out screencap -p > {self.log_dir}/init_{device_id}.png"
        else:
            cmd = f"adb -s emulator-{self.port[device_id]} exec-out screencap -p > {self.log_dir}/{snapshot_name}.png"

        _ = subprocess.run(cmd, text=True, shell=True)
        print("\n\nScreen shot takend : ", snapshot_name)
        time.sleep(1)


    def build_devices(self):
        """ make the snapshot of each environment """

        for device_info in self.device_info:
            avd_name = self._avd_instance_name(device_info["device_id"])
            cmd = f'/bin/bash {_SCRIPT_PATH}/avd_creation.sh {avd_name} "{self.system_image[device_info["device_id"]]}" {self.device_code[device_info["device_id"]]}'
            _ = subprocess.run(cmd, text=True, shell=True)

            device_id = device_info["device_id"]

            print(f"\n\navd {device_id} created")
            self.emulator_on(device_id)

            if self.device_type[device_id] != "pixel":
                self.permission_init(device_id)
            
            # disable unnecessary apps
            self.apk_disable(device_id)
            
            # appium server init
            appium_command = [_APPIUM_PATH]
            self.appium_process = subprocess.Popen(appium_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # install the downloaded apks (in asset/environments/resource/apks/*)
            self.apk_install(device_id, f"{_WORK_PATH}/asset/environments/resource/apks")
            
            # init settings of custom apps
            self.initialize_apps(avd_name)
            
            # add image for snapseed tasks (must include -s serial and trigger media scan)
            _port = self.port[device_id]
            command = f'adb -s emulator-{_port} push {_RESOURCE_PATH}/image/99_Colosseum.jpg /sdcard/Pictures/99_Colosseum.jpg'
            _ = subprocess.run(command, text=True, shell=True)
            time.sleep(1)
            # trigger media scanner so the image appears in gallery / file pickers
            command = (f'adb -s emulator-{_port} shell am broadcast '
                       f'-a android.intent.action.MEDIA_SCANNER_SCAN_FILE '
                       f'-d file:///sdcard/Pictures/99_Colosseum.jpg')
            _ = subprocess.run(command, text=True, shell=True)
            time.sleep(3)

            self.snapshot_save(device_id, "init")
            self.screenshot_save(device_id, "init")

            self.emulator_off(device_id)
            time.sleep(5)


    def build_environments(self, mode="train"):
        """ make the snapshot of each environment """
        csv_file = open(f"{_CONFIG_PATH}/environments_{mode}.csv", mode="r", encoding="utf-8")
        env_dict = csv.DictReader(csv_file)

        first_processed = True
        for idx, row in enumerate(tqdm(env_dict)):
            if self.env_ids and row["idx"] not in self.env_ids:
                continue
            device_id = row["device_id"]

            if first_processed:
                prev_device_id = device_id
                self.emulator_on(device_id)
                first_processed = False
            if prev_device_id != device_id:
                self.emulator_off(prev_device_id)
                time.sleep(5)
                self.emulator_on(device_id)
            snapshot_name = f'{self.mode}_env_{row["idx"].zfill(2)}'
            print(f"\n\nbuilding snapshot: {snapshot_name}")

            # load snapshot & set root previlage
            self.snapshot_load(device_id, "init")
            self.set_root_previlage(device_id)

            # modify environments
            self.set_locale(device_id, row["locale"])

            self.set_wallpaper(device_id, row["wallpaper"])

            self.set_dpi(device_id, row["dpi"])

            # set_alarm.sh and set_home_screen.sh both call `adb reboot`.
            # Any volume changes via set_sound would be wiped by a subsequent reboot.
            # Therefore set_sound MUST be called LAST — after all rebooting steps.
            self.set_alarm(device_id) #TODO: move to init_app

            if row["darkmode"] == "True":
                self.set_darkmode(device_id)

            self.set_home_screen(device_id, snapshot_name)

            # set_sound AFTER all reboots so volumes survive into the snapshot
            self.set_sound(device_id, sound=0)

            # save snapshot & screenshot
            self.snapshot_save(device_id, snapshot_name)
            self.screenshot_save(device_id, snapshot_name)
            
            # to save time for launching emulator
            prev_device_id = device_id

        self.emulator_off(prev_device_id)

        csv_file.close()


def parse_args():
    parser = argparse.ArgumentParser()
    # avd
    parser.add_argument("--avd_name", type=str, default=f"pixel3")
    parser.add_argument("--mode", type=str, default="train", help="['train', 'test']")
    parser.add_argument("--port", type=int, default=6555)
    parser.add_argument("--env_ids", type=str, default=None,
                        help="comma-separated env indices to build (e.g. '100,101,105'). Builds all envs if not set.")
    # log
    parser.add_argument("--log_dir", type=str, default=f"{_LOG_PATH}/environment")
    # skip AVD creation / app init (useful when AVD already exists)
    parser.add_argument("--skip_build_devices", action="store_true", default=False,
                        help="Skip build_devices() step; only rebuild environment snapshots")

    args = parser.parse_args()
    return args


def run(args):
    # modify resources
    convert_jpg_to_bmp()

    # turning on avd
    env_builder = EnvBuilder(args, args.avd_name)

    if not args.skip_build_devices:
        env_builder.build_devices()
    env_builder.build_environments(args.mode)


if __name__ == "__main__":
    args = parse_args()
    args.avd_name = args.avd_name.zfill(2)

    # logging directory
    log_dir = args.log_dir
    avd_log_dir = f"{log_dir}/{args.avd_name}"
    if not os.path.isdir(log_dir): os.makedirs(log_dir)
    if not os.path.isdir(avd_log_dir): os.makedirs(avd_log_dir)
    args.log_dir = avd_log_dir

    # main run
    run(args)

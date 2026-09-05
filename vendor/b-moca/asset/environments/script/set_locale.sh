#!/bin/bash
locale=$1
port=$2

# 1) system property (persist.sys.locale)
adb -s emulator-$port shell "setprop persist.sys.locale $locale"

# 2) Settings DB system_locales  (Android 7+ UI locale)
adb -s emulator-$port shell content delete --uri content://settings/system --where "name=\'system_locales\'"
adb -s emulator-$port shell content insert --uri content://settings/system --bind name:s:system_locales --bind value:s:$locale

# Allow SettingsProvider to flush the XML write to disk before killing Zygote
sleep 5

# 3) restart Zygote to apply locale to all running processes
adb -s emulator-$port shell "setprop ctl.restart zygote"

# Wait for Zygote to fully restart before returning
sleep 30

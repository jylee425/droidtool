#!/bin/bash
sound=$1
port=$2

adb -s emulator-$port shell media volume --show --stream 1 --set $sound
sleep 2
adb -s emulator-$port shell media volume --show --stream 3 --set $sound
sleep 2
# Set alarm volume via media command AND settings DB for reliability
# (media volume --stream 4 may silently fail on some Android 10 builds)
adb -s emulator-$port shell media volume --show --stream 4 --set $((sound + 1))
sleep 1
adb -s emulator-$port shell settings put system volume_alarm $((sound + 1))
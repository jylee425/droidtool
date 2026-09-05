#!/bin/bash

avd_name=$1
port=$2
max_attempts=20
attempts=0

# FATAL 방지: 기존 lock 파일 및 좀비 프로세스 정리
pkill -9 -f "qemu-system.*avd ${avd_name}" 2>/dev/null || true
sleep 2
find ~/.android/avd -name "*.lock" -delete 2>/dev/null || true

emulator -port $port -avd $avd_name -no-audio -no-window -no-skin \
    -no-snapshot-load -gpu "swiftshader_indirect" -feature -Vulkan &
# emulator -port $port -avd $avd_name -no-audio -no-skin -gpu "swiftshader_indirect" &
timeout 120 adb -s emulator-$port wait-for-device || { echo "Emulator did not appear within 120s. Aborting."; exit 1; }

until adb -s emulator-$port shell getprop sys.boot_completed | grep -m 1 "1"; do
    if [ $attempts -ge $max_attempts ]; then
        echo "Failed to start emulator after $max_attempts attempts."
        exit 1
    fi

    echo "Waiting for emulator to fully boot..."
    sleep 5
    attempts=$((attempts + 1))
done

sleep 10

echo "Emulator turn on complete"

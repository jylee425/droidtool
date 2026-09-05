#!/bin/bash
snapshot_name=$1
port=$2
if [ -z "$snapshot_name" ]; then
    echo "Usage: $0 <snapshot_name>"
    exit 1
fi
output=$(adb -s emulator-$port emu avd snapshot load $snapshot_name 2>&1)
printf '%s\n' "$output"
if printf '%s\n' "$output" | grep -q '^KO:'; then
    exit 1
fi

sleep 15
echo "Snapshot loaded as $snapshot_name"

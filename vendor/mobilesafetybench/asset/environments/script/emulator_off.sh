#!/bin/bash
#Turn off emulator
port=$1
echo "Emulator turn off start"
adb -s emulator-$port emu kill 2>/dev/null || true

# Wait for the emulator process to actually die (up to 60s)
for i in $(seq 1 30); do
    if ! pgrep -af "port $port" | grep -q "qemu-system" 2>/dev/null; then
        break
    fi
    sleep 2
done

# Force kill if still alive
pkill -9 -f "qemu-system.*port $port" 2>/dev/null || true
sleep 2

# Clean up stale lock files
find ~/.android/avd -name "*.lock" -delete 2>/dev/null || true
echo "Emulator turn off complete"

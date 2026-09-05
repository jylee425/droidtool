#!/usr/bin/env bash
# Build the shared benchmark AVD and its clean APK-installed baseline snapshot.
set -euo pipefail

PROJECT_PATH="${PROJECT_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
HOME_PATH="${HOME_PATH:-$HOME}"
ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$HOME_PATH/.local/share/android/sdk}"
ANDROID_AVD_HOME="${ANDROID_AVD_HOME:-$HOME_PATH/.android/avd}"
AVD_NAME="${AVD_NAME:-unified_environment}"
SNAPSHOT_NAME="${SNAPSHOT_NAME:-apk_installed_base}"
SERIAL="${SERIAL:-emulator-5554}"
ADB="$ANDROID_SDK_ROOT/platform-tools/adb"
EMULATOR="$ANDROID_SDK_ROOT/emulator/emulator"
AVDMANAGER="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/avdmanager"
IMAGE="system-images;android-34;google_apis;x86_64"

cd "$PROJECT_PATH"
export ANDROID_SDK_ROOT ANDROID_HOME="$ANDROID_SDK_ROOT" ANDROID_AVD_HOME

if [[ ! -d "$ANDROID_AVD_HOME/$AVD_NAME.avd" ]]; then
  echo no | "$AVDMANAGER" --silent create avd -n "$AVD_NAME" -k "$IMAGE" -d pixel_7
fi

cfg="$ANDROID_AVD_HOME/$AVD_NAME.avd/config.ini"
sed -i -E 's/^hw\.keyboard[[:space:]]*=.*/hw.keyboard=yes/' "$cfg"

"$EMULATOR" -avd "$AVD_NAME" -no-snapshot-load -no-window -no-audio \
  -no-boot-anim -gpu swiftshader_indirect -port 5554 -grpc 8554 \
  >"/tmp/${AVD_NAME}_setup.log" 2>&1 &
emu_pid=$!
cleanup() { "$ADB" -s "$SERIAL" emu kill >/dev/null 2>&1 || true; wait "$emu_pid" 2>/dev/null || true; }
trap cleanup EXIT
"$ADB" -s "$SERIAL" wait-for-device
for _ in $(seq 1 180); do
  [[ "$("$ADB" -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]] && break
  sleep 2
done
[[ "$("$ADB" -s "$SERIAL" shell getprop sys.boot_completed | tr -d '\r')" == 1 ]]

install_apk() {
  local apk="$1"; shift
  "$ADB" -s "$SERIAL" install "$@" -r -d -g "$apk" >/dev/null
  echo "[installed] $apk"
}
for apk in asset/android_world/apks/*.apk; do
  [[ "$apk" == *clipper.apk ]] && install_apk "$apk" --bypass-low-target-sdk-block || install_apk "$apk"
done
for apk in Calculator_univ.apk Instagram_arm64.apk Snapseed_arm64.apk Walmart_univ.apk Wikipedia_univ.apk; do
  install_apk "asset/b_moca/apks/$apk"
done
for apk in asset/mobilesafetybench/apks/*.apk; do install_apk "$apk"; done

# Common media fixture. Benchmark-specific mutable state is initialized per task.
fixture="logs/manual_tool_verification/osmand_after_tools.png"
if [[ -f "$fixture" ]]; then
  "$ADB" -s "$SERIAL" shell mkdir -p /sdcard/Download /sdcard/Pictures
  "$ADB" -s "$SERIAL" push "$fixture" /sdcard/Download/test.png >/dev/null
  "$ADB" -s "$SERIAL" push "$fixture" /sdcard/Pictures/test.png >/dev/null
fi
"$ADB" -s "$SERIAL" shell input keyevent HOME
"$ADB" -s "$SERIAL" emu avd snapshot delete "$SNAPSHOT_NAME" >/dev/null 2>&1 || true
"$ADB" -s "$SERIAL" emu avd snapshot save "$SNAPSHOT_NAME"
sleep 5
"$ADB" -s "$SERIAL" emu avd snapshot list
echo "[done] AVD=$AVD_NAME snapshot=$SNAPSHOT_NAME"

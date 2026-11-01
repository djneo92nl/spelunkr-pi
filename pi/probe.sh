#!/bin/sh
# Find out what THIS Pi can do. Run it on the Pi (SSH is fine) with the TV attached. Takes a couple of minutes. Needs network.
# Usage: sh pi/probe.sh [url]
# Part 1 describes the board and the graphics set-up, part 2 measures decoding with no display (works even if the picture does not yet),
# part 3 tries the real video outputs (needs a DRM device: see INSTALL-PI.md step 6).
URL="${1:-https://archive.org/download/education_2/education_2.mp4}"
CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
CMD=/boot/firmware/cmdline.txt; [ -f "$CMD" ] || CMD=/boot/cmdline.txt
section() { echo; echo "== $1 =="; }

section "board"
tr -d '\0' </proc/device-tree/model; echo
grep -m1 PRETTY_NAME /etc/os-release
uname -srm
free -m | sed -n '1,2p'
if command -v vcgencmd >/dev/null; then
  vcgencmd get_mem arm; vcgencmd get_mem gpu; vcgencmd measure_temp; vcgencmd get_throttled
fi

section "graphics (--vo=drm needs a DRM device and KMS)"
ls -l /dev/dri 2>&1
echo "drm connectors:"; ls /sys/class/drm 2>&1
echo "--- $CFG (active lines)"; grep -v '^#' "$CFG" | grep -v '^$'
echo "--- $CMD"; cat "$CMD"
echo "--- kernel messages about the display"
(dmesg 2>/dev/null || sudo dmesg 2>/dev/null) | grep -i -E 'vc4|drm|v3d|fb0|composite' | tail -15

section "hardware video decoder devices"
ls -l /dev/video* 2>&1 | head
command -v v4l2-ctl >/dev/null && v4l2-ctl --list-devices 2>&1 | head -20

section "mpv"
mpv --version | head -1
echo "hwdec options that matter here:"; mpv --hwdec=help | grep -i -E 'v4l2|drm|rpi|mmal' || echo "  (none of v4l2m2m / drm / rpi listed)"
echo "video outputs:"; mpv --vo=help | grep -E '^ +(drm|gpu|gpu-next|fbdev|sdl|null) '

section "decoding speed, no display (--vo=null, as fast as it can). 25 fps or more means real time is possible"
for h in no v4l2m2m-copy; do
  echo "--- hwdec=$h"
  mpv --no-config --vo=null --no-audio --untimed --hwdec="$h" --length=20 --start=60 \
      --msg-level=all=warn,vd=v,statusline=status \
      --term-status-msg='fps=${estimated-vf-fps} dropped=${decoder-frame-drop-count}' "$URL" 2>&1 \
    | grep -E 'Using hardware|hwdec|Failed|Error|error|fps=' | tail -4
done

section "real video output (needs a DRM device)"
if [ -e /dev/dri/card0 ] || ls /dev/dri/card* >/dev/null 2>&1; then
  for combo in "drm v4l2m2m-copy" "drm no" "gpu v4l2m2m-copy" "gpu no"; do
    set -- $combo
    echo; echo "=== vo=$1 hwdec=$2 (15 s) ==="
    mpv --no-config --vo="$1" --hwdec="$2" --length=15 --start=60 --no-audio \
        --msg-level=all=warn,vd=v,vo=v,statusline=status \
        --term-status-msg='dropped=${decoder-frame-drop-count}/${frame-drop-count} fps=${estimated-vf-fps}' "$URL" 2>&1 \
      | grep -E "Using hardware|hwdec|Failed|Error|error|dropped=" | sort | uniq -c | sort -rn | head -8
  done
  echo; echo "Pick the combination that says 'Using hardware decoding' (or runs clean in software) with few dropped frames,"
  echo "then edit profiles.pi.mpv in config.json."
else
  echo "There is NO DRM device (/dev/dri is empty), so mpv has no way to show video: the KMS display driver is not active."
  echo "Fix: enable it in $CFG (dtoverlay=vc4-kms-v3d,composite) and reboot; see INSTALL-PI.md step 6. Then run this again."
fi

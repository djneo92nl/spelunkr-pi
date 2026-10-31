#!/bin/sh
# Find which mpv video output + hardware decoder works on THIS Pi. Run it on the Pi (SSH is fine) with the TV attached.
# Usage: pi/probe.sh [url]     Takes ~15 s per combination. Needs network.
URL="${1:-https://archive.org/download/education_2/education_2.mp4}"
echo "== board =="; tr -d '\0' </proc/device-tree/model; echo
command -v vcgencmd >/dev/null && { vcgencmd get_mem gpu; vcgencmd measure_temp; }
echo; echo "== mpv =="; mpv --version | head -1
echo; echo "== available hwdec =="; mpv --hwdec=help | head -20
echo; echo "== available vo =="; mpv --vo=help | head -20
echo; echo "== DRM connectors =="; ls /sys/class/drm 2>/dev/null
for combo in "drm v4l2m2m-copy" "drm no" "gpu v4l2m2m-copy" "gpu no"; do
  set -- $combo
  echo; echo "=== vo=$1 hwdec=$2 (15 s) ==="
  mpv --no-config --vo="$1" --hwdec="$2" --length=15 --start=60 --no-audio \
      --msg-level=all=warn,vd=v,vo=v,statusline=status \
      --term-status-msg='dropped=${decoder-frame-drop-count}/${frame-drop-count} fps=${estimated-vf-fps}' "$URL" 2>&1 \
    | grep -E "Using hardware|hwdec|Failed|Error|error|dropped=" | sort | uniq -c | sort -rn | head -8
done
echo
echo "Pick the combination that says 'Using hardware decoding' with low dropped=, then edit profiles.pi.mpv in config.json."

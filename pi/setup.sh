#!/bin/sh
# Install the spelunkr.tv client on Raspberry Pi OS Lite (32-bit).
# Run on the Pi from the project folder:   sudo pi/setup.sh [--apply-boot-config]
set -e
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")/.." && pwd)"
apt-get update
apt-get install -y mpv python3 ca-certificates cec-utils
mkdir -p /opt/spelunkr-tv
cp "$HERE"/tv.py "$HERE"/schedule.py "$HERE"/osd.py "$HERE"/mpvctl.py "$HERE"/inputs.py "$HERE"/webremote.py "$HERE"/subs.py "$HERE"/prefetch.py "$HERE"/config.json /opt/spelunkr-tv/
chown -R pi:pi /opt/spelunkr-tv
install -m 644 "$HERE"/pi/spelunkr-tv.service /etc/systemd/system/spelunkr-tv.service
systemctl daemon-reload
echo "Service installed but not started. Start it with:  sudo systemctl enable --now spelunkr-tv"

if [ "$1" = "--apply-boot-config" ]; then
  CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
  cp "$CFG" "$CFG.bak-spelunkr"
  cat >> "$CFG" <<'EOC'

# --- spelunkr.tv: PAL composite (legacy firmware video path) ---
sdtv_mode=2
sdtv_aspect=1
disable_overscan=1
enable_tvout=1
EOC
  echo "Appended PAL composite lines to $CFG (backup: $CFG.bak-spelunkr). Reboot to apply."
else
  echo "Boot config NOT changed. See README 'Composite PAL', or re-run with --apply-boot-config."
fi

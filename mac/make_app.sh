#!/bin/sh
# Build "Spelunkr TV.app" (double-click launcher with an icon).   Usage: sh mac/make_app.sh [destination-folder]
# The app contains a copy of the client; run this again after changing the code.
# Needs: mpv (brew install mpv). Optional: rumps (pip3 install rumps) for the menu bar item.
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HERE/dist}"
APP="$DEST/Spelunkr TV.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app/mac"

# the client
cp "$HERE"/tv.py "$HERE"/schedule.py "$HERE"/osd.py "$HERE"/mpvctl.py "$HERE"/inputs.py "$HERE"/webremote.py \
   "$HERE"/config.json "$APP/Contents/Resources/app/"
cp "$HERE"/mac/menubar.py "$APP/Contents/Resources/app/mac/"

# icon
ICONSET="$(mktemp -d)/AppIcon.iconset"
python3 "$HERE/mac/make_icon.py" "$ICONSET"
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Spelunkr TV</string>
  <key>CFBundleDisplayName</key><string>Spelunkr TV</string>
  <key>CFBundleIdentifier</key><string>local.spelunkr-pi.client</string>
  <key>CFBundleExecutable</key><string>spelunkr-tv</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF

# launcher: apps start with a minimal PATH, so add Homebrew; prefer a python3 that has rumps (menu bar), else plain tv.py
cat > "$APP/Contents/MacOS/spelunkr-tv" <<'EOF'
#!/bin/sh
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
APPDIR="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
LOG="$HOME/Library/Logs/spelunkr-tv.log"
exec >>"$LOG" 2>&1
echo "--- $(date) start"
for PY in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -x "$PY" ] && "$PY" -c "import rumps" 2>/dev/null && exec "$PY" "$APPDIR/mac/menubar.py" "$@"
done
for PY in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -x "$PY" ] && exec "$PY" "$APPDIR/tv.py" "$@"
done
echo "python3 not found"; exit 1
EOF
chmod +x "$APP/Contents/MacOS/spelunkr-tv"

echo "Built: $APP"
echo "Open it with:  open \"$APP\"     (log: ~/Library/Logs/spelunkr-tv.log)"

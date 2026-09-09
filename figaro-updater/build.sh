#!/bin/bash
# Build the FigaroUpdater menu-bar helper into a bundled .app.
set -euo pipefail
cd "$(dirname "$0")"

APP=FigaroUpdater.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

swiftc -O -o "$APP/Contents/MacOS/FigaroUpdater" \
  FigaroUpdater.swift -framework Cocoa -framework UserNotifications

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.figaro.updater</string>
  <key>CFBundleName</key><string>FigaroUpdater</string>
  <key>CFBundleDisplayName</key><string>Figaro Updater</string>
  <key>CFBundleExecutable</key><string>FigaroUpdater</string>
  <key>CFBundleIconFile</key><string>figaro.icns</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

cp figaro.icns "$APP/Contents/Resources/figaro.icns"
codesign --force --sign - "$APP"
echo "built $APP"
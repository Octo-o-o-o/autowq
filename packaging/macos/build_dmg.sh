#!/usr/bin/env bash
# Build dist/WorldQuant-<version>.dmg: universal2 menubar app + engine wheel + first-run helper.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c "import re; print(re.search(r'^version = \"(.+?)\"', open('pyproject.toml').read(), re.M).group(1))")"
DIST="$ROOT/dist"
BUILD="$DIST/build"
APP="$DIST/WorldQuant.app"
WHEELS="$BUILD/wheels"
rm -rf "$APP" "$BUILD"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$WHEELS"

echo "==> Building engine wheel"
python3 -m pip wheel --no-deps -w "$WHEELS" "$ROOT"

echo "==> Compiling WorldQuantMenu (arm64 + x86_64)"
for arch in arm64 x86_64; do
    swiftc macos/WorldQuantMenu.swift -o "$BUILD/WorldQuantMenu-$arch" \
        -framework AppKit -target "$arch-apple-macosx12.0"
done
lipo -create -output "$APP/Contents/MacOS/WorldQuantMenu" \
    "$BUILD/WorldQuantMenu-arm64" "$BUILD/WorldQuantMenu-x86_64"
lipo -info "$APP/Contents/MacOS/WorldQuantMenu"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleExecutable</key>
	<string>WorldQuantMenu</string>
	<key>CFBundleIdentifier</key>
	<string>com.worldquant.wq-menu</string>
	<key>CFBundleIconFile</key>
	<string>WorldQuant</string>
	<key>CFBundleName</key>
	<string>WorldQuant</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>${VERSION}</string>
	<key>CFBundleVersion</key>
	<string>${VERSION}</string>
	<key>LSMinimumSystemVersion</key>
	<string>12.0</string>
	<key>LSUIElement</key>
	<true/>
</dict>
</plist>
EOF

cp macos/Assets/ResearchIcon.png "$APP/Contents/Resources/ResearchIcon.png"
cp packaging/macos/WorldQuant.icns "$APP/Contents/Resources/WorldQuant.icns"
cp "$WHEELS"/wq_pilot-*.whl "$APP/Contents/Resources/"
cp packaging/macos/app_setup.py "$APP/Contents/Resources/app_setup.py"

echo "==> Signing"
if [ -n "${MACOS_SIGN_IDENTITY:-}" ]; then
    codesign --force --options runtime --timestamp --sign "$MACOS_SIGN_IDENTITY" "$APP"
else
    echo "    MACOS_SIGN_IDENTITY 未设置，使用 adhoc 签名"
    codesign --force --sign - "$APP"
fi

DMG="$DIST/WorldQuant-${VERSION}.dmg"
RW="$BUILD/rw.dmg"
MOUNT="$BUILD/mount"
rm -f "$DMG" "$RW"
mkdir -p "$MOUNT"

echo "==> Creating DMG"
hdiutil create -size 100m -fs HFS+ -volname "WorldQuant" "$RW" >/dev/null
hdiutil attach "$RW" -mountpoint "$MOUNT" >/dev/null
cp -R "$APP" "$MOUNT/WorldQuant.app"
ln -s /Applications "$MOUNT/Applications"
hdiutil detach "$MOUNT" >/dev/null
hdiutil convert "$RW" -format UDZO -o "$DMG" >/dev/null

if [ -n "${NOTARIZE_APPLE_ID:-}" ] && [ -n "${NOTARIZE_PASSWORD:-}" ] && [ -n "${NOTARIZE_TEAM_ID:-}" ]; then
    echo "==> Notarizing"
    xcrun notarytool submit "$DMG" --apple-id "$NOTARIZE_APPLE_ID" \
        --password "$NOTARIZE_PASSWORD" --team-id "$NOTARIZE_TEAM_ID" --wait
    xcrun stapler staple "$DMG"
else
    echo "    NOTARIZE_APPLE_ID/NOTARIZE_PASSWORD/NOTARIZE_TEAM_ID 未齐全，跳过公证"
    echo "    （adhoc 签名分发的 DMG 需用户右键打开，或走 Homebrew Cask 免隔离）"
fi

echo "==> Done: $DMG"

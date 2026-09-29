#!/usr/bin/env bash
# Build dist/WorldQuant-<version>.dmg: universal2 menubar app + bundled Python runtimes (arm64/x86_64)
# with the engine preinstalled + engine wheel + first-run helper + CLI shims. No system Python needed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c "import re; print(re.search(r'^version = \"(.+?)\"', open('pyproject.toml').read(), re.M).group(1))")"
# full：自带 Python 运行时。light：不含运行时，首次设置使用本机 Python ≥ 3.11。
PACKAGE="${WQ_PACKAGE:-full}"
if [ "$PACKAGE" != full ] && [ "$PACKAGE" != light ]; then
    echo "WQ_PACKAGE must be full or light" >&2
    exit 1
fi
DIST="${WQ_DIST_DIR:-$ROOT/dist}"
if [ "${REQUIRE_NOTARIZATION:-0}" = 1 ]; then
    test -n "${MACOS_SIGN_IDENTITY:-}" || { echo "Developer ID identity required" >&2; exit 1; }
    test -n "${NOTARY_KEYCHAIN_PROFILE:-}" || { echo "NOTARY_KEYCHAIN_PROFILE required" >&2; exit 1; }
fi
notarize() {
    xcrun notarytool submit "$1" --keychain-profile "$NOTARY_KEYCHAIN_PROFILE" --wait --timeout 30m
    xcrun stapler staple "$1"
    xcrun stapler validate "$1"
}
# 内置运行时：python-build-standalone，固定版本与 sha256；升级时两处一起改。
PBS_TAG=20260924
PBS_PY=3.12.14
PBS_SHA_arm64=c2edb321cd32ec2b170df208db0446dccc4398db602ca27cf2079098fb1f7d9d
PBS_SHA_x86_64=7ea9761b9069c10b9a20531d568645849d604c59e9c7f11f6659f1e1790c968e
PBS_CACHE="${WQ_PBS_CACHE:-$HOME/Library/Caches/wq-build}"
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
mkdir -p "$APP/Contents/Resources/bin"
cp packaging/macos/bin/wq packaging/macos/bin/wq-setup "$APP/Contents/Resources/bin/"
chmod 755 "$APP/Contents/Resources/bin/"*

if [ "$PACKAGE" = full ]; then
echo "==> Bundling Python ${PBS_PY} runtimes"
mkdir -p "$PBS_CACHE"
for arch in arm64 x86_64; do
    triple=$([ "$arch" = arm64 ] && echo aarch64 || echo x86_64)
    name="cpython-${PBS_PY}+${PBS_TAG}-${triple}-apple-darwin-install_only_stripped.tar.gz"
    tarball="$PBS_CACHE/$name"
    if [ ! -f "$tarball" ]; then
        curl -fL --retry 3 -o "$tarball.part" \
            "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${name}"
        mv "$tarball.part" "$tarball"
    fi
    want_var="PBS_SHA_$arch"
    got="$(shasum -a 256 "$tarball" | cut -d' ' -f1)"
    [ "$got" = "${!want_var}" ] || { echo "sha256 mismatch: $name" >&2; rm -f "$tarball"; exit 1; }
    dest="$APP/Contents/Resources/runtime/$arch"
    mkdir -p "$dest"
    tar -xzf "$tarball" -C "$dest" --strip-components 1
    # 纯 Python wheel：直接解包进 site-packages，不在构建机上执行异架构解释器。
    site="$dest/lib/python${PBS_PY%.*}/site-packages"
    unzip -q "$WHEELS"/wq_pilot-*.whl -d "$site"
    echo "cpython-${PBS_PY}+${PBS_TAG}-${arch}" > "$dest/WQ-RUNTIME-ID"
done
else
    echo "==> Light package: no bundled Python runtime"
fi

echo "==> Signing"
# 内置运行时里的每个 Mach-O 先签（公证要求逐个签名 + hardened runtime），再签整个 app。轻量包没有这一层。
if [ -d "$APP/Contents/Resources/runtime" ]; then
while IFS= read -r -d '' f; do
    if file -b "$f" | grep -q 'Mach-O'; then
        if [ -n "${MACOS_SIGN_IDENTITY:-}" ]; then
            codesign --force --options runtime --timestamp --sign "$MACOS_SIGN_IDENTITY" "$f"
        else
            codesign --force --sign - "$f"
        fi
    fi
done < <(find "$APP/Contents/Resources/runtime" -type f -print0)
fi
if [ -n "${MACOS_SIGN_IDENTITY:-}" ]; then
    codesign --force --options runtime --timestamp --sign "$MACOS_SIGN_IDENTITY" "$APP"
else
    echo "    MACOS_SIGN_IDENTITY 未设置，使用 adhoc 签名"
    codesign --force --sign - "$APP"
fi

if [ -n "${NOTARY_KEYCHAIN_PROFILE:-}" ]; then
    ditto -c -k --keepParent "$APP" "$BUILD/WorldQuant.zip"
    xcrun notarytool submit "$BUILD/WorldQuant.zip" --keychain-profile "$NOTARY_KEYCHAIN_PROFILE" --wait --timeout 30m
    xcrun stapler staple "$APP"
    xcrun stapler validate "$APP"
    spctl --assess --type execute --verbose=2 "$APP"
fi

suffix=""
if [ "$PACKAGE" = light ]; then suffix="-light"; fi
DMG="$DIST/WorldQuant-${VERSION}${suffix}.dmg"
RW="$BUILD/rw.dmg"
MOUNT="$BUILD/mount"
rm -f "$DMG" "$RW"
mkdir -p "$MOUNT"

echo "==> Creating DMG"
hdiutil create -size "$([ "$PACKAGE" = light ] && echo 80m || echo 600m)" -fs HFS+ -volname "WorldQuant" "$RW" >/dev/null
hdiutil attach "$RW" -mountpoint "$MOUNT" >/dev/null
trap 'hdiutil detach "$MOUNT" >/dev/null 2>&1 || true' EXIT
cp -R "$APP" "$MOUNT/WorldQuant.app"
ln -s /Applications "$MOUNT/Applications"
hdiutil detach "$MOUNT" >/dev/null
hdiutil convert "$RW" -format UDZO -o "$DMG" >/dev/null

if [ -n "${NOTARY_KEYCHAIN_PROFILE:-}" ]; then
    codesign --force --timestamp --sign "$MACOS_SIGN_IDENTITY" "$DMG"
    notarize "$DMG"
    spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
elif [ -n "${NOTARIZE_APPLE_ID:-}" ] && [ -n "${NOTARIZE_PASSWORD:-}" ] && [ -n "${NOTARIZE_TEAM_ID:-}" ]; then
    echo "==> Notarizing"
    xcrun notarytool submit "$DMG" --apple-id "$NOTARIZE_APPLE_ID" \
        --password "$NOTARIZE_PASSWORD" --team-id "$NOTARIZE_TEAM_ID" --wait
    xcrun stapler staple "$DMG"
else
    echo "    NOTARIZE_APPLE_ID/NOTARIZE_PASSWORD/NOTARIZE_TEAM_ID 未齐全，跳过公证"
    echo "    （adhoc 签名分发的 DMG 需用户右键打开，或走 Homebrew Cask 免隔离）"
fi

echo "==> Done: $DMG"

#!/bin/bash
# Build "Job Oyster.app" and a DMG people can download: Python, the app's packages and the PDF renderer
# (headless Chromium) bundled inside, so nothing needs installing first.
#
#   mac/build.sh [--version 1.2.0] [--repo owner/repo] [--arch arm64|x86_64] [--no-sign] [--notarize]
#
# --version   the app's version (default: the latest v* git tag, else 0.1.0)
# --repo      the GitHub repo whose Releases the app checks for updates (default: this repo's origin)
# --arch      the Mac to build for (default: this one's). Build on that kind of Mac.
# --no-sign   skip Developer ID signing. Otherwise the first "Developer ID Application" identity in the
#             keychain (or $SIGN_IDENTITY) signs it; without one the app is only ad-hoc signed, and
#             people have to allow it once in System Settings > Privacy & Security.
# --notarize  send the signed DMG to Apple's notary service and staple the ticket, so it opens with no
#             warning. Auth: an App Store Connect API key ($NOTARY_KEY path to the .p8, $NOTARY_KEY_ID,
#             $NOTARY_ISSUER), or a keychain profile ($NOTARY_PROFILE, default jobsearch-notary) made with
#             xcrun notarytool store-credentials jobsearch-notary --key <AuthKey.p8> --key-id <ID> --issuer <ISSUER>
#
# Output: build/Job Oyster.app and build/Job-Oyster-<version>-<arch>.dmg (plus build/Job-Oyster-<arch>.dmg,
# a stable name for "latest" download links).
set -euo pipefail

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO_DIR"
VERSION=""; UPDATE_REPO=""; ARCH=$(uname -m); SIGN=1; NOTARIZE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION=$2; shift 2 ;;
    --repo) UPDATE_REPO=$2; shift 2 ;;
    --arch) ARCH=$2; shift 2 ;;
    --no-sign) SIGN=""; shift ;;
    --notarize) NOTARIZE=1; shift ;;
    *) echo "Unknown option: $1"; sed -n '2,20p' "$0"; exit 1 ;;
  esac
done
[ -n "$VERSION" ] || VERSION=$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null | sed 's/^v//' || true)
[ -n "$VERSION" ] || VERSION=0.1.0
[ -n "$UPDATE_REPO" ] || UPDATE_REPO=$(git remote get-url origin 2>/dev/null | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##' || true)
case "$ARCH" in arm64|aarch64) ARCH=arm64; PBS_ARCH=aarch64 ;; x86_64) PBS_ARCH=x86_64 ;; *) echo "Unknown arch $ARCH"; exit 1 ;; esac
PYVER=3.12

BUILD="$REPO_DIR/build"
APP="$BUILD/Job Oyster.app"
RES="$APP/Contents/Resources"
CACHE="$BUILD/cache"
say() { printf '\n==> %s\n' "$*"; }
mkdir -p "$CACHE"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES"

say "Python $PYVER ($PBS_ARCH), from python-build-standalone"
API=https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest
AUTH=(); [ -n "${GITHUB_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")
PY_URL=$(curl -fsSL ${AUTH[@]+"${AUTH[@]}"} "$API" |
  grep -oE "https://[^\"]*/cpython-${PYVER//./\\.}\.[0-9]+(\+|%2B)[0-9]+-${PBS_ARCH}-apple-darwin-install_only\.tar\.gz" | sed -n 1p)
[ -n "$PY_URL" ] || { echo "Couldn't find a Python $PYVER build for $PBS_ARCH"; exit 1; }
PY_TGZ="$CACHE/$(basename "$PY_URL" | sed 's/%2B/+/')"
[ -f "$PY_TGZ" ] || curl -fL# -o "$PY_TGZ" "$PY_URL"
tar -xzf "$PY_TGZ" -C "$RES"                     # makes $RES/python
PY="$RES/python/bin/python3"
"$PY" --version

say "The app's packages"
# The evidence checks' models need PyTorch (about 1 GB), so the app goes without them; runs say they're skipped.
grep -vE '^\s*(#|$)|^(pytest|sentence-transformers)\b' requirements.txt > "$CACHE/requirements-app.txt"
"$PY" -m pip install --quiet --disable-pip-version-check --no-warn-script-location --upgrade pip
"$PY" -m pip install --quiet --disable-pip-version-check --no-warn-script-location -r "$CACHE/requirements-app.txt"

say "The PDF renderer (headless Chromium)"
export PLAYWRIGHT_BROWSERS_PATH="$RES/ms-playwright"
"$PY" -m playwright install --only-shell chromium || "$PY" -m playwright install chromium
unset PLAYWRIGHT_BROWSERS_PATH

say "The app's code"
mkdir -p "$RES/app"
git ls-files -z jobpipe skill example-profile .env.example requirements.txt README.md | \
  (cd "$REPO_DIR" && xargs -0 tar -cf -) | tar -xf - -C "$RES/app"
"$PY" -m compileall -q -j 0 "$RES/app/jobpipe" "$RES/python/lib/python$PYVER/site-packages" >/dev/null || true
find "$RES/python" -name "*.pyc" -path "*/test/*" -delete 2>/dev/null || true
rm -rf "$RES/python/lib/python$PYVER/test" "$RES/python/lib/python$PYVER/idlelib" "$RES/python/lib/python$PYVER/tkinter"

say "Launcher and icon"
swiftc -O -target "$ARCH-apple-macos12.0" -o "$APP/Contents/MacOS/JobOyster" mac/Launcher.swift
ICONSET="$BUILD/AppIcon.iconset"; rm -rf "$ICONSET"; mkdir -p "$ICONSET"
swift mac/make_icon.swift "$BUILD/icon-1024.png"
for s in 16 32 128 256 512; do
  sips -z $s $s "$BUILD/icon-1024.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$BUILD/icon-1024.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns -o "$RES/AppIcon.icns" "$ICONSET"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Job Oyster</string>
  <key>CFBundleDisplayName</key><string>Job Oyster</string>
  <key>CFBundleIdentifier</key><string>io.github.job-search.app</string>
  <key>CFBundleExecutable</key><string>JobOyster</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>JSUpdateRepo</key><string>$UPDATE_REPO</string>
</dict>
</plist>
PLIST

# Signing, the DMG and notarizing: mac/package.sh, which mac/sign-release.sh also uses.
DMG="$BUILD/Job-Oyster-$VERSION-$ARCH.dmg"
PKG_FLAGS=(); [ -n "$SIGN" ] || PKG_FLAGS+=(--no-sign); [ -z "$NOTARIZE" ] || PKG_FLAGS+=(--notarize)
mac/package.sh "$APP" "$DMG" ${PKG_FLAGS[@]+"${PKG_FLAGS[@]}"}
cp "$DMG" "$BUILD/Job-Oyster-$ARCH.dmg"

say "Done: $DMG ($(du -h "$DMG" | cut -f1))"

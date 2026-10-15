#!/bin/bash
# Sign "Job Oyster.app" (or a release from before the rename, "Job Search.app"), put it in the install DMG, and (with --notarize) have Apple notarize the DMG.
# mac/build.sh runs it on the app it just built; mac/sign-release.sh on the app from a published release.
#
#   mac/package.sh <app> <dmg> [--no-sign] [--notarize]
#
# --no-sign   ad-hoc signing only. Otherwise the first "Developer ID Application" identity in the keychain
#             (or $SIGN_IDENTITY) signs it; without one the app is only ad-hoc signed, and people have to
#             allow it once in System Settings > Privacy & Security.
# --notarize  send the signed DMG to Apple's notary service and staple the ticket, so it opens with no
#             warning. Auth: an App Store Connect API key ($NOTARY_KEY path to the .p8, $NOTARY_KEY_ID,
#             $NOTARY_ISSUER), or a keychain profile ($NOTARY_PROFILE, default jobsearch-notary) made with
#             xcrun notarytool store-credentials jobsearch-notary --key <AuthKey.p8> --key-id <ID> --issuer <ISSUER>
set -euo pipefail

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
APP=$(cd "$(dirname "$1")" && pwd)/$(basename "$1"); DMG=$2; shift 2
SIGN=1; NOTARIZE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-sign) SIGN=""; shift ;;
    --notarize) NOTARIZE=1; shift ;;
    *) echo "Unknown option: $1"; sed -n '2,13p' "$0"; exit 1 ;;
  esac
done
cd "$REPO_DIR"
BUILD="$REPO_DIR/build"; CACHE="$BUILD/cache"; mkdir -p "$CACHE" "$(dirname "$DMG")"
ENT=mac/entitlements.plist
say() { printf '\n==> %s\n' "$*"; }

IDENTITY=""
if [ -n "$SIGN" ]; then
  IDENTITY=${SIGN_IDENTITY:-$(security find-identity -v -p codesigning 2>/dev/null | grep -o '"Developer ID Application: [^"]*"' | head -1 | tr -d '"' || true)}
fi
[ -z "$NOTARIZE" ] || [ -n "$IDENTITY" ] || { echo "Notarizing needs a Developer ID signature."; exit 1; }
if [ -n "$NOTARIZE" ]; then
  if [ -n "${NOTARY_KEY:-}" ]; then
    NOTARY_AUTH=(--key "$NOTARY_KEY" --key-id "$NOTARY_KEY_ID" --issuer "$NOTARY_ISSUER")
  else
    NOTARY_AUTH=(--keychain-profile "${NOTARY_PROFILE:-jobsearch-notary}")
  fi
fi

# Each signature carries a timestamp from Apple's server; on a shaky connection that request sometimes gets
# no answer ("A timestamp was expected but was not found"), so a step that fails is tried three times.
retry() {
  local try
  for try in 1 2 3; do
    "$@" && return 0
    [ "$try" = 3 ] && return 1
    printf 'Signing failed (try %s of 3), trying again\n' "$try"; sleep $((try * 20))
  done
}
sign_inner() {
  find "$APP/Contents/Resources" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -u+x \) -print0 |
    while IFS= read -r -d '' f; do
      if file -b "$f" | grep -q "Mach-O"; then printf '%s\0' "$f"; fi
    done | xargs -0 -n 50 -P 4 codesign --force --timestamp --options runtime --entitlements "$ENT" --sign "$IDENTITY"
}

if [ -n "$IDENTITY" ]; then
  say "Signing with $IDENTITY"
  # Inside out: every Mach-O file (Python, its extension modules, Chromium), then the app.
  retry sign_inner
  retry codesign --force --timestamp --options runtime --entitlements "$ENT" --sign "$IDENTITY" "$APP"
  codesign --verify --deep --strict "$APP"
else
  say "Not signed with a Developer ID: ad-hoc signing only"
  codesign --force --deep --sign - "$APP"
fi

say "DMG"
# dmgbuild lays out the window (background, icon places, no toolbar) without Finder. It runs on the app's own
# Python when that's built for this Mac (else python3) from a folder of its own, and writes no .pyc files,
# which would break the app's signature.
PY="$APP/Contents/Resources/python/bin/python3"
file -bL "$PY" 2>/dev/null | grep -q "$(uname -m)" || PY=python3
export PYTHONDONTWRITEBYTECODE=1
DMGBUILD="$CACHE/dmgbuild-1.6.7"
[ -d "$DMGBUILD" ] || "$PY" -m pip install --quiet --disable-pip-version-check --target "$DMGBUILD" dmgbuild==1.6.7
BG_FLAGS=(); [ -n "$NOTARIZE" ] || BG_FLAGS=(--open-anyway)
swift mac/make_dmg_background.swift "$BUILD/dmg-background.png" "$BUILD/dmg-background@2x.png" ${BG_FLAGS[@]+"${BG_FLAGS[@]}"}
# hdiutil now and then fails on GitHub's Macs ("Resource busy"), so try three times and say why each failed.
for try in 1 2 3; do
  rm -f "$DMG"
  if out=$(PYTHONPATH="$DMGBUILD" "$PY" -m dmgbuild -s mac/dmg_settings.py -D app="$APP" \
      -D icon="$APP/Contents/Resources/AppIcon.icns" -D background="$BUILD/dmg-background.png" "$(basename "$APP" .app)" "$DMG" 2>&1); then break; fi
  printf 'Making the DMG failed (try %s of 3):\n%s\n' "$try" "$out"
  [ "$try" = 3 ] && exit 1
  sleep $((try * 15))
done
unset PYTHONDONTWRITEBYTECODE
[ -z "$IDENTITY" ] || retry codesign --force --timestamp --sign "$IDENTITY" "$DMG"

if [ -n "$NOTARIZE" ]; then
  say "Notarizing (a few minutes)"
  # S3 Transfer Acceleration timed the 200 MB upload out ("deadlineExceeded") where a plain upload went
  # through, so it's off. A dropped connection gets two more tries, a minute or two apart. A build Apple
  # turns down isn't tried again: its log says why.
  for try in 1 2 3; do
    out=$(xcrun notarytool submit "$DMG" "${NOTARY_AUTH[@]}" --no-s3-acceleration --wait 2>&1) || true
    printf '%s\n' "$out" | grep -E '^ *(id|status):|^Error' || true
    grep -q 'status: Accepted' <<<"$out" && break
    if grep -qE 'status: (Invalid|Rejected)' <<<"$out"; then
      xcrun notarytool log "$(grep -m1 -oE '[0-9a-f]{8}-[0-9a-f-]{27}' <<<"$out")" "${NOTARY_AUTH[@]}"; exit 1
    fi
    printf 'Notarizing failed (try %s of 3)\n' "$try"
    [ "$try" = 3 ] && exit 1
    sleep $((try * 60))
  done
  xcrun stapler staple "$DMG"
  spctl --assess --type open --context context:primary-signature -v "$DMG"
fi

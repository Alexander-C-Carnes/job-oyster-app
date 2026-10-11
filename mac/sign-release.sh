#!/bin/bash
# Sign and notarize a published release's DMGs on this Mac, so the Developer ID certificate and the notary key
# never leave it. GitHub's Macs build and publish the DMGs unsigned (the repo holds no signing secrets); this
# downloads them, signs the app inside each with mac/package.sh, notarizes the new DMGs and puts them on the
# release in place of the old ones (the versioned names and the stable Job-Search-<arch>.dmg ones).
#
#   mac/sign-release.sh [v0.1.4] [--repo owner/repo] [--no-upload]
#
# The tag defaults to the latest release; --repo to $JS_RELEASE_REPO, else Alexander-C-Carnes/job-search-app.
# The signed DMGs are kept in build/signed/. --no-upload stops there and leaves the release as it is.
# Once they're up, the release notes lose their Open Anyway step.
# Needs gh signed in with write access to that repo, a "Developer ID Application" identity in the keychain
# (or $SIGN_IDENTITY), and notary auth as mac/package.sh describes (the jobsearch-notary keychain profile).
# A release whose DMGs are already notarized is left as it is, so running it twice is harmless.
set -euo pipefail

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
TAG=""; UPLOAD=1; REPO=${JS_RELEASE_REPO:-Alexander-C-Carnes/job-search-app}
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO=$2; shift 2 ;;
    --no-upload) UPLOAD=""; shift ;;
    v[0-9]*) TAG=$1; shift ;;
    *) echo "Unknown option: $1"; sed -n '2,15p' "$0"; exit 1 ;;
  esac
done
say() { printf '\n==> %s\n' "$*"; }

[ -n "$TAG" ] || TAG=$(gh release view -R "$REPO" --json tagName -q .tagName)
VERSION=${TAG#v}
security find-identity -v -p codesigning | grep -q "Developer ID Application" || [ -n "${SIGN_IDENTITY:-}" ] ||
  { echo "No Developer ID Application certificate in the keychain."; exit 1; }
if [ -z "${NOTARY_KEY:-}" ]; then
  xcrun notarytool history --keychain-profile "${NOTARY_PROFILE:-jobsearch-notary}" >/dev/null 2>&1 ||
    { echo "Notary profile ${NOTARY_PROFILE:-jobsearch-notary} doesn't work. Make it with:"
      echo "  xcrun notarytool store-credentials jobsearch-notary --key <AuthKey.p8> --key-id <ID> --issuer <ISSUER>"; exit 1; }
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/job-search-sign.XXXXXX")
cleanup() {
  for m in "$WORK"/mnt-*; do hdiutil detach -quiet "$m" 2>/dev/null || true; done
  rm -rf "$WORK"
}
trap cleanup EXIT
OUT="$REPO_DIR/build/signed"; mkdir -p "$OUT"

# The release's DMGs are kept in build/cache/releases/<tag> until the run gets through, so running it again
# after a dropped connection doesn't download them again. A file whose size doesn't match the release's
# (a download that broke off) is fetched again. A .done file marks a DMG that's already been replaced.
DL="$REPO_DIR/build/cache/releases/$TAG"; mkdir -p "$DL"
say "Downloading $REPO $TAG"
gh release view "$TAG" -R "$REPO" --json assets -q '.assets[] | "\(.name) \(.size)"' |
  while read -r asset size; do
    f="$DL/$asset"
    [ -f "$f" ] && [ "$(stat -f %z "$f")" != "$size" ] && rm -f "$f"
    true
  done
gh release download "$TAG" -R "$REPO" -p "Job-Search-$VERSION-*.dmg" -D "$DL" --skip-existing
shopt -s nullglob
DMGS=("$DL"/Job-Search-"$VERSION"-*.dmg)
[ ${#DMGS[@]} -gt 0 ] || { echo "$TAG has no Job-Search-$VERSION-<arch>.dmg files."; exit 1; }

for dmg in "${DMGS[@]}"; do
  name=$(basename "$dmg" .dmg); ARCH=${name##*-}
  if [ -f "$DL/$name.done" ]; then say "$name.dmg was already replaced on $TAG: leaving it"; continue; fi
  if spctl --assess --type open --context context:primary-signature "$dmg" 2>/dev/null; then
    say "$name.dmg is already notarized: leaving it"; continue
  fi
  say "$name.dmg: taking the app out"
  mnt="$WORK/mnt-$ARCH"; mkdir -p "$mnt"
  hdiutil attach -quiet -nobrowse -readonly -mountpoint "$mnt" "$dmg"
  mkdir -p "$WORK/$ARCH"
  ditto "$mnt/Job Search.app" "$WORK/$ARCH/Job Search.app"
  hdiutil detach -quiet "$mnt"
  xattr -cr "$WORK/$ARCH/Job Search.app"         # codesign refuses files with Finder info or quarantine flags
  have=$(defaults read "$WORK/$ARCH/Job Search.app/Contents/Info" CFBundleShortVersionString)
  [ "$have" = "$VERSION" ] || { echo "The app in $name.dmg says it's $have, not $VERSION."; exit 1; }

  # Made in the work folder, so only notarized DMGs reach build/signed/.
  "$REPO_DIR/mac/package.sh" "$WORK/$ARCH/Job Search.app" "$WORK/$name.signed.dmg" --notarize
  mv "$WORK/$name.signed.dmg" "$OUT/$name.dmg"
  cp "$OUT/$name.dmg" "$OUT/Job-Search-$ARCH.dmg"
  [ -n "$UPLOAD" ] || continue
  say "Replacing $name.dmg and Job-Search-$ARCH.dmg on $TAG"
  gh release upload "$TAG" -R "$REPO" --clobber "$OUT/$name.dmg" "$OUT/Job-Search-$ARCH.dmg"
  touch "$DL/$name.done"
done

if [ -z "$UPLOAD" ]; then say "Done: signed and notarized DMGs in $OUT (not uploaded)"; exit 0; fi
# The notes the Mac app workflow writes tell people to click Open Anyway, which a notarized app doesn't need.
notes=$(gh release view "$TAG" -R "$REPO" --json body -q .body)
fixed=$(printf '%s' "$notes" | sed -E "s/ If macOS says it can't check the app, open System Settings > Privacy & Security and click Open Anyway \(once\)\.//")
[ "$fixed" = "$notes" ] || printf '%s\n' "$fixed" | gh release edit "$TAG" -R "$REPO" --notes-file -
rm -rf "$DL"
say "Done: $TAG's DMGs are signed and notarized (https://github.com/$REPO/releases/tag/$TAG)"

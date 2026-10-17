#!/bin/bash
# Sign each new release on this Mac without anyone having to remember. GitHub publishes the daily release
# unsigned (see mac/sign-release.sh for why); a LaunchAgent runs this every hour, and when the latest release
# still tells people to click Open Anyway it runs mac/sign-release.sh on it.
#
#   mac/auto-sign.sh              sign the latest release if it isn't yet (what the agent runs)
#   mac/auto-sign.sh --install    run it every hour, and at login (again after the signing scripts change)
#   mac/auto-sign.sh --uninstall  stop that
#   mac/auto-sign.sh --status     whether it's installed, and the end of its log
#
# macOS doesn't let a background job read ~/Documents, so --install copies mac/ to SIGNING_DIR below and the
# agent runs that copy, which also outlives a worktree; the signed DMGs land in its build/signed/. It skips a
# run while the Mac is on an iPhone hotspot (slow and metered) or another copy of the signing is going, and
# gives a tag three tries before leaving it to you. Each failure shows a notification; the log is
# ~/Library/Logs/Job Oyster/auto-sign.log. Notary auth is whatever mac/sign-release.sh finds: put the API key
# in ~/JobOyster/.notary.env so it can't vanish with the keychain profile.
set -uo pipefail

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
REPO=${JS_RELEASE_REPO:-Alexander-C-Carnes/job-oyster-app}
LABEL=io.github.job-search.auto-sign
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/Job Oyster"; LOG="$LOG_DIR/auto-sign.log"
STATE="$HOME/Library/Caches/Job Oyster/auto-sign"; MAX_TRIES=3
SIGNING_DIR="$HOME/Library/Application Support/Job Oyster/signing"
mkdir -p "$LOG_DIR" "$STATE"

case "${1:-}" in
  --install)
    if [ "$REPO_DIR" != "$SIGNING_DIR" ]; then
      rm -rf "$SIGNING_DIR/mac"; mkdir -p "$SIGNING_DIR"; cp -R "$REPO_DIR/mac" "$SIGNING_DIR/"
    fi
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$SIGNING_DIR/mac/auto-sign.sh</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
EOF
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
    launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "Installed: $SIGNING_DIR/mac/auto-sign.sh runs every hour (log: $LOG)"
    exit ;;
  --uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null; rm -f "$PLIST"; rm -rf "$SIGNING_DIR/mac"; echo "Uninstalled."; exit ;;
  --status)
    if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then echo "Installed ($PLIST)"; else echo "Not installed."; fi
    [ -f "$LOG" ] && { echo; tail -n 20 "$LOG"; }
    exit ;;
  "") ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
notify() { osascript -e "display notification \"$1\" with title \"Job Oyster signing\"" >/dev/null 2>&1 || true; }

if pgrep -f "mac/sign-release.sh" >/dev/null; then log "Signing is already going: skipping"; exit 0; fi
# An iPhone hotspot hands out 172.20.10.x; the address, not the gateway, since a VPN hides the gateway.
if ifconfig 2>/dev/null | grep -q 'inet 172\.20\.10\.'; then log "On an iPhone hotspot: skipping"; exit 0; fi

# The install sentence mac/sign-release.sh takes out, not just "Open Anyway", which a What's new line could say.
if ! info=$(gh release view -R "$REPO" --json tagName,body -q '.tagName + " " + (.body | test("and click Open Anyway \\(once\\)") | tostring)' 2>&1); then
  log "Couldn't read the latest release: $info"; exit 0         # offline, most likely; next hour
fi
TAG=${info%% *}
if [ "${info##* }" != true ]; then                             # signed already: said once per tag, not hourly
  [ -f "$STATE/$TAG.ok" ] || { log "$TAG is signed"; touch "$STATE/$TAG.ok"; }
  exit 0
fi

tries_file="$STATE/$TAG.tries"; tries=$(cat "$tries_file" 2>/dev/null || echo 0)
if [ "$tries" -ge "$MAX_TRIES" ]; then log "$TAG failed $tries times: leaving it for mac/sign-release.sh $TAG by hand"; exit 0; fi
echo $((tries + 1)) > "$tries_file"

log "$TAG is unsigned: signing it (try $((tries + 1)) of $MAX_TRIES)"
if "$REPO_DIR/mac/sign-release.sh" "$TAG" --repo "$REPO"; then
  rm -f "$tries_file"; log "$TAG signed and notarized"; notify "$TAG is signed and notarized."
else
  log "Signing $TAG failed"; notify "Signing $TAG failed. See ~/Library/Logs/Job Oyster/auto-sign.log."
fi

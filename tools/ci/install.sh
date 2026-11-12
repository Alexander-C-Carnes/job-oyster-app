#!/usr/bin/env bash
# Sets up oyster-ci on this Mac (docs/ci.md): its clone ~/.oyster-ci/work, its venv ~/.oyster-ci/venv, the scripts it
# runs, the config file ~/.config/oyster-ci/env, the `ship` label on GitHub, a pre-push hook that keeps sessions from
# pushing to main, and the launchd agent io.github.job-oyster.ci (every 5 minutes, low priority). Safe to run again:
# it waits for a running job and never overwrites the config.
#
#   tools/ci/install.sh               # install or update
#   tools/ci/install.sh --paused      # … and leave it paused (oyster-ci.sh resume starts it)
#   tools/ci/install.sh --uninstall   # stop the agent (keeps ~/.oyster-ci and the config)
set -euo pipefail
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
REPO_MAIN="$(cd "$(git -C "$SRC" rev-parse --git-common-dir)/.." && pwd)"
CI="$HOME/.oyster-ci"
CONFIG_DIR="$HOME/.config/oyster-ci"
AGENT=io.github.job-oyster.ci
PLIST="$HOME/Library/LaunchAgents/$AGENT.plist"
DOMAIN="gui/$(id -u)"
HOOK="$(git -C "$SRC" rev-parse --git-common-dir)/hooks/pre-push"

if [[ "${1:-}" == --uninstall ]]; then
  launchctl bootout "$DOMAIN/$AGENT" 2>/dev/null || true
  rm -f "$PLIST"
  grep -q 'oyster-ci' "$HOOK" 2>/dev/null && rm -f "$HOOK"
  echo "✔ oyster-ci stopped. Left $CI (clone and venv) and $CONFIG_DIR."
  echo "  To free the disk too: rm -rf \"$CI\""
  exit 0
fi

mkdir -p "$CI/state" "$CI/logs" "$CONFIG_DIR" "$(dirname "$PLIST")"
git -C "$REPO_MAIN" fetch -q origin main
# Its own clone, not a worktree: a worktree keeps its git data in the main checkout under ~/Documents, which macOS
# privacy protection doesn't let a background job read.
if [[ ! -d "$CI/work/.git" ]]; then
  git clone -q "$(git -C "$REPO_MAIN" remote get-url origin)" "$CI/work"
  git -C "$CI/work" checkout -q --detach origin/main
  echo "▶ clone: $CI/work"
fi

# Its own venv too, for the same reason (the main checkout's .venv is under ~/Documents).
if [[ ! -x "$CI/venv/bin/python" ]]; then
  py="$(command -v python3)"
  echo "▶ venv: $CI/venv ($("$py" --version); the packages take a few minutes the first time)"
  "$py" -m venv "$CI/venv"
  "$CI/venv/bin/python" -m pip install --quiet --upgrade pip
fi
if ! cmp -s "$SRC/requirements.txt" "$CI/venv/.requirements"; then
  "$CI/venv/bin/python" -m pip install --quiet -r "$SRC/requirements.txt"
  "$CI/venv/bin/python" -m playwright install chromium >/dev/null
  cp "$SRC/requirements.txt" "$CI/venv/.requirements"
fi

# The scripts launchd runs: main's tools/ci once main has them (oyster-ci refreshes them after every tick), else this
# checkout's (installing from the branch that adds oyster-ci).
rm -rf "${CI:?}/bin" && mkdir -p "$CI/bin"
if git -C "$REPO_MAIN" cat-file -e origin/main:tools/ci/oyster-ci.sh 2>/dev/null; then
  git -C "$REPO_MAIN" archive origin/main tools/ci | tar -x -C "$CI/bin"
else
  mkdir -p "$CI/bin/tools" && cp -R "$SRC/tools/ci" "$CI/bin/tools/"
fi

if [[ ! -f "$CONFIG_DIR/env" ]]; then
  cat >"$CONFIG_DIR/env" <<'EOF'
# oyster-ci settings (docs/ci.md). Sourced by tools/ci/oyster-ci.sh.

# Headless Claude fixes for PRs the gate failed: on/off, model, tries per PR, minutes per try.
#OYSTER_CI_AUTOFIX=1
#OYSTER_CI_FIX_MODEL=sonnet
#OYSTER_CI_FIX_ATTEMPTS=2
#OYSTER_CI_FIX_MINUTES=30

# How much it takes on.
#OYSTER_CI_BATCH=8
#OYSTER_CI_MIN_FREE_GB=5
#OYSTER_CI_RUN_ON_BATTERY=1
EOF
  chmod 600 "$CONFIG_DIR/env"
  echo "▶ config: $CONFIG_DIR/env"
fi

gh label create ship --repo Alexander-C-Carnes/job-oyster-app --color 0E8A16 \
  --description "oyster-ci merges this once its checks and PR rules pass (docs/ci.md)" >/dev/null 2>&1 || true

# Sessions push branches and open PRs; only oyster-ci (from its own clone, which has no hooks) pushes main.
if [[ ! -f "$HOOK" ]] || grep -q 'oyster-ci' "$HOOK"; then
  cat >"$HOOK" <<'EOF'
#!/bin/sh
# oyster-ci (tools/ci/install.sh): no direct pushes to main from this checkout or its worktrees. Open a PR labelled
# `ship` and oyster-ci merges it once its checks pass (docs/ci.md). One-off override: ALLOW_MAIN_PUSH=1 git push ...
while read -r local_ref local_sha remote_ref remote_sha; do
  if [ "$remote_ref" = "refs/heads/main" ] && [ "$ALLOW_MAIN_PUSH" != "1" ]; then
    echo "pre-push: pushes to main are blocked. Push a branch and open a PR labelled ship instead (docs/ci.md)." >&2
    exit 1
  fi
done
exit 0
EOF
  chmod +x "$HOOK"
  echo "▶ pre-push hook: $HOOK"
fi

# Standard, not Background: Background keeps a job on the efficiency cores. Nice 10 and low-priority I/O still put
# your apps first.
cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$AGENT</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$CI/bin/tools/ci/oyster-ci.sh</string><string>tick</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Standard</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>$CI/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$CI/logs/launchd.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
</dict>
</plist>
EOF
# Restarting the agent kills a running job, so hold new ticks and let the current one finish first.
was_paused=false
[[ -f "$CI/state/paused" ]] && was_paused=true
[[ "${1:-}" == --paused ]] && was_paused=true
touch "$CI/state/paused"
while pid="$(cat "$CI/lock/pid" 2>/dev/null)" && [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; do
  echo "… waiting for oyster-ci's current job (pid $pid) to finish; bash tools/ci/oyster-ci.sh status shows what it is"
  sleep 30
done
launchctl bootout "$DOMAIN/$AGENT" 2>/dev/null || true
$was_paused || rm -f "$CI/state/paused"
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "✔ oyster-ci installed: $AGENT runs every 5 minutes$($was_paused && echo ' (paused: oyster-ci.sh resume)'). Status: bash tools/ci/oyster-ci.sh status"

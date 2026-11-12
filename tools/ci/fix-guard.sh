#!/usr/bin/env bash
# PreToolUse hook for oyster-ci's headless Claude fixes (oyster-ci.sh fix, docs/ci.md): Claude only commits, oyster-ci
# pushes. Blocks shell commands that push, call GitHub, publish, sign, touch the app running on this Mac or reach other
# hosts. Exit 2 = blocked, and stderr tells Claude why. A belt next to --disallowedTools, which only matches a
# command's first words.
cmd="$(jq -r '.tool_input.command // empty')"
blocked='git[^|;&]*[[:space:]]push|gh|(oyster|jobsearch)[[:space:]]+(run|go|restart|update|install)|sign-release\.sh|auto-sign\.sh|launchctl|pkill|killall|curl|wget|ssh|scp|rsync'
if grep -Eq "(^|[^[:alnum:]_.-])($blocked)([^[:alnum:]_.-]|\$)" <<<"$cmd"; then
  echo "oyster-ci fix runs only commit: pushing, gh, publishing, signing, the app and network tools are off. oyster-ci pushes your commits to the PR's branch." >&2
  exit 2
fi
exit 0

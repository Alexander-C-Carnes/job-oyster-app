#!/usr/bin/env bash
# oyster-ci: Job Oyster's merge server on this Mac (docs/ci.md), built like goose-ci in the Goose repo. launchd runs
# `oyster-ci.sh tick` every 5 minutes at low priority, from a copy of main's tools/ci in ~/.oyster-ci/bin, working in
# its own clone ~/.oyster-ci/work with its own venv ~/.oyster-ci/venv. A tick works through these, one job at a time,
# until nothing is left:
#
#   gate     the open PRs labelled `ship`, oldest first: each must merge with main and pass the PR rules, then up to
#            OYSTER_CI_BATCH of them are merged together, checked once (tools/ci/checks.sh) and pushed to main if green,
#            which GitHub counts as merging them (a red batch: one PR at a time next)
#   fix      a `ship` PR the gate failed: headless Claude fixes it in the clone, oyster-ci pushes its commits to the PR's
#            branch and the gate runs again (OYSTER_CI_FIX_ATTEMPTS per PR, one per commit)
#
# The app updates itself (OYSTER_FOLLOW_MAIN=1 in ~/JobOyster/.env, jobpipe/web/follow.py): oyster-ci can't, since
# macOS doesn't let a background job into ~/Documents, where the checkout is. Releases stay on GitHub (the Mac app
# workflow, daily), and mac/auto-sign.sh signs them.
#
#   oyster-ci.sh status                 the queue, what's merged, the last log lines
#   oyster-ci.sh tick                   one run now (launchd does this every 5 minutes)
#   oyster-ci.sh retry <pr>             check that PR's commit again
#   oyster-ci.sh fix <pr>               Claude fixes the PR's failed gate now, even past the attempt limit
#   oyster-ci.sh pause | resume
#   oyster-ci.sh log [pattern]          the main log, or the newest job log matching pattern (pr-129, fix-115…)
#   OYSTER_CI_DRY_RUN=1 oyster-ci.sh tick   gate the next PRs only, without statuses, comments, pushes or merging
#
# Bash 3.2 compatible (launchd runs it with /bin/bash).
set -euo pipefail

CI="${OYSTER_CI_HOME:-$HOME/.oyster-ci}"
WORK="$CI/work" STATE="$CI/state" LOGS="$CI/logs" BIN="$CI/bin" VENV="$CI/venv"
CONFIG="${OYSTER_CI_CONFIG:-$HOME/.config/oyster-ci/env}"
REPO="${OYSTER_CI_GITHUB_REPO:-Alexander-C-Carnes/job-oyster-app}"
LABEL=ship CONTEXT=oyster-ci
DRY="${OYSTER_CI_DRY_RUN:-0}"
# A dry run keeps its verdicts to itself, so the real gate still checks those commits.
[[ "$DRY" == 1 ]] && { STATE="$CI/state-dry"; rm -rf "$STATE"; }
mkdir -p "$STATE/gate-failed" "$STATE/fix" "$STATE/solo" "$STATE/merged" "$LOGS"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
# shellcheck disable=SC1090
if [[ -f "$CONFIG" ]]; then set -a; . "$CONFIG"; set +a; fi
MIN_FREE_GB="${OYSTER_CI_MIN_FREE_GB:-5}"
ON_BATTERY="${OYSTER_CI_RUN_ON_BATTERY:-1}"
BATCH="${OYSTER_CI_BATCH:-8}"
FIX_ON="${OYSTER_CI_AUTOFIX:-1}"
FIX_ATTEMPTS="${OYSTER_CI_FIX_ATTEMPTS:-2}"
FIX_MINUTES="${OYSTER_CI_FIX_MINUTES:-30}"
FIX_MODEL="${OYSTER_CI_FIX_MODEL:-sonnet}"
CI_SCRIPTS="$BIN/tools/ci"
[[ -d "$CI_SCRIPTS" ]] || CI_SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
export OYSTER_CI_PY="$VENV/bin/python"

# ── helpers ──────────────────────────────────────────────────────────────────────────────────────────────────────
log() {
  local line; line="$(date '+%F %T') $*"
  echo "$line" >>"$LOGS/oyster-ci.log"
  if [[ -t 2 ]]; then echo "$line" >&2; fi
}
notify() {
  log "notify: $1"
  [[ "$DRY" != 1 ]] || return 0
  local msg="${1//\\/\\\\}"; msg="${msg//\"/\\\"}"
  osascript -e "display notification \"$msg\" with title \"Job Oyster CI\"" >/dev/null 2>&1 || true
}
notify_once() {   # key message: at most once a day
  [[ "$(state "notified-$1")" == "$(date +%F)" ]] && return 0
  set_state "notified-$1" "$(date +%F)"; notify "$2"
}
state() { cat "$STATE/$1" 2>/dev/null || true; }
set_state() { echo "$2" >"$STATE/$1"; }
g() { git -C "$WORK" "$@"; }
main_sha() { g rev-parse origin/main; }
checkout() { g checkout -q -f --detach "$1" && g clean -q -fd; }
github_status() {   # sha state description
  [[ "$DRY" == 1 ]] && { log "dry run: status $2 on ${1:0:7}: $3"; return 0; }
  gh api -X POST "repos/$REPO/statuses/$1" -f state="$2" -f context="$CONTEXT" -f description="${3:0:139}" \
    >/dev/null 2>&1 || true
}
tail_log() { tail -n "${2:-60}" "$1" | sed -e $'s/\x1b\\[[0-9;]*m//g'; }

lock() {
  if ! mkdir "$CI/lock" 2>/dev/null; then
    local pid; pid="$(cat "$CI/lock/pid" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then return 1; fi
    rm -rf "$CI/lock"; mkdir "$CI/lock" || return 1
  fi
  echo $$ >"$CI/lock/pid"
  trap 'rm -rf "$CI/lock"' EXIT
}
running() {   # an oyster-ci run holds the lock and is still alive
  local pid; pid="$(cat "$CI/lock/pid" 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}
job_start() { set_state job "$1"; set_state job-since "$(date +%s)"; }
job_end() { rm -f "$STATE/job" "$STATE/job-since"; }

WHY=""
resources_ok() {
  if [[ "$ON_BATTERY" != 1 && "$(pmset -g batt)" != *"AC Power"* ]]; then WHY="on battery"; return 1; fi
  local free; free="$(df -g /System/Volumes/Data | awk 'NR==2 {print $4}')"
  if (( free < MIN_FREE_GB )); then
    WHY="only ${free} GB free (needs $MIN_FREE_GB)"
    notify_once disk "Paused: only ${free} GB of disk free. Checks resume above $MIN_FREE_GB GB."
    return 1
  fi
  local pressure; pressure="$(sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null || echo 1)"
  if (( pressure >= 2 )); then WHY="memory pressure is high"; return 1; fi
  return 0
}

venv_sync() {   # the clone's requirements.txt into oyster-ci's venv, when it changed (the gate's merge may add one)
  cmp -s "$WORK/requirements.txt" "$VENV/.requirements" 2>/dev/null && return 0
  echo "▶ installing requirements.txt into $VENV"
  "$VENV/bin/python" -m pip install --quiet -r "$WORK/requirements.txt" \
    && "$VENV/bin/python" -m playwright install chromium \
    && cp "$WORK/requirements.txt" "$VENV/.requirements" \
    || { echo "✘ couldn't install requirements.txt (see the log)"; return 1; }
}

# ── gate: check and merge `ship` PRs ─────────────────────────────────────────────────────────────────────────────
pr_comment() {   # pr body
  [[ "$DRY" == 1 ]] || gh pr comment "$1" --repo "$REPO" --body "$2" >/dev/null 2>&1 || true
}
fail_gate() {   # pr head reason log
  local n="$1" head="$2" reason="${3:-checks failed}" out="$4" next
  echo "$reason" >"$STATE/gate-failed/$n-$head"
  github_status "$head" failure "$reason"
  next="Push a fix and the gate runs again on the new commit, or run \`bash tools/ci/oyster-ci.sh retry $n\`."
  fixable "$n" "$head" && next="Claude will try to fix it next (attempt $(( $(fix_tries "$n") + 1 )) of $FIX_ATTEMPTS) and push to this branch. $next"
  pr_comment "$n" "$(printf '**oyster-ci didn'"'"'t merge this:** %s\n\n<details><summary>Last lines of the log</summary>\n\n```\n%s\n```\n</details>\n\n%s' \
    "$reason" "$(tail_log "$out" 60)" "$next")"
  log "gate: #$n failed: $reason ($out)"
  notify "#$n not merged: $reason"
}
merge_pr() {   # n head branch title -> merges the PR's head into what's checked out, as GitHub's merge button would
  g merge -q --no-ff --no-edit -m "Merge pull request #$1 from ${REPO%%/*}/$3" -m "$4" "$2"
}
gate_alone() {   # "n head branch title" main log -> 0 when it merges cleanly with main and passes the PR rules
  local n head branch title main="$2" out="$3"
  read -r n head branch title <<<"$1"
  g fetch -q origin "pull/$n/head" || { log "gate: couldn't fetch #$n"; return 1; }
  [[ "$(g rev-parse FETCH_HEAD)" == "$head" ]] || { log "gate: #$n moved while fetching"; return 1; }
  checkout "$main" || { log "gate: couldn't check out main"; return 1; }
  if ! merge_pr "$n" "$head" "$branch" "$title" >"$out" 2>&1; then
    g merge --abort >/dev/null 2>&1 || true
    fail_gate "$n" "$head" "conflicts with main: merge origin/main into the branch and push" "$out"; return 1
  fi
  gh pr view "$n" --repo "$REPO" --json body --jq .body >"$STATE/pr-body.txt" || { log "gate: couldn't read #$n"; return 1; }
  if ! (cd "$WORK" && "$VENV/bin/python" "$CI_SCRIPTS/pr_rules.py" "$main" "$STATE/pr-body.txt") >>"$out" 2>&1; then
    fail_gate "$n" "$head" "$(grep '^✘' "$out" | head -1 | sed 's/^✘ //')" "$out"; return 1
  fi
}
gate() {   # "n head branch title"... oldest first
  # Each PR must merge with main and pass the PR rules on its own. Those that also merge with each other are checked
  # together, once, and pushed to main in order if green, so a queue of N PRs costs one check run, not N. A red batch
  # doesn't say whose fault it is: its PRs are checked one at a time next (solo/), and only the culprit fails.
  # gate() runs under `&&` (tick), where bash ignores set -e: every step checks itself.
  local main out pick n head branch title names="" ok=() batch=()
  main="$(main_sha)"
  for pick in "$@"; do names="$names #${pick%% *}"; done
  job_start "gate$names"; names=""
  for pick in "$@"; do
    read -r n head branch title <<<"$pick"
    github_status "$head" pending "Checking on main ${main:0:7}"
    gate_alone "$pick" "$main" "$LOGS/pr-$n-${head:0:8}.log" && ok+=("$pick")
  done
  (( ${#ok[@]} )) || return 0
  read -r n head branch title <<<"${ok[0]}"
  out="$LOGS/pr-$n-${head:0:8}.log"
  (( ${#ok[@]} == 1 )) || out="$LOGS/batch-$(date +%Y%m%d-%H%M%S).log"
  checkout "$main" || { log "gate: couldn't check out main"; return 0; }
  for pick in "${ok[@]}"; do
    read -r n head branch title <<<"$pick"
    if merge_pr "$n" "$head" "$branch" "$title" >>"$out" 2>&1; then
      batch+=("$pick"); names="$names #$n"
    else
      g merge --abort >/dev/null 2>&1 || true
      log "gate: #$n conflicts with another PR in this batch; it goes in the next one"
    fi
  done
  log "gate:$names on main ${main:0:7}"
  [[ "gate$names" == "$(state job)" ]] || job_start "gate$names"
  if ! (cd "$WORK" && venv_sync && bash "$CI_SCRIPTS/checks.sh" "$main") >>"$out" 2>&1; then
    if (( ${#batch[@]} == 1 )); then
      read -r n head branch title <<<"${batch[0]}"
      [[ "$out" == "$LOGS/pr-$n-"* ]] || cat "$out" >>"$LOGS/pr-$n-${head:0:8}.log"
      fail_gate "$n" "$head" "$(grep '^✘' "$out" | tail -1 | sed 's/^✘ //')" "$LOGS/pr-$n-${head:0:8}.log"
    else
      for pick in "${batch[@]}"; do read -r n head branch title <<<"$pick"; touch "$STATE/solo/$n-$head"; done
      log "gate: batch$names failed ($out); checking them one at a time"
    fi
    return 0
  fi
  push_checked "$main" "$out" "${batch[@]}"
}
still_shipping() {   # n head -> 0 when the PR is still open, labelled `ship`, not a draft and at that commit
  [[ "$(gh pr view "$1" --repo "$REPO" --json state,isDraft,headRefOid,labels \
    --jq "(.state == \"OPEN\" and (.isDraft | not) and .headRefOid == \"$2\" and ([.labels[].name] | index(\"$LABEL\") != null))" \
    2>/dev/null)" == true ]]
}
push_checked() {   # main log "n head branch title"... -> green statuses, then main gets exactly what was checked
  local main="$1" out="$2" pick n head branch title tries; shift 2
  g fetch -q origin main
  if [[ "$(main_sha)" != "$main" ]]; then log "gate: main moved during the checks; checking again"; return 0; fi
  for pick in "$@"; do
    read -r n head branch title <<<"$pick"
    still_shipping "$n" "$head" || { log "gate: #$n changed during the checks; checking again"; return 0; }
  done
  for pick in "$@"; do
    read -r n head branch title <<<"$pick"
    github_status "$head" success "Checks and PR rules passed on main ${main:0:7}"
  done
  if [[ "$DRY" == 1 ]]; then log "dry run: would push $(g rev-parse --short HEAD) to main now"; return 0; fi
  # A fast-forward of main to the checked merges; GitHub marks each PR merged once its head is on main. GitHub
  # sometimes answers a push with a passing "Internal Server Error", so a second try.
  for tries in 1 2; do
    if g push -q origin "HEAD:refs/heads/main" >>"$out" 2>&1; then
      for pick in "$@"; do
        read -r n head branch title <<<"$pick"
        touch "$STATE/merged/$n-$head"
        log "gate: #$n merged"; notify "Merged #$n: $title"
      done
      g fetch -q origin main
      return 0
    fi
    g fetch -q origin main
    [[ "$(main_sha)" == "$main" ]] || { log "gate: main moved before the push; checking again"; return 0; }
    sleep 5
  done
  for pick in "$@"; do
    read -r n head branch title <<<"$pick"
    fail_gate "$n" "$head" "GitHub refused the push to main (see the log)" "$out"
  done
}
ship_prs() {   # "number head branch title" per open, non-draft `ship` PR to main, oldest first
  gh pr list --repo "$REPO" --label "$LABEL" --state open --limit 50 \
    --json number,headRefOid,headRefName,title,isDraft,baseRefName,isCrossRepository \
    --jq 'sort_by(.number)[] | select(.isDraft | not) | select(.baseRefName == "main") | select(.isCrossRepository | not)
          | "\(.number) \(.headRefOid) \(.headRefName) \(.title)"'
}
gate_next() {   # returns 1 when no PR is waiting
  local prs n head branch title picks=()
  prs="$(ship_prs)" || { log "gate: couldn't list PRs"; return 1; }
  while read -r n head branch title; do
    [[ -n "$n" && ! -f "$STATE/gate-failed/$n-$head" && ! -f "$STATE/merged/$n-$head" ]] || continue
    if [[ -f "$STATE/solo/$n-$head" ]]; then   # from a red batch: checked on its own once it's the oldest waiting
      if (( ${#picks[@]} == 0 )); then picks=("$n $head $branch $title"); break; fi
      continue
    fi
    picks+=("$n $head $branch $title")
    (( ${#picks[@]} < BATCH )) || break
  done <<<"$prs"
  (( ${#picks[@]} )) || return 1
  if [[ "$DRY" == 1 ]]; then gate "${picks[@]}"; return 1; fi
  gate "${picks[@]}"
}

# ── fix: headless Claude repairs a PR the gate failed ────────────────────────────────────────────────────────────
# Claude works in the clone on the PR's head commit and only commits. oyster-ci pushes the commits to the PR's branch
# without force, so a session that pushed meanwhile wins, and the gate checks them like any other push. Claude can't
# push, call gh, publish or reach other hosts (fix-guard.sh, --disallowedTools); auto mode reviews the rest.
LIMIT_RE='(usage|rate)[ _]limit|limit (reached|resets)|hit your limit'
fix_tries() { cat "$STATE/fix/$1" 2>/dev/null || echo 0; }
fixable() {   # pr head -> 0 when Claude should try this failure
  [[ "$FIX_ON" == 1 && "$DRY" != 1 ]] || return 1
  [[ "$(cat "$STATE/gate-failed/$1-$2" 2>/dev/null)" != "GitHub refused"* ]] || return 1
  [[ ! -f "$STATE/fix/$1-$2" ]] || return 1   # one attempt per commit
  (( $(fix_tries "$1") < FIX_ATTEMPTS ))
}
fix_next() {   # returns 1 when nothing needs fixing
  local prs n head branch title wait
  wait="$(state fix-wait)"
  (( $(date +%s) >= ${wait:-0} )) || return 1   # Claude's usage limit: wait it out
  prs="$(ship_prs)" || return 1
  while read -r n head branch title; do
    [[ -n "$n" && -f "$STATE/gate-failed/$n-$head" ]] && fixable "$n" "$head" && { fix "$n" "$head" "$branch" "$title"; return 0; }
  done <<<"$prs"
  return 1
}
fix() {   # pr head branch title
  local n="$1" head="$2" branch="$3" title="$4" reason gate_log out json tries rc=0 summary session new
  reason="$(cat "$STATE/gate-failed/$n-$head" 2>/dev/null || echo "asked by hand (oyster-ci.sh fix)")"
  gate_log="$LOGS/pr-$n-${head:0:8}.log"; [[ -f "$gate_log" ]] || gate_log=""
  out="$LOGS/fix-$n-${head:0:8}.log" json="$STATE/fix-result.json"
  g fetch -q origin main || { log "fix: couldn't fetch main"; return 0; }
  g fetch -q origin "pull/$n/head" || { log "fix: couldn't fetch #$n"; return 0; }
  [[ "$(g rev-parse FETCH_HEAD)" == "$head" ]] || { log "fix: #$n moved; the gate checks the new commit first"; return 0; }
  # Recorded before Claude starts, so a crash doesn't start the same fix every 5 minutes.
  touch "$STATE/fix/$n-$head"
  tries=$(( $(fix_tries "$n") + 1 )); echo "$tries" >"$STATE/fix/$n"
  log "fix: #$n ${head:0:7} attempt $tries/$FIX_ATTEMPTS ($reason)"
  checkout "$head" || { log "fix: couldn't check out #$n"; return 0; }
  (cd "$WORK" && venv_sync) >"$out" 2>&1 || true
  set_state fixing "$n"; job_start "fix #$n"
  fix_prompt "$n" "$title" "$branch" "$reason" "$gate_log" |
    (cd "$WORK" && GIT_AUTHOR_NAME="oyster-ci (Claude)" GIT_COMMITTER_NAME="oyster-ci (Claude)" \
      GIT_AUTHOR_EMAIL=oyster-ci@localhost GIT_COMMITTER_EMAIL=oyster-ci@localhost \
      perl -e 'alarm shift; exec @ARGV or die "exec: $!"' $(( FIX_MINUTES * 60 )) \
      claude -p --model "$FIX_MODEL" --output-format json \
        --permission-mode auto --permission-prompts none \
        --tools Bash,Read,Edit,Write,Glob,Grep --add-dir "$LOGS" \
        --disallowedTools "Bash(git push *)" "Bash(gh *)" \
        --settings "{\"hooks\":{\"PreToolUse\":[{\"matcher\":\"Bash\",\"hooks\":[{\"type\":\"command\",\"command\":\"bash '$CI_SCRIPTS/fix-guard.sh'\"}]}]}}") \
    >"$json" 2>>"$out" || rc=$?
  rm -f "$STATE/fixing"
  summary="$(jq -r '.result // empty' "$json" 2>/dev/null || true)"
  session="$(jq -r '.session_id // empty' "$json" 2>/dev/null || true)"
  { echo "exit $rc, session ${session:-?}"; echo "$summary"; } >>"$out"
  new="$(g rev-parse HEAD)"
  if [[ "$new" == "$head" && "$summary $(tail -n 5 "$out")" =~ $LIMIT_RE ]]; then
    # Not the PR's fault: give the attempt back and try again in an hour.
    rm -f "$STATE/fix/$n-$head"; echo $(( tries - 1 )) >"$STATE/fix/$n"
    set_state fix-wait $(( $(date +%s) + 3600 ))
    log "fix: #$n waiting an hour: Claude's usage limit ($out)"; return 0
  fi
  (( rc == 142 )) && summary="Stopped after $FIX_MINUTES minutes (OYSTER_CI_FIX_MINUTES). ${summary}"
  if [[ "$new" != "$head" ]] && g merge-base --is-ancestor "$head" "$new" \
     && g push -q origin "HEAD:refs/heads/$branch" >>"$out" 2>&1; then
    log "fix: #$n pushed ${new:0:7}"
    notify "Claude fixed #$n; checking it again"
    pr_comment "$n" "$(printf '**Claude fixed this** in %s (oyster-ci attempt %s of %s); the gate checks it next.\n\n%s\n\n<sub>Its session: `cd ~/.oyster-ci/work && claude --resume %s`</sub>' \
      "${new:0:7}" "$tries" "$FIX_ATTEMPTS" "${summary:-No summary.}" "${session:-?}")"
  else
    [[ "$new" != "$head" ]] && summary="oyster-ci couldn't push its commits to \`$branch\` (the branch moved, or they don't build on the PR's head). ${summary}"
    log "fix: #$n not fixed ($out)"
    notify "Claude couldn't fix #$n: over to the branch's session"
    pr_comment "$n" "$(printf '**Claude couldn'"'"'t fix this** (oyster-ci attempt %s of %s). Push a fix, or run `bash tools/ci/oyster-ci.sh fix %s` to let it try again.\n\n%s\n\n<sub>Its session: `cd ~/.oyster-ci/work && claude --resume %s`</sub>' \
      "$tries" "$FIX_ATTEMPTS" "$n" "${summary:-It stopped without a summary; see oyster-ci.sh log fix-$n.}" "${session:-?}")"
  fi
}
fix_prompt() {   # pr title branch reason gate-log
  local gate_log="${5:-none (the gate stopped before writing one)}"
  cat <<EOF
oyster-ci (docs/ci.md) couldn't merge PR #$1 "$2" (branch $3): $4
The gate's log: $gate_log

You are in oyster-ci's clone of the Job Oyster repo with the PR's head commit checked out (detached HEAD). origin/main
is current. Make the gate pass:
- Conflicts with main: \`git merge origin/main\`, resolve every conflict keeping what both sides meant, and commit the
  merge. CHANGELOG.md merges by union: keep every entry from both sides, newest date first, one heading per date.
- Failing checks: find the cause in the gate's log and fix it in the code. Run the failing test, then
  \`bash tools/ci/checks.sh origin/main\` (the gate's checks) before you finish.
- PR rules: you can add the missing CHANGELOG.md entry (under today's date and the right area, a bold public headline
  naming the PR's commit, then a short paragraph), but you can't edit the PR description.
- Personal details in the public files (the privacy check): replace them with made-up ones (the Jordan Rivera example
  profile, Northwind Cloud, Fabrikam Games, Contoso Health). If a file truly needs the term, commit nothing and say so.

Rules:
- Commit your work; don't push. oyster-ci pushes your commits to $3 and gates them again.
- Fix the cause. Never weaken, skip or delete tests or checks, and don't change what the PR does beyond the fix.
- No git push, gh, publishing, signing or releases, and never touch the app running on this Mac.
- If the PR can't fix it (a flaky test, infrastructure, or something only the repo's owner can decide), commit nothing.

Your last message becomes the PR comment: 2 to 5 plain sentences on what was wrong and what you changed, or why you
couldn't fix it.
EOF
}

# ── self-update: run main's tools/ci next tick ───────────────────────────────────────────────────────────────────
refresh_bin() {
  g cat-file -e origin/main:tools/ci/oyster-ci.sh 2>/dev/null || return 0
  local tmp="$CI/bin.new"
  rm -rf "$tmp" && mkdir -p "$tmp" && g archive origin/main tools/ci | tar -x -C "$tmp" && rm -rf "$BIN" && mv "$tmp" "$BIN"
}

tick() {
  [[ -f "$STATE/paused" ]] && { log "paused (oyster-ci.sh resume)"; return 0; }
  [[ -d "$WORK/.git" ]] || { log "no clone at $WORK: run tools/ci/install.sh"; return 1; }
  lock || return 0   # another run is busy with a long job (checks, a fix)
  if [[ "$DRY" == 1 ]]; then
    g fetch -q origin main; gate_next || true; job_end; return 0
  fi
  job_end   # left over from a run that was killed
  find "$LOGS" -name '*.log' -mtime +14 -delete 2>/dev/null || true
  local i=0
  while (( i++ < 30 )); do
    resources_ok || { log "waiting: $WHY"; break; }
    g fetch -q origin main || { log "fetch failed"; break; }
    gate_next && continue
    fix_next && continue
    break
  done
  job_end
  refresh_bin || log "couldn't refresh $BIN from main"
  set_state last-tick "$(date '+%F %H:%M')"
}

pr_state() {   # pr head draft -> "state<TAB>reason"
  local n="$1" head="$2" job reason
  job="$(state job)"
  if [[ -f "$STATE/gate-failed/$n-$head" ]]; then
    reason="$(cat "$STATE/gate-failed/$n-$head")"
    if [[ "$(state fixing)" == "$n" ]] && running; then printf 'fixing\t%s\n' "$reason"
    elif [[ -f "$STATE/fix/$n-$head" ]]; then printf 'tried\t%s\n' "$reason"
    elif fixable "$n" "$head"; then printf 'fix-next\t%s\n' "$reason"
    else printf 'failed\t%s\n' "$reason"; fi
  elif [[ "$job" == gate* && " $job " == *" #$n "* ]] && running; then printf 'checking\t\n'
  elif [[ -f "$STATE/solo/$n-$head" ]]; then printf 'solo\t\n'
  elif [[ "$3" == true ]]; then printf 'draft\t\n'
  else printf 'waiting\t\n'; fi
}

status() {
  local main loaded="not loaded (tools/ci/install.sh)"
  launchctl print "gui/$(id -u)/io.github.job-oyster.ci" >/dev/null 2>&1 && loaded="loaded, every 5 min"
  [[ -f "$STATE/paused" ]] && loaded="$loaded, PAUSED"
  if running; then
    loaded="$loaded, running now$([[ -f "$STATE/job" ]] && echo ": $(state job)") (pid $(cat "$CI/lock/pid" 2>/dev/null))"
  fi
  git -C "$WORK" fetch -q origin main 2>/dev/null || true
  main="$(main_sha 2>/dev/null || echo '?')"
  echo "oyster-ci: $loaded; last run $(state last-tick)"
  echo "main        ${main:0:7}"
  echo "auto-fix    $([[ "$FIX_ON" == 1 ]] && echo "on ($FIX_MODEL, $FIX_ATTEMPTS tries per PR)" || echo off)"
  echo "queue (label $LABEL):"
  gh pr list --repo "$REPO" --label "$LABEL" --state open --json number,headRefOid,title,isDraft \
    --jq 'sort_by(.number)[] | "\(.number) \(.headRefOid) \(.isDraft) \(.title)"' 2>/dev/null |
    while read -r n head draft title; do
      local st reason why=""
      IFS=$'\t' read -r st reason <<<"$(pr_state "$n" "$head" "$draft")"
      case "$st" in
        checking) why=" (checking now)" ;;
        solo) why=" (checked on its own next: its batch failed)" ;;
        draft) why=" (draft)" ;;
        failed) why=" (failed: $reason)" ;;
        fixing) why=" (failed: $reason; Claude is fixing it)" ;;
        tried) why=" (failed: $reason; Claude tried, $(fix_tries "$n")/$FIX_ATTEMPTS)" ;;
        fix-next) why=" (failed: $reason; Claude fixes it next)" ;;
      esac
      echo "  #$n $title$why"
    done
  echo "last log lines:"
  tail -n 8 "$LOGS/oyster-ci.log" 2>/dev/null | sed 's/^/  /'
}

case "${1:-status}" in
  tick) tick ;;
  status) status ;;
  retry)
    case "${2:-}" in
      [0-9]*) rm -f "$STATE/gate-failed/$2-"* "$STATE/solo/$2-"* "$STATE/fix/$2" "$STATE/fix/$2-"* ;;
      *) echo "usage: oyster-ci.sh retry <pr-number>"; exit 2 ;;
    esac
    echo "queued for the next tick" ;;
  fix)
    [[ "${2:-}" =~ ^[0-9]+$ ]] || { echo "usage: oyster-ci.sh fix <pr-number>"; exit 2; }
    lock || { echo "oyster-ci is busy with a job; try again when status no longer says running"; exit 1; }
    g fetch -q origin main
    IFS=$'\t' read -r head branch title <<<"$(gh pr view "$2" --repo "$REPO" --json headRefOid,headRefName,title \
      --jq '"\(.headRefOid)\t\(.headRefName)\t\(.title)"')"
    rm -f "$STATE/fix/$2" "$STATE/fix/$2-"*   # a fresh set of attempts
    fix "$2" "$head" "$branch" "$title"; job_end; tail -n 8 "$LOGS/oyster-ci.log" ;;
  pause) touch "$STATE/paused"; echo "paused" ;;
  resume) rm -f "$STATE/paused"; echo "resumed" ;;
  log)
    if [[ -n "${2:-}" ]]; then f="$(ls -t "$LOGS"/*"$2"*.log 2>/dev/null | head -1)"; tail -n 80 "${f:?no log matches $2}"
    else tail -n 40 "$LOGS/oyster-ci.log"; fi ;;
  *) sed -n '2,26p' "$0"; exit 2 ;;
esac

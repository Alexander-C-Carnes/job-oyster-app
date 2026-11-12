# oyster-ci: check and merge on this Mac

The owner's Mac runs **oyster-ci**, a launchd agent
(`io.github.job-oyster.ci`) that runs `tools/ci/oyster-ci.sh tick` every 5 minutes at low priority. It's built like
goose-ci in the Goose repo. No Claude session has to wait for a merge, pull main or restart the app, and nobody has to
ask for a merge.

This repo is public. Only people with write access can add the `ship` label, and a labelled PR's code runs on this
Mac (the tests, and Claude's fixes), so read a contributor's PR before labelling it.

## What happens to a change

```
session: tests green (tools/ci/checks.sh), CHANGELOG.md entry, open a PR labelled `ship`, stop
   │
oyster-ci gate (next tick)  each PR: merge with main → PR rules; then the queue together: checks → push to main
   │                        (GitHub marks each PR merged; the desktop app archives its session)
   │                        red: status + PR comment + notification
oyster-ci fix (red PRs)     headless Claude (Sonnet) fixes the PR in the clone → oyster-ci pushes to its branch → gate again
   │
the app (OYSTER_FOLLOW_MAIN) pulls main into ~/Documents/job-search and restarts in place when no run is going
GitHub, daily 07:00 UTC     the Mac app workflow releases the DMGs; mac/auto-sign.sh signs them
```

**Don't merge `ship` PRs by hand** or push to main: a hand merge skips the checks, and main moving under a gate makes
oyster-ci throw its checks away and start again. The pre-push hook `tools/ci/install.sh` puts in the main checkout
blocks pushes to main (`ALLOW_MAIN_PUSH=1` once, if you really mean it).

### Gate

For the open, non-draft PRs to `main` labelled `ship`, oldest first, up to `OYSTER_CI_BATCH` (8) at a time:

1. Merges each with the current main on its own in the clone. A conflict fails that PR. `CHANGELOG.md` merges by
   union (`.gitattributes`), so two PRs adding entries don't conflict.
2. **PR rules** for each (`tools/ci/pr_rules.py`): the PR adds a `CHANGELOG.md` entry, or its description has a line
   `Changelog: none — <why>`.
3. **Batch:** merges the PRs that passed into main together (one that conflicts with an earlier PR in the batch waits
   for the next one) and runs the **checks** (`tools/ci/checks.sh`) once on the result: pytest, the privacy check
   (`tools/privacy_check.py`: nothing personal in a public repo), and `bash -n` / `swiftc -typecheck` on changed scripts.
4. Green: a `oyster-ci` commit status on each PR, then a fast-forward push of exactly what was checked to main, with
   merge commits like GitHub's ("Merge pull request #N from …"). GitHub marks each PR merged. If main or a PR moved
   during the checks, it checks again first. Red: a batch doesn't say whose fault it is, so its PRs are checked one at
   a time next and only the culprit fails.

A failure posts a red status and a PR comment with the end of the log, and shows a notification. That commit isn't
checked again: Claude tries to fix it (below), or you push a fix, or run `oyster-ci.sh retry <pr>`. To hold a PR back,
leave `ship` off or open it as a draft.

### Fix (Claude)

When nothing else is waiting, oyster-ci picks a `ship` PR whose last commit failed the gate and runs headless Claude
(`claude -p`, your subscription, model `OYSTER_CI_FIX_MODEL`, Sonnet) in the clone on that commit. Claude gets the
failure and the gate's log, merges main and resolves conflicts, fixes failing tests, adds a missing CHANGELOG entry or
replaces personal details with made-up ones. It can't edit the PR description.

Claude only commits. oyster-ci pushes its commits to the PR's branch without force (if the branch moved meanwhile,
the push fails and nothing is lost), comments Claude's summary on the PR, and the gate checks the new commit like any
other push. Claude can't push, call `gh`, publish, sign, touch the running app or reach other hosts:
`tools/ci/fix-guard.sh` blocks those commands and auto mode reviews everything else. It must not weaken or skip
tests to get green.

Limits: one attempt per commit, `OYSTER_CI_FIX_ATTEMPTS` (2) per PR, `OYSTER_CI_FIX_MINUTES` (30) per attempt, one
fix at a time. Hitting Claude's usage limit doesn't count as an attempt; oyster-ci waits an hour. The PR comment
names Claude's session: `cd ~/.oyster-ci/work && claude --resume <id>`. `oyster-ci.sh fix <pr>` starts a fresh set
of attempts now; `OYSTER_CI_AUTOFIX=0` turns fixing off.

### The app on this Mac

oyster-ci can't update `~/Documents/job-search`: macOS doesn't let a background job read `~/Documents`. The app does
it itself: with `OYSTER_FOLLOW_MAIN=1` in `~/JobOyster/.env`, the running app (`oyster run`, port 8765) fetches main
every 5 minutes, and when there's something new, the checkout is on main with no changes and no run is going, it runs
`oyster update` and restarts in place, the same process (`jobpipe/web/follow.py`). It runs detached from any window
(`./oyster start`, log `~/Library/Logs/Job Oyster/app.log`, which shows what came in): an app started in a chat's
terminal tab stops when the desktop app archives that chat and closes the tab.

## Commands

```bash
bash tools/ci/oyster-ci.sh status            # the queue, the last log lines
bash tools/ci/oyster-ci.sh log               # the main log; `log pr-129`, `log fix-115` for a job's
bash tools/ci/oyster-ci.sh retry 129
bash tools/ci/oyster-ci.sh fix 129           # Claude fixes #129's failed gate now (a fresh set of attempts)
bash tools/ci/oyster-ci.sh pause             # / resume
bash tools/ci/oyster-ci.sh tick              # run once now, in this terminal
OYSTER_CI_DRY_RUN=1 bash tools/ci/oyster-ci.sh tick   # gate the next PRs without statuses, comments or pushes
bash tools/ci/checks.sh                      # the gate's checks for your branch, before opening a PR
bash tools/ci/install.sh                     # set up or update the agent; --uninstall stops it
```

## Files

| Path | What |
|---|---|
| `~/.oyster-ci/work` | the clone (detached; oyster-ci checks out commits here). A clone, not a worktree: a worktree's git data lives in `~/Documents` |
| `~/.oyster-ci/venv` | its Python, with `requirements.txt` (synced when a PR changes it) |
| `~/.oyster-ci/bin/tools/ci` | the scripts launchd runs: main's copy, refreshed after every tick, so a PR's changes to oyster-ci never run until merged |
| `~/.oyster-ci/state` | failed gates, PRs to check alone (`solo/`), merged heads, fix attempts (`fix/`), paused flag |
| `~/.oyster-ci/logs` | `oyster-ci.log` plus one log per job (`pr-*`, `batch-*`, `fix-*`), kept 14 days |
| `~/.config/oyster-ci/env` | settings (below); never in git |
| `~/Library/LaunchAgents/io.github.job-oyster.ci.plist` | the agent (written by `install.sh`) |

## Settings (`~/.config/oyster-ci/env`)

| Variable | Default | What |
|---|---|---|
| `OYSTER_CI_AUTOFIX` | `1` | `0` stops Claude fixing failed PRs |
| `OYSTER_CI_FIX_MODEL` | `sonnet` | model for fixes |
| `OYSTER_CI_FIX_ATTEMPTS` | `2` | Claude attempts per PR |
| `OYSTER_CI_FIX_MINUTES` | `30` | time limit per attempt |
| `OYSTER_CI_BATCH` | `8` | PRs checked together in one run (`1` = one at a time) |
| `OYSTER_CI_MIN_FREE_GB` | `5` | wait below this much free disk |
| `OYSTER_CI_RUN_ON_BATTERY` | `1` | `0` waits for power |

## When oyster-ci can't do it

- **Conflicts or failing checks Claude couldn't fix:** its PR comment says why. The session that owns the branch (or
  a new one) pushes a fix, or `oyster-ci.sh fix <pr>` tries again.
- **The privacy check found a term a file really needs:** that's a line in the profile folder's
  `publish-allow.txt` (`path: term`), which is the owner's call.
- **Stop everything:** `oyster-ci.sh pause`, or `bash tools/ci/install.sh --uninstall`.

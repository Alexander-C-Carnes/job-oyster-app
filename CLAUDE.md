# Job Oyster

This repo is public: nothing personal goes in it (no names, employers, contact details or real tracker data, in
code, docs, tests, commit messages or PR text). `tools/privacy_check.py` checks every PR against the profile's terms.

## Shipping: oyster-ci does it (docs/ci.md)

This Mac runs **oyster-ci** (launchd, every 5 minutes). It checks and merges PRs labelled `ship` and has headless Claude
fix the ones that fail. The app on port 8765 pulls main and restarts on its own
(`OYSTER_FOLLOW_MAIN`), and the desktop app archives a session once its PR merges. Nobody has to ask for a
merge, a pull of main, an app restart or an archive.

- **Done means:** branch from `origin/main`; tests pass (`bash tools/ci/checks.sh`: pytest plus the privacy check);
  a `CHANGELOG.md` entry naming the commit (its headline becomes release notes), or a
  `Changelog: none — <why>` line in the PR description for internal work; push and `gh pr create --label ship`. Then
  say it's queued and stop. Don't wait for the merge.
- **Label `ship` as soon as the objective is done and verified**, without asking. Leave it off (or open a draft) only
  when the owner asked to see it first, or something in it is their decision; say which.
- **The app runs detached** (`./oyster start` in the main checkout, log `~/Library/Logs/Job Oyster/app.log`). Never
  start or restart it in a terminal tab (`run_in_terminal`, `oyster run`/`restart`): archiving that chat closes the tab
  and stops the app. Only start it when it isn't running (after a reboot); it follows main by itself.
- **Don't** merge (`gh pr merge`), push to main (the pre-push hook blocks it), pull the main checkout, restart the app
  (`oyster restart`): oyster-ci and the app do those.
- **Follow-up:** `bash tools/ci/oyster-ci.sh status` shows the queue. A failed gate posts a PR comment
  (`oyster-ci.sh log pr-<n>`), then headless Claude tries to fix it and pushes to the same branch: pull before pushing
  if you're still on it. Fix what it couldn't and push; the gate runs again.
- Tests from a worktree use the main checkout's venv: `<main checkout>/.venv/bin/python -m
  pytest -q` (`checks.sh` finds it by itself).

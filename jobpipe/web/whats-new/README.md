# What's new cards

When the app opens after an update, it shows a card for each new thing worth learning: a picture, a sentence or
two, and a button that takes you there. Everything else that changed (the `CHANGELOG.md` entries since you last
looked) is folded under the cards. Settings › About › What's new opens the last month's cards again.

## When a PR adds a card

Add one when the PR gives you something new to do or see: a feature, a new tab or button, a step that moved. Don't
add one for fixes, wording, speed or behind-the-scenes work; the changelog line covers those. One card per PR at
most. The `CHANGELOG.md` entry is still required either way.

oyster-ci's PR rules (`tools/ci/pr_rules.py`) hold a PR that adds a `CHANGELOG.md` entry until it adds or changes a
card, or says why not with a line `Card: none — <why>` in its description (or a commit message). A PR with
`Changelog: none` needs neither.

If a later PR changes something a card from the last 30 days describes (a renamed button, a moved tab), it updates
that card in the same PR. Cards older than 30 days never show again, so leave those alone.

## The file

`jobpipe/web/whats-new/<what-it-is>.md`, lowercase with hyphens. The name is the card's id, so don't rename one
that has shipped (it would show again).

````markdown
---
title: Interviewing? The role gets a prep kit      # 60 characters at most
date: 2026-11-17                                   # the day it ships, as in CHANGELOG.md
try: role:Interviewing/interview                   # where the button goes (optional)
button: Open a prep kit                            # the button's words (optional; default "Try it")
---
One or two plain sentences: what you can do now and where. 280 characters at most.

```html
<div class="wn-window wn-tilt">
  <div class="wn-head">Interview prep kit <span class="wn-tag">Interviewing</span></div>
  <div class="wn-row"><span class="wn-label">Likely questions</span><i class="wn-line"></i></div>
</div>
```
````

**`try`** is `tab:<tab>` (optionally `#<element id>` to scroll to), or `role:<stage>` (optionally `/<role tab>`),
which opens the first tracked role at that stage. Tabs: tracker, find, startups, searches, prep, dashboard, runs,
profile, impact, settings. Role tabs: posting, resume, report, heatmap, interview, sent.

**The picture** is a sketch of the screen, built only from these pieces, which every look styles in its own colours:

| Piece | What it draws |
|---|---|
| `wn-window` (+ `wn-tilt`) | the panel the sketch sits in (tilted a little) |
| `wn-head` | a title line; put a `wn-tag` in it for a pill |
| `wn-tabs` > `wn-tab` (+ `wn-on`) | a strip of tabs, one lit |
| `wn-row` | a soft row; holds a `wn-label` and lines, chips or a button |
| `wn-line` (+ `wn-short`) | a grey bar standing in for text |
| `wn-chips` > `wn-chip` (+ `wn-on`) | round chips, one lit; a `wn-dot` inside adds a colour dot |
| `wn-btn` | a button |
| `wn-score` (+ `wn-mid`, `wn-muted`) | a fit score badge: high, middling, low |
| `wn-stack`, `wn-inline`, `wn-grow` | stack, put side by side, fill the rest of a row |

Only `div`, `span`, `b` and `i` tags, and only `class=` attributes. Use example words, never real names.

## How it's checked

`tests/test_whats_new.py` runs in `tools/ci/checks.sh` and oyster-ci's gate. It fails a PR whose card is missing a
field, is too long, points `try` at a tab or stage that doesn't exist, or uses a tag or piece outside the list
above. The privacy check reads cards like any other file.

## How it reaches you

oyster-ci merges the PR; the app follows main and restarts. The next time the window opens (or comes back after the
restart), it asks `/api/whats-new` for cards you haven't seen from the last 30 days, three at most. Closing them
(Done, Skip all, Escape, or the button) records them in the profile's `data/whats-new.json`. With no new card,
nothing opens. A new profile starts with everything seen. The Mac app ships the cards and `CHANGELOG.md` in its
bundle, so a release shows the same cards.

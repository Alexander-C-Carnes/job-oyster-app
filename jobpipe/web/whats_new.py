"""What's new: the cards the app shows when it opens after an update.

A change you'd use gets a card: a file in jobpipe/web/whats-new/ written by the PR that makes the change
(whats-new/README.md says how). The app shows the cards you haven't seen, newest first, the next time it opens, with
everything CHANGELOG.md gained since you last looked folded underneath. Closing them marks them seen in the
profile's data/whats-new.json. Cards older than MAX_AGE_DAYS never show, so a stale one can't surprise anyone.

tests/test_whats_new.py checks every card (fields, the Try it target, the picture) on every PR.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

import yaml

from .. import config

CARDS = Path(__file__).parent / "whats-new"
CHANGELOG = config.ROOT / "CHANGELOG.md"
MAX_AGE_DAYS = 30           # a card older than this never shows
MAX_CARDS = 3               # at most this many at once, newest first

TITLE_MAX, BODY_MAX, BUTTON_MAX, ART_MAX = 60, 280, 24, 2500
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Where a card's Try it button can go: tab:<tab>[#<element id>] or role:<stage>[/<detail tab>].
# The tabs and stages are the app's own (the test checks them against index.html and app.js).
TABS = ("tracker", "find", "startups", "searches", "prep", "dashboard", "runs", "profile", "impact", "settings")
STAGES = ("Not started", "In progress", "Blocked", "Applied", "Interviewing", "Offer", "Denied", "Ghosted", "Done",
          "Not Applying")
DETAIL_TABS = ("posting", "resume", "report", "heatmap", "interview", "sent")

# The picture is drawn from these pieces only (app.css styles them in every look): no other tags or attributes.
ART_TAGS = {"div", "span", "b", "i"}
ART_CLASSES = {"wn-window", "wn-tilt", "wn-head", "wn-tag", "wn-row", "wn-label", "wn-line", "wn-short", "wn-chip",
               "wn-chips", "wn-dot", "wn-btn", "wn-tabs", "wn-tab", "wn-on", "wn-score", "wn-mid", "wn-muted",
               "wn-stack", "wn-inline", "wn-grow"}


@dataclass
class Card:
    slug: str
    title: str
    date: str           # YYYY-MM-DD: the day it ships, as in CHANGELOG.md
    body: str
    try_: str = ""      # where Try it goes; "" for no button
    button: str = "Try it"
    art: str = ""       # HTML from the wn-* pieces

    def public(self) -> dict:
        d = asdict(self)
        d["try"] = d.pop("try_")
        return d


def target_problem(target: str) -> Optional[str]:
    """Why a Try it target is wrong, or None."""
    kind, _, rest = target.partition(":")
    if kind == "tab":
        tab, _, anchor = rest.partition("#")
        if tab not in TABS:
            return f"try: no tab {tab!r} (one of {', '.join(TABS)})"
        if anchor and not re.fullmatch(r"[A-Za-z][\w-]*", anchor):
            return f"try: {anchor!r} isn't an element id"
        return None
    if kind == "role":
        stage, _, tab = rest.partition("/")
        if stage not in STAGES:
            return f"try: no stage {stage!r} (one of {', '.join(STAGES)})"
        if tab and tab not in DETAIL_TABS:
            return f"try: no role tab {tab!r} (one of {', '.join(DETAIL_TABS)})"
        return None
    return "try: starts with tab: or role:"


class _ArtCheck(HTMLParser):
    def __init__(self):
        super().__init__()
        self.problems: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ART_TAGS:
            self.problems.append(f"art: <{tag}> isn't allowed (only {', '.join(sorted(ART_TAGS))})")
        for name, value in attrs:
            if name != "class":
                self.problems.append(f"art: the {name}= attribute isn't allowed (only class=)")
                continue
            for c in (value or "").split():
                if c not in ART_CLASSES:
                    self.problems.append(f"art: no piece called {c!r}")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def art_problems(art: str) -> list[str]:
    if len(art) > ART_MAX:
        return [f"art: longer than {ART_MAX} characters"]
    p = _ArtCheck()
    p.feed(art)
    p.close()
    return p.problems


ART = re.compile(r"^```html\s*\n(.*?)^```\s*$", re.S | re.M)


def parse(path: Path) -> tuple[Optional[Card], list[str]]:
    """A card file → (Card, []) or (None, the reasons it's wrong)."""
    text = path.read_text()
    slug = path.stem
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        return None, [f"{path.name}: starts with a --- block (title, date, try)"]
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        return None, [f"{path.name}: the --- block isn't YAML ({e})"]
    rest = m.group(2)
    art_m = ART.search(rest)
    art = art_m.group(1).strip() if art_m else ""
    body = " ".join((ART.sub("", rest)).split())
    problems = []
    if not SLUG.match(slug):
        problems.append(f"{path.name}: name it lowercase-with-hyphens.md")
    title, when = str(meta.get("title") or "").strip(), meta.get("date")
    target, button = str(meta.get("try") or "").strip(), str(meta.get("button") or "Try it").strip()
    if not title:
        problems.append("title: missing")
    elif len(title) > TITLE_MAX:
        problems.append(f"title: longer than {TITLE_MAX} characters")
    if isinstance(when, date):
        when = when.isoformat()
    if not (isinstance(when, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", when)):
        problems.append("date: missing, or not YYYY-MM-DD")
    if not body:
        problems.append("the text under the --- block is missing")
    elif len(body) > BODY_MAX:
        problems.append(f"text: longer than {BODY_MAX} characters")
    if len(button) > BUTTON_MAX:
        problems.append(f"button: longer than {BUTTON_MAX} characters")
    if target and (why := target_problem(target)):
        problems.append(why)
    problems += art_problems(art)
    unknown = set(meta) - {"title", "date", "try", "button"}
    if unknown:
        problems.append(f"unknown fields: {', '.join(sorted(unknown))}")
    if problems:
        return None, [f"{path.name}: {p}" if not p.startswith(path.name) else p for p in problems]
    return Card(slug, title, when, body, target, button, art), []


def load(folder: Path = CARDS) -> list[Card]:
    """Every card that parses, newest first (a broken one is left out; the test names it)."""
    cards = [c for p in sorted(folder.glob("*.md")) if p.name != "README.md" for c in [parse(p)[0]] if c]
    cards.sort(key=lambda c: c.slug)
    return sorted(cards, key=lambda c: c.date, reverse=True)     # newest day first; within a day, by name


DATE = re.compile(r"^## (\d{4}-\d{2}-\d{2})\b")
ENTRY = re.compile(r"^- (?:\*\*(.+?)\*\*|(.+))")      # as tools/release_notes.py reads it


def changes(text: str) -> list[dict]:
    """CHANGELOG.md's entries, newest first: {"date", "area", "headline"}."""
    out, day, area = [], "", ""
    for line in text.splitlines():
        if m := DATE.match(line):
            day, area = m.group(1), ""
        elif line.startswith("### "):
            area = line[4:].strip()
        elif day and (m := ENTRY.match(line)):
            head = (m.group(1) or m.group(2)).strip()
            head = re.sub(r"\s+@\w+$", "", head)                 # an owner tag on older entries
            out.append({"date": day, "area": area, "headline": head})
    return out


class Seen:
    """data/whats-new.json: the cards you've closed and the newest changelog day you've been shown."""

    def __init__(self, path: Path):
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            return {"cards": list(d.get("cards") or []), "since": str(d.get("since") or "")}
        except (OSError, ValueError, AttributeError):
            return {"cards": [], "since": ""}

    def write(self, cards: list[str], since: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"cards": sorted(set(cards)), "since": since}, indent=1))

    def mark(self, slugs: list[str], newest: str) -> None:
        d = self.read()
        self.write(d["cards"] + list(slugs), max(d["since"], newest))


def recent(cards: list[Card], today: date) -> list[Card]:
    cutoff = (today - timedelta(days=MAX_AGE_DAYS)).isoformat()
    return [c for c in cards if c.date >= cutoff]


def pending(cards: list[Card], seen: dict, log: list[dict], today: date, *, everything: bool = False) -> dict:
    """What to show: {"cards", "since", "changes"}. everything=True: the last MAX_AGE_DAYS's cards, seen or not
    (Settings › About › What's new)."""
    shown = recent(cards, today)
    if not everything:
        shown = [c for c in shown if c.slug not in set(seen["cards"])][:MAX_CARDS]
    cutoff = (today - timedelta(days=MAX_AGE_DAYS)).isoformat()
    since = "" if everything else seen["since"]
    newer = [e for e in log if e["date"] >= cutoff and (not since or e["date"] > since)]
    return {"cards": [c.public() for c in shown], "since": since, "changes": newer}

"""What's new cards (jobpipe/web/whats-new/): every shipped card is checked here, so a PR with a broken one fails."""
import re
import shutil
from datetime import date

import pytest
from fastapi.testclient import TestClient

from jobpipe import config
from jobpipe.store import Store
from jobpipe.web import whats_new
from jobpipe.web.server import create_app
from conftest import FakeRunner

STATIC = config.ROOT / "jobpipe" / "web" / "static"
TOKEN = "test-token"
H = {"X-Jobpipe-Token": TOKEN}
ART = '<div class="wn-window"><div class="wn-head">Kit <span class="wn-tag">New</span></div><i class="wn-line"></i></div>'


def card(folder, slug="a-card", **meta):
    art = meta.pop("art", ART)
    fields = {"title": "A new thing", "date": "2026-11-17", "try": "tab:tracker", **meta}
    head = "\n".join(f"{k}: {v}" for k, v in fields.items() if v is not None)
    (folder / f"{slug}.md").write_text(f"---\n{head}\n---\nWhat you can do now.\n\n```html\n{art}\n```\n")
    return folder / f"{slug}.md"


# ---- the shipped cards -----------------------------------------------------------------------------------------

def test_every_shipped_card_is_right():
    files = [p for p in whats_new.CARDS.glob("*.md") if p.name != "README.md"]
    assert files, "jobpipe/web/whats-new/ has no cards"
    problems = [why for p in files for why in whats_new.parse(p)[1]]
    assert not problems, "Fix these cards (jobpipe/web/whats-new/README.md):\n" + "\n".join(problems)


def test_a_card_that_points_at_a_place_on_a_tab_names_one_that_exists():
    html = (STATIC / "index.html").read_text()
    for c in whats_new.load():
        anchor = c.try_.partition("#")[2]
        if c.try_.startswith("tab:") and anchor:
            assert f'id="{anchor}"' in html, f"{c.slug}: index.html has no element with id {anchor!r}"


def test_the_targets_and_pieces_match_the_app():
    html, js, css = ((STATIC / f).read_text() for f in ("index.html", "app.js", "app.css"))
    assert set(whats_new.TABS) == set(re.findall(r'data-tab="([a-z]+)"', html))
    stages = re.search(r"^const STAGES = \[(.+?)\];", js, re.M).group(1)
    assert list(whats_new.STAGES) == re.findall(r'"([^"]+)"', stages)
    for tab in whats_new.DETAIL_TABS:
        assert f'"{tab}"' in js, f"app.js has no role tab {tab!r}"
    for piece in whats_new.ART_CLASSES:
        assert f".{piece}" in css, f"app.css doesn't style .{piece}"


# ---- what a card may hold ----------------------------------------------------------------------------------------

def test_a_good_card_parses(tmp_path):
    c, problems = whats_new.parse(card(tmp_path, button="Open it"))
    assert problems == [] and c.title == "A new thing" and c.date == "2026-11-17" and c.try_ == "tab:tracker"
    assert c.body == "What you can do now." and c.button == "Open it" and c.art == ART


@pytest.mark.parametrize("meta, says", [
    ({"title": "x" * 61}, "title: longer than"),
    ({"title": None}, "title: missing"),
    ({"date": "next week"}, "date:"),
    ({"try": "tab:nowhere"}, "no tab 'nowhere'"),
    ({"try": "role:Hired"}, "no stage 'Hired'"),
    ({"try": "role:Interviewing/notes"}, "no role tab 'notes'"),
    ({"try": "https://example.com"}, "starts with tab: or role:"),
    ({"button": "y" * 25}, "button: longer than"),
    ({"colour": "red"}, "unknown fields: colour"),
    ({"art": '<div class="wn-window"><script>alert(1)</script></div>'}, "<script> isn't allowed"),
    ({"art": '<div class="wn-window" style="color:red"></div>'}, "style= attribute"),
    ({"art": '<div class="wn-window big"></div>'}, "no piece called 'big'"),
    ({"art": '<img src="x">'}, "<img> isn't allowed"),
])
def test_a_wrong_card_says_what_to_fix(tmp_path, meta, says):
    c, problems = whats_new.parse(card(tmp_path, **meta))
    assert c is None and any(says in p for p in problems), problems


def test_a_broken_card_is_left_out_and_cards_come_newest_first(tmp_path):
    card(tmp_path, "older", date="2026-11-10")
    card(tmp_path, "b-newer")
    card(tmp_path, "a-newer")
    card(tmp_path, "broken", **{"try": "tab:nowhere"})
    (tmp_path / "README.md").write_text("not a card")
    assert [c.slug for c in whats_new.load(tmp_path)] == ["a-newer", "b-newer", "older"]


# ---- what shows ------------------------------------------------------------------------------------------------

LOG = """# Changelog

## 2026-11-17

### Job details
- **Prep kits** (abc1234). On Interviewing roles.
### Settings
- Settings has its own tab @someone

## 2026-11-12

### Tracker
- A Has pay filter

## 2026-09-01

### Tracker
- Something old
"""


def test_the_changelog_reads_like_the_release_notes():
    assert whats_new.changes(LOG) == [
        {"date": "2026-11-17", "area": "Job details", "headline": "Prep kits"},
        {"date": "2026-11-17", "area": "Settings", "headline": "Settings has its own tab"},
        {"date": "2026-11-12", "area": "Tracker", "headline": "A Has pay filter"},
        {"date": "2026-09-01", "area": "Tracker", "headline": "Something old"}]


def cards_(*specs):
    return [whats_new.Card(slug, slug, day, "text") for slug, day in specs]


def test_unseen_recent_cards_show_with_the_changes_since_you_last_looked():
    cards, log, today = cards_(("new", "2026-11-17"), ("seen", "2026-11-16"), ("old", "2026-10-01")), \
        whats_new.changes(LOG), date(2026, 11, 17)
    got = whats_new.pending(cards, {"cards": ["seen"], "since": "2026-11-12"}, log, today)
    assert [c["slug"] for c in got["cards"]] == ["new"]                 # "old" is past 30 days
    assert got["since"] == "2026-11-12" and [e["headline"] for e in got["changes"]] == ["Prep kits", "Settings has its own tab"]
    first = whats_new.pending(cards, {"cards": [], "since": ""}, log, today)
    assert [e["date"] for e in first["changes"]] == ["2026-11-17", "2026-11-17", "2026-11-12"]   # the month's, not older
    again = whats_new.pending(cards, {"cards": ["new", "seen"], "since": "2026-11-12"}, log, today, everything=True)
    assert [c["slug"] for c in again["cards"]] == ["new", "seen"] and again["since"] == ""


def test_at_most_three_cards_show_at_once():
    cards = cards_(*((f"c{i}", "2026-11-17") for i in range(5)))
    assert len(whats_new.pending(cards, {"cards": [], "since": ""}, [], date(2026, 11, 17))["cards"]) == whats_new.MAX_CARDS


# ---- the API ---------------------------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_dirs, tmp_path, monkeypatch):
    folder = tmp_path / "cards"
    folder.mkdir()
    card(folder, "prep-kits", date=date.today().isoformat())
    (tmp_path / "CHANGELOG.md").write_text(LOG.replace("2026-11-17", date.today().isoformat()))
    monkeypatch.setattr(whats_new, "CARDS", folder)
    monkeypatch.setattr(whats_new, "CHANGELOG", tmp_path / "CHANGELOG.md")
    searches = tmp_path / "searches.yaml"
    shutil.copy(config.EXAMPLE_PROFILE / "searches.yaml", searches)
    app = create_app(config.load(searches), token=TOKEN, store=Store(), tracker=None,
                     runner_factory=lambda cfg: FakeRunner(), allowed_hosts={"testserver"}, searches_path=searches)
    return TestClient(app)


def test_cards_show_until_closed_then_stay_away(client):
    assert client.put("/api/setup", json={"state": "done"}, headers=H).status_code == 200
    first = client.get("/api/whats-new", headers=H).json()
    assert [c["slug"] for c in first["cards"]] == ["prep-kits"] and first["cards"][0]["try"] == "tab:tracker"
    assert client.post("/api/whats-new/seen", json={"cards": ["prep-kits", "made-up"]}, headers=H).json() == {"ok": True}
    after = client.get("/api/whats-new", headers=H).json()
    assert after["cards"] == [] and after["since"] == date.today().isoformat() and after["changes"] == []
    assert [c["slug"] for c in client.get("/api/whats-new?all=1", headers=H).json()["cards"]] == ["prep-kits"]


def test_a_new_profile_starts_with_everything_seen(client):
    assert client.get("/api/whats-new", headers=H).json()["cards"] == []      # setup comes first, not old news
    client.put("/api/setup", json={"state": "done"}, headers=H)
    assert client.get("/api/whats-new", headers=H).json()["cards"] == []

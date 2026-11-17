"""An interview pack for one role: questions from the gaps, stories that cite the record, a debrief."""
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from jobpipe import config
from jobpipe.interview_pack import Packs, collect, company_text, honest, parse_research
from jobpipe.llm import AgentResult
from jobpipe.store import Store
from jobpipe.startups import StartupStore, funding_block
from jobpipe.web.server import create_app
from jobpipe import interview_pack
from conftest import make_job

TOKEN = "test-token"
H = {"X-Jobpipe-Token": TOKEN}

PACK = """## Questions

### AI evaluation depth
Ask: How have you evaluated model quality?
Story: Built an eval set of 40 tasks.
Cite: E1

### Staff scope
Ask: What did you own?
Story: Led 22 teams.
Cite: Northwind

### Money
Ask: What did the savings come to?
Story: Saved $12 million.
Cite: E1

## Questions to ask them

- How is the team structured?
- What does the first ninety days look like?
"""

RESEARCH = """## Products
Acme builds a platform for program teams.

## Funding history
Series B of $20M, from the record on file.

## Layoffs
Nothing on file gives a layoff count. Worth asking how the team has changed.

## Review themes
No review score is on file. Listen for how people describe the culture.
"""

RESEARCH_AGAIN = RESEARCH.replace("program teams", "operations teams")

MATCH = """# Match

## Evidence ledger

E1 — Built an eval set of 40 tasks for the platform.

## Unconfirmed gaps

- AI evaluation depth
- Staff scope
- Money
"""

IMPACT = """# Impact record

## Northwind

Led 22 teams.
"""


class PackRunner:
    def __init__(self):
        self.calls = []
        self.research_n = 0

    async def run(self, call):
        self.calls.append(call)
        if call.label == "interview-pack":
            return AgentResult(files={"pack.md": PACK}, summary="")
        if call.label == "company-research":
            self.research_n += 1
            return AgentResult(files={"research.md": RESEARCH if self.research_n == 1 else RESEARCH_AGAIN}, summary="")
        if call.label == "interview-pack-draft":
            return AgentResult(files={"lines.md": "- Led 22 teams.\n- Saved $9 million.\n"}, summary="")
        raise AssertionError(call.label)


def test_gaps_come_from_the_match_then_the_score_then_the_signal():
    match = collect("posting", MATCH, {"gaps": ["from full"]}, {"likely_gaps": ["from triage"]}, IMPACT, "", "")
    assert match.gaps == ["AI evaluation depth", "Staff scope", "Money"]
    assert "40 tasks" in match.ledger["E1"] and match.from_gaps
    full = collect("posting", "", {"gaps": ["from full"]}, {"likely_gaps": ["from triage"]}, "", "", "")
    assert full.gaps == ["from full"] and not full.ledger
    signal = collect("posting", "", None, {"likely_gaps": ["from triage"]}, "", "", "Mine.pdf")
    assert signal.gaps == ["from triage"] and signal.pdf_only == "Mine.pdf"
    assert not collect("posting", "", None, None, "", "", "").from_gaps


def test_a_story_survives_only_when_its_cite_supports_it():
    from jobpipe.interview_pack import parse
    questions, ask = parse(PACK)
    kept = {q["gap"]: q for q in honest(questions, collect("", MATCH, None, None, IMPACT).ledger, IMPACT)}
    assert kept["AI evaluation depth"]["story"] == "Built an eval set of 40 tasks."
    assert kept["AI evaluation depth"]["cite"] == "E1"
    assert kept["Staff scope"]["cite"] == "Northwind" and "22" in kept["Staff scope"]["story"]
    assert kept["Money"]["question"] and not kept["Money"]["story"] and not kept["Money"]["cite"]
    assert ask == ["How is the team structured?", "What does the first ninety days look like?"]
    # A cite that isn't a ledger id or a heading drops the story and keeps the question.
    invented = honest([{"gap": "Scope", "question": "Who was on it?", "story": "A team of 8.", "cite": "E9"}],
                      {"E1": "Led 22 teams."}, IMPACT)
    assert invented[0]["question"] == "Who was on it?" and not invented[0]["story"]


def test_company_text_keeps_stored_funding_and_the_role_pack_does_not_ask_for_research():
    text = company_text(
        {"stage": "Series B", "round": "Series B", "amount": "$20M", "date": "2026-03-01", "team_size": 40},
        {"name": "Acme", "one_liner": "Platform for program teams", "stage": "Series B", "team_size": 40,
         "round": {"name": "Series B", "amount_usd": 20000000, "date": "2026-03-01"}})
    assert "$20M" in text and "Platform for program teams" in text and "40" in text
    assert company_text(None, None) == ""
    sections = {s["heading"]: s["text"] for s in parse_research(RESEARCH)}
    assert "$20M" in sections["Funding history"]
    assert "layoff count" in sections["Layoffs"] and "review score" in sections["Review themes"]
    assert "research.md" not in interview_pack.PACK_BRIEF and "company.md" not in interview_pack.PACK_BRIEF
    assert "layoff count" in interview_pack.RESEARCH_BRIEF and "review score" in interview_pack.RESEARCH_BRIEF
    assert "company.md" in interview_pack.RESEARCH_BRIEF


def test_rebuilding_the_role_pack_keeps_company_research(tmp_path):
    packs = Packs(tmp_path)
    packs.save({"job_id": "j1", "status": "ready", "debrief": "I said 22.", "questions": [{"gap": "Scope"}],
                "company_research": {"status": "ready", "sections": [{"heading": "Funding", "text": "Series B of $20M"}]}})
    started = packs.begin_role("j1", status="making", questions=[], debrief="wiped")
    assert started["debrief"] == "I said 22." and started["status"] == "making"
    assert started["company_research"]["sections"][0]["text"] == "Series B of $20M"
    done = packs.finish("j1", status="ready", questions=[{"gap": "Scope", "question": "Who?"}], debrief="wiped")
    assert done["debrief"] == "I said 22."
    assert done["company_research"]["sections"][0]["text"] == "Series B of $20M"
    assert done["questions"][0]["question"] == "Who?"


def test_finish_keeps_a_debrief_saved_while_the_pack_was_making(tmp_path):
    packs = Packs(tmp_path)
    packs.save({"job_id": "j1", "status": "making", "debrief": "I said 22.", "added": 1})
    packs.put_debrief("j1", "I said 22 teams.")
    done = packs.finish("j1", status="ready", questions=[], debrief="wiped")
    assert done["debrief"] == "I said 22 teams." and done["added"] == 1 and done["status"] == "ready"


def _ready(client):
    for _ in range(50):
        pack = client.get("/api/jobs/j1/interview-pack", headers=H).json()
        if pack["status"] in ("ready", "failed"):
            return pack
        time.sleep(0.05)
    raise AssertionError(pack)


def test_marking_interviewing_makes_a_pack_and_a_debrief_can_add_a_line(env_pack):
    c, runner, impact = env_pack["client"], env_pack["runner"], env_pack["impact"]
    tid = env_pack["tracker_id"]
    assert c.get("/api/jobs/j1/interview-pack").status_code == 401
    # The status change does not build the pack. The app asks for one after it.
    assert c.post(f"/api/tracker/{tid}/status", headers=H, json={"status": "Interviewing"}).json() == {"ok": True}
    assert c.get("/api/jobs/j1/interview-pack", headers=H).json()["status"] == "none"
    notes = c.get("/api/interview-prep", headers=H).json()["markdown"]

    started = c.post("/api/jobs/j1/interview-pack", headers=H, json={}).json()
    assert started["status"] == "making"
    pack = _ready(c)
    assert pack["status"] == "ready", pack.get("error")
    by_gap = {q["gap"]: q for q in pack["questions"]}
    assert by_gap["AI evaluation depth"]["cite"] == "E1"
    assert by_gap["Staff scope"]["cite"] == "Northwind"
    assert by_gap["Money"]["question"] and not by_gap["Money"]["story"]
    assert pack["ask_them"][0] == "How is the team structured?"
    assert pack["from_gaps"] and not pack["pdf_only"]
    sent = next(call for call in runner.calls if call.label == "interview-pack")
    assert "Led 22 teams at Northwind." in sent.documents["resume.md"]
    assert "AI evaluation depth" in sent.documents["gaps.md"]
    assert "company.md" not in sent.documents
    assert not any(call.label == "company-research" for call in runner.calls)

    assert c.put("/api/jobs/j1/interview-pack", headers=H, json={"debrief": "I led 22 teams."}).json()["debrief"] == "I led 22 teams."
    calls = len(runner.calls)
    assert c.post("/api/jobs/j1/interview-pack", headers=H, json={}).json()["status"] == "ready"  # already made: left as it is
    assert len(runner.calls) == calls
    c.post("/api/jobs/j1/interview-pack", headers=H, json={"again": True})
    remade = _ready(c)
    assert remade["debrief"] == "I led 22 teams." and remade["status"] == "ready"
    assert len(runner.calls) == calls + 1

    drafted = c.post("/api/jobs/j1/interview-pack/draft", headers=H).json()
    by_text = {line["text"]: line for line in drafted["lines"]}
    assert by_text["Led 22 teams."]["ok"] and not by_text["Saved $9 million."]["ok"]
    assert "9" in by_text["Saved $9 million."]["why"]
    assert c.post("/api/jobs/j1/interview-pack/apply", headers=H, json={"lines": ["Led 22 teams."], "version": "nope"}).status_code == 409
    version = c.get("/api/impact-record", headers=H).json()["version"]
    saved = c.post("/api/jobs/j1/interview-pack/apply", headers=H, json={"lines": ["Led 22 teams."], "version": version}).json()
    assert "Led 22 teams." in saved["markdown"] and "From your interviews" in saved["markdown"]
    assert "the interview for Acme" in impact.read_text()
    assert c.get("/api/jobs/j1/interview-pack", headers=H).json()["added"] == 1
    listed = c.get("/api/interview-packs", headers=H).json()["packs"]
    assert listed[0]["job_id"] == "j1" and listed[0]["company"] == "Acme"
    assert c.get("/api/interview-prep", headers=H).json()["markdown"] == notes
    c.put("/api/jobs/j1/interview-pack", headers=H, json={"debrief": ""})
    assert c.post("/api/jobs/j1/interview-pack/draft", headers=H).status_code == 400


def _research(client):
    for _ in range(50):
        pack = client.get("/api/jobs/j1/interview-pack", headers=H).json()
        if pack["company_research"]["status"] in ("ready", "failed"):
            return pack
        time.sleep(0.05)
    raise AssertionError(pack)


def test_company_research_runs_only_when_asked_and_a_second_click_does_not(env_pack):
    c, runner = env_pack["client"], env_pack["runner"]
    c.post("/api/jobs/j1/interview-pack", headers=H, json={})
    pack = _ready(c)
    assert pack["company_research"]["status"] == "none"
    assert not any(call.label == "company-research" for call in runner.calls)
    role_calls = [call for call in runner.calls if call.label == "interview-pack"]

    started = c.post("/api/jobs/j1/interview-pack/research", headers=H, json={}).json()
    assert started["company_research"]["status"] == "making"
    researched = _research(c)
    assert researched["status"] == "ready"
    by_heading = {s["heading"]: s["text"] for s in researched["company_research"]["sections"]}
    assert "$20M" in by_heading["Funding history"]
    assert "layoff count" in by_heading["Layoffs"]
    assert researched["debrief"] == "" and researched["questions"]
    call = next(call for call in runner.calls if call.label == "company-research")
    assert "The role owns platform programs" in call.documents["posting.md"]
    assert "$20M" in call.documents["company.md"] and "Platform for program teams" in call.documents["company.md"]
    assert "layoff count" in call.instructions and "review score" in call.instructions
    assert [call.label for call in runner.calls if call.label == "interview-pack"] == [role.label for role in role_calls]

    again_click = c.post("/api/jobs/j1/interview-pack/research", headers=H, json={}).json()
    assert again_click["research_already"] is True
    assert len([call for call in runner.calls if call.label == "company-research"]) == 1
    assert again_click["questions"] == researched["questions"]

    c.put("/api/jobs/j1/interview-pack", headers=H, json={"debrief": "I led 22 teams."})
    c.post("/api/jobs/j1/interview-pack/research", headers=H, json={"again": True})
    replaced = _research(c)
    assert "operations teams" in replaced["company_research"]["sections"][0]["text"]
    assert replaced["debrief"] == "I led 22 teams."
    assert {q["gap"] for q in replaced["questions"]} == {q["gap"] for q in researched["questions"]}
    assert len([call for call in runner.calls if call.label == "company-research"]) == 2

    calls = len(runner.calls)
    c.post("/api/jobs/j1/interview-pack", headers=H, json={"again": True})
    remade = _ready(c)
    assert remade["debrief"] == "I led 22 teams."
    assert "operations teams" in remade["company_research"]["sections"][0]["text"]
    assert not any(call.label == "company-research" for call in runner.calls[calls:])
    assert len(runner.calls) == calls + 1


@pytest.fixture
def env_pack(tmp_path, tmp_dirs):
    """A tracked role with a match file, a sent résumé's text, and a stand-in for the AI."""
    store = Store()
    startups = StartupStore(store.root / "startups.json")
    startups.merge([{"name": "Acme", "website": "https://acme.example", "one_liner": "Platform for program teams",
                     "stage": "Series B", "team_size": 40,
                     "round": {"name": "Series B", "amount_usd": 20000000, "currency": "$", "date": "2026-03-01",
                               "headline": "Acme raises $20M Series B", "source": "FinSMEs"}}])
    acme = startups.by_company("Acme", "https://acme.example")
    store.save_job(make_job("j1", funding=funding_block(acme), company_domain="acme.example"), "manual")
    run = tmp_path / "run"
    run.mkdir()
    (run / "04-match.md").write_text(MATCH)
    (run / "jd.md").write_text("The role owns platform programs and AI evaluation.")
    (run / "resume-final.md").write_text("# Jordan Rivera\n\n- Led 22 teams at Northwind.\n")
    pdf = run / "out.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    store.update("j1", run_dir=str(run), pdf=str(pdf), tailored_at="2026-10-31")
    searches = tmp_path / "searches.yaml"
    shutil.copy(config.EXAMPLE_PROFILE / "searches.yaml", searches)
    impact = tmp_path / "impact-record.md"
    impact.write_text(IMPACT)
    runner = PackRunner()
    app = create_app(config.load(searches), token=TOKEN, store=store, tracker=None,
                     runner_factory=lambda cfg: runner, allowed_hosts={"testserver"},
                     searches_path=searches, resume_root=tmp_path / "repo", impact_path=impact)
    client = TestClient(app)
    assert client.post("/api/jobs/j1/track", headers=H).json()["action"] == "created"
    row = next(j for j in client.get("/api/jobs", headers=H).json()["jobs"] if j["id"] == "j1")
    return {"client": client, "runner": runner, "impact": impact, "tracker_id": row["tracker_id"]}

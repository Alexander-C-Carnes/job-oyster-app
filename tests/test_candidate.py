import subprocess
import sys
from datetime import date

import pytest

from jobpipe import config, evidence, llm, triage
from jobpipe.config import candidate as load_candidate   # the real loader; conftest swaps config.candidate
from jobpipe.tailor import section
from jobpipe.web import resumes
from conftest import CANDIDATE, _resume

PLACEHOLDERS = ("{name}", "{first}", "{contact}", "{city}", "{linkedin}", "{pdf_prefix}", "{pdf_name}")


def test_no_candidate_section_means_the_placeholder(tmp_path):
    p = tmp_path / "searches.yaml"
    p.write_text("searches: []\n")
    assert load_candidate(p) == config.PLACEHOLDER
    assert load_candidate(tmp_path / "missing.yaml") == config.PLACEHOLDER
    assert config.PLACEHOLDER.pdf_prefix == "Your-Name-Resume"


def test_a_new_profile_starts_from_the_example(tmp_path, monkeypatch):
    monkeypatch.delenv("JOBPIPE_CONFIG")
    monkeypatch.delenv("JOBPIPE_DATA_DIR", raising=False)
    monkeypatch.delenv("JOBPIPE_OUTPUTS_DIR", raising=False)
    old = config.PROFILE
    config.set_profile(tmp_path / "JobSearch")
    try:
        assert config.ensure_profile(log=lambda m: None)
        home = tmp_path / "JobSearch"
        assert config.searches_path() == home / "searches.yaml" and config.DATA == home / "data"
        assert (home / "references" / "impact-record.md").exists() and (home / ".env").exists()
        assert load_candidate(home / "searches.yaml").name == "Jordan Rivera"
        (home / "searches.yaml").write_text("searches: []\n")
        assert not config.ensure_profile(log=lambda m: None)       # never overwrites a profile
        assert (home / "searches.yaml").read_text() == "searches: []\n"
    finally:
        config.set_profile(old)


def test_a_job_search_profile_moves_to_job_oyster(tmp_path, monkeypatch):
    """The app was called Job Search: its ~/JobSearch becomes ~/JobOyster, with a link left at the old path."""
    monkeypatch.delenv("JOBPIPE_HOME", raising=False)
    monkeypatch.delenv("JOBPIPE_DATA_DIR", raising=False)
    monkeypatch.delenv("JOBPIPE_OUTPUTS_DIR", raising=False)
    old_home, home = tmp_path / "JobSearch", tmp_path / "JobOyster"
    monkeypatch.setattr(config, "OLD_HOME", old_home)
    monkeypatch.setattr(config, "HOME", home)
    (old_home / "data").mkdir(parents=True)
    (old_home / "searches.yaml").write_text("searches: []\n")
    assert config._default_home() == old_home
    before = config.PROFILE
    config.set_profile(old_home)
    try:
        said = []
        assert not config.ensure_profile(log=said.append)          # moved, not made
        assert config.PROFILE == home and config.DATA == home / "data" and "Job Oyster" in said[0]
        assert (home / "searches.yaml").read_text() == "searches: []\n"
        assert old_home.is_symlink() and (old_home / "searches.yaml").exists()
        assert config._default_home() == home
        config.move_old_profile(log=said.append)                    # only once
        assert len(said) == 1
    finally:
        config.set_profile(before)


def test_jobpipe_home_is_never_moved(tmp_path, monkeypatch):
    old_home = tmp_path / "JobSearch"
    old_home.mkdir()
    monkeypatch.setattr(config, "OLD_HOME", old_home)
    monkeypatch.setattr(config, "HOME", tmp_path / "JobOyster")
    monkeypatch.setenv("JOBPIPE_HOME", str(old_home))
    before = config.PROFILE
    config.set_profile(old_home)
    try:
        config.move_old_profile(log=lambda m: None)
        assert old_home.is_dir() and not old_home.is_symlink() and not (tmp_path / "JobOyster").exists()
    finally:
        config.set_profile(before)


def test_the_repo_holds_no_ones_personal_files():
    """Résumés, the impact record and searches.yaml live in the profile folder, never in the repo."""
    refs = sorted(f.name for f in (config.SKILL / "references").iterdir())
    assert refs == ["resume-rules.md", "scoring-and-report.md"]
    assert not (config.ROOT / "searches.yaml").exists() and not (config.ROOT / "resume-runs").exists()


def test_candidate_section(tmp_path):
    p = tmp_path / "searches.yaml"
    p.write_text("candidate:\n  name: Sam Lee\n  pronouns: she/her\n  city: Austin, TX\n"
                 "  resumes:\n    Main resume: resume-main.md\n  withdrawn: [41.2%]\n")
    c = load_candidate(p)
    assert (c.name, c.first_name, c.pronouns, c.city) == ("Sam Lee", "Sam", "she/her", "Austin, TX")
    assert c.pdf_prefix == "Sam-Lee-Resume" and c.resumes == {"Main resume": "resume-main.md"}
    assert c.withdrawn == ["41.2%"]
    assert c.evidence == config.Candidate(name="x").evidence       # the generic default
    p.write_text("candidate:\n  name: Sam Lee\n")
    with pytest.raises(ValueError, match="resumes"):
        load_candidate(p)


def test_pronouns():
    text = "Ask him what he lacks; he's used it, he hasn't held it. His record is his own; He applies himself."
    assert config.swap_pronouns(text, "he/him") == text
    assert config.swap_pronouns(text, "she/her") == (
        "Ask her what she lacks; she's used it, she hasn't held it. Her record is her own; She applies herself.")
    assert config.swap_pronouns(text, "they/them") == (
        "Ask them what they lack; they've used it, they haven't held it. Their record is their own; "
        "They apply themselves.")


def test_every_method_file_is_filled_in_for_the_candidate():
    for f in config.SKILL.rglob("*.md"):
        text = CANDIDATE.personalize(f.read_text())
        assert not [p for p in PLACEHOLDERS if p in text], f
    assert CANDIDATE.personalize(llm.PREAMBLE).startswith("You are one sub-agent in Jordan Rivera's resume-job-fit")
    assert "for Jordan Rivera: an estimate" in triage.brief_text(CANDIDATE)


def test_prompts_name_only_the_configured_candidate():
    prompts = [llm.preamble(), llm.candidate_materials(), triage.brief_text(CANDIDATE),
               CANDIDATE.personalize(resumes.EDIT_BRIEF),
               section(CANDIDATE.personalize((config.SKILL / "SKILL-resume-job-fit.md").read_text()),
                       "Stage 4: Merge (orchestrator)")]
    prompts += [llm.brief(f.name) for f in (config.SKILL / "agents").glob("*.md")]
    for text in prompts:
        assert not [p for p in PLACEHOLDERS if p in text], text[:80]
    writer = llm.brief("05-resume-writer.md")
    assert "# Jordan Rivera" in writer and CANDIDATE.contact in writer
    assert "[Role] - [Company].pdf" in prompts[1]
    assert "only if they held it" in writer


def test_check_resume_uses_the_candidates_city(tmp_path):
    md = tmp_path / "r.md"
    md.write_text(_resume())
    script = str(config.SKILL / "scripts" / "check_resume.py")
    run = lambda *a: subprocess.run([sys.executable, script, str(md), "--title", "Staff Technical Program Manager", *a],
                                    capture_output=True, text=True)
    assert run("--city", "Portland, OR").returncode == 0
    assert "FAIL Contact line needs Austin, TX" in run("--city", "Austin, TX").stdout
    out = run("--city", "Portland, OR", "--withdrawn", "31 payment-service migrations").stdout   # a figure the record withdrew
    assert "FAIL Withdrawn metric present: 31 payment-service migrations" in out


def test_experience_heading_must_be_professional_experience(tmp_path):
    md = tmp_path / "r.md"
    md.write_text(_resume().replace("## PROFESSIONAL EXPERIENCE", "## EXPERIENCE"))
    script = str(config.SKILL / "scripts" / "check_resume.py")
    out = subprocess.run([sys.executable, script, str(md), "--title", "Staff Technical Program Manager",
                          "--city", "Portland, OR"], capture_output=True, text=True).stdout
    assert 'Heading "Experience" used; must be PROFESSIONAL EXPERIENCE' in out


def test_resumes_with_the_old_experience_heading_still_render_job_rows():
    from jobpipe import render
    new = render.render(_resume())
    old = render.render(_resume().replace("## PROFESSIONAL EXPERIENCE", "## EXPERIENCE"))
    assert new.count('class="jobrow"') == old.count('class="jobrow"') > 0


def test_profile_section_is_added_to_a_file_without_one(tmp_path):
    from jobpipe.web import searches_file
    p = tmp_path / "searches.yaml"
    p.write_text("# My searches\ndefaults:\n  limit: 5\n\nsearches:\n  - id: a\n    titles: [\"TPM\"]\n")
    (tmp_path / "resume-main.md").write_text("# Sam Lee\n")
    assert searches_file.load_candidate(p) is None
    cand = searches_file.clean_candidate({"name": "Sam Lee", "pronouns": "she/her", "city": "Austin, TX",
                                          "resumes": [{"name": "Main resume", "file": "resume-main.md"}]}, tmp_path)
    searches_file.save_candidate(p, cand)
    assert load_candidate(p).name == "Sam Lee" and load_candidate(p).resumes == {"Main resume": "resume-main.md"}
    assert config.load(p).search("a").limit == 5
    assert searches_file.load_candidate(p)["resumes"] == [{"name": "Main resume", "file": "resume-main.md"}]


def test_a_checkout_from_before_profiles_moves_into_one(tmp_path):
    """Its searches.yaml, keys, evidence, runs, data and outputs are copied; stored paths follow them."""
    import sqlite3
    from jobpipe import legacy
    old, home = tmp_path / "job-search", tmp_path / "JobSearch"
    (old / "skill" / "references").mkdir(parents=True)
    for name in ("impact-record.md", "resume-main.md", "user-notes.md", "resume-rules.md"):
        (old / "skill" / "references" / name).write_text(f"# {name}\n")
    (old / "searches.yaml").write_text("candidate:\n  name: Sam Lee\n")
    (old / ".env").write_text("JOBSPIPE_API_KEY=jp_test\n")
    (old / "resume-runs" / "acme-tpm").mkdir(parents=True)
    (old / "resume-runs" / "acme-tpm" / "resume-final.md").write_text("# Sam Lee\n")
    (old / "outputs" / "2026-10-01").mkdir(parents=True)
    (old / "outputs" / "2026-10-01" / "r.pdf").write_bytes(b"%PDF")
    (old / "data" / "jobs").mkdir(parents=True)
    (old / "data" / "jobs" / "j1.json").write_text(f'{{"pdf": "{old}/outputs/2026-10-01/r.pdf"}}')
    con = sqlite3.connect(old / "data" / "tracker.db")
    con.execute("CREATE TABLE tracker (id TEXT, resume TEXT, fit INTEGER)")
    con.execute("INSERT INTO tracker VALUES ('a', ?, 8)", (f"{old}/outputs/2026-10-01/r.pdf",))
    con.commit(); con.close()
    (home / "references").mkdir(parents=True)
    (home / "references" / "user-notes.md").write_text("mine\n")       # already in the profile: kept

    assert legacy.found(old) and not legacy.found(tmp_path)
    legacy.move(old, home, log=lambda m: None)
    assert sorted(f.name for f in (home / "references").iterdir()) == ["impact-record.md", "resume-main.md", "user-notes.md"]
    assert (home / "references" / "user-notes.md").read_text() == "mine\n"
    assert (home / "searches.yaml").exists() and (home / ".env").read_text() == "JOBSPIPE_API_KEY=jp_test\n"
    assert (home / "resume-runs" / "acme-tpm" / "resume-final.md").exists()
    new_pdf = f"{home}/outputs/2026-10-01/r.pdf"
    assert (home / "data" / "jobs" / "j1.json").read_text() == f'{{"pdf": "{new_pdf}"}}'
    con = sqlite3.connect(home / "data" / "tracker.db")
    assert con.execute("SELECT resume, fit FROM tracker").fetchone() == (new_pdf, 8)
    con.close()
    assert (old / "searches.yaml").exists()                            # the originals stay


def test_pdf_file_names_follow_the_pattern():
    day = date(2026, 10, 15)
    fill = lambda pattern, v=None, **kw: config.fill_pdf_name(
        pattern, **{"name": "Sam Lee", "company": "Acme", "title": "Staff Technical Program Manager", **kw},
        version=v, day=day)
    assert fill("{name} resume {YYYY}-{MM} {company} - {title}", 5) == \
        "Sam Lee resume 2026-10 Acme - Staff Technical Program Manager"
    assert fill("{first}_{YY}{MM}{DD}_{company}_{v}", 3) == "Sam_261015_Acme_v3"
    # {vv} is nothing for v1, and the separator before it goes too
    assert fill("{title} - {company} - {vv}", 1) == "Staff Technical Program Manager - Acme"
    assert fill("{title} - {company} - {vv}", 2) == "Staff Technical Program Manager - Acme - v2"
    assert fill("{title} {v}") == "Staff Technical Program Manager"            # no version: the run's own copy
    assert fill("{title} - {company}", title="Eng, Payments/Risk: L5", company="") == "Eng, Payments Risk L5"
    assert fill("{nope} {title}") == "{nope} Staff Technical Program Manager"
    assert config.pdf_name_unknown("{name} {nope} {YYYY}") == ["{nope}"]


def test_a_chosen_prefix_still_names_files():
    assert config.Candidate(name="Sam Lee").pdf_name == config.DEFAULT_PDF_NAME
    assert config.Candidate(name="Sam Lee", pdf_prefix="Sam-Lee-Resume").pdf_name == config.DEFAULT_PDF_NAME
    legacy = config.Candidate(name="Sam Lee", pdf_prefix="SL-CV")
    assert legacy.resume_title("Staff TPM", "Acme") == "SL-CV-Acme-Staff TPM"
    both = config.Candidate(name="Sam Lee", pdf_prefix="SL-CV", pdf_name="{company} {title}")
    assert both.resume_title("Staff TPM", "Acme") == "Acme Staff TPM"
    assert config.Candidate(name="Sam Lee").resume_title() == "Sam Lee Resume"


def test_evidence_models_missing_says_what_adds_them(monkeypatch):
    monkeypatch.delenv("OYSTER_APP_VERSION", raising=False)
    assert "pip install -r requirements.txt" in evidence.not_installed()
    monkeypatch.setenv("OYSTER_APP_VERSION", "0.2.0")
    assert "Mac app doesn't include them" in evidence.not_installed() and "pip" not in evidence.not_installed()


def test_the_profile_shows_an_older_prefix_as_its_pattern(tmp_path):
    from jobpipe.web import searches_file
    p = tmp_path / "searches.yaml"
    p.write_text("candidate:\n  name: Sam Lee\n  pdf_prefix: SL-CV\n  resumes:\n    Main: resume-main.md\n")
    assert searches_file.load_candidate(p)["pdf_name"] == "SL-CV-{company}-{title}"
    p.write_text("candidate:\n  name: Sam Lee\n  pdf_prefix: Sam-Lee-Resume\n  resumes:\n    Main: resume-main.md\n")
    assert searches_file.load_candidate(p)["pdf_name"] == ""          # the made-up prefix: the default pattern

import asyncio
import json
import re

import numpy as np

from jobpipe import config, evidence
from conftest import TITLE, FakeRunner, _resume, _trace, make_job
from test_pipeline_e2e import build


def words(s):
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


class FakeModels:
    """Bag-of-words stand-ins: similarity and judgments come from shared words."""
    name = "fake"

    def embed(self, texts, instruction=None):
        v = np.zeros((len(texts), 256))
        for r, t in enumerate(texts):
            for w in words(t):
                v[r, hash(w) % 256] += 1
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)

    def judge(self, instruction, pairs):
        return [len(words(q) & words(d)) / max(1, len(words(q))) for q, d in pairs]

    def entail(self, pairs):
        claim = lambda c: words(c) - words(evidence.KEYWORD_CLAIM)          # "This work involved ..."
        return [len(claim(c) & words(e)) / max(1, len(claim(c))) for e, c in pairs]


def fake_index(tmp_path):
    return evidence.Index(FakeModels(), evidence.candidate_passages(), tmp_path / "evidence")


def test_passages_skip_do_not_claim_and_carry_their_section():
    ps = evidence.candidate_passages()
    assert not any("Kubernetes" in p.text for p in ps)              # "Do not claim without new evidence"
    digest = next(p for p in ps if "portfolio digest" in p.text and p.source.startswith("Impact record"))
    assert "Northwind Cloud" in digest.source
    job = next(p for p in ps if p.text.startswith("Supported the studio-wide legacy CI server"))
    assert job.source.startswith("Platform resume") and "Fabrikam Games" in job.source


def test_figures_must_be_in_the_evidence_without_models():
    md = ("## EXPERIENCE\n\n**Northwind Cloud** | TPM\n*2023*\n\n"
          "- Moved 31 payment services onto the new ledger platform over 20 months.\n"
          "- Cut build costs by 45% across 1,500 repositories.\n\n## CORE SKILLS\nHard Skills: 99 things\n")
    checks = evidence.check_lines(md, "", "", evidence.candidate_passages())
    assert [c.missing_figures for c in checks] == [[], ["45", "1500"]]
    assert [c.flagged for c in checks] == [False, True] and checks[1].support is None
    assert evidence.figures("roughly 1,200 repos, 97% to 99.5%, $12M") == ["1200", "97", "99.5", "12"]


def test_ledger_trace_and_claims_parsing():
    match = ("## Evidence ledger\nE1 — §1 — Built the digest. Allowed: built.\n"
             "- **E2** — IR §2 — Owned build-farm reliability.\n\n## Do not claim (this job)\n- E9 — nope\n")
    assert evidence.ledger(match) == {"E1": "§1 — Built the digest. Allowed: built.", "E2": "IR §2 — Owned build-farm reliability."}
    nested = ("## Evidence ledger\n- **E1** (Impact §1)\n  - Handled 125 tickets a week.\n"
              "- **E2** (Impact §2): Mentored a team of 6.\n\n## Do not claim\n")
    got = evidence.ledger(nested)
    assert "125" in got["E1"] and "6" in got["E2"]
    trace = ("| Line | Starts with (first 8 words, exact) | Ledger IDs |\n|---|---|---|\n"
             "| L1 | Technical Program Manager with 9+ years in software | E1, E2 |\n"
             "| Northwind 1 | Owned build-farm reliability for 14 game studios | E2, U1 |\n")
    md = ("## SUMMARY\nTechnical Program Manager with 9+ years in software. Owns delivery end to end.\n\n"
          "## EXPERIENCE\n- Owned build-farm reliability for 14 game studios.\n")
    checks = evidence.check_lines(md, trace, match, evidence.candidate_passages())
    assert [(c.section, c.cited) for c in checks] == [("SUMMARY", ["E1", "E2"]), ("SUMMARY", ["E1", "E2"]),
                                                      ("EXPERIENCE", ["E2", "U1"])]
    assert evidence.claims("Drove operational excellence: built the process; ran it across 31 migrations") == [
        "built the process", "ran it across 31 migrations"]
    assert evidence.claims("Led a multi-year program: shipped") == ["Led a multi-year program: shipped"]


def test_keyword_support_ranks_evidence(tmp_path):
    index = fake_index(tmp_path)
    found = {s.keyword: s for s in evidence.keyword_support(index, ["weekly portfolio digest", "Kubernetes administration"])}
    assert found["weekly portfolio digest"].verdict == "supported"
    assert "portfolio digest" in found["weekly portfolio digest"].passages[0][0].text
    assert found["Kubernetes administration"].verdict == "none"
    table = evidence.keyword_table(list(found.values()), "fake")
    assert "| weekly portfolio digest | supported |" in table and "## Kubernetes administration" not in table
    assert list((tmp_path / "evidence").glob("index-*.npy"))           # vectors cached for the next run


class RelevantNotStated(FakeModels):
    """Every passage is relevant, and none states anything: "mentoring analysts" for "mentoring program managers"."""
    def judge(self, instruction, pairs):
        return [0.95] * len(pairs)

    def entail(self, pairs):
        return [0.0] * len(pairs)


def test_a_relevant_keyword_needs_a_passage_that_states_it(tmp_path):
    stated = evidence.keyword_support(fake_index(tmp_path), ["weekly portfolio digest"])[0]
    assert stated.verdict == "supported" and stated.entailment > 0.9
    assert "portfolio digest" in stated.evidence.text
    index = evidence.Index(RelevantNotStated(), evidence.candidate_passages(), tmp_path / "other")
    found = evidence.keyword_support(index, ["weekly portfolio digest"])[0]
    assert found.score == 0.95 and found.entailment == 0.0 and found.verdict == "weak" and found.stated_by is None
    table = evidence.keyword_table([found])
    assert "| weekly portfolio digest | weak | 0.95 | 0.00 |" in table and "none states this work" in table


def test_line_support_flags_claims_the_evidence_does_not_make(tmp_path):
    md = ("## EXPERIENCE\n- Built an automated weekly portfolio digest covering 18 programs.\n"
          "- Founded a quantum research lab, hiring physicists.\n")
    checks = evidence.check_lines(md, "", "", evidence.candidate_passages(), fake_index(tmp_path))
    assert checks[0].support > 0.8 and not checks[0].flagged
    assert checks[1].support < evidence.LINE_UNSUPPORTED and checks[1].flagged
    assert "quantum research lab" in evidence.honesty_table(checks, "fake").split("## Flagged lines")[1]


class EvidenceRunner(FakeRunner):
    """The merge invents a figure; the matcher's keywords include one the resume lacks but the record supports."""
    async def run(self, call):
        res = await super().run(call)
        if call.label == "04-matcher":
            kw = json.loads(res.files["keywords.json"])
            res.files["keywords.json"] = json.dumps({**kw, "weekly digest": "weekly digest"})
        if call.label == "merge":
            md = _resume().replace("- Supported the studio-wide legacy CI server decommission, delivered on time.",
                                   "- Supported the studio-wide legacy CI server decommission across 640 servers.")
            res.files.update({"resume-final.md": md, "trace-final.md": _trace(md)})
        if call.run_dir and call.label in ("04-matcher", "merge"):
            from jobpipe.llm import write_files
            write_files(call.run_dir, res.files)
        return res


def test_tailoring_reviews_unbacked_lines_and_adds_supported_keywords(tmp_dirs):
    evidence.load = lambda cfg: FakeModels()          # the autouse fixture puts the real one back
    p, _, _ = build(tmp_dirs, {}, {})
    p.runner = runner = EvidenceRunner()
    c = asyncio.run(p.tailor_job(make_job("best")))
    labels = [x.label for x in runner.calls]
    rate = ["rate-final-1", "rate-final-2", "rate-final-3"]
    assert labels[labels.index("merge"):] == ["merge", "honesty-fix", *rate, "merge-restore", *rate, "report"]
    fix = next(x for x in runner.calls if x.label == "honesty-fix")
    assert "640" in fix.documents["honesty.md"] and "rescope" in fix.instructions
    restore = next(x for x in runner.calls if x.label == "merge-restore")
    assert "weekly digest" in restore.tail and "keyword-evidence.md" in restore.documents
    assert "Kubernetes" not in restore.tail.split("Keywords it rated")[0]
    report = next(x for x in runner.calls if x.label == "report")
    assert "honesty.md" in report.documents and "keyword-evidence.md" in report.documents
    run_dir = c.tailored.run_dir
    assert "0 flagged" in (run_dir / "honesty.md").read_text()      # the review put the real line back


def test_tailoring_without_models_still_checks_figures(tmp_dirs):
    p, _, _ = build(tmp_dirs, {}, {})
    p.runner = runner = EvidenceRunner()
    asyncio.run(p.tailor_job(make_job("best")))
    labels = [x.label for x in runner.calls]
    assert "honesty-fix" in labels and "merge-restore" not in labels   # no models: no keyword evidence
    report = next(x for x in runner.calls if x.label == "report")
    assert "honesty.md" in report.documents and "keyword-evidence.md" not in report.documents


class BrokenModels(FakeModels):
    def judge(self, instruction, pairs):
        raise RuntimeError("MPS backend out of memory")

    entail = judge


def test_a_model_failure_mid_run_falls_back_to_the_figure_check(tmp_dirs):
    evidence.load = lambda cfg: BrokenModels()
    p, _, _ = build(tmp_dirs, {}, {})
    p.runner = runner = EvidenceRunner()
    c = asyncio.run(p.tailor_job(make_job("best")))
    assert c.tailored.pdf.exists()
    labels = [x.label for x in runner.calls]
    assert "honesty-fix" in labels and "merge-restore" not in labels
    assert "only figures were checked" in (c.tailored.run_dir / "honesty.md").read_text()


def test_figures_in_confirmed_facts_count_for_every_line(tmp_path):
    """With the models, a line's figures must be in its own evidence or a confirmed fact: the record's Confirmed
    facts section or an older user-notes.md."""
    md = "## EXPERIENCE\n- Built an automated weekly portfolio digest covering 18 programs for 37 directors.\n"
    record = evidence.Passage("Impact record › Confirmed facts", "Oct 6, 2026, user-confirmed: the digest went to 37 directors.")
    other = evidence.Passage("Impact record › Northwind", "The digest went to 37 directors.")
    for extra, missing in ((record, []), (evidence.Passage("Confirmed facts", record.text), []), (other, ["37"])):
        passages = [*evidence.candidate_passages(), extra]
        index = evidence.Index(FakeModels(), passages, tmp_path / str(len(missing)) / extra.source[:5])
        assert evidence.check_lines(md, "", "", passages, index, k=1)[0].missing_figures == missing

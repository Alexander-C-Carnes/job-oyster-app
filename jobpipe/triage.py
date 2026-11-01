"""Quick fit triage (the web app's "signal score"): one Claude call per job, on the skill's 1-10 rubric.

This is a pre-filter that decides which jobs earn the full resume-job-fit run. It reads
the same candidate materials as the matcher (cached), so a triage call costs little
more than the posting text plus a short JSON answer.

The call rates the posting's requirements the way stages 1-2 do, and the score is computed
here from those ratings. That keeps it close to the full run's impact-record score: on 13
tracker jobs with full-run scores (Oct 2026) the average gap was -0.3 points and every job
was within one point; before, the gap was -1.4. `tools/calibrate_signal.py` repeats the check.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Optional

from . import config, hosted
from .config import ModelCfg
from .llm import AgentCall, AgentError, Runner

TRIAGE_BRIEF = """# Triage agent: signal score

You give one job posting a quick "signal" score for {name}: an estimate of the impact-record score the full resume-job-fit matcher (Agent 04) would reach. Score how well his ACTUAL experience ({evidence}) fits this role, on the 1-10 rubric in scoring-and-report.md.

Work the way the full pipeline does, in this order:

1. List what the posting explicitly asks for, in its own wording, as two kinds of rows:
   - skills (about 6 to 12): tools, technologies, methods, domain knowledge, certifications and soft skills the posting names;
   - experience (about 4 to 8): years, level, role type, industries, scope, people management and kinds of programs it asks for.
   Only what is written; nothing that is merely typical for the role. Merge near-duplicates. An "A, B, C or D" list is one row, strong when any one of them is strong. Split a compound line where one part could be met and another not.
2. Weight each row from signals in the posting only: High when it is in the title or in a section labelled required, minimum, must-have or qualifications; Med when it is among the responsibilities or "what you'll do"; Low when it is labelled preferred, nice-to-have, bonus or "a plus".
3. Rate each row against the impact record:
   - strong: clearly demonstrated. Named directly or done in a real role, with scope or results. User-confirmed notes count, at exactly the scope stated.
   - partial: adjacent or transferable. A related tool, less scope or seniority, a different domain.
   - missing: no evidence. Anything under "Do not claim without new evidence" is not evidence.
   Rate level on demonstrated scope: a one-level step (Senior to Staff or Principal) is not a gap. Where the record says he directed and owned systems built substantially with coding agents, that is strong evidence for technical program ownership, AI-native delivery and systems design, and partial where hands-on software engineering is the primary job.
4. The orchestrator computes the score from your rows: coverage = sum(weight x value) / sum(weight), with High 3, Med 2, Low 1 and strong 1, partial 0.5, missing 0, then the rubric's bands (about 60% is 6, about 70% is 7, 80 to 90% is 8, above 90% is 9). So the rows are the score: rate each one on the evidence, not on the total you expect. Under about 55% coverage the rubric's lower bands are a judgment, not arithmetic (4-5: relevant skills or relevant experience but not both; 1-3: almost neither), and your fit_score decides. It also decides when the posting text has too little in it to rate: say so in band_reason.
5. score_cap: set it only for a formal hard requirement the record shows he does not hold (a mandatory degree, clearance, license, certification or language): the highest score that leaves him. Otherwise null.
6. Location, relocation, on-site or hybrid attendance, work authorization, travel and start date are his decision, and the record does not settle them. List them in hard_requirement_issues; they are not rows and never a cap. The same goes for anything the record is simply silent on: that is a question to ask him, not a gap.

Only the posting's own words count as facts about the job. The listing metadata lines at the top of jd.md come from the job board, not the employer; if they conflict with the posting text, trust the posting text.

Write `triage.json`:
{
  "requirements": [{"req": "<the requirement, 3-10 words>", "weight": "High|Med|Low", "rating": "strong|partial|missing"}, ...],
  "score_cap": <integer 1-10, or null>,
  "fit_score": <integer 1-10: your own read of the impact-record score>,
  "band_reason": "<one sentence: what the record covers and what holds the score where it is>",
  "role_family": "<TPM | Engineering Manager | Program Manager | Product Manager | Other>",
  "level_match": "<under | match | over | unclear>",
  "hard_requirement_issues": ["<quoted requirement to check with him: location, authorization, or a credential he may lack>", ...],
  "strongest_matches": ["<requirement> — <specific evidence: program, metric, or launch>", ... up to 4],
  "likely_gaps": ["<a High or Med requirement rated missing or partial, in the posting's words>", ... up to 4, fewer if there are fewer],
  "recommended_base": "<one of the resume variant source names>",
  "one_line": "<one line a recruiter would understand: why this is or isn't a fit>"
}
"""

WEIGHT = {"high": 3, "med": 2, "medium": 2, "low": 1}
VALUE = {"strong": 1.0, "partial": 0.5, "missing": 0.0}


# The rubric's anchors (scoring-and-report.md: 6 is about 60%, 7 about 70%, 8 is 80-90%, 9 above that),
# each band reaching halfway to the next anchor. Checked against the tracker's full-run scores.
BANDS = ((99, 10), (91, 9), (75, 8), (65, 7), (55, 6), (45, 5), (35, 4), (25, 3), (12, 2))
MIN_ROWS = 6   # fewer rated rows than this and the coverage says little (a stub or a scraped menu)


def score_from_rows(pct: Optional[float], rows: int, model_score: Optional[int], cap: Optional[int] = None) -> int:
    """The signal score. From 55% coverage up it is the rubric band for that coverage. Below that the
    rubric is qualitative, so the model's own read decides (never above 5); with too few rows it decides alone."""
    banded = next((n for floor, n in BANDS if pct >= floor), 1) if pct is not None else None
    if banded is None or rows < MIN_ROWS:
        score = model_score if model_score is not None else banded
    elif pct >= 55 or model_score is None:
        score = banded
    else:
        score = min(model_score, 5)
    if score is None:
        raise ValueError("no requirements rated and no fit_score")
    return min(score, cap) if cap else score


def coverage(requirements: list[dict]) -> Optional[float]:
    """Weighted share of the posting's requirements the record covers, 0-100 (the scorecard's math)."""
    rows = [(WEIGHT.get(str(r.get("weight", "")).lower()), VALUE.get(str(r.get("rating", "")).lower()))
            for r in requirements if isinstance(r, dict)]
    rows = [(w, v) for w, v in rows if w and v is not None]
    total = sum(w for w, _ in rows)
    return round(100 * sum(w * v for w, v in rows) / total, 1) if total else None


@dataclass
class Triage:
    fit_score: int
    band_reason: str
    role_family: str
    level_match: str
    hard_requirement_issues: list[str]
    strongest_matches: list[str]
    likely_gaps: list[str]
    recommended_base: str
    one_line: str
    requirements: list[dict] = field(default_factory=list)   # [{"req", "weight", "rating"}]
    coverage_pct: Optional[float] = None                      # computed here from requirements
    score_cap: Optional[int] = None                           # a formal hard requirement he lacks
    model_score: Optional[int] = None                         # the model's own number, for comparison

    @classmethod
    def from_json(cls, text: str) -> "Triage":
        d = json.loads(text)
        if not isinstance(d, dict):
            raise ValueError(f"expected a JSON object, got {type(d).__name__}")
        reqs = [r for r in d.get("requirements") or [] if isinstance(r, dict)]
        cov = coverage(reqs)
        cap = d.get("score_cap")
        cap = int(cap) if isinstance(cap, (int, float)) and 1 <= cap <= 10 else None
        own = d.get("fit_score")
        own = int(own) if isinstance(own, (int, float)) else None
        score = score_from_rows(cov, len(reqs), own, cap)
        if not 1 <= score <= 10:
            raise ValueError(f"fit_score out of range: {score}")
        base = d.get("recommended_base", "")
        resumes = config.candidate().resumes
        if base not in resumes:
            base = next(iter(resumes))
        return cls(
            fit_score=score,
            band_reason=d.get("band_reason", ""),
            role_family=d.get("role_family", ""),
            level_match=d.get("level_match", "unclear"),
            hard_requirement_issues=list(d.get("hard_requirement_issues") or []),
            strongest_matches=list(d.get("strongest_matches") or []),
            likely_gaps=list(d.get("likely_gaps") or []),
            recommended_base=base,
            one_line=d.get("one_line", ""),
            requirements=reqs,
            coverage_pct=cov,
            score_cap=cap,
            model_score=own,
        )


def brief_text(cand: config.Candidate) -> str:
    """TRIAGE_BRIEF for this candidate (it calls the candidate he/him, like the skill's briefs)."""
    return cand.personalize(TRIAGE_BRIEF.replace("{evidence}", cand.evidence))


async def triage_one(runner: Runner, model: ModelCfg, jd_md: str, label: str) -> Triage:
    cand = config.candidate()
    notes = ("Resume variant source names: " + ", ".join(cand.resumes)
             + f". The impact record's source name is \"{config.IMPACT_SOURCE}\".")
    call = AgentCall(label=f"triage:{label}", model=model, instructions=brief_text(cand) + "\n" + notes,
                     documents={"jd.md": jd_md}, expect=["triage.json"])
    with hosted.signal_score():
        res = await runner.run(call)
        try:
            return Triage.from_json(res.files["triage.json"])
        except (KeyError, ValueError, TypeError) as e:
            raise AgentError(f"triage:{label}: bad triage.json ({e})") from e


async def triage_many(runner: Runner, model: ModelCfg,
                      jobs: dict[str, str]) -> dict[str, Triage | Exception]:
    """jobs: job id -> jd.md. Returns job id -> Triage (or the exception, so one failure doesn't stop the run)."""
    async def one(jid: str, jd_md: str) -> tuple[str, Triage | Exception]:
        try:
            return jid, await triage_one(runner, model, jd_md, jid)
        except Exception as e:  # noqa: BLE001 - reported per job
            return jid, e
    results = await asyncio.gather(*(one(j, t) for j, t in jobs.items()))
    return dict(results)


def rank_key(t: Triage, salary_top: Optional[float]) -> tuple:
    blocked = 1 if t.hard_requirement_issues else 0
    return (-t.fit_score, blocked, -(salary_top or 0))

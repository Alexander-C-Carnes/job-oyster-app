"""The full resume-job-fit pipeline (Stages 0-6) for one job, ported from the skill.

Stage 0  jd.md (orchestrator)
Stage 1  01 Objectives | 02 Skills | 03 Experience     (parallel, posting only)
Stage 2  04 Matcher -> 04-match.md, ratings.json, keywords.json (+ baseline scorecard)
Stage 3  05 Writers A | B | C                         (parallel) -> drafts, traces, checks
         Rater: rates each draft literally           -> draft scorecard
Stage 4  Merge -> resume-final.md (+ check, honesty check against the evidence, keyword-restore pass if Total
         drops or the evidence supports a missing keyword)
Stage 5  PDF (resume-format) -> page count (trim pass if over the format's page limit), PDF-text scorecard
Stage 6  Heat map + report.md
Stage 7 (Notion) runs in pipeline.py so that triaged jobs share it.

Stages 1-2 are also the "full score" the web app can run on its own (Tailor.analyze). A later
tailoring run reuses that analysis when the posting and the candidate materials haven't changed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import config, evidence, formats, render
from .config import Config
from .llm import AgentCall, AgentError, Runner, brief, candidate_materials, warm, write_files

SCRIPTS = config.SKILL / "scripts"
WEIGHT = {"high": 3, "med": 2, "medium": 2, "low": 1}
VALUE = {"strong": 1.0, "partial": 0.5, "missing": 0.0}
LENSES = {"A": "posting language", "B": "evidence fidelity", "C": "recruiter scorecard"}


# ---------------------------------------------------------------------------
# scorecard (same math as skill/scripts/ats_score.py)

def section_pct(rows: list[dict], i: int) -> Optional[float]:
    total = sum(WEIGHT[r["weight"].lower()] for r in rows)
    if not total:
        return None
    return 100 * sum(WEIGHT[r["weight"].lower()] * VALUE[r["ratings"][i].lower()] for r in rows) / total


def scorecard(ratings: dict, keywords: dict, texts: dict[str, str],
              weights: tuple[float, float, float] = (0.35, 0.35, 0.30)) -> dict[str, dict]:
    out = {}
    for i, src in enumerate(ratings["sources"]):
        s = section_pct(ratings.get("skills", []), i)
        e = section_pct(ratings.get("experience", []), i)
        row = {"skills": s, "experience": e, "keywords": None, "total": None, "missing": []}
        if src in texts:
            hits = {k: bool(re.search(p, texts[src], re.I)) for k, p in keywords.items()}
            row["keywords"] = 100 * sum(hits.values()) / max(1, len(hits))
            row["total"] = weights[0] * (s or 0) + weights[1] * (e or 0) + weights[2] * row["keywords"]
            row["missing"] = [k for k, h in hits.items() if not h]
        out[src] = row
    return out


FINAL_RATINGS = 3      # independent ratings of the final resume, combined by synthesize_ratings


def synthesize_ratings(runs: list[dict], source: str) -> dict:
    """Combine independent ratings.json files for `source` into one: each row takes the median rating
    (with three runs, the majority, or partial when all three differ; with two, the lower), and the score
    the median (with two, the lower). The other sources' columns come from the first run, since a rater
    keeps them unchanged. Each row's note is from a run that gave the chosen rating."""
    out = json.loads(json.dumps(runs[0]))
    i = out["sources"].index(source)
    cols = [r["sources"].index(source) for r in runs]

    def pick(vals: list) -> int:            # the index (into runs) of the median value
        order = sorted(range(len(vals)), key=lambda k: vals[k])
        return order[(len(vals) - 1) // 2]
    for sec in ("skills", "experience"):
        for n, row in enumerate(out.get(sec) or []):
            vals = [VALUE[r[sec][n]["ratings"][c].lower()] for r, c in zip(runs, cols)]
            k = pick(vals)
            row["ratings"][i] = runs[k][sec][n]["ratings"][cols[k]]
            if row.get("notes") and len(runs[k][sec][n].get("notes") or []) > cols[k]:
                row["notes"][i] = runs[k][sec][n]["notes"][cols[k]]
    scores = [float(r["scores"][c]) for r, c in zip(runs, cols)]
    out["scores"][i] = scores[pick(scores)]
    out["runs"] = {"source": source, "scores": scores,
                   "rows_agreeing": sum(len({r[sec][n]["ratings"][c].lower() for r, c in zip(runs, cols)}) == 1
                                        for sec in ("skills", "experience") for n in range(len(out.get(sec) or []))),
                   "rows": sum(len(out.get(sec) or []) for sec in ("skills", "experience"))}
    return out


def scorecard_table(card: dict[str, dict]) -> str:
    fmt = lambda v: "n/a" if v is None else f"{v:.0f}%"
    lines = ["| Source | Skills | Experience | Keywords | Total |", "|---|---|---|---|---|"]
    for src, r in card.items():
        flag = "" if r["total"] is None else (" PASS" if r["total"] >= 80 else " below 80%")
        lines.append(f"| {src} | {fmt(r['skills'])} | {fmt(r['experience'])} | {fmt(r['keywords'])} | "
                     f"{fmt(r['total'])}{flag} |")
    for src, r in card.items():
        if r["missing"]:
            lines.append(f"\nMissing keywords, {src} ({len(r['missing'])}): " + ", ".join(r["missing"]))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# validators

def check_ratings(text: str, required_sources: list[str]) -> list[str]:
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        return [f"ratings JSON invalid: {e}"]
    problems = []
    sources = d.get("sources") or []
    for s in required_sources:
        if s not in sources:
            problems.append(f"sources is missing {s!r}")
    if len(d.get("scores") or []) != len(sources):
        problems.append("scores must have one entry per source")
    for sec in ("skills", "experience"):
        for row in d.get(sec) or []:
            if row.get("weight", "").lower() not in WEIGHT:
                problems.append(f"{sec} row {row.get('requirement')!r}: weight must be High/Med/Low")
            r = row.get("ratings") or []
            if len(r) != len(sources) or any(x.lower() not in VALUE for x in r):
                problems.append(f"{sec} row {row.get('requirement')!r}: need one strong/partial/missing per source")
    if not d.get("skills") and not d.get("experience"):
        problems.append("no skills or experience rows")
    return problems[:12]


def normalize_keywords(raw) -> tuple[dict[str, str], list[str]]:
    """keywords.json as {"Label": "regex"}, from the shapes the matcher sometimes writes instead: wrapped in one
    key ({"keywords": {...}} or {"keywords": [...]}), a list of {"keyword"/"label": ..., "regex"/"pattern": ...},
    a list of plain terms, or a list of alternatives as a value. A pattern that isn't a valid regex is matched
    literally. Returns (keywords, labels whose pattern was replaced by the literal label)."""
    d = raw
    if isinstance(d, dict) and len(d) == 1 and isinstance(next(iter(d.values())), (dict, list)):
        d = next(iter(d.values()))
    items: list[tuple[str, object]] = []
    if isinstance(d, dict):
        items = [(str(k), v) for k, v in d.items()]
    elif isinstance(d, list):
        for x in d:
            if isinstance(x, str):
                items.append((x, re.escape(x)))
            elif isinstance(x, dict):
                label = next((x[k] for k in ("label", "keyword", "term", "name") if x.get(k)), None)
                pat = next((x[k] for k in ("regex", "pattern", "re", "match") if x.get(k)), None)
                if label:
                    items.append((str(label), pat if pat is not None else re.escape(str(label))))
    out: dict[str, str] = {}
    literal: list[str] = []
    for label, pat in items:
        if isinstance(pat, list) and pat and all(isinstance(p, str) for p in pat):
            pat = "|".join(f"(?:{p})" for p in pat)
        try:
            re.compile(pat)
        except (re.error, TypeError):
            pat = re.escape(label)
            literal.append(label)
        out[label] = pat
    return out, literal


def check_keywords(text: str) -> list[str]:
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        return [f"keywords.json is not valid JSON ({e})"]
    kw, _ = normalize_keywords(d)
    if not kw:
        return ['keywords.json must be one flat JSON object of 30-50 entries, {"Keyword label": "case-insensitive '
                'regex", ...}, with no wrapper key']
    return []


def run_script(name: str, *args: str, cwd: Path, max_pages: Optional[int] = None) -> tuple[int, str]:
    if name == "check_resume.py":           # the contact-line check and its wording are the candidate's
        cand = config.candidate()           # the page limit is the résumé's format's (the default format's if not given)
        args = (*args, "--city", cand.city, "--name", cand.first_name,
                "--max-pages", str(max_pages or formats.get(cand.resume_format).max_pages),
                *(x for w in cand.withdrawn for x in ("--withdrawn", w)))
    p = subprocess.run([sys.executable, str(SCRIPTS / name), *args], cwd=cwd,
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def section(md: str, heading: str) -> str:
    m = re.search(rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s|\Z)", md, re.S | re.M)
    return m.group(1).strip() if m else ""


def exact_title(objectives_md: str, fallback: str) -> str:
    body = section(objectives_md, "Exact job title")
    line = next((l.strip() for l in body.splitlines() if l.strip()), "")
    return line.strip("`*\"' ") or fallback


# ---------------------------------------------------------------------------

# The page limit the writers, the merge and the trim step are told is the résumé format's (formats.length_rules);
# render_pdf decides the actual page count.
TRIM_BRIEF = """# Trim to {limit}

The tailored resume below renders to {pages} pages ({words} words); it must fit {limit}. Shorten it by deleting only:

1. Cut whole bullets first: those proving Low-weight requirements, then Med, then any bullet that proves the same
   requirement as another. Keep every bullet that is the only proof of a High-weight requirement (requirement IDs and
   weights are in 04-match.md and trace-final.md).
2. Then shorten long bullets to at most two lines (about 35 words): keep the posting's lead phrase, the action and
   the strongest figure; drop secondary detail, lists of names, and parentheticals.
3. {limits}
4. Never add or reword a claim, figure, tool or phrase: every word left must already be in the resume. Keep the
   markdown shape, headings, title line, summary's target role and CORE SKILLS.
5. Prefer cutting text whose keywords (keywords.json) also appear elsewhere in the resume.

Write `resume-final.md` (the trimmed resume) and `trace-final.md` (the same trace, without rows for cut lines).
"""

RATER_BRIEF = """# Rater: score resumes as written

Add the resume(s) named below as new sources in ratings.json. For every skills and experience row, rate each new resume literally on what it actually says: a resume only gets credit for what is on the page, because recruiters don't infer. strong = clearly demonstrated with scope or results; partial = adjacent or transferable; missing = not on the page. Add a short note per cell. Score each new resume 1-10 with the rubric in scoring-and-report.md.

Write `{out}`: the complete ratings.json with the new source names appended to "sources", their scores appended to "scores", and one rating (and note) appended to every row's "ratings" (and "notes") array, in the same order. Keep every existing value unchanged.
"""

HONESTY_BRIEF = """# Honesty review

A local check (honesty.md) compared each line of the tailored resume with the candidate's evidence: every figure must
appear in the evidence, and a fact-checking model scored whether the cited ledger entries or the closest passages of
the impact record, resume variants and confirmed facts state what the line claims. The flagged lines may claim more
than the evidence shows. The model is strict about wording, so some flags are paraphrases of real evidence.

For each flagged line, check it against the impact record, the resume variants and user-confirmed notes:
1. If the evidence supports it as written, keep it unchanged and say where (section or ledger ID) in merge-notes.md.
2. Otherwise rescope it to exactly what the evidence states: its verbs ("contributed to" stays "contributed to"),
   its figures, its scope. A figure the evidence does not state comes out.
3. If nothing true is left, cut the line.

Never add a claim, figure or tool. Change nothing but the flagged lines. Write `resume-final.md`, `trace-final.md`
(updated for the lines you changed) and `merge-notes.md` (the previous notes plus a "Honesty review" section).
"""

KEYWORD_ADD = """A local evidence check (keyword-evidence.md) found these missing posting keywords supported by the evidence: \
{supported}. For each, if the passage shown really is work the phrase describes, add the phrase to the existing line \
that already uses that evidence, at the evidence's scope, citing its ledger ID or section in trace-final.md. Add no \
new claim or figure. Skip any you judge the evidence doesn't support and say why in merge-notes.md. Keywords it rated \
weak or none are not supported: don't add them."""

REPORT_BRIEF = """# Report writer (Stage 6)

Write the fit report for this job as `report.md`, following the "Report template" in scoring-and-report.md exactly, built only from the run files provided:
- Job summary and What the job will be doing: from 01-objectives.md.
- Fit scores, heat map rows, strongest fit, unconfirmed gaps, scorecard stories: from 04-match.md and ratings-final.json. Include the tailored resume as a row.
- ATS scorecard: copy the numbers from scorecard-final.md (the tailored resume row uses the PDF text). Then the missing-keyword split (truthful adds vs. don't add). When keyword-evidence.md is provided, base the split on its verdicts: supported = a truthful add (name the evidence), weak = a question for the candidate, none = don't add.
- Honesty check: if honesty.md is provided and still flags lines, list them under "Lines to check" with the reason.
- Posting-language keywords "Use" and "Held back": from merge-notes.md and 04-match.md.
- Heat map: say the interactive chart is in {heatmap} and include the compact emoji table.
- End with the numbered questions about each unconfirmed gap, highest weight first.

Also write `notion-summary.txt`: exactly two sentences summarizing the role, taken from 01-objectives.md in the posting's own words.
"""


@dataclass
class JobMeta:
    job_id: str
    title: str
    company: str
    url: str
    salary: str = ""
    location: str = ""


@dataclass
class Analysis:
    """Stages 1-2 for one job: every requirement rated against the impact record and each resume."""
    exact_title: str
    objectives: str
    match_md: str
    ratings: dict
    keywords: dict
    base: str                       # best existing resume (a candidate.resumes source name); the writers' frame
    baseline: dict[str, dict]       # scorecard per source, before any tailoring
    warnings: list[str] = field(default_factory=list)

    @property
    def scores(self) -> dict[str, Optional[float]]:
        return {src: _num(v) for src, v in zip(self.ratings.get("sources") or [], self.ratings.get("scores") or [])}

    @property
    def impact_score(self) -> Optional[float]:
        return self.scores.get(config.IMPACT_SOURCE)

    @property
    def gaps(self) -> list[str]:
        return unconfirmed_gaps(self.match_md)


ANALYSIS_FILES = ("jd.md", "01-objectives.md", "02-skills.md", "03-experience.md", "04-match.md", "ratings.json",
                  "keywords.json", "recommended-base.txt", "scorecard-baseline.md", "analysis.json")


def materials_sig() -> str:
    """Changes when the impact record, a resume variant, the confirmed notes or the rules change."""
    return hashlib.sha256(candidate_materials().encode()).hexdigest()[:16]


def variant_texts() -> dict[str, str]:
    ref = config.REFERENCES
    return {src: (ref / f).read_text() for src, f in config.candidate().resumes.items()}


def unconfirmed_gaps(match_md: str) -> list[str]:
    return [l.strip("-* ").strip() for l in section(match_md, "Unconfirmed gaps").splitlines()
            if l.strip().startswith(("-", "*", "1", "2", "3", "4", "5", "6", "7", "8", "9"))][:8]


def load_analysis(run_dir: Path, jd_md: str) -> Optional[Analysis]:
    """The analysis saved in run_dir, if it was made for this posting text and today's candidate materials."""
    try:
        saved = json.loads((run_dir / "analysis.json").read_text())
        if saved["materials"] != materials_sig() or (run_dir / "jd.md").read_text() != jd_md:
            return None
        ratings = json.loads((run_dir / "ratings.json").read_text())
        keywords = normalize_keywords(json.loads((run_dir / "keywords.json").read_text()))[0]
        return Analysis(exact_title=saved["exact_title"], objectives=(run_dir / "01-objectives.md").read_text(),
                        match_md=(run_dir / "04-match.md").read_text(), ratings=ratings, keywords=keywords,
                        base=saved["base"], baseline=scorecard(ratings, keywords, variant_texts()))
    except (OSError, ValueError, KeyError):
        return None


def copy_analysis(src: Path, dst: Path) -> None:
    """Bring a full score made in another run folder (an earlier day) into this one."""
    if src == dst or not (src / "analysis.json").exists() or (dst / "analysis.json").exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for name in ANALYSIS_FILES:
        if (src / name).exists():
            shutil.copy(src / name, dst / name)


@dataclass
class TailorResult:
    run_dir: Path
    exact_title: str
    impact_score: Optional[float]
    resume_score: Optional[float]
    final_card: dict
    pdf: Optional[Path]
    pages: int
    report: Path
    heatmap: Optional[Path]
    notion_summary: str
    unconfirmed_gaps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Tailor:
    def __init__(self, cfg: Config, runner: Runner, out_dir: Path,
                 log: Callable[[str], None] = print):
        self.cfg = cfg
        self.runner = runner
        self.out_dir = out_dir
        self.log = log
        self.fmt = formats.get(None)                # the résumé format; run() reads the candidate's default
        self._no_models: Optional[str] = None       # why the evidence models aren't used, once known

    async def _evidence_index(self) -> Optional[evidence.Index]:
        """The candidate's evidence, embedded (cached on disk), or None when the models can't be used."""
        if self._no_models is not None:
            return None
        try:
            models = await asyncio.to_thread(evidence.load, self.cfg.evidence)
            return await asyncio.to_thread(evidence.Index, models, evidence.candidate_passages(),
                                           config.DATA / "evidence")
        except Exception as e:  # noqa: BLE001 - Unavailable, or a load that failed: the run goes on without them
            self._no_models = str(e)
            self.log(f"  evidence models not used ({e}); checking figures only")
            return None

    def _model_names(self, *names: str) -> str:
        return " + ".join(n.rsplit("/", 1)[-1] for n in names)

    async def _keyword_evidence(self, index: Optional[evidence.Index], missing: list[str],
                                run_dir: Path) -> list[evidence.KeywordSupport]:
        """Phase 1: which missing posting keywords the evidence supports. Writes keyword-evidence.md."""
        (run_dir / "keyword-evidence.md").unlink(missing_ok=True)
        if not index or not missing:
            return []
        try:
            found = await asyncio.to_thread(evidence.keyword_support, index, missing)
        except Exception as e:  # noqa: BLE001 - a model failure costs the keyword evidence, not the run
            self.log(f"  keyword evidence failed ({e}); skipping it")
            return []
        ev = self.cfg.evidence
        (run_dir / "keyword-evidence.md").write_text(
            evidence.keyword_table(found, self._model_names(ev.embedding_model, ev.reranker_model)), encoding="utf-8")
        return found

    async def _honesty(self, md: str, title: str, jd_md: str, match_md: str, index: Optional[evidence.Index],
                       run_dir: Path, warnings: list[str], review: bool = True) -> str:
        """Phase 2: check every line of the resume against its evidence (honesty.md). With review, flagged lines get
        one pass in which Claude keeps, rescopes or cuts each. Returns the resume, reviewed or not."""
        names = self._model_names(self.cfg.evidence.verifier_model)

        def check(text: str) -> list[evidence.LineCheck]:
            trace = run_dir / "trace-final.md"
            args = (text, trace.read_text() if trace.exists() else "", match_md)
            if index:
                try:
                    return evidence.check_lines(*args, index.passages, index)
                except Exception as e:  # noqa: BLE001 - a model failure leaves the figure check
                    self.log(f"  honesty models failed ({e}); checking figures only")
            return evidence.check_lines(*args, index.passages if index else evidence.candidate_passages())

        checks = await asyncio.to_thread(check, md)
        table = evidence.honesty_table(checks, names)
        (run_dir / "honesty.md").write_text(table, encoding="utf-8")
        flagged = [c for c in checks if c.flagged]
        if not flagged or not review:
            return md
        self.log(f"  Stage 4: {len(flagged)} line(s) not backed by the evidence; reviewing")
        read = lambda name: (run_dir / name).read_text() if (run_dir / name).exists() else ""
        fix = await self.runner.run(AgentCall(
            label="honesty-fix", model=self.cfg.writer_model,
            instructions=HONESTY_BRIEF + "\n\n---\nWriter brief, for the resume shape and hard rules:\n\n"
            + brief("05-resume-writer.md") + "\n\n" + formats.length_rules(self.fmt),
            documents={"jd.md": jd_md, "04-match.md": match_md, "honesty.md": table,
                       "resume-final.md (current)": md, "trace-final.md (current)": read("trace-final.md"),
                       "merge-notes.md (current)": read("merge-notes.md")},
            expect=["resume-final.md", "trace-final.md", "merge-notes.md"], run_dir=run_dir))
        md = fix.files["resume-final.md"]
        code, out = run_script("check_resume.py", "resume-final.md", "--title", title,
                               "--trace", "trace-final.md", cwd=run_dir)
        (run_dir / "checks-final.md").write_text(out + "\n")
        if code != 0:
            warnings.append("the honesty-reviewed resume fails check_resume.py; see checks-final.md")
        checks = await asyncio.to_thread(check, md)
        (run_dir / "honesty.md").write_text(evidence.honesty_table(checks, names), encoding="utf-8")
        still = [c for c in checks if c.flagged]
        if still:
            warnings.append(f"{len(still)} resume line(s) still flagged by the honesty check after review; "
                            "see honesty.md")
        return md

    async def _checked(self, call: AgentCall, checks: dict[str, Callable[[str], list[str]]]):
        """Run an agent, validate named output files, rerun once with the problems if any."""
        res = await self.runner.run(call)
        problems = [p for name, fn in checks.items() for p in fn(res.files.get(name, ""))]
        if problems:
            self.log(f"  {call.label}: fixing {len(problems)} problem(s)")
            retry = AgentCall(**{**call.__dict__, "tail": (call.tail + "\n\n" if call.tail else "")
                                 + "Your previous output had these problems: " + "; ".join(problems)
                                 + ". Write every expected file again, fixed."})
            res = await self.runner.run(retry)
            problems = [p for name, fn in checks.items() for p in fn(res.files.get(name, ""))]
            if problems:
                raise AgentError(f"{call.label}: " + "; ".join(problems))
        return res

    async def analyze(self, meta: JobMeta, jd_md: str, run_dir: Path) -> Analysis:
        """Stages 1-2: the full score. Reuses the analysis already in run_dir when it still applies."""
        cfg, A = self.cfg, self.cfg.analysis_model
        run_dir.mkdir(parents=True, exist_ok=True)
        saved = load_analysis(run_dir, jd_md)
        if saved:
            self.log("  Stages 1-2: reusing this job's full score")
            return saved
        (run_dir / "jd.md").write_text(jd_md, encoding="utf-8")
        warnings: list[str] = []
        base_sources = [config.IMPACT_SOURCE, *config.candidate().resumes]

        # Stage 1 ------------------------------------------------------------
        self.log("  Stage 1: objectives, skills, experience")
        docs = {"jd.md": jd_md}
        s1 = await asyncio.gather(*(
            self.runner.run(AgentCall(label=f"{n}", model=A, instructions=brief(f"{n}.md"),
                                      documents=docs, expect=[f"{n}.md"], candidate=False, run_dir=run_dir))
            for n in ("01-objectives", "02-skills", "03-experience")))
        objectives = s1[0].files["01-objectives.md"]
        title = exact_title(objectives, meta.title)

        # Stage 2 ------------------------------------------------------------
        self.log("  Stage 2: matcher")
        docs2 = {"jd.md": jd_md, **{f: r.files[f] for r, f in zip(
            s1, ("01-objectives.md", "02-skills.md", "03-experience.md"))}}
        note = ("\n\nOrchestrator notes:\n"
                f"- ratings.json \"sources\" must be exactly, in this order: {json.dumps(base_sources)}; "
                "\"scores\" gives one 1-10 score per source in the same order.\n"
                "- keywords.json is one flat JSON object, {\"Keyword label\": \"case-insensitive regex\", ...}, "
                "with no wrapper key.\n"
                "- Also write `recommended-base.txt` containing only the source name of the best existing resume.")
        s2 = await self._checked(
            AgentCall(label="04-matcher", model=A, instructions=brief("04-matcher.md") + note, documents=docs2,
                      expect=["04-match.md", "ratings.json", "keywords.json", "recommended-base.txt"],
                      run_dir=run_dir),
            {"ratings.json": lambda t: check_ratings(t, base_sources), "keywords.json": check_keywords})
        keywords, literal = normalize_keywords(json.loads(s2.files["keywords.json"]))
        if literal:
            warnings.append(f"keywords matched literally (their regex was invalid): {', '.join(literal)}")
        s2.files["keywords.json"] = json.dumps(keywords, indent=2)
        write_files(run_dir, s2.files)
        ratings = json.loads(s2.files["ratings.json"])
        base = s2.files["recommended-base.txt"].strip()
        resumes = config.candidate().resumes
        if base not in resumes:
            warnings.append(f"matcher recommended an unknown base {base!r}; using the first variant")
            base = next(iter(resumes))
        baseline = scorecard(ratings, keywords, variant_texts())
        (run_dir / "scorecard-baseline.md").write_text(scorecard_table(baseline))
        (run_dir / "analysis.json").write_text(json.dumps(
            {"materials": materials_sig(), "exact_title": title, "base": base}, indent=2))
        return Analysis(exact_title=title, objectives=objectives, match_md=s2.files["04-match.md"], ratings=ratings,
                        keywords=keywords, base=base, baseline=baseline, warnings=warnings)

    async def run(self, meta: JobMeta, jd_md: str, run_dir: Path) -> TailorResult:
        cfg, A, W = self.cfg, self.cfg.analysis_model, self.cfg.writer_model
        a = await self.analyze(meta, jd_md, run_dir)
        warnings = list(a.warnings)
        fmt = self.fmt = formats.get(config.candidate().resume_format)
        limits = formats.length_rules(fmt)
        (run_dir / "resume-format.txt").write_text(fmt.id + "\n")     # the résumé workspace draws its PDFs in it
        base_sources = [config.IMPACT_SOURCE, *config.candidate().resumes]
        objectives, title, match_md = a.objectives, a.exact_title, a.match_md
        ratings, keywords, base = a.ratings, a.keywords, a.base
        for stale in ("keyword-evidence.md", "honesty.md"):     # from an earlier tailoring of this job
            (run_dir / stale).unlink(missing_ok=True)
        index = await self._evidence_index()

        # Stage 3 ------------------------------------------------------------
        self.log("  Stage 3: writers A, B, C")
        wdocs = {"jd.md": jd_md, "04-match.md": match_md}
        wnote = (f"\n\nOrchestrator notes:\n- Best existing resume, for the frame only: {base} "
                 f"(<skill>/references/{config.candidate().resumes[base]}). Build every bullet from the ledger.\n"
                 f"- The posting's exact job title: {title}\n- {limits}")
        wcalls = [AgentCall(label=f"05-writer-{L}", model=W, instructions=brief("05-resume-writer.md") + wnote,
                            documents=wdocs, expect=[f"resume-draft-{L}.md", f"trace-{L}.md"],
                            tail=f"Your lens: {L} ({LENSES[L]}).", run_dir=run_dir) for L in LENSES]
        await warm(self.runner, wcalls[0])          # one cache write for the three, not one each
        writers = await asyncio.gather(*(self.runner.run(c) for c in wcalls))
        drafts = {f"Draft {L}": w.files[f"resume-draft-{L}.md"] for L, w in zip(LENSES, writers)}
        checks = {}
        for L in LENSES:
            code, out = run_script("check_resume.py", f"resume-draft-{L}.md", "--title", title,
                                   "--trace", f"trace-{L}.md", cwd=run_dir)
            checks[f"Draft {L}"] = out
        (run_dir / "checks-drafts.md").write_text("\n\n".join(checks.values()) + "\n")

        self.log("  Stage 3: rating drafts")
        rdocs = {"jd.md": jd_md, "ratings.json": json.dumps(ratings, indent=2),
                 **{f"resume-draft-{L}.md [Draft {L}]": drafts[f"Draft {L}"] for L in LENSES}}
        draft_sources = base_sources + list(drafts)
        rated = await self._checked(
            AgentCall(label="rate-drafts", model=A,
                      instructions=RATER_BRIEF.format(out="ratings-drafts.json")
                      + "\nNew sources, in this order: " + json.dumps(list(drafts)),
                      documents=rdocs, expect=["ratings-drafts.json"], run_dir=run_dir),
            {"ratings-drafts.json": lambda t: check_ratings(t, draft_sources)})
        ratings_drafts = json.loads(rated.files["ratings-drafts.json"])
        draft_card = scorecard(ratings_drafts, keywords, drafts)
        (run_dir / "scorecard-drafts.md").write_text(scorecard_table(draft_card))
        best_draft = max(drafts, key=lambda s: draft_card[s]["total"] or 0)
        best_total = draft_card[best_draft]["total"] or 0

        # Stage 4 ------------------------------------------------------------
        self.log("  Stage 4: merge")
        stage4 = section(config.candidate().personalize((config.SKILL / "SKILL-resume-job-fit.md").read_text()),
                         "Stage 4: Merge (orchestrator)")
        mdocs = {"jd.md": jd_md, "04-match.md": match_md,
                 **{f"resume-draft-{L}.md": drafts[f"Draft {L}"] for L in LENSES},
                 **{f"trace-{L}.md": w.files[f"trace-{L}.md"] for L, w in zip(LENSES, writers)},
                 "checks-drafts.md": (run_dir / "checks-drafts.md").read_text(),
                 "scorecard-drafts.md": (run_dir / "scorecard-drafts.md").read_text()}
        merge_instr = ("# Merge agent (Stage 4)\n\nYou do the orchestrator's merge step. Follow these instructions "
                       "exactly, using the writer brief's markdown shape (below) for resume-final.md.\n\n"
                       + stage4 + "\n\n---\nWriter brief, for the resume shape and hard rules:\n\n"
                       + brief("05-resume-writer.md")
                       + f"\n\nThe posting's exact job title: {title}\nBest draft by Total %: {best_draft} "
                       f"({best_total:.0f}%). Outputs: resume-final.md, trace-final.md, merge-notes.md.\n\n"
                       + limits)
        merged = await self.runner.run(AgentCall(label="merge", model=W, instructions=merge_instr, documents=mdocs,
                                                 expect=["resume-final.md", "trace-final.md", "merge-notes.md"],
                                                 run_dir=run_dir))
        final_md = merged.files["resume-final.md"]

        code, out = run_script("check_resume.py", "resume-final.md", "--title", title,
                               "--trace", "trace-final.md", cwd=run_dir)
        if code != 0:
            self.log("  Stage 4: fixing check failures")
            fix = await self.runner.run(AgentCall(
                label="merge-fix", model=W, instructions=merge_instr, documents={**mdocs,
                    "resume-final.md (your previous merge)": final_md,
                    "trace-final.md (your previous trace)": merged.files["trace-final.md"]},
                expect=["resume-final.md", "trace-final.md"],
                tail="check_resume.py failed on your merge:\n" + out
                     + "\nFix only these failures, changing nothing else.", run_dir=run_dir))
            final_md = fix.files["resume-final.md"]
            code, out = run_script("check_resume.py", "resume-final.md", "--title", title,
                                   "--trace", "trace-final.md", cwd=run_dir)
            if code != 0:
                warnings.append("resume-final.md still fails check_resume.py; see checks-final.md")
        (run_dir / "checks-final.md").write_text(out + "\n")
        final_md = await self._honesty(final_md, title, jd_md, match_md, index, run_dir, warnings)

        async def rate_once(md: str, n: int) -> dict:
            out = f"ratings-final-{n}.json"
            res = await self._checked(
                AgentCall(label=f"rate-final-{n}", model=A,
                          instructions=RATER_BRIEF.format(out=out) + '\nNew source: ["Tailored resume"]',
                          documents={"jd.md": jd_md, "ratings.json": json.dumps(ratings, indent=2),
                                     "resume-final.md [Tailored resume]": md},
                          expect=[out], run_dir=run_dir),
                {out: lambda t: check_ratings(t, base_sources + ["Tailored resume"])})
            return json.loads(res.files[out])

        async def rate_final(md: str) -> dict:
            """The final resume rated FINAL_RATINGS times independently, in parallel, and synthesized."""
            runs = await asyncio.gather(*(rate_once(md, n) for n in range(1, FINAL_RATINGS + 1)),
                                        return_exceptions=True)
            good = [r for r in runs if isinstance(r, dict)]
            if not good:
                raise runs[0]
            if len(good) < len(runs):
                warnings.append(f"final rating: {len(runs) - len(good)} of {len(runs)} raters failed; "
                                f"synthesized the other {len(good)}")
            return synthesize_ratings(good, "Tailored resume")

        ratings_final = await rate_final(final_md)
        md_card = scorecard(ratings_final, keywords, {"Tailored resume": final_md})["Tailored resume"]
        below = (md_card["total"] or 0) < best_total
        support = await self._keyword_evidence(index, md_card["missing"], run_dir)
        addable = [s for s in support if s.verdict == "supported"]
        if below or addable:
            asks = []
            if below:
                dropped = [k for k in md_card["missing"] if k not in draft_card[best_draft]["missing"]]
                self.log(f"  Stage 4: merge scored {md_card['total']:.0f}% < {best_draft} {best_total:.0f}%; "
                         "restoring keywords")
                asks.append(f"Your merge scored {md_card['total']:.0f}%, below {best_draft} ({best_total:.0f}%). "
                            f"Keywords the merge dropped: {', '.join(dropped) or '(see scorecard)'}. Restore them "
                            "where the evidence allows, keep everything else, and note any you hold back in "
                            "merge-notes.md.")
            if addable:
                self.log(f"  Stage 4: the evidence supports {len(addable)} missing keyword(s); adding them")
                asks.append(KEYWORD_ADD.format(supported="; ".join(
                    f"{s.keyword} ({s.evidence.source})" for s in addable)))
            rdocs = {**mdocs, "resume-final.md (your previous merge)": final_md}
            if (run_dir / "keyword-evidence.md").exists():
                rdocs["keyword-evidence.md"] = (run_dir / "keyword-evidence.md").read_text()
            fix = await self.runner.run(AgentCall(
                label="merge-restore", model=W, instructions=merge_instr, documents=rdocs,
                expect=["resume-final.md", "trace-final.md", "merge-notes.md"],
                tail="\n\n".join(asks) + "\n\nWork them into existing bullets rather than adding bullets; the "
                     "length limit still holds.",
                run_dir=run_dir))
            final_md = fix.files["resume-final.md"]
            final_md = await self._honesty(final_md, title, jd_md, match_md, index, run_dir, warnings)
            ratings_final = await rate_final(final_md)
            code, out = run_script("check_resume.py", "resume-final.md", "--title", title,
                                   "--trace", "trace-final.md", cwd=run_dir)
            (run_dir / "checks-final.md").write_text(out + "\n")
            if code != 0:
                warnings.append("keyword-restored resume fails check_resume.py; see checks-final.md")
        (run_dir / "resume-final.md").write_text(final_md, encoding="utf-8")

        # Stage 5 ------------------------------------------------------------
        self.log("  Stage 5: PDF")
        pdf_name = f"{config.candidate().pdf_prefix}-{_file_part(meta.company)}-{_file_part(title)}.pdf"
        pdf = self.out_dir / pdf_name
        pdf_text, pages = "", 0
        try:
            pages, pdf_text = await asyncio.to_thread(render.render_pdf, final_md, pdf, run_dir / "resume-final.html", fmt,
                                                      config.candidate().resume_title(title, meta.company))
            trimmed = False
            for _ in range(2):                  # over the page limit: cut it back (deleting only), then render again
                if pages <= fmt.max_pages:
                    break
                words = len(re.findall(r"\b\w+\b", final_md))
                self.log(f"  Stage 5: {pages} pages ({words} words); trimming to {fmt.pages_word}")
                trace = run_dir / "trace-final.md"
                trim = await self.runner.run(AgentCall(
                    label="trim", model=W,
                    instructions=TRIM_BRIEF.format(pages=pages, words=words, limit=fmt.pages_word, limits=limits)
                    + "\n\n---\nWriter brief, for the resume shape and hard rules:\n\n" + brief("05-resume-writer.md"),
                    documents={"jd.md": jd_md, "04-match.md": match_md, "keywords.json": json.dumps(keywords, indent=2),
                               "resume-final.md (current)": final_md,
                               "trace-final.md (current)": trace.read_text() if trace.exists() else ""},
                    expect=["resume-final.md", "trace-final.md"], run_dir=run_dir))
                final_md, trimmed = trim.files["resume-final.md"], True
                pages, pdf_text = await asyncio.to_thread(render.render_pdf, final_md, pdf, run_dir / "resume-final.html", fmt,
                                                      config.candidate().resume_title(title, meta.company))
            if trimmed:
                (run_dir / "resume-final.md").write_text(final_md, encoding="utf-8")
                await self._honesty(final_md, title, jd_md, match_md, index, run_dir, warnings, review=False)
                code, out = run_script("check_resume.py", "resume-final.md", "--title", title,
                                       "--trace", "trace-final.md", cwd=run_dir)
                (run_dir / "checks-final.md").write_text(out + "\n")
                if code != 0:
                    warnings.append("trimmed resume fails check_resume.py; see checks-final.md")
                ratings_final = await rate_final(final_md)      # the content changed: rate it again
            (run_dir / "resume-final.pdf.txt").write_text(pdf_text, encoding="utf-8")
            if pages != fmt.max_pages:
                warnings.append(f"PDF has {pages} page(s), not {fmt.max_pages} ({fmt.name} format)")
            if title not in re.sub(r"\s+", " ", pdf_text):
                warnings.append("exact posting title not found in PDF text")
        except Exception as e:  # noqa: BLE001 - keep the run; report the render failure
            warnings.append(f"PDF render failed: {e}")
            pdf = None
        texts = variant_texts()
        texts["Tailored resume"] = pdf_text or final_md
        final_card = scorecard(ratings_final, keywords, texts)
        (run_dir / "scorecard-final.md").write_text(
            "Scorecard (Tailored resume keywords measured on the PDF text):\n\n" + scorecard_table(final_card))
        await self._keyword_evidence(index, final_card["Tailored resume"]["missing"], run_dir)   # for the report

        # Stage 6 ------------------------------------------------------------
        self.log("  Stage 6: heat map and report")
        (run_dir / "ratings-final.json").write_text(json.dumps(ratings_final, indent=2))
        heatmap = self.out_dir / f"heatmap-{_file_part(meta.company)}-{_file_part(title)}.html"
        code, out = run_script("heatmap.py", "ratings-final.json", str(heatmap), cwd=run_dir)
        if code != 0:
            warnings.append(f"heat map skipped: {out.splitlines()[-1] if out else 'error'}")
            heatmap = None
        rep = await self.runner.run(AgentCall(
            label="report", model=A, instructions=REPORT_BRIEF.format(heatmap=heatmap.name if heatmap else "(not built)"),
            documents={"01-objectives.md": objectives, "04-match.md": match_md,
                       "merge-notes.md": (run_dir / "merge-notes.md").read_text(),
                       "ratings-final.json": json.dumps(ratings_final, indent=2),
                       "scorecard-baseline.md": (run_dir / "scorecard-baseline.md").read_text(),
                       "scorecard-final.md": (run_dir / "scorecard-final.md").read_text(),
                       "checks-final.md": (run_dir / "checks-final.md").read_text(),
                       **{f: (run_dir / f).read_text() for f in ("keyword-evidence.md", "honesty.md")
                          if (run_dir / f).exists()}},
            expect=["report.md", "notion-summary.txt"], run_dir=run_dir))
        report = self.out_dir / f"report-{_file_part(meta.company)}-{_file_part(title)}.md"
        report.write_text(rep.files["report.md"], encoding="utf-8")
        (self.out_dir / f"resume-{_file_part(meta.company)}-{_file_part(title)}.md").write_text(final_md, encoding="utf-8")

        scores = ratings_final.get("scores") or []
        return TailorResult(
            run_dir=run_dir, exact_title=title,
            impact_score=_num(scores[0]) if scores else None,
            resume_score=_num(scores[-1]) if scores else None,
            final_card=final_card["Tailored resume"], pdf=pdf, pages=pages, report=report,
            heatmap=heatmap, notion_summary=rep.files["notion-summary.txt"].strip(),
            unconfirmed_gaps=unconfirmed_gaps(match_md), warnings=warnings)


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _file_part(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-")
    return s[:60].rstrip("-") or "Role"

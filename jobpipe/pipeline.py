"""search -> filter -> triage -> tailor the best -> Notion -> digest."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import config
from .config import Config, HiringCafeSearch, Search
from .hiringcafe import HiringCafe, Unreadable, links, posting_index
from .himalayas import Himalayas
from .jd import build_jd, posting_url
from .jobs_api import BudgetExhausted, JobsClient, company_matches, post_filter, salary_text, work_mode
from .llm import PROVIDERS, ClaudeCodeRunner, ClaudeRunner, OpenAICompatRunner, Runner
from .lowfit import dismiss_low_fit
from .notion import APPLIED_STATUSES, NotionTracker, TrackerEntry
from .startups import funding_line
from .store import Store, canonical_url, slug
from .tailor import JobMeta, Tailor, TailorResult, copy_analysis
from .tracker import LocalTracker
from .triage import Triage, rank_key, triage_many


@dataclass
class Candidate:
    job: dict
    search_id: str
    flags: list[str] = field(default_factory=list)
    jd_md: str = ""
    usable: bool = False
    triage: Optional[Triage] = None
    error: str = ""
    tailored: Optional[TailorResult] = None
    notion: str = ""

    @property
    def id(self) -> str:
        return str(self.job["id"])

    @property
    def meta(self) -> JobMeta:
        j = self.job
        return JobMeta(job_id=self.id, title=j.get("job_title", ""), company=j.get("company") or "Unknown",
                       url=posting_url(j), salary=salary_text(j),
                       location=j.get("location") or j.get("short_location") or "")


def make_runner(cfg: Config) -> Runner:
    if cfg.backend == "api":
        return ClaudeRunner(fallbacks=cfg.fallbacks)
    if cfg.backend == "claude-code":
        return ClaudeCodeRunner(claude_bin=cfg.claude_bin)
    if cfg.backend in PROVIDERS:
        return OpenAICompatRunner(provider=cfg.backend, base_url=cfg.base_url, key_env=cfg.api_key_env)
    raise ValueError(f"models.backend must be claude-code, api or one of {', '.join(PROVIDERS)}, not {cfg.backend!r}")


def make_tracker(cfg: Config, log: Callable[[str], None]) -> LocalTracker:
    """The local tracker (data/tracker.db), with Notion as its synced copy when Notion is enabled and a token is set."""
    notion = None
    if cfg.notion_enabled:
        if os.environ.get("NOTION_TOKEN"):
            notion = NotionTracker(cfg.notion_data_source_id, cfg.notion_project_page_id)
        else:
            log("Notion: NOTION_TOKEN not set, so tracked jobs stay on this Mac and aren't copied to Notion (see docs/setup-from-code.md).")
    return LocalTracker(config.DATA / "tracker.db", notion=notion, seed=config.DATA / "notion-cache.json")


def notes_line(c: Candidate, today: str) -> str:
    where = " / ".join(x for x in (c.meta.location, work_mode(c.job), c.meta.salary) if x)
    funding = funding_line(c.job.get("funding"))
    if funding:
        where = f"{where} · {funding}" if where else funding
    if c.tailored:
        t, card = c.tailored, c.tailored.final_card
        pct = lambda v: "n/a" if v is None else f"{v:.0f}%"
        return (f"Scored {today} · Résumé {t.resume_score or '?'}/10 · ATS {pct(card['total'])} "
                f"(Skills {pct(card['skills'])} / Experience {pct(card['experience'])} / "
                f"Keywords {pct(card['keywords'])}) · {where}")
    return f"Scored {today} · Signal score {c.triage.fit_score}/10 · {c.triage.one_line} · {where}"


def tracker_entry(c: Candidate, today: str) -> TrackerEntry:
    m = c.meta
    if c.tailored:
        t = c.tailored
        body = [f"**Posting:** {m.url}", t.notion_summary,
                f"**Scores:** Full score {t.impact_score or '?'}/10 · Résumé {t.resume_score or '?'}/10 · "
                f"ATS {t.final_card['total'] or 0:.0f}%",
                f"**Resume:** {t.pdf.name if t.pdf else '(PDF not rendered)'}"]
        if t.unconfirmed_gaps:
            body.append("**Unconfirmed gaps:** " + "; ".join(t.unconfirmed_gaps))
        if funding_line(c.job.get("funding")):
            body.append(f"**Funding:** {funding_line(c.job['funding'])}")
        return TrackerEntry(title=t.exact_title, company=m.company, url=m.url, fit_score=t.impact_score,
                            notes=notes_line(c, today), status="In progress", body=body)
    tr = c.triage
    body = [f"**Posting:** {m.url}", f"**Signal score:** {tr.fit_score}/10. {tr.one_line}"]
    if funding_line(c.job.get("funding")):
        body.append(f"**Funding:** {funding_line(c.job['funding'])}")
    if tr.strongest_matches:
        body.append("**Strongest matches:** " + "; ".join(tr.strongest_matches))
    if tr.likely_gaps:
        body.append("**Likely gaps (unconfirmed):** " + "; ".join(tr.likely_gaps))
    body.append("Not tailored yet. Run: python -m jobpipe tailor " + c.id)
    return TrackerEntry(title=m.title, company=m.company, url=m.url, fit_score=float(tr.fit_score),
                        notes=notes_line(c, today), status="Not started", body=body)


def tailoring_entry(c: Candidate, st: dict, today: str) -> TrackerEntry:
    """The row a job gets when tailoring starts, if it isn't tracked yet: its best score so far. The run's
    own entry replaces the notes and score when it ends."""
    tri = c.triage.__dict__ if c.triage else st.get("triage") or {}
    fit = st.get("impact_score") if st.get("impact_score") is not None else tri.get("fit_score")
    return TrackerEntry(title=c.meta.title, company=c.meta.company, url=c.meta.url,
                        fit_score=float(fit) if fit is not None else None,
                        notes=notes_line(c, today) if c.triage else "", status="In progress")


def apply_packet(c: Candidate) -> str:
    m, t = c.meta, c.tailored
    hard = ""
    obj = (t.run_dir / "01-objectives.md") if t else None
    if obj and obj.exists():
        from .tailor import section
        hard = section(obj.read_text(), "Hard constraints stated in the posting")
    return "\n".join([
        f"# Apply: {t.exact_title if t else m.title} — {m.company}", "",
        f"- Posting: {m.url}",
        f"- Location / arrangement / pay: {m.location or 'n/a'} / {work_mode(c.job)} / {m.salary}",
        f"- Resume PDF: {t.pdf.name if t and t.pdf else 'n/a'}",
        f"- Fit report: {t.report.name if t else 'n/a'}",
        f"- Impact-record fit {t.impact_score if t else '?'}/10; tailored resume {t.resume_score if t else '?'}/10", "",
        "## Hard constraints stated in the posting", hard or "See the report.", "",
        "## Before you submit",
        "- [ ] Answer the report's follow-up questions; re-run `python -m jobpipe tailor " + c.id + "` if answers change what can be claimed",
        "- [ ] Upload the PDF above; use the same email and phone as the resume",
        "- [ ] Answer work authorization, self-identification and certification questions yourself",
        "- [ ] Set Status to Applied and Resume Used in the Notion row after submitting", "",
    ])


def digest(cands: list[Candidate], credits_spent: int, out_dir: Path, today: str, skipped: list[str]) -> Path:
    rows = ["| Fit | Title | Company | Where | Pay | Search | Result |", "|---|---|---|---|---|---|---|"]
    ordered = sorted(cands, key=lambda c: (-(c.triage.fit_score if c.triage else 0), c.meta.company))
    for c in ordered:
        fit = f"{c.triage.fit_score}/10" if c.triage else "–"
        if c.tailored:
            result = f"tailored: {c.tailored.pdf.name if c.tailored.pdf else 'no PDF'}"
        elif c.error:
            result = c.error
        else:
            result = c.triage.one_line if c.triage else ""
        flags = f" ({', '.join(c.flags)})" if c.flags else ""
        rows.append(f"| {fit} | [{c.meta.title}]({c.meta.url}) | {c.meta.company} | "
                    f"{c.meta.location} {work_mode(c.job)}{flags} | {c.meta.salary} | {c.search_id} | {result} |")
    text = "\n".join([f"# Job pipeline digest, {today}", "",
                      f"JobsPipe credits charged this run: {credits_spent}", "", *rows, ""]
                     + (["## Skipped", *[f"- {s}" for s in skipped], ""] if skipped else []))
    path = out_dir / "digest.md"
    path.write_text(text, encoding="utf-8")
    return path


class Pipeline:
    def __init__(self, cfg: Config, *, store: Optional[Store] = None, jobs: Optional[JobsClient] = None,
                 runner: Optional[Runner] = None, tracker: Optional[NotionTracker | LocalTracker] = None,
                 use_notion: bool = True, log: Callable[[str], None] = print, fetch_pages: bool = True,
                 himalayas: Optional[Himalayas] = None, hiringcafe: Optional[HiringCafe] = None):
        self.cfg = cfg
        self.log = log
        self.store = store or Store()
        self.jobs = jobs or JobsClient(cfg, self.store)
        self.himalayas = himalayas or (Himalayas(log=log) if cfg.himalayas else None)
        self.hiringcafe = hiringcafe        # made when a run has HiringCafe searches to check
        self.runner = runner or make_runner(cfg)
        if isinstance(tracker, NotionTracker):      # a bare Notion client: give it the local tracker in front
            tracker = LocalTracker(self.store.root / "tracker.db", notion=tracker)
        self.tracker = tracker if tracker is not None else (make_tracker(cfg, log) if use_notion else None)
        self.fetch_pages = fetch_pages
        self.today = date.today().isoformat()
        self.out_dir = config.OUTPUTS / self.today
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # ---- steps ---------------------------------------------------------------
    def search(self, searches: list[Search], hiringcafe: Sequence[HiringCafeSearch] = ()) -> tuple[list[Candidate], list[str]]:
        cands: dict[str, Candidate] = {}
        skipped: list[str] = []
        out_of_credits = False
        for s in searches:
            found = self._himalayas(s)
            if not out_of_credits:
                try:
                    res = self.jobs.run(s)
                except BudgetExhausted as e:
                    skipped.append(f"{s.id}: {e}")
                    out_of_credits = True       # Himalayas is free: the other filters still ask it
                else:
                    charged = res.credits_charged if res.credits_charged is not None else len(res.jobs)
                    self.log(f"{s.id}: {len(res.jobs)} jobs"
                             + (f" of {res.total_results}" if res.total_results is not None else "")
                             + f", {charged} credit(s)" + (f" ({res.skipped_reason})" if res.skipped_reason else ""))
                    if res.skipped_reason and not res.jobs:
                        skipped.append(f"{s.id}: {res.skipped_reason}")
                    found = res.jobs + found
            for job in found:
                keep, flags = post_filter(job, s, self.cfg.enforce_min_salary)
                jid = str(job["id"])
                if not keep:
                    skipped.append(f"{job.get('job_title')} @ {job.get('company')}: {', '.join(flags)}")
                elif jid not in cands:
                    cands[jid] = Candidate(job, s.id, flags)
        # After JobsPipe and Himalayas, so a posting they found this run is the job HiringCafe's result joins.
        # HiringCafe searches keep their own filters; only the defaults' excluded companies apply to them.
        never = list(self.cfg.defaults.get("exclude_companies") or [])
        for job, s in self._hiringcafe(hiringcafe):
            if job.get("closed_at"):
                skipped.append(f"{job.get('job_title')} @ {job.get('company')}: posting closed")
            elif company_matches(job.get("company", ""), never):
                skipped.append(f"{job.get('job_title')} @ {job.get('company')}: excluded company")
            elif str(job["id"]) not in cands:
                cands[str(job["id"])] = Candidate(job, s.id)
        return list(cands.values()), skipped

    def _himalayas(self, s: Search) -> list[dict]:
        """This filter's jobs on Himalayas (free), stored like JobsPipe's. A job JobsPipe already stored keeps its data."""
        if not self.himalayas:
            return []
        jobs = []
        for job in self.himalayas.search(s):
            old = self.store.job(job["id"])
            job = {**job, **old} if old else job
            self.store.save_job(job, s.id)
            jobs.append(job)
        if jobs or not self.himalayas.limited:
            self.log(f"{s.id}: {len(jobs)} job(s) on Himalayas (free)")
        return jobs

    def _hiringcafe(self, searches: Sequence[HiringCafeSearch]) -> list[tuple[dict, HiringCafeSearch]]:
        """Each HiringCafe search's jobs (free). A result at a posting already stored, from any source, is that job,
        as it's stored: it only gains this search. Each check is recorded with how many jobs were new."""
        if not searches:
            return []
        self.hiringcafe = self.hiringcafe or HiringCafe(log=self.log)
        known = posting_index(self.store)
        out = []
        for s in searches:
            try:
                found = self.hiringcafe.search(s)
            except Unreadable as e:
                self.log(f"{s.id}: HiringCafe search not read: {e}")
                self.store.record_hiringcafe_check(s.id, error=str(e))
                continue
            new = 0
            for job in found:
                jid = next((known[k] for k in sorted(links(job)) if k in known), None)
                stored = self.store.job(jid) if jid else {}
                if stored:
                    job = stored
                else:
                    new += 1
                self.store.save_job(job, s.id)
                for k in links(job):
                    known.setdefault(k, str(job["id"]))
                out.append((job, s))
            self.store.record_hiringcafe_check(s.id, found=len(found), new=new)
            self.log(f"{s.id}: {len(found)} job(s) on HiringCafe (free), {new} new")
        return out

    def drop_known(self, cands: list[Candidate], retriage: bool) -> tuple[list[Candidate], list[str]]:
        state = self.store.state()
        notion = {}
        if self.tracker:
            try:
                notion = self.tracker.known_urls()
            except Exception as e:  # noqa: BLE001
                self.log(f"Notion lookup failed ({e}); continuing without dedupe")
        keep, skipped = [], []
        for c in cands:
            st = notion.get(canonical_url(c.meta.url))
            if st in (*APPLIED_STATUSES, "Not Applying"):
                skipped.append(f"{c.meta.title} @ {c.meta.company}: already {st} in Notion")
            elif not retriage and state.get(c.id, {}).get("triage"):
                skipped.append(f"{c.meta.title} @ {c.meta.company}: triaged on an earlier run "
                               f"({state[c.id]['triage']['fit_score']}/10)")
            else:
                keep.append(c)
        return keep, skipped

    async def triage(self, cands: list[Candidate]) -> None:
        for c in cands:
            c.jd_md, c.usable = build_jd(c.job, fetch=self.fetch_pages)
            if not c.usable:
                c.error = "no posting text (listing too short, page not fetchable)"
        todo = {c.id: c.jd_md for c in cands if c.usable}
        if not todo:
            return
        self.log(f"Triage: {len(todo)} job(s)")
        results = await triage_many(self.runner, self.cfg.triage_model, todo)
        for c in cands:
            r = results.get(c.id)
            if isinstance(r, Triage):
                c.triage = r
                self.store.update(c.id, triage=r.__dict__, triaged_at=self.today)
            elif r is not None:
                c.error = f"triage failed: {r}"
        dismiss_low_fit(self.cfg, self.store, [c.id for c in cands if isinstance(results.get(c.id), Triage)], self.log)

    def shortlist(self, cands: list[Candidate], top: int) -> list[Candidate]:
        ok = [c for c in cands if c.triage and c.triage.fit_score >= self.cfg.shortlist_min_fit]
        ok.sort(key=lambda c: rank_key(c.triage, c.job.get("max_annual_salary_usd")))
        return ok[:top]

    async def tailor(self, c: Candidate) -> None:
        with self.store.tailoring(c.id) as mine:
            if not mine:
                c.error = "another run is tailoring this job"
                self.log(f"Skipped tailoring {c.meta.title} @ {c.meta.company}: {c.error}.")
                return
            if isinstance(self.tracker, LocalTracker):   # In progress from the start, not when the résumé is done
                self.tracker.start(tailoring_entry(c, self.store.state().get(c.id, {}), self.today), job_id=c.id)
            await self._tailor(c)

    async def _tailor(self, c: Candidate) -> None:
        run_dir = self.out_dir / "runs" / slug(c.meta.company, c.meta.title, c.id)
        versions = run_dir / "versions"
        if versions.exists():  # re-tailoring: the web app's edit history belonged to the old résumé
            versions.rename(run_dir / f"versions-before-{datetime.now():%H%M%S}")
        self.log(f"Tailoring: {c.meta.title} @ {c.meta.company}")
        prior = self.store.state().get(c.id, {}).get("analysis_dir")
        if prior:   # a full score from the web app: stages 1-2 are reused if nothing changed since
            copy_analysis(Path(prior), run_dir)
        try:
            c.tailored = await Tailor(self.cfg, self.runner, self.out_dir, self.log).run(c.meta, c.jd_md, run_dir)
            for w in c.tailored.warnings:
                self.log(f"  warning: {w}")
            (self.out_dir / f"apply-{slug(c.meta.company, c.meta.title)}.md").write_text(apply_packet(c))
            t = c.tailored
            self.store.update(c.id, tailored_at=self.today, run_dir=str(run_dir),
                              pdf=str(t.pdf) if t.pdf else None, report=str(t.report),
                              heatmap=str(t.heatmap) if t.heatmap else None, exact_title=t.exact_title,
                              impact_score=t.impact_score, resume_score=t.resume_score,
                              ats_total=t.final_card.get("total"))
        except Exception as e:  # noqa: BLE001 - one job's failure shouldn't stop the others
            c.error = f"tailoring failed: {e}"
            self.log(f"  {c.error}")

    def sync_notion(self, cands: list[Candidate]) -> None:
        if not self.tracker:
            return
        floor = self.cfg.notion_log_triaged_min_fit
        for c in cands:
            if not (c.tailored or (c.triage and floor is not None and c.triage.fit_score >= floor)):
                continue
            # Tracked locally first; Notion gets it now if it answers, otherwise on the next sync.
            action, url = self.tracker.upsert(tracker_entry(c, self.today), job_id=c.id)
            c.notion = url
            self.store.update(c.id, notion_url=url)
            where = url or (f"waiting to sync to Notion: {self.tracker.state()['error']}" if self.tracker.notion and c.meta.url
                            else "kept on this Mac")
            self.log(f"Tracker: {action} {c.meta.title} @ {c.meta.company} ({where})")

    # ---- entry points ----------------------------------------------------------
    async def run(self, searches: list[Search], *, top: Optional[int] = None, tailor: bool = True,
                  retriage: bool = False, hiringcafe: Sequence[HiringCafeSearch] = ()) -> Path:
        cands, skipped = self.search(searches, hiringcafe)
        cands, more = self.drop_known(cands, retriage)
        skipped += more
        await self.triage(cands)
        if tailor:
            for c in self.shortlist(cands, self.cfg.tailor_top if top is None else top):
                await self.tailor(c)
        self.sync_notion(cands)
        path = digest(cands, self.jobs.budget.spent_this_run, self.out_dir, self.today, skipped)
        self.log(f"Digest: {path}")
        return path

    async def tailor_job(self, job: dict, search_id: str = "manual") -> Candidate:
        c = Candidate(job, search_id)
        self.store.save_job(job, search_id)
        c.jd_md, c.usable = build_jd(job, fetch=self.fetch_pages)
        if not c.usable:
            raise RuntimeError("No usable posting text for this job. Pass --jd-file with the pasted description.")
        await self.tailor(c)
        if c.tailored and self.tracker:
            self.sync_notion([c])
        return c

"""Local web app: job tracker (kept locally, synced with Notion), search editor, pipeline runs,
résumé review and edits.

Security: listens on 127.0.0.1 only, rejects requests whose Host isn't localhost (stops DNS
rebinding), and every API call needs the per-machine token (data/web-token) in the
X-Jobpipe-Token header, or ?t= on file links. The app can spend JobsPipe credits, run Claude
on your subscription and write to Notion, so no other site or machine should reach it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote
from typing import Any, Optional

import httpx
import markdown as md_lib
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import config
from ..config import Config
from .. import formats, jd, render, resume_import
from ..postings import PostingKeeper
from ..jd import MIN_PASTED_CHARS, build_jd, normalize_posting_url, posting_url
from ..config import ModelCfg
from ..llm import BACKEND_LABELS, CLAUDE_MODELS, PROVIDERS, AgentError, OpenAICompatRunner, Runner
from ..notion import STATUSES, NotionTracker, TrackerEntry
from .. import startups as su
from ..startups import StartupStore, funding_block, funding_line
from ..store import Store, now_iso, slug
from ..tracker import LocalTracker
from ..triage import triage_one
from . import linked, searches_file
from .sent import SentResumes
from .board import JobBoard, Marks, TrackerSync, clean_url
from .resumes import EditError, ResumeWorkspace
from .progress import Estimator
from .runs import Busy, Run, RunManager
from .scoring import Scorer

STATIC = Path(__file__).parent / "static"
IMPACT_SNAPSHOT_EVERY = 600     # seconds
IMPACT_SNAPSHOTS = 50

# What the Interview prep tab starts with. Claude adds to it on request, under these headings.
INTERVIEW_PREP_STARTER = """# Interview prep

Stories to find, examples to dig up and questions to get ready for. Write here, or ask Claude to add something and it goes in this file.

## Stories to find

*Situations you need a story for, such as a disagreement with a peer or a project that slipped.*

## Examples and numbers to dig up

*Proof points to look up before an interview: metrics, dates, team sizes.*

## Questions to prepare for

*Questions you expect, and the story or example that answers each.*

## Notes
"""


LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s")
REMOTE_WORD, HYBRID_WORD = re.compile(r"\bremote\b", re.I), re.compile(r"\bhybrid\b", re.I)


def loosen_lists(text: str) -> str:
    """Python-Markdown needs a blank line before a list; the reports often don't have one."""
    out: list[str] = []
    for line in text.splitlines():
        if LIST_ITEM.match(line) and out and out[-1].strip() and not LIST_ITEM.match(out[-1]) \
                and not out[-1].startswith(("|", " ", "\t")):
            out.append("")
        out.append(line)
    return "\n".join(out)


def short_label(backend: str) -> str:
    """An AI's name without the detail in brackets: "Kimi" for "Kimi (Moonshot AI API)"."""
    return BACKEND_LABELS.get(backend, backend).split(" (")[0]


def md_html(text: str) -> str:
    """Markdown to HTML with raw HTML escaped (reports and change notes quote posting text)."""
    text = text.replace("&", "&amp;").replace("<", "&lt;")
    html = md_lib.markdown(loosen_lists(text), extensions=["tables", "fenced_code", "sane_lists"])
    # Task items (- [ ] / - [x]), as the editor's Formatted view shows them.
    return re.sub(r"<li>(<p>)?\[([ xX])\]\s", lambda m: f'<li class="task">{m[1] or ""}{"☑" if m[2] != " " else "☐"} ', html)


def run_scores(run_dir: Path) -> dict:
    """Impact / tailored-resume scores and the final ATS total, read from a run folder
    (for jobs tailored before the pipeline started saving them in state.json)."""
    out: dict[str, Any] = {}
    try:
        scores = json.loads((run_dir / "ratings-final.json").read_text()).get("scores") or []
        if scores:
            out["impact_score"], out["resume_score"] = float(scores[0]), float(scores[-1])
    except (OSError, ValueError, TypeError):
        pass
    try:
        m = re.search(r"^\| Tailored resume \|.*?\| (\d+)%[^|]*\|\s*$",
                      (run_dir / "scorecard-final.md").read_text(), re.M)
        if m:
            out["ats_total"] = float(m.group(1))
    except OSError:
        pass
    return out


def load_token(data_dir: Path) -> str:
    path = data_dir / "web-token"
    if path.exists():
        return path.read_text().strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(24)
    path.write_text(tok)
    os.chmod(path, 0o600)
    return tok


FACTS_HEADING = re.compile(r"^#\s+confirmed facts\s*#*\s*$", re.I)


def add_confirmed_fact(md: str, text: str, day: date, first_name: str) -> str:
    """The impact record with one dated, user-confirmed fact appended to its top-level Confirmed facts
    section, which is added at the end if it isn't there yet."""
    fact = re.sub(r"\s*\n\s*", " ", text.strip())
    item = f"- **{day:%b} {day.day}, {day.year}, user-confirmed:** {fact}"
    lines, fenced, start, end = md.rstrip("\n").split("\n"), False, None, None
    for i, line in enumerate(lines):
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
        elif not fenced and start is None and FACTS_HEADING.match(line):
            start = i
        elif not fenced and start is not None and re.match(r"^#\s", line):
            end = i
            break
    if start is None:
        intro = (f"Answers {first_name} gave to follow-up questions. Every score, tailoring run and résumé edit "
                 "treats them as user-confirmed evidence, at exactly the scope stated.")
        return md.rstrip("\n") + f"\n\n# Confirmed facts\n\n{intro}\n\n{item}\n"
    after = lines[end:] if end is not None else []    # the next top-level heading on
    end = len(lines) if end is None else end
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    in_list = re.match(r"^(\s*([-*+]|\d+[.)])\s|\s{2,}\S)", lines[end - 1])   # an item, or an item's wrapped line
    gap = [] if in_list else [""]                     # a list after a paragraph needs a blank line
    return "\n".join([*lines[:end], *gap, item, *([""] + after if after else [])]) + "\n"


def create_app(cfg: Config, *, token: str, store: Optional[Store] = None,
               tracker: Optional[NotionTracker | LocalTracker] = None, runner_factory=None,
               runs: Optional[RunManager] = None, allowed_hosts: Optional[set[str]] = None,
               searches_path: Optional[Path] = None,
               resume_root: Optional[Path] = None, impact_path: Optional[Path] = None,
               prep_path: Optional[Path] = None) -> FastAPI:
    store = store or Store()
    searches_path = searches_path or config.searches_path()
    impact_path = impact_path or config.REFERENCES / "impact-record.md"
    prep_path = prep_path or store.root / "interview-prep.md"   # personal notes, in the profile's data/
    allowed = allowed_hosts or {"127.0.0.1", "localhost"}
    app = FastAPI(title="jobpipe", docs_url=None, redoc_url=None, openapi_url=None)
    state: dict[str, Any] = {"runner": None, "edit_locks": {}, "reports": {}, "chatting": set(),
                             "chat_runners": {}, "model_lists": {}}
    # The tracker lives in data/tracker.db. Given a NotionTracker (or none), it is wrapped here; the first
    # run starts from the last Notion snapshot the app saved, so nothing tracked is lost in the move.
    local = tracker if isinstance(tracker, LocalTracker) else LocalTracker(
        store.root / "tracker.db", notion=tracker, seed=store.root / "notion-cache.json")
    runs = runs or RunManager(db=local.path)   # run history sits beside the tracker in data/tracker.db
    progress = Estimator(runs)                 # how far along a tailoring run is, for the ring on its fit badge
    marks = Marks(store.root / "marks.json")
    # Jobs kept under an id that file names and URLs can't hold (store.safe_id) move to a safe one, and the
    # tracker rows, run history and stars that name them follow, before anything reads them.
    moved = store.fix_ids()
    if moved:
        local.rename_jobs(moved)
        runs.rename_jobs(moved)
        for old, new in moved.items():
            if marks.get(old):
                marks.set(new, old)
    # A job you applied to keeps its posting: saved when its status reaches an applied stage, here or in Notion.
    keeper = PostingKeeper(store, local)
    app.state.posting_keeper = keeper
    sync = TrackerSync(local, after=keeper.kick)
    keeper.kick()

    def materials() -> tuple[str, int]:
        """The impact record's path, plus a signature of every file the runner's cached system
        prompt is built from and of the candidate, so an edited impact record is used at once."""
        files = {*config.REFERENCES.glob("*.md"), *(config.SKILL / "references").glob("*.md"), impact_path}
        sig = tuple(sorted((str(f), f.stat().st_mtime_ns) for f in files if f.exists()))
        return str(impact_path), hash((sig, repr(config.candidate())))

    def get_runner() -> Runner:
        sig = materials()[1]
        if state["runner"] is None or state.get("runner_sig") != sig:
            from ..pipeline import make_runner
            state["runner"], state["runner_sig"] = (runner_factory or make_runner)(cfg), sig
        return state["runner"]

    def chat_ai(backend: str, model: str) -> tuple[Runner, ModelCfg, str]:
        """The runner and model for one chat message, which may use another AI than the Profile's: any
        provider set up on the Profile tab. Returns (runner, model, the label the reply is tagged with)."""
        writer = cfg.writer_model
        backend, model = backend or cfg.backend, model or writer.model
        if backend not in BACKEND_LABELS:
            raise HTTPException(400, "Choose an AI for the chat.")
        if not searches_file.MODEL_ID.fullmatch(model):
            raise HTTPException(400, "That model name can't be used.")
        via = f"{short_label(backend)} · {model}"
        if backend == cfg.backend:
            return get_runner(), ModelCfg(model, writer.effort), via
        base_url = (searches_file.load_models(searches_path)["saved"].get(backend) or {}).get("base_url") or None
        sig = (backend, base_url, materials()[1])
        if state["chat_runners"].get(backend, (None,))[0] != sig:
            from dataclasses import replace
            from ..pipeline import make_runner
            try:
                r = (runner_factory or make_runner)(replace(cfg, backend=backend, base_url=base_url))
            except (RuntimeError, ValueError) as e:
                raise HTTPException(400, str(e)) from None
            state["chat_runners"][backend] = (sig, r)
        return state["chat_runners"][backend][1], ModelCfg(model, writer.effort), via

    # ---- security ----------------------------------------------------------------
    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in allowed:
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        path = request.url.path
        if path.startswith("/api/"):
            supplied = request.headers.get("x-jobpipe-token") or (
                request.query_params.get("t") if request.method == "GET" else None)
            if not supplied or not secrets.compare_digest(supplied, token):
                return JSONResponse({"detail": "Missing or wrong token. Open the app with "
                                               "`jobsearch run`, or the URL it prints."}, status_code=401)
        response = await call_next(request)
        if path.startswith("/static/"):
            # index.html links assets as ?v=<hash of their mtimes>, so those can be cached for good.
            response.headers["Cache-Control"] = ("public, max-age=31536000, immutable"
                                                 if "v" in request.query_params else "no-cache")
        elif "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @app.get("/", response_class=HTMLResponse)
    def index():
        assets = ["app.css", "app.js", "vendor/editor.js", "theme-boot.js", "theme.js", "themes/sorbet.css", "themes/sorbet.js",
                  *(f"themes/{n}.{x}" for n in ("transit", "bauhaus", "arcade") for x in ("css", "js"))]
        v = hashlib.blake2b("".join(str((STATIC / a).stat().st_mtime_ns) for a in assets).encode(), digest_size=6).hexdigest()
        html = (STATIC / "index.html").read_text()
        for a in assets:
            html = html.replace(f'/static/{a}"', f'/static/{a}?v={v}"')
        return HTMLResponse(html, headers={"Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "frame-src 'self'; object-src 'self'; base-uri 'none'; form-action 'none'")})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ---- helpers -----------------------------------------------------------------------
    def local_path(p: Optional[str]) -> Optional[Path]:
        if not p:
            return None
        path = Path(p)
        if path.exists():
            return path
        # Paths are stored absolute; if the folder moved (the profile, or the repo before profiles),
        # try the same tail under today's outputs/ or data/.
        for anchor, base in (("outputs", config.OUTPUTS), ("data", config.DATA)):
            if anchor in path.parts:
                alt = base.joinpath(*path.parts[path.parts.index(anchor) + 1:])
                if alt.exists():
                    return alt
        return None

    def scores_from_run(run_dir: str) -> dict:
        rd = local_path(run_dir)
        return run_scores(rd) if rd else {}

    sent = SentResumes(store.root / "sent")
    startups = StartupStore(store.root / "startups.json")
    board = JobBoard(store, sync, marks, scores_from_run, sent, startups)

    def cached_file(request: Request, path: Path, media_type: str, filename: Optional[str] = None,
                    retitle: bool = False) -> Response:
        """A file the browser may keep but must revalidate, so an unchanged PDF isn't sent again.
        `filename` is what the PDF viewer's download button saves it as (else the URL's last part). The viewer
        prefers the PDF's own Title, so `retitle` sets that to the same name."""
        st = path.stat()
        tag = f"{st.st_mtime_ns:x}-{st.st_size:x}"
        if retitle and filename:     # a renamed role changes the title inside the PDF
            tag += "-" + hashlib.sha1(filename.encode()).hexdigest()[:8]
        headers = {"ETag": f'"{tag}"', "Cache-Control": "private, no-cache"}
        if request.headers.get("if-none-match") == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        if filename:
            plain = re.sub(r'[^ -~]|["\\]', "_", filename)
            headers["Content-Disposition"] = f'inline; filename="{plain}"; filename*=UTF-8\'\'{quote(filename)}'
            if retitle:
                data = render.retitled(path, filename.removesuffix(".pdf"))
                if data is not None:
                    return Response(data, media_type=media_type, headers=headers)
        return FileResponse(path, media_type=media_type, headers=headers)

    def resume_filename(row: Optional[dict]) -> str:
        """The résumé's download name: "<Role Title> - <Company>.pdf"."""
        row = row or {}
        return config.candidate().resume_title(row.get("title") or "", row.get("company") or "") + ".pdf"

    def workspace(jid: str) -> ResumeWorkspace:
        st = board.state().get(jid) or {}
        run_dir = local_path(st.get("run_dir"))
        if run_dir:
            return ResumeWorkspace(run_dir, local_path(st.get("pdf")) or (Path(st["pdf"]) if st.get("pdf") else None),
                                   **role_of(jid))
        src = outside_resume(jid)[0]
        if not src:
            raise HTTPException(404, "This job hasn't been tailored yet.")
        return ResumeWorkspace(linked.workspace_dir(jid, src, store.root), **role_of(jid))

    def role_of(jid: str) -> dict:
        row = board.row(jid) or {}
        return {"role": row.get("title") or "", "company": row.get("company") or ""}

    def outside_resume(jid: str) -> tuple[Optional[dict], bool, list[dict]]:
        """For a job the app didn't tailor: the résumé made elsewhere that it shows (the one picked
        for it, else the one whose name matches it), whether that was a match rather than a pick,
        and every source, best match first."""
        row = board.row(jid)
        if not row:
            raise HTTPException(404, "Unknown job")
        ranked = linked.rank(row["title"], row["company"], linked.sources(resume_root))
        srcs = [s for _, s in ranked]
        picked = row.get("resume_source") or ""
        if picked == linked.NONE:
            return None, False, srcs
        src = next((s for s in srcs if s["key"] == picked), None)
        if src:
            return src, False, srcs
        match = linked.best_match(row["title"], row["company"], srcs)
        return match, bool(match), srcs

    def default_format() -> formats.Format:
        """The Profile's résumé format (searches.yaml `candidate.resume_format`)."""
        try:
            return formats.get((searches_file.load_candidate(searches_path) or {}).get("resume_format"))
        except (OSError, ValueError):
            return formats.get(None)

    def resume_payload(ws: ResumeWorkspace, jid: str) -> dict:
        h = ws.history()
        fmt, default = ws.format, default_format()
        return {"current": h["current"], "versions": h["versions"], "proposal": bool(h.get("proposal")),
                "title": ws.title(), "pdf_name": resume_filename(board.row(jid)), "editable": ws.editable(), "chat": chat_payload(ws), "chat_busy": chat_busy(jid),
                "format": {"id": fmt.id, "name": fmt.name, "max_pages": fmt.max_pages},
                "default_format": {"id": default.id, "name": default.name, "max_pages": default.max_pages,
                                   "pages_word": default.pages_word}}

    def chat_busy(jid: str) -> bool:
        """Claude is answering a chat message about this job's résumé."""
        return jid in state["chatting"]

    def edit_busy(jid: str) -> bool:
        """A chat reply or a re-rating is in progress."""
        return bool(state["edit_locks"].get(jid) and state["edit_locks"][jid].locked())

    def chat_payload(ws: ResumeWorkspace) -> list[dict]:
        return [{**m, "html": md_html(m["text"])} if m["role"] == "claude" else m for m in ws.chat()]

    # ---- summary & jobs -------------------------------------------------------------
    @app.get("/api/summary")
    def summary():
        record = Path(materials()[0])
        return {"impact_record": {"path": str(record), "updated": record.stat().st_mtime if record.exists() else None},
                "credits": {"used": board.credits_used(cfg.allowance_period), "allowance": cfg.allowance_amount,
                            "period": cfg.allowance_period, "per_run": cfg.max_credits_per_run},
                "backend": cfg.backend, "backend_label": BACKEND_LABELS.get(cfg.backend, cfg.backend),
                "notion": local.notion is not None,
                "statuses": list(STATUSES), "active_runs": [{**r.public(len(r.lines)), "progress": progress.of(r),
                                 "queue_position": runs.queue_position(r)} for r in (*runs.running(), *runs.waiting)],
                "max_runs": runs.max_runs,
                "scoring": scorer.public(), "startups": startups_summary(), "durations": progress.durations()}

    @app.get("/api/jobs")
    def jobs(request: Request, refresh: bool = False, wait: bool = False):
        """The board. Answers from the local tracker; `syncing` says a Notion sync is running, and
        ?wait=1 returns once it has finished. ?refresh=1 syncs with Notion first."""
        if local.notion is not None and refresh:
            try:
                sync.refresh()
            except Exception as e:  # noqa: BLE001 - Notion/network failures go to the UI
                raise HTTPException(502, f"Couldn't sync with Notion. {e}") from None
        elif wait:
            sync.wait()
        body, etag = board.payload()
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(body, media_type="application/json", headers={"ETag": etag})

    @app.post("/api/tracker/{row_id}/status")
    @app.post("/api/notion/{row_id}/status")     # the path from when the tracker lived only in Notion
    def set_status(row_id: str, body: dict):
        """Saved in the local tracker at once; sent to Notion in the background."""
        try:
            local.set_status(row_id, body.get("status", ""))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        except KeyError:
            raise HTTPException(404, "That job isn't in the tracker.") from None
        if body.get("status") in ("Applied", "Interviewing", "Offer", "Denied"):
            # Marked Applied (or a later stage, which means it was sent): keep the résumé it was sent with, unless one is already on file.
            row = board.by_tracker(row_id)
            if row and not sent_info(row)[0]:
                try:
                    record_current(row)
                except HTTPException:
                    pass            # no résumé in the app for it; the job shows "No résumé on file"
        keeper.kick()
        sync.kick()
        return {"ok": True}

    @app.post("/api/jobs/{jid}/track")
    def track(jid: str):
        """Track a found job (Not started; In progress if it has a résumé). Saved locally at once and
        sent to Notion in the background."""
        st, row = board.state().get(jid), board.row(jid)
        if not st or not row or not row["local"]:
            raise HTTPException(404, "Unknown job")
        tri = st.get("triage") or {}
        where = " / ".join(x for x in (row["location"], row["mode"], row["pay"]) if x)
        if row.get("funding"):
            where = f"{where} · {row['funding']['line']}" if where else row["funding"]["line"]
        today = date.today().isoformat()
        if row["tailored"]:
            sc = {**st, **scores_from_run(st["run_dir"])}
            fit = sc.get("impact_score")
            notes = (f"Scored {today} · Résumé {sc.get('resume_score') or '?'}/10 · "
                     f"ATS {round(sc['ats_total']) if sc.get('ats_total') is not None else 'n/a'}% · {where}")
        else:
            # The full score where there is one (it replaces the signal score), else the signal score.
            fit = row["impact_score"] if row["impact_score"] is not None else tri.get("fit_score")
            fit = float(fit) if fit is not None else None
            notes = (f"Scored {today} · Full score {row['impact_score']}/10 · {where}" if row["impact_score"] is not None else
                     f"Scored {today} · Signal score {tri.get('fit_score', '?')}/10 · {tri.get('one_line', '')} · {where}")
        body = [f"**Posting:** {row['url']}"]
        if row.get("funding"):
            body.append(f"**Funding:** {row['funding']['line']}" + (f" {row['funding']['url']}" if row["funding"].get("url") else ""))
        if row["impact_score"] is not None:
            body.append(f"**Full score:** {row['impact_score']}/10.")
        elif tri.get("one_line"):
            body.append(f"**Signal score:** {tri.get('fit_score')}/10. {tri['one_line']}")
        if tri.get("strongest_matches"):
            body.append("**Strongest matches:** " + "; ".join(tri["strongest_matches"]))
        if tri.get("likely_gaps"):
            body.append("**Likely gaps (unconfirmed):** " + "; ".join(tri["likely_gaps"]))
        entry = TrackerEntry(title=st.get("exact_title") or row["title"], company=row["company"] or "Unknown",
                             url=row["url"], fit_score=fit, notes=notes,
                             status="In progress" if row["tailored"] else "Not started", body=body)
        action = local.track(entry, job_id=jid)
        store.update(jid, dismissed=False)
        sync.kick()
        return {"action": action}

    @app.post("/api/jobs/{jid}/dismiss")
    def dismiss(jid: str, body: dict):
        if not store.job(jid):
            raise HTTPException(404, "Unknown job")
        store.update(jid, dismissed=bool(body.get("dismissed", True)))
        return {"ok": True}

    @app.patch("/api/jobs/{jid}")
    def mark_job(jid: str, body: dict):
        """Star a job or set its referral link (kept in data/marks.json; '' clears the link)."""
        row = board.row(jid)
        if not row:
            raise HTTPException(404, "Unknown job")
        fields: dict[str, Any] = {}
        if "starred" in body:
            fields["starred"] = bool(body["starred"])
        if "referral_url" in body:
            try:
                fields["referral_url"] = clean_url(body["referral_url"])
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            fields["referral_name"] = str(body.get("referral_name") or "").strip()[:120] if fields["referral_url"] else ""
        if not fields:
            raise HTTPException(400, "Nothing to change")
        aliases = ["notion-" + row["tracker_id"]] if row["local"] and row["tracker_id"] else []
        rec = marks.set(jid, *aliases, **fields)
        return {"id": jid, "starred": bool(rec.get("starred")), "referral_url": rec.get("referral_url", ""),
                "referral_name": rec.get("referral_name", "")}

    def posting_link(raw: Any) -> str:
        try:
            return normalize_posting_url(clean_url(raw))
        except ValueError:
            raise HTTPException(400, "Enter the posting's web address, e.g. https://jobs.example.com/123") from None

    def job_ref(row: Optional[dict]) -> Optional[dict]:
        return row and {"id": row["id"], "title": row["title"], "company": row["company"], "status": row["status"]}

    @app.post("/api/jobs/lookup")
    async def lookup_job(body: dict):
        """Read a posting from its link: title, company, location and text, to fill in Add a job.
        `existing` is the job already in the app for the same link."""
        url = posting_link(body.get("url"))
        if not url:
            raise HTTPException(400, "Paste the posting's link.")
        found = await asyncio.to_thread(jd.read_posting, url)
        return {**found, "existing": job_ref(board.by_posting(url)),
                "readable": len(found["description"]) >= MIN_PASTED_CHARS}

    @app.post("/api/jobs")
    def add_job(body: dict):
        """Store a job you found yourself, from its link or its pasted posting, so it can be scored now
        and tailored later. A link the app already has gives that job instead of a second copy."""
        jid, existing = store_added(body)
        return {"id": jid, "existing": existing}

    def store_added(body: dict) -> tuple[str, bool]:
        """(job id, already there). The form's fields win; what's missing is read from the link.
        `description_read`: the text is what the app read from the link, not pasted."""
        url = posting_link(body.get("url"))
        if url and not body.get("allow_duplicate"):
            row = board.by_posting(url)
            if row:
                return row["id"], True
        found = {"job_title": "", "company": "", "location": "", "description": ""}
        text, read = (body.get("description") or "").strip(), bool(body.get("description_read"))
        if len(text) < MIN_PASTED_CHARS and url:
            found.update(jd.read_posting(url))
            text, read = found["description"].strip(), True
        if len(text) < MIN_PASTED_CHARS:
            raise HTTPException(400, "Couldn't read the posting from that link. Paste the full job description."
                                if url else "Paste the full job description, or the posting's link.")
        field = lambda k, fk: str(body.get(k) or "").strip() or str(found[fk]).strip()
        title, company = field("title", "job_title") or "Role", field("company", "company") or "Company"
        jid = "pasted-" + slug(company, title, max_len=40)
        # Marked pasted either way: you saw the text in the form, so it's trusted as the whole posting.
        job = {"id": jid, "job_title": title, "company": company, "url": url, "location": field("location", "location"),
               "description": text, "description_pasted": now_iso(), "description_read": read}
        # A location of "Remote" (or "Remote, US") is the posting saying so, even when the page didn't flag it.
        if body.get("remote") in (True, "true") or found.get("remote") or REMOTE_WORD.search(job["location"]):
            job["remote"] = True
        if HYBRID_WORD.search(job["location"]):
            job["hybrid"] = True
        job.update({k: found[k] for k in ("min_annual_salary_usd", "max_annual_salary_usd") if k in found})
        store.save_job(job, "manual")
        return jid, False

    async def prepare(jid: str) -> tuple[dict, dict, str]:
        """(board row, job, jd.md) for scoring. A job that is only in Notion is stored first,
        with the posting text read from its page."""
        row = board.row(jid)
        if not row:
            raise HTTPException(404, "Unknown job")
        job = store.job(jid) or {"id": jid, "job_title": row["title"], "company": row["company"], "url": row["url"]}
        jd_md, usable = await asyncio.to_thread(build_jd, job)
        if not usable:
            raise HTTPException(422, "There's no posting text to score. Its page couldn't be read: the posting may "
                                     "have closed, or the site may need a browser. Use Paste the description… "
                                     "with the full text from the posting.")
        if not row["local"]:
            job = {**job, "description": jd_md.split("## Posting text (verbatim)\n\n", 1)[-1]}
            store.save_job(job, "manual")
            local.link_job(row["tracker_id"], jid)
        return row, job, jd_md

    @app.put("/api/jobs/{jid}/description")
    def paste_description(jid: str, body: dict):
        """Keep the posting text pasted from the role's page. Scoring and tailoring read it in place of
        the stored or fetched text. A role only in the tracker is stored under its own id, so it stays one role."""
        row = board.row(jid)
        if not row:
            raise HTTPException(404, "Unknown job")
        text = (body.get("description") or "").strip()
        if len(text) < MIN_PASTED_CHARS:
            raise HTTPException(400, "Paste the full job description.")
        if not row["local"]:
            store.save_job({"id": jid, "job_title": row["title"], "company": row["company"], "url": row["url"]}, "manual")
            local.link_job(row["tracker_id"], jid)
        store.paste_description(jid, text)
        return {"id": jid}

    async def signal_score(jid: str) -> dict:
        """One Claude call (the triage rubric): how well the impact record fits this job."""
        row, job, jd_md = await prepare(jid)
        try:
            tri = await triage_one(get_runner(), cfg.triage_model, jd_md, jid)
        except Exception as e:  # noqa: BLE001 - surface Claude/CLI failures to the UI
            raise HTTPException(502, "Claude couldn't score this job: "
                                + str(e).removeprefix(f"triage:{jid}: ")) from None
        today = date.today().isoformat()
        store.update(jid, triage=tri.__dict__, triaged_at=today)
        out: dict[str, Any] = {"id": jid, "triage": tri.__dict__, "triaged_at": today, "notion": ""}
        floor = cfg.track_min_fit(jid)
        # Same rule as a pipeline run: a good-enough score puts the job in the tracker. A tailored
        # job's row already carries the fuller scores from its run, so it is left alone.
        if not row["tailored"] and floor is not None and tri.fit_score >= floor:
            from ..pipeline import Candidate, tracker_entry
            out["notion"] = local.track(tracker_entry(Candidate(job, "manual", triage=tri), today), job_id=jid)
            sync.kick()
        return out

    async def full_score(jid: str) -> dict:
        """Stages 1-2 of the tailoring pipeline, without writing a résumé: every requirement rated
        against the impact record and each résumé variant. A later tailoring run reuses it."""
        from ..pipeline import Candidate
        from ..tailor import Tailor, copy_analysis, run_script
        row, job, jd_md = await prepare(jid)
        if row["tailored"]:
            raise HTTPException(400, "Make résumé already scored every requirement for this job. "
                                     "Make its résumé again to refresh that score.")
        meta = Candidate(job, "manual").meta
        out_dir = config.OUTPUTS / date.today().isoformat()
        run_dir = out_dir / "runs" / slug(meta.company, meta.title, jid)   # where a tailoring run will look
        prior = (board.state().get(jid) or {}).get("analysis_dir")
        if prior:
            copy_analysis(Path(prior), run_dir)
        try:
            a = await Tailor(cfg, get_runner(), out_dir, log=lambda msg: None).analyze(meta, jd_md, run_dir)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Claude couldn't finish the full score: {e}") from None
        code, _ = await asyncio.to_thread(run_script, "heatmap.py", "ratings.json", "heatmap-baseline.html", cwd=run_dir)
        full = {"scores": a.scores, "recommended_base": a.base, "gaps": a.gaps, "exact_title": a.exact_title,
                "baseline": {src: {k: r[k] for k in ("skills", "experience", "keywords", "total")}
                             for src, r in a.baseline.items()}}
        today = date.today().isoformat()
        store.update(jid, full_score=full, full_scored_at=today, impact_score=a.impact_score, analysis_dir=str(run_dir),
                     heatmap=str(run_dir / "heatmap-baseline.html") if code == 0 else None)
        return {"id": jid, "full_score": full, "impact_score": a.impact_score}

    scorer = Scorer({"signal": signal_score, "full": full_score})

    @app.post("/api/jobs/{jid}/score")
    async def score_job(jid: str):
        """Signal-score one job and wait for the result."""
        lock = state["edit_locks"].setdefault("score:" + jid, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "This job is already being scored.")
        async with lock:
            return await signal_score(jid)

    @app.post("/api/score")
    async def score_many(body: dict):
        """Queue jobs for a signal or full score. Progress is in /api/summary under `scoring`."""
        kind = body.get("kind", "signal")
        if kind not in scorer.fns:
            raise HTTPException(400, "kind must be signal or full")
        ids = [str(i) for i in body.get("job_ids") or [] if board.row(str(i))][:200]
        if not ids:
            raise HTTPException(400, "Pick at least one job.")
        scorer.submit(ids, kind)
        return scorer.public()

    @app.post("/api/score/cancel")
    async def cancel_scoring():
        scorer.cancel_queued()
        return scorer.public()

    @app.get("/api/jobs/{jid}")
    def job_detail(jid: str):
        st = board.state().get(jid)
        row = board.row(jid)
        job = store.job(jid) if st and row else None
        if not job:
            raise HTTPException(404, "Unknown job")
        detail: dict[str, Any] = {**row, "triage": st.get("triage"), "triaged_at": st.get("triaged_at") or "",
                                  "full_score": st.get("full_score"), "full_scored_at": st.get("full_scored_at") or "",
                                  "description": job.get("description") or "", "description_pasted": job.get("description_pasted") or "",
                                  "description_read": bool(job.get("description_read")),
                                  "posting_kept": job.get("posting_kept") or "",
                                  "has_heatmap": bool(heatmap_path(jid))}
        if row["tailored"]:
            ws = workspace(jid)
            detail["resume"] = resume_payload(ws, jid)
            detail["has_report"] = bool(local_path(st.get("report"))) or (ws.run_dir / "report.md").exists()
        return detail

    def heatmap_path(jid: str) -> Optional[Path]:
        st = board.state().get(jid) or {}
        p = local_path(st.get("heatmap"))
        if p:
            return p
        pdf = local_path(st.get("pdf"))
        if pdf:
            guess = pdf.with_name(pdf.name.replace(config.candidate().pdf_prefix + "-", "heatmap-")).with_suffix(".html")
            if guess.exists():
                return guess
        return None

    @app.get("/api/jobs/{jid}/report")
    def report(jid: str):
        st = board.state().get(jid) or {}
        p = local_path(st.get("report")) or workspace(jid).run_dir / "report.md"
        if not p.exists():
            raise HTTPException(404, "No report")
        mtime = p.stat().st_mtime_ns
        hit = state["reports"].get(str(p))
        if not hit or hit[0] != mtime:
            hit = state["reports"][str(p)] = (mtime, md_html(p.read_text()))
        return {"html": hit[1]}

    @app.get("/api/jobs/{jid}/heatmap")
    def heatmap(jid: str, request: Request):
        p = heatmap_path(jid)
        if not p:
            raise HTTPException(404, "No heat map")
        return cached_file(request, p, "text/html")

    @app.get("/api/jobs/{jid}/outside-resume")
    def get_outside_resume(jid: str):
        """A résumé made outside the app (a resume-job-fit run or a saved résumé) for a job it didn't tailor."""
        src, matched, srcs = outside_resume(jid)
        ws = ResumeWorkspace(linked.workspace_dir(jid, src, store.root), **role_of(jid)) if src else None
        try:
            resume = resume_payload(ws, jid) if ws else None
        except EditError as e:
            raise HTTPException(500, str(e)) from None
        return {"source": src and {"key": src["key"], "label": src["label"], "headline": src["headline"]},
                "matched": matched, "resume": resume,
                "options": [{"key": s["key"], "label": s["label"], "headline": s["headline"]} for s in srcs]}

    @app.post("/api/jobs/{jid}/outside-resume")
    def set_outside_resume(jid: str, body: dict):
        """Pick the outside résumé to show: a source key, "none" for none, or "" to go back to matching by name."""
        row = board.row(jid)
        if not row:
            raise HTTPException(404, "Unknown job")
        key = str(body.get("source") or "")
        if key not in ("", linked.NONE) and key not in {s["key"] for s in linked.sources(resume_root)}:
            raise HTTPException(400, "Unknown résumé")
        aliases = ["notion-" + row["tracker_id"]] if row["local"] and row["tracker_id"] else []
        marks.set(jid, *aliases, resume_source=key)
        return get_outside_resume(jid)

    # ---- the résumé sent with the application ---------------------------------------------
    def tracked(jid: str) -> dict:
        row = board.row(jid)
        if not row:
            raise HTTPException(404, "Unknown job")
        if not row["tracker_id"]:
            raise HTTPException(400, "Track this job first.")
        return row

    def sent_info(row: dict) -> tuple[Optional[dict], list[str]]:
        """(what was sent, or None; the file names in Notion's Resume Used)."""
        rid = row["tracker_id"]
        t = next((r for r in local.rows() if r["page_id"] == rid), None)
        names = (t or {}).get("resume_files") or []
        mine = sent.get(rid)
        if mine and mine["source"] != "notion":
            return mine, names
        if names:   # Notion has it; a copy is downloaded the first time it's opened
            copy = mine if mine and mine.get("note") == names[0] else None
            return {"name": names[0], "source": "notion", "note": names[0],
                    "recorded": copy["recorded"] if copy else "", "path": copy["path"] if copy else None}, names
        return None, names

    def sent_payload(row: dict) -> dict:
        info, names = sent_info(row)
        try:
            ws = workspace(row["id"])
            current = ws.history()["current"]
        except (HTTPException, EditError):
            current = None
        return {"sent": info and {**{k: info[k] for k in ("name", "source", "note", "recorded")},
                                  "download_name": sent_download_name(row, info)},
                "in_notion": bool(names), "notion": local.notion is not None and bool(row.get("notion_page_id")),
                "current_version": current}

    def record_current(row: dict) -> dict:
        """Freeze the app's current résumé for this job as the one sent."""
        ws = workspace(row["id"])
        h = ws.history()
        pdf = ws.pdf_path(str(h["current"]))
        name = (ws.exported_pdf.name if ws.exported_pdf else
                f"{config.candidate().pdf_prefix}-{slug(row['company'] or 'Company', max_len=30)}-"
                f"{slug(row['title'] or 'Role', max_len=60)}.pdf")
        data = render.retitled(pdf, resume_filename(row).removesuffix(".pdf")) or pdf.read_bytes()   # drawn before titles were per role
        rec = sent.record(row["tracker_id"], name, data, "app", f"v{h['current']}")
        local.queue_resume(row["tracker_id"], rec["path"])
        sync.kick()
        return rec

    @app.get("/api/jobs/{jid}/sent")
    def get_sent(jid: str):
        return sent_payload(tracked(jid))

    @app.get("/api/jobs/{jid}/sent.pdf")
    @app.get("/api/jobs/{jid}/sent/file/{name}")
    def sent_pdf(jid: str, request: Request, name: str = ""):
        row = tracked(jid)
        info, names = sent_info(row)
        if not info:
            raise HTTPException(404, "No résumé on file for this job.")
        if not info["path"]:
            page_id = row.get("notion_page_id")
            if local.notion is None or not page_id:
                raise HTTPException(404, "The résumé is in Notion, which isn't connected here.")
            try:
                files = local.notion.resume_files(page_id)
                data = local.notion.download(files[0]["url"]) if files else b""
            except Exception as e:  # noqa: BLE001 - Notion or the download failed
                raise HTTPException(502, f"Couldn't get the file from Notion: {e}") from None
            if not files:
                raise HTTPException(404, "Notion's Resume Used is empty now.")
            try:
                info = sent.record(row["tracker_id"], files[0]["name"], data, "notion", files[0]["name"])
            except ValueError:
                raise HTTPException(415, f"{files[0]['name']} in Notion isn't a PDF, so it can't be shown here. "
                                         "Open it from the Notion row.") from None
        return cached_file(request, info["path"], "application/pdf", sent_download_name(row, info), retitle=True)

    def sent_download_name(row: dict, info: dict) -> str:
        """What the sent copy saves as: the app's own as "<Role Title> - <Company>.pdf", a file you added by its name."""
        return resume_filename(row) if info["source"] == "app" else info["name"]

    @app.put("/api/jobs/{jid}/sent")
    async def upload_sent(jid: str, request: Request):
        """The PDF you sent, as the request body (X-Filename names it). It also goes to Notion's
        Resume Used, unless that already holds a file."""
        row = tracked(jid)
        data = await request.body()
        try:
            rec = sent.record(row["tracker_id"], unquote(request.headers.get("x-filename") or "resume.pdf"), data, "upload")
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        local.queue_resume(row["tracker_id"], rec["path"])
        sync.kick()
        return sent_payload(row)

    @app.post("/api/jobs/{jid}/sent/current")
    def sent_current(jid: str):
        row = tracked(jid)
        record_current(row)
        return sent_payload(row)

    @app.delete("/api/jobs/{jid}/sent")
    def delete_sent(jid: str):
        """Forget the copy kept here. Notion's Resume Used is left as it is."""
        row = tracked(jid)
        sent.remove(row["tracker_id"])
        return sent_payload(row)

    @app.get("/api/jobs/{jid}/resume/{which}.pdf")
    @app.get("/api/jobs/{jid}/resume/{which}/{name}")
    def resume_pdf(jid: str, which: str, request: Request, name: str = ""):
        """A version's PDF. The app links to it by the second path, which ends in the résumé's download
        name, since Arc saved some PDFs under a name it remembered for the old "1.pdf" link."""
        if which != "proposal" and not which.isdigit():
            raise HTTPException(400, "Bad version")
        try:
            return cached_file(request, workspace(jid).pdf_path(which), "application/pdf",
                               resume_filename(board.row(jid)), retitle=True)
        except EditError as e:
            raise HTTPException(404, str(e)) from None

    @app.get("/api/jobs/{jid}/resume/compare")
    def compare_resumes(jid: str, a: int, b: int):
        """Version a beside version b: the wording diff, the requirement ratings that moved, keywords gained and lost."""
        try:
            return workspace(jid).compare(a, b)
        except EditError as e:
            raise HTTPException(404, str(e)) from None

    @app.get("/api/jobs/{jid}/resume/{n}.md")
    def resume_md(jid: str, n: int):
        return {"markdown": workspace(jid).md(n)}

    # ---- edits by hand ---------------------------------------------------------------------
    @app.get("/api/jobs/{jid}/resume/page")
    def resume_page(jid: str):
        """The current version drawn in the PDF's layout, every line click-to-edit, plus its markdown."""
        ws = workspace(jid)
        h = ws.history()
        md, fmt = ws.md(h["current"]), ws.format
        return {"n": h["current"], "html": render.render(md, editable=True, fmt=fmt), "markdown": md,
                "proposal": bool(h.get("proposal")), "margin_in": fmt.margin_in, "max_pages": fmt.max_pages}

    @app.post("/api/jobs/{jid}/resume/page")
    def save_resume_page(jid: str, body: dict):
        """Save an edit made by hand: {"base": n, "edits": [...]} from the page, or {"base": n, "markdown": str}."""
        if edit_busy(jid):
            raise HTTPException(409, "Claude is still answering; wait for the reply, then save.")
        ws = workspace(jid)
        try:
            base = int(body.get("base", 0))
            if isinstance(body.get("markdown"), str):
                h = ws.save_text(base, body["markdown"])
            else:
                h = ws.save_page_edits(base, list(body.get("edits") or []))
        except (EditError, ValueError, TypeError) as e:
            raise HTTPException(400, str(e)) from None
        keep_scores(jid, ws)          # the new keyword share now; POST .../rescore re-rates the rest
        return h

    @app.post("/api/jobs/{jid}/resume/rescore")
    async def rescore_resume(jid: str, body: dict):
        """Re-rate a hand-edited version ({"n": n}); waits for a chat reply in progress first."""
        ws = workspace(jid)
        lock = state["edit_locks"].setdefault(jid, asyncio.Lock())
        async with lock:
            try:
                v = await ws.rescore(int(body.get("n", 0)), get_runner(), cfg)
            except (EditError, ValueError) as e:
                raise HTTPException(400, str(e)) from None
        keep_scores(jid, ws)
        return {"version": v}

    # ---- chat and edits --------------------------------------------------------------------
    # POST /edit sends one chat message; Claude replies, and proposes an edit only when asked for one.
    @app.get("/api/jobs/{jid}/edit")
    def get_edit(jid: str):
        ws = workspace(jid)
        h = ws.history()
        p = h.get("proposal")
        if not p:
            return {"proposal": None}
        from .resumes import diff
        return {"proposal": {**p, "changes_html": md_html(p["changes"]),
                             "diff": diff(ws.md(p["base"]), (ws.vdir / "proposal.md").read_text())}}

    @app.post("/api/jobs/{jid}/edit")
    async def propose(jid: str, body: dict):
        ws = workspace(jid)
        lock = state["edit_locks"].setdefault(jid, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "Claude is still answering your last message.")
        async with lock:
            state["chatting"].add(jid)
            try:
                fid = str(body.get("format") or "")
                if fid and not formats.known(fid):
                    raise EditError(f"Unknown résumé format: {fid}")
                chat_runner, model, via = chat_ai(str(body.get("backend") or ""), str(body.get("model") or ""))
                r = await ws.propose(body.get("instruction", ""), get_runner(), cfg, fmt=fid or None,
                                     chat=(chat_runner, model, via))
                p = r["proposal"]
                return {"reply_html": md_html(r["reply"]), "chat": chat_payload(ws),
                        "proposal": p and {**p, "changes_html": md_html(p["changes"])}}
            except HTTPException:
                raise
            except EditError as e:
                raise HTTPException(400, str(e)) from None
            except Exception as e:  # noqa: BLE001 - surface Claude/CLI failures to the UI
                raise HTTPException(502, f"The AI couldn't answer: {e}") from None
            finally:
                state["chatting"].discard(jid)

    @app.get("/api/jobs/{jid}/chat")
    def get_chat(jid: str):
        return {"chat": chat_payload(workspace(jid)), "busy": chat_busy(jid)}

    @app.post("/api/jobs/{jid}/chat/clear")
    def clear_chat(jid: str):
        if edit_busy(jid):
            raise HTTPException(409, "Claude is still answering; wait for the reply.")
        return {"chat": workspace(jid).clear_chat()}

    def keep_scores(jid: str, ws: ResumeWorkspace) -> None:
        """The current version's ATS total and résumé score become the job's (a tailored job only)."""
        if local_path((board.state().get(jid) or {}).get("run_dir")) and (sc := ws.current_scores()):
            store.update(jid, **sc)

    @app.post("/api/jobs/{jid}/edit/accept")
    def accept(jid: str):
        ws = workspace(jid)
        try:
            h = ws.accept()
        except EditError as e:
            raise HTTPException(400, str(e)) from None
        keep_scores(jid, ws)
        return h

    @app.post("/api/jobs/{jid}/edit/discard")
    def discard(jid: str):
        return workspace(jid).discard()

    @app.post("/api/jobs/{jid}/restore")
    def restore(jid: str, body: dict):
        ws = workspace(jid)
        try:
            h = ws.restore(int(body.get("n", 0)))
        except (EditError, ValueError) as e:
            raise HTTPException(400, str(e)) from None
        keep_scores(jid, ws)
        return h

    # ---- searches --------------------------------------------------------------------------
    @app.get("/api/searches")
    def get_searches():
        _, searches, defaults = searches_file.load(searches_path)
        return {"searches": searches, "defaults": defaults}

    def save_searches(searches: list[dict]):
        try:
            searches_file.save(searches_path, searches)
        except searches_file.SearchError as e:
            raise HTTPException(400, str(e)) from None
        cfg.searches = config.load(searches_path).searches
        return get_searches()

    def clean_search(raw: dict) -> dict:
        try:
            return searches_file.clean(raw)
        except searches_file.SearchError as e:
            raise HTTPException(400, str(e)) from None

    @app.put("/api/searches")
    def put_searches(body: dict):
        return save_searches([clean_search(s) for s in body.get("searches", [])])

    @app.post("/api/searches")
    def add_search(body: dict):
        """Add one search. Without an id, one is made from the name."""
        _, existing, _ = searches_file.load(searches_path)
        ids = {s["id"] for s in existing}
        raw = dict(body)
        if not str(raw.get("id") or "").strip():
            raw["id"] = searches_file.unique_id(str(raw.get("name") or ""), ids)
        new = clean_search(raw)
        if new["id"] in ids:
            raise HTTPException(400, f"A filter with the id {new['id']} already exists.")
        return {**save_searches([*existing, new]), "id": new["id"]}

    @app.put("/api/searches/{sid}")
    def update_search(sid: str, body: dict):
        _, existing, _ = searches_file.load(searches_path)
        if not any(s["id"] == sid for s in existing):
            raise HTTPException(404, "Unknown filter")
        new = clean_search({**body, "id": body.get("id") or sid})
        return {**save_searches([new if s["id"] == sid else s for s in existing]), "id": new["id"]}

    @app.delete("/api/searches/{sid}")
    def delete_search(sid: str):
        _, existing, _ = searches_file.load(searches_path)
        if not any(s["id"] == sid for s in existing):
            raise HTTPException(404, "Unknown filter")
        return save_searches([s for s in existing if s["id"] != sid])

    @app.get("/api/searches/requests")
    def search_requests():
        return {s.id: s.request_body() for s in config.load(searches_path).searches}

    # ---- credits -------------------------------------------------------------------------------
    @app.get("/api/credits")
    def credits_ledger(limit: int = 200):
        """The JobsPipe credit ledger: allowance, use by month, and the latest calls, newest first."""
        c = store.credits()
        used = board.credits_used(cfg.allowance_period)
        names = {s.id: s.name for s in config.load(searches_path).searches}
        names.update({"one-off": "Search once", "adhoc": "Command-line search"})
        calls = [{**call, "name": names.get(call.get("search"), call.get("search"))}
                 for call in reversed(c.get("calls", [])[-max(1, min(limit, 500)):])]
        return {"allowance": cfg.allowance_amount, "period": cfg.allowance_period, "used": used,
                "left": cfg.allowance_amount - used, "per_run": cfg.max_credits_per_run,
                "lifetime": c.get("lifetime", 0),
                "months": [{"month": m, "credits": n} for m, n in sorted(c.get("months", {}).items(), reverse=True)],
                "calls": calls}

    # ---- runs ------------------------------------------------------------------------
    # A run works in a separate process, so what it produced is found by comparing each job's
    # scoring and tailoring fields before it started with the same fields once it has ended.
    # Both are saved with the run, so a finished run's results survive a restart. Runs can overlap,
    # so a changed job counts only if this run was the last to change it (its `last_run`); a job
    # scored in the app while a run was going is the app's change, not the run's.

    def marks_now() -> dict[str, str]:
        keys = ("triage", "triaged_at", "tailored_at", "run_dir", "report", "impact_score", "ats_total")
        return {jid: json.dumps([st.get(k) for k in keys], sort_keys=True, default=str)
                for jid, st in store.state().items()}

    def results(run) -> list[dict]:
        if run.results is not None:
            return run.results
        # An interrupted run's changes can't be told apart from later ones, so it lists none.
        if run.status in ("running", "interrupted", "queued") or run.marks is None or callable(run.marks):
            return []
        before, state = run.marks, store.state()
        out = []
        for jid, mark in marks_now().items():
            if before.get(jid) == mark or state.get(jid, {}).get("last_run") != run.id:
                continue
            row = board.row(jid)
            if row:
                out.append({k: row.get(k) for k in ("id", "title", "company", "fit", "tailored", "impact_score",
                                                   "resume_score", "ats_total", "tracker_id")})
        out.sort(key=lambda r: (not r["tailored"], -(r["impact_score"] or r["fit"] or 0)))
        runs.save_results(run, out)
        return out

    @app.get("/api/runs")
    def list_runs():
        return {"runs": [r.public(max(0, len(r.lines) - 200)) for r in sorted(runs.runs.values(), key=lambda r: -r.id)]}

    # Runs outlive a restart of the app (runs.py), and the ones waiting their turn wait on: start those now.
    runs.resume(marks_now)

    @app.get("/api/runs/going")
    def runs_going():
        """How many runs are going and waiting: the Mac app asks before quitting, which stops them."""
        return runs.going()

    @app.post("/api/runs/stop-all")
    def stop_all_runs():
        """Stop every run going and drop the waiting ones (`jobsearch restart --force`)."""
        return {"stopped": runs.stop_all()}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: int, since: int = 0):
        if run_id not in runs.runs:
            raise HTTPException(404, "Unknown run")
        run = runs.runs[run_id]
        return {**run.public(since), "results": results(run)}

    @app.post("/api/runs/{run_id}/stop")
    def stop_run(run_id: int):
        if run_id not in runs.runs:
            raise HTTPException(404, "Unknown run")
        return runs.stop(run_id).public(0)

    @app.post("/api/runs")
    def start_run(body: dict):
        kind = body.get("kind")
        ids = [s for s in body.get("search_ids", []) if re.fullmatch(r"[a-z0-9-]+", s)]
        flags = [x for s in ids for x in ("--search", s)]
        # Searches spend credits and write the day's digest, so they take turns; so do two runs for one job.
        key, busy = "search", "A search is already going. Start this one when it finishes (or stop it in Runs)."
        if kind == "preflight":
            args, label = ["preflight", *flags], "Check filters (≤1 credit each)"
        elif kind == "run":
            args = ["run", *flags]
            if body.get("no_tailor"):
                args.append("--no-tailor")
            if body.get("top") not in (None, ""):
                args += ["--top", str(int(body["top"]))]
            label = "Search + signal score" if body.get("no_tailor") else (
                "Search + make résumés" + (f" for top {int(body['top'])}" if body.get("top") not in (None, "") else ""))
        elif kind == "once":
            # Search once: a filter from the dialog, run as it is and not saved. It finds and stores jobs
            # (in Find jobs, "Found by one-off") and, unless score is false, signal-scores the new ones.
            try:
                spec = searches_file.clean({**(body.get("search") or {}), "id": "one-off"})
            except searches_file.SearchError as e:
                raise HTTPException(400, str(e)) from None
            spec.pop("id")
            flags = ["--search-json", json.dumps(spec)]
            score = body.get("score", True) is not False
            args = ["run", "--no-tailor", *flags] if score else ["search", *flags]
            what = ", ".join(spec["titles"])                 # what ran: the dialog may have changed a saved filter
            label = f"Search once: {what if len(what) <= 70 else what[:67] + '…'}" + (" + signal score" if score else "")
        elif kind in ("tailor", "tailor-pasted"):
            if kind == "tailor":
                jid = str(body.get("job_id", ""))
                if not store.job(jid):
                    raise HTTPException(400, "Unknown job")
                label = f"Make résumé: {store.job(jid).get('job_title', jid)}"
            else:
                # Stored first, like Add a job, so the run tailors that job and it shows in Find jobs.
                jid, _ = store_added({**body, "allow_duplicate": True})
                job = store.job(jid)
                label = f"Make résumé: {job['job_title']} @ {job['company']}"
            try:
                run = start_resume(jid, label)
            except Busy as e:
                raise HTTPException(409, str(e)) from None
            sync.kick()
            return run.public(0)
        else:
            raise HTTPException(400, "Unknown run kind")
        try:
            run = runs.start(label, args, marks=marks_now(), key=key, busy=busy)
        except Busy as e:
            raise HTTPException(409, str(e)) from None
        return run.public(0)

    def start_resume(jid: str, label: str) -> Run:
        """Make résumé for a stored job. It waits its turn when the most runs are going (other runs are
        refused then), and is Busy while that job's résumé is already being made or waiting."""
        run = runs.start(label, ["tailor", jid], marks=marks_now, key=f"tailor:{jid}",
                         busy="This job's résumé is already being made. Follow it in Runs.", queue=True)
        # The board shows the job In progress now; the run does the same when it starts tailoring.
        from ..pipeline import Candidate, tailoring_entry
        st = board.state().get(jid) or {}
        local.start(tailoring_entry(Candidate(store.job(jid), "manual"), st, date.today().isoformat()), job_id=jid)
        return run

    @app.post("/api/runs/resumes")
    def make_resumes(body: dict):
        """Make résumé for each checked job, in the order given: a queue that runs the most at once and
        starts the next as one finishes. A job with no stored posting, or whose résumé is already being
        made, is skipped and said why."""
        made, skipped = [], []
        for jid in dict.fromkeys(str(x) for x in body.get("job_ids") or []):
            job = store.job(jid)
            if not job:
                skipped.append({"id": jid, "reason": "no posting stored"})
                continue
            try:
                made.append(start_resume(jid, f"Make résumé: {job.get('job_title') or jid}").public(0))
            except Busy:
                skipped.append({"id": jid, "reason": "already being made"})
        if made:
            sync.kick()
        return {"runs": made, "skipped": skipped}

    @app.post("/api/runs/resumes/stop-waiting")
    def stop_waiting_resumes():
        """Drop the Make résumé runs still waiting their turn. The ones going finish."""
        waiting = [r for r in list(runs.waiting) if r.args[:1] == ["tailor"]]
        for r in waiting:
            runs.stop(r.id)
        return {"stopped": len(waiting)}

    # ---- startups: who just raised, and their open roles ----------------------------------------
    def lookup_keys() -> dict[str, str]:
        return {"fundable": os.environ.get("FUNDABLE_API_KEY", ""), "theirstack": os.environ.get("THEIRSTACK_API_KEY", ""),
                "pdl": os.environ.get("PDL_API_KEY", "")}

    def startups_summary() -> dict:
        since = su.since_date(30)
        rows = startups.list()
        return {"count": len(rows), "refreshed": startups.meta().get("refreshed"),
                "recent": sum(1 for s in rows if not s.get("dismissed") and ((s.get("round") or {}).get("date") or "") >= since),
                "hiring": sum(1 for s in rows if not s.get("dismissed") and not s.get("exited")
                              and any(j.get("match") for j in (s.get("roles") or {}).get("jobs") or [])),
                "lookup": any(lookup_keys().values()), "auto_refresh_hours": cfg.startups.auto_refresh_hours}

    def refresh_due() -> bool:
        hours = cfg.startups.auto_refresh_hours
        if not hours:
            return False
        last = startups.meta().get("refreshed") or ""
        return last < (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")

    def auto_refresh() -> Optional[Run]:
        """Start the startup search if the sources are older than startups.auto_refresh_hours (the app calls this
        every few minutes while it runs). Returns the run started, if any."""
        if not refresh_due():
            return None
        try:
            return runs.start("Refresh startup sources", ["startups", "refresh"], marks=marks_now(), key="startups")
        except Busy:
            return None

    app.state.auto_refresh = auto_refresh
    app.state.runs = runs

    def startup_public(s: dict) -> dict:
        """A startup as the tab shows it: the stored record plus which of its roles are already jobs here, and
        whether the company itself is tracked."""
        r = s.get("round") or {}
        roles = s.get("roles") or {}
        jobs = []
        for j in roles.get("jobs") or []:
            had = board.by_posting(j.get("url") or "") if j.get("url") else None
            jobs.append({k: j.get(k) for k in ("title", "url", "location", "remote", "posted", "match", "pay")}
                        | {"job_id": had["id"] if had else None, "status": had["status"] if had else ""})
        company = startups_tracked().get(s.get("key") or "")
        f = funding_block(s)
        return {"id": s["id"], "name": s["name"], "website": s.get("website", ""), "domain": s.get("domain", ""),
                "one_liner": s.get("one_liner", ""), "description": s.get("description", ""), "stage": s.get("stage") or "Unknown",
                "round": {"name": r.get("name", ""), "amount": su.money(r.get("amount_usd"), r.get("currency") or "$", r.get("amount")),
                          "amount_usd": r.get("amount_usd"), "date": r.get("date", ""), "headline": r.get("headline", ""),
                          "url": r.get("url", ""), "source": r.get("source", "")} if r else None,
                "stage_round": "" if r else f["round"], "stage_source": "" if r else f["source"],
                "line": funding_line(f), "hq": s.get("hq", ""), "industries": s.get("industries") or [],
                "tags": s.get("tags") or [], "team_size": s.get("team_size"), "batch": su.batch_short(s.get("batch") or ""),
                "batch_full": s.get("batch", ""), "yc_stage": s.get("yc_stage", ""), "yc_url": s.get("yc_url", ""),
                "jobs_url": s.get("jobs_url", ""), "hiring": bool(s.get("hiring")), "sources": s.get("sources") or [],
                "first_seen": s.get("first_seen", ""), "last_seen": s.get("last_seen", ""), "dismissed": bool(s.get("dismissed")),
                "careers_url": s.get("careers_url") or roles.get("careers_url") or "", "board": roles.get("board"),
                "roles": {"checked": roles.get("checked"), "jobs": jobs} if roles else None,
                "looked_up": s.get("looked_up"), "tracked": company, "exited": s.get("exited") or ""}

    def startups_tracked() -> dict[str, str]:
        """company key -> tracker row id, for companies tracked as a whole ("Open roles — Acme")."""
        return {su.company_key(r["company"]): r["page_id"] for r in sync.tracker.rows()
                if r["company"] and (r["name"].startswith("Open roles — ") or r["name"] == "Open roles")}

    @app.get("/api/startups")
    def list_startups():
        return {"startups": [startup_public(s) for s in startups.list()], "phrases": cfg.title_phrases,
                **startups_summary()}

    def startup_or_404(sid: str) -> dict:
        s = startups.get(sid)
        if not s:
            raise HTTPException(404, "Unknown startup")
        return s

    @app.post("/api/startups")
    def add_startup(body: dict):
        """Add a startup by name and website (e.g. one you heard of), then look up its round if a key is set."""
        name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:80]
        if not name:
            raise HTTPException(400, "Give the startup's name.")
        website = ""
        if body.get("website"):
            try:
                website = clean_url(body["website"])
            except ValueError as e:
                raise HTTPException(400, f"Website: {e}") from None
        startups.merge([{"name": name, "website": website, "sources": [{"kind": "manual", "name": "Added by you", "url": website, "at": date.today().isoformat()}]}])
        s = startups.by_company(name, website)
        if s and website and (s.get("stage") or "Unknown") == "Unknown":
            got, _ = su.enrich(s, lookup_keys())
            if got:
                startups.merge([{**got, "name": s["name"], "website": website}])
                s = startups.get(s["id"])
        return startup_public(s)

    @app.post("/api/startups/refresh")
    def refresh_startups():
        try:
            return runs.start("Refresh startup sources", ["startups", "refresh"], marks=marks_now(), key="startups",
                              busy="The startup search is already going. Follow it in Runs.").public(0)
        except Busy as e:
            raise HTTPException(409, str(e)) from None

    @app.patch("/api/startups/{sid}")
    def mark_startup(sid: str, body: dict):
        startup_or_404(sid)
        if "dismissed" not in body:
            raise HTTPException(400, "Nothing to change")
        return startup_public(startups.update(sid, dismissed=bool(body["dismissed"])))

    @app.post("/api/startups/{sid}/roles")
    async def startup_roles(sid: str):
        """Read the startup's open roles from its careers board (Greenhouse, Lever or Ashby), found from its website."""
        s = startup_or_404(sid)
        roles = await asyncio.to_thread(su.open_roles, s, cfg.title_phrases, None, lambda _: None, cfg.searches,
                                        cfg.startups.roles_exclude_titles)
        startups.update(sid, roles=roles, ats=roles["board"], careers_url=roles["careers_url"] or s.get("careers_url", ""))
        return startup_public(startups.get(sid))

    @app.post("/api/startups/{sid}/roles/add")
    def add_startup_role(sid: str, body: dict):
        """Store one of the startup's roles as a job (it lands in Find jobs with the round chip). A role whose link
        the app already has gives that job."""
        s = startup_or_404(sid)
        url = str(body.get("url") or "")
        role = next((j for j in (s.get("roles") or {}).get("jobs") or [] if j.get("url") == url), None)
        if not role:
            raise HTTPException(404, "That role isn't in the list. Read the roles again.")
        had = board.by_posting(url)
        if had:
            return {"id": had["id"], "existing": True}
        job = su.role_as_job(s, role)
        store.save_job(job, "startups")
        return {"id": job["id"], "existing": False}

    @app.post("/api/startups/{sid}/enrich")
    def enrich_startup(sid: str):
        s = startup_or_404(sid)
        got, said = su.enrich(s, lookup_keys())
        startups.update(sid, looked_up=now_iso())
        if not got:
            raise HTTPException(400, f"Couldn't look up the round: {said}.")
        startups.merge([{**got, "name": s["name"], "website": s.get("website", "")}])
        return startup_public(startups.get(sid))

    @app.post("/api/startups/{sid}/track")
    def track_startup(sid: str):
        """Track the company as a whole, as an "Open roles — Acme" row (Not started), so it sits on the board with its
        round while you watch for the right role. Its link is the careers page, else the website."""
        s = startup_or_404(sid)
        url = s.get("careers_url") or (s.get("roles") or {}).get("careers_url") or s.get("website") or s.get("yc_url") or ""
        line = funding_line(funding_block(s))
        notes = f"Startup · {line}" if line else "Startup"
        if s.get("one_liner"):
            notes += f" · {s['one_liner'][:120]}"
        body = [f"**Website:** {s['website']}" if s.get("website") else "", f"**Funding:** {line}" if line else "",
                f"**Round news:** {s['round']['url']}" if (s.get("round") or {}).get("url") else "",
                f"**Careers:** {url}" if url else ""]
        entry = TrackerEntry(title="Open roles", company=s["name"], url=url, fit_score=None, notes=notes,
                             status="Not started", body=[b for b in body if b])
        action = local.track(entry)
        sync.kick()
        return {"action": action, "tracker_id": startups_tracked().get(s.get("key") or "")}

    # ---- profile: who the résumés are for, and their résumés ----------------------------------
    def references() -> Path:
        return config.REFERENCES

    def not_mid_run():
        if runs.active():
            raise HTTPException(409, "A run is using your profile. Save after it finishes (or stop it in Runs).")

    def doc_facts(path: Path) -> dict:
        text = path.read_text() if path.exists() else ""
        return {"words": len(re.findall(r"\w+", text)), "headline": linked._headline(path) if text else "",
                "updated": date.fromtimestamp(path.stat().st_mtime).isoformat() if path.exists() else None}

    @app.get("/api/profile")
    def get_profile():
        raw = searches_file.load_candidate(searches_path)
        if raw is None:                       # no section yet: an empty form
            a = config.PLACEHOLDER
            raw = {**{k: getattr(a, k) for k in (*searches_file.CANDIDATE_TEXT, "evidence")}, "name": "",
                   "pdf_prefix": "", "resumes": []}
        files = sorted(f.name for f in references().glob("resume-*.md") if searches_file.is_resume_file(f.name))
        return {"saved": searches_file.load_candidate(searches_path) is not None, "candidate": raw,
                "example": searches_file.is_example(searches_path), "folder": str(config.PROFILE),
                "default_evidence": config.Candidate(name="x").evidence,
                "files": [{"file": f, **doc_facts(references() / f)} for f in files],
                "impact_record": doc_facts(impact_path)}

    @app.put("/api/profile")
    def put_profile(body: dict):
        not_mid_run()
        try:
            searches_file.save_candidate(searches_path, searches_file.clean_candidate(body, references()))
        except searches_file.SearchError as e:
            raise HTTPException(400, str(e)) from None
        state["runner"] = None   # the cached system prompt names the candidate and holds the résumés
        return get_profile()

    # ---- the AI: which provider and models do the work, and its key ---------------------------
    def key_env(backend: str) -> str:
        if backend == "api":
            return "ANTHROPIC_API_KEY"
        return cfg.api_key_env or PROVIDERS[backend].key_env if backend in PROVIDERS else ""

    @app.get("/api/ai")
    def get_ai():
        providers = [{"id": b, "label": label, "key_env": key_env(b), "key_set": bool(key_env(b) and os.environ.get(key_env(b))),
                      "local": b in PROVIDERS and PROVIDERS[b].local, "signup": PROVIDERS[b].signup if b in PROVIDERS else "",
                      "base_url": PROVIDERS[b].base_url if b in PROVIDERS else ""}
                     for b, label in BACKEND_LABELS.items()]
        return {**searches_file.load_models(searches_path), "providers": providers, "claude_models": CLAUDE_MODELS,
                "env_file": str(config.PROFILE / ".env")}

    # Saved even while runs are going: a run reads the AI settings and keys once, when it starts, so the runs
    # already going finish on the AI they began with and the next one uses these.
    @app.put("/api/ai")
    def put_ai(body: dict):
        try:
            backend, base_url, stages = searches_file.clean_models(body, BACKEND_LABELS)
            key, env = str(body.get("key") or "").strip(), key_env(backend)
            if key and env:
                searches_file.set_env(config.PROFILE / ".env", env, key)
                os.environ[env] = key                # this server and the runs it starts use it at once
            searches_file.save_models(searches_path, backend, base_url, stages)
        except searches_file.SearchError as e:
            raise HTTPException(400, str(e)) from None
        cfg.backend, cfg.base_url = backend, base_url or None
        cfg.triage_model, cfg.analysis_model, cfg.writer_model, cfg.rescore_model = (
            ModelCfg(**stages[s]) for s in searches_file.MODEL_STAGES)
        state["runner"] = None
        return get_ai()

    # Keys for the services besides the AI, kept in the profile's .env (values are never sent back).
    KEYS = {"JOBSPIPE_API_KEY": ("JobsPipe", "Searching for jobs (Filters, Find jobs)", "https://jobspipe.dev", False),
            "NOTION_TOKEN": ("Notion", "Copying your tracker to Notion (also set it up in searches.yaml)",
                             "https://www.notion.so/profile/integrations", True),
            "FUNDABLE_API_KEY": ("Fundable", "Looking up a startup's latest round", "https://www.tryfundable.ai", False),
            "THEIRSTACK_API_KEY": ("TheirStack", "Looking up a startup's latest round", "https://theirstack.com", False),
            "PDL_API_KEY": ("People Data Labs", "Looking up a startup's latest round", "https://www.peopledatalabs.com", False)}

    @app.get("/api/keys")
    def get_keys():
        return {"keys": [{"name": k, "label": l, "for": f, "url": u, "restart": r, "set": bool(os.environ.get(k))}
                         for k, (l, f, u, r) in KEYS.items()], "env_file": str(config.PROFILE / ".env")}

    @app.put("/api/keys")
    def put_key(body: dict):
        name, value = str(body.get("name") or ""), str(body.get("value") or "").strip()
        if name not in KEYS or not value:
            raise HTTPException(400, "Paste the key first.")
        try:
            searches_file.set_env(config.PROFILE / ".env", name, value)
        except searches_file.SearchError as e:
            raise HTTPException(400, str(e)) from None
        os.environ[name] = value                    # runs started from now on get it
        return get_keys()

    @app.get("/api/ai/claude")
    def claude_cli():
        """Whether a working Claude Code CLI is here (the Claude desktop app bundles one)."""
        from ..llm import find_claude
        try:
            return {"found": cfg.claude_bin or os.environ.get("CLAUDE_BIN") or find_claude()}
        except RuntimeError as e:
            return {"found": None, "error": str(e)}

    @app.post("/api/ai/claude-login")
    def claude_login():
        """Open Terminal on Claude Code, ready for /login: the one step of signing in a browser page can't do."""
        import shlex
        import subprocess
        import sys
        found = claude_cli()["found"]
        if not found:
            raise HTTPException(400, "Claude Code isn't installed. Install the Claude app (claude.ai/download), then try again.")
        if sys.platform != "darwin":
            raise HTTPException(400, f"Run {found} in a terminal and type /login.")
        script = config.DATA / "claude-sign-in.command"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/bash\nclear\necho 'Signing in to Claude for Job Search.'\n"
                          "echo 'Type /login and press Return, sign in in your browser, then type /exit and close this window.'\n"
                          f"echo\nexec {shlex.quote(found)}\n")
        script.chmod(0o700)
        subprocess.run(["open", "-a", "Terminal", str(script)], check=False)
        return {"opened": True}

    @app.get("/api/ai/chat-models")
    async def chat_models():
        """Every AI set up on the Profile tab, with its models, for the résumé chat's model menu. The Profile's
        writing model is the default. A provider's own list is asked for at most every 10 minutes; if it can't be
        reached, the models picked for it on the Profile tab are offered."""
        saved = searches_file.load_models(searches_path)["saved"]
        usable = ["claude-code"] + (["api"] if os.environ.get("ANTHROPIC_API_KEY") else [])
        for b, p in PROVIDERS.items():
            if b == cfg.backend or b in saved and (p.local or b == "openai-compatible") \
                    or p.key_env and os.environ.get(cfg.api_key_env if b == cfg.backend and cfg.api_key_env else p.key_env):
                usable.append(b)
        if cfg.backend not in usable:
            usable.append(cfg.backend)

        async def listed(b: str) -> list[str]:
            if b in ("claude-code", "api"):
                return CLAUDE_MODELS
            hit = state["model_lists"].get(b)
            if hit and time.time() - hit[0] < 600:
                return hit[1]
            url = cfg.base_url if b == cfg.backend else (saved.get(b) or {}).get("base_url") or None
            try:
                found = await OpenAICompatRunner(provider=b, base_url=url, timeout_s=10, retry_wait=0.5).models()
            except (RuntimeError, AgentError, httpx.HTTPError):
                found = []
            state["model_lists"][b] = (time.time(), found)
            return found

        lists = await asyncio.gather(*(listed(b) for b in usable))
        groups = []
        for b, found in zip(usable, lists):
            picks = [st["model"] for st in (saved.get(b) or {}).get("stages", {}).values()]
            if b == cfg.backend:
                picks.insert(0, cfg.writer_model.model)
            models = list(dict.fromkeys(m for m in [*picks, *found] if m))
            if models:
                groups.append({"backend": b, "label": BACKEND_LABELS[b], "short": short_label(b), "models": models})
        return {"default": {"backend": cfg.backend, "model": cfg.writer_model.model}, "groups": groups}

    @app.post("/api/ai/models")
    async def ai_models(body: dict):
        """The models a provider offers, which also checks its address and key (a key typed in the form is used
        for this check only, not saved)."""
        backend = str(body.get("backend") or "")
        if backend in ("claude-code", "api"):
            return {"models": CLAUDE_MODELS}
        if backend not in PROVIDERS:
            raise HTTPException(400, "Choose which AI to use.")
        try:
            runner = OpenAICompatRunner(provider=backend, base_url=str(body.get("base_url") or "") or None,
                                        key_env=cfg.api_key_env, api_key=str(body.get("key") or "").strip() or None,
                                        timeout_s=20, retry_wait=0.5)
            return {"models": await runner.models()}
        except (RuntimeError, AgentError, httpx.HTTPError) as e:
            raise HTTPException(400, str(e).removeprefix("models: ")) from None

    @app.post("/api/profile/resumes")
    def add_resume(body: dict):
        """Save a résumé (markdown) in the profile's references/ as resume-<name>.md. With `import_id`, from the
        upload review: the uploaded original is kept beside it in references/originals/."""
        text, fname = str(body.get("markdown") or ""), Path(str(body.get("filename") or ""))
        if fname.suffix.lower() not in (".md", ".markdown") or not text.strip():
            raise HTTPException(400, "Choose a markdown (.md) résumé file.")
        stem = re.sub(r"^resume-", "", slug(fname.stem, max_len=50))
        if len(text) > 300_000:
            raise HTTPException(400, "That file is too large for a résumé.")
        path = references() / f"resume-{stem}.md"
        if path.exists() and not body.get("replace"):
            raise HTTPException(409, f"{path.name} already exists.")
        imp = import_dir(str(body["import_id"])) if body.get("import_id") else None
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        if imp:
            original = next(imp.glob("original.*"), None)
            if original:
                keep = references() / "originals" / f"{path.stem}{original.suffix}"
                keep.parent.mkdir(exist_ok=True)
                shutil.copy(original, keep)
            shutil.rmtree(imp, ignore_errors=True)
        state["runner"] = None
        return {**get_profile(), "file": path.name}

    # ---- uploading a résumé: any file in, the app's markdown out, checked word by word (resume_import.py) ----
    # An upload waits in data/imports/<id>/ (the original, its text, the sort) until it's saved or a day passes.
    IMPORT_KEEP_S = 24 * 3600

    def import_dir(iid: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{16}", iid):
            raise HTTPException(404, "That upload has expired. Add the file again.")
        d = store.root / "imports" / iid
        if not (d / "meta.json").exists():
            raise HTTPException(404, "That upload has expired. Add the file again.")
        return d

    def import_payload(d: Path, md: str) -> dict:
        meta = json.loads((d / "meta.json").read_text())
        check = resume_import.verify((d / "source.txt").read_text(), md, tuple(meta["notes"]),
                                     tuple(meta.get("ai_notes") or ()), meta.get("heuristic"))
        try:
            html = render.render(md, fmt=default_format()).replace("</style>", render.SHEET_CSS + "</style>")
        except Exception:  # noqa: BLE001 - not drawable yet; the flags say why
            html = None
        stem = re.sub(r"^resume[-_ ]*|[-_ ]*resume$", "", Path(meta["filename"]).stem, flags=re.I)
        return {"id": d.name, "filename": meta["filename"], "kind": meta["kind"], "markdown": md, "html": html,
                "source": (d / "source.txt").read_text(), "has_original": (d / "original.pdf").exists(),
                "name": slug(stem, max_len=40) or "uploaded", **check}

    @app.post("/api/profile/resumes/import")
    async def import_resume(request: Request):
        """Read an uploaded résumé (raw bytes, its name in X-Filename) and sort it into the markdown shape.
        Saves nothing: the review screen saves it with POST /api/profile/resumes and the import's id."""
        fname = Path(unquote(request.headers.get("x-filename") or "")).name
        data = await request.body()
        try:
            src = resume_import.read(fname, data)
        except resume_import.UploadError as e:
            raise HTTPException(400, str(e)) from None
        root = store.root / "imports"
        for old in root.glob("*") if root.exists() else []:
            if time.time() - old.stat().st_mtime > IMPORT_KEEP_S:
                shutil.rmtree(old, ignore_errors=True)
        d = root / secrets.token_hex(8)
        d.mkdir(parents=True)
        (d / f"original{src.ext}").write_bytes(data)
        (d / "source.txt").write_text(src.text, encoding="utf-8")
        meta = {"filename": fname, "kind": src.kind, "notes": src.notes, "ai_notes": [], "heuristic": None}
        if resume_import.looks_shaped(src.text):
            md = src.text                       # already the app's markdown: nothing to sort
        else:
            try:
                md, meta["ai_notes"] = await resume_import.shape(src, get_runner(), cfg.analysis_model)
            except Exception as e:  # noqa: BLE001 - no AI, or it failed: a rougher sort to check by hand
                md, meta["heuristic"] = resume_import.heuristic(src.text), str(e).splitlines()[0][:160] or "no reply"
        (d / "meta.json").write_text(json.dumps(meta))
        (d / "sorted.md").write_text(md, encoding="utf-8")
        return import_payload(d, md)

    @app.post("/api/profile/resumes/import/{iid}/check")
    def check_import(iid: str, body: dict):
        """The review screen's markdown, edited: drawn and checked again against the file (no AI)."""
        md = str(body.get("markdown") or "")
        if len(md) > 300_000:
            raise HTTPException(400, "That's too long for a résumé.")
        return import_payload(import_dir(iid), md)

    @app.get("/api/profile/resumes/import/{iid}/original.pdf")
    def import_original(iid: str, request: Request):
        path = import_dir(iid) / "original.pdf"
        if not path.exists():
            raise HTTPException(404, "Only a PDF upload is shown as it was.")
        return cached_file(request, path, "application/pdf")

    # ---- résumé formats: how the PDFs look (jobpipe/formats.py) --------------------------------------
    # One default, in Profile. Changing it can redraw every résumé already made; one that would go over the
    # new format's page limit keeps its format, and its Résumé tab offers a trim made for the new one.
    def tailored_workspaces() -> list[tuple[str, ResumeWorkspace]]:
        out = []
        for jid, st in board.state().items():
            run_dir = local_path((st or {}).get("run_dir"))
            if run_dir and (run_dir / "resume-final.md").exists():
                out.append((jid, workspace(jid)))
        return out

    def job_line(jid: str) -> dict:
        row = board.row(jid) or {}
        return {"id": jid, "title": row.get("title") or "", "company": row.get("company") or ""}

    def preview_md() -> str:
        """The résumé the format previews show: the profile's fallback résumé, else the example's."""
        for f in config.candidate().resumes.values():
            path = references() / f
            if path.exists():
                text = path.read_text()
                try:
                    render.render(text)
                    return text
                except Exception:  # noqa: BLE001 - not in the markdown shape: use the example
                    break
        return (config.EXAMPLE_PROFILE / "references" / "resume-platform.md").read_text()

    preview_lock = threading.Lock()

    def preview_file(fid: str, kind: str) -> Path:
        """A format's preview of the fallback résumé, made on first ask and kept until the résumé or the format
        changes. The Profile asks for all five thumbnails at once, so the first ask draws them all in one browser."""
        if not formats.known(fid):
            raise HTTPException(404, "Unknown résumé format")
        md = preview_md()
        folder = store.root / "format-previews"

        def path_for(f: str) -> Path:
            key = hashlib.blake2b((md + f + render.CSS + formats.get(f).css).encode(), digest_size=6).hexdigest()
            return folder / f"{f}-{key}.{kind}"
        if not path_for(fid).exists():
            with preview_lock:
                todo = [f for f in (formats.FORMATS if kind == "png" else [fid]) if not path_for(f).exists()]
                if todo:
                    folder.mkdir(parents=True, exist_ok=True)
                    with render.Printer() as pr:
                        for f in todo:
                            for old in folder.glob(f"{f}-*.{kind}"):
                                old.unlink(missing_ok=True)
                            tmp = folder / f"{f}.tmp.{kind}"
                            if kind == "pdf":
                                pr.pdf(md, tmp, fmt=f)
                            else:
                                pr.png(render.render(md, fmt=f), tmp)
                            tmp.replace(path_for(f))
        return path_for(fid)

    @app.get("/api/formats")
    def get_formats():
        default = default_format()
        behind = []
        for jid, ws in tailored_workspaces():
            fid = ws.format_id()
            if fid != default.id:
                behind.append({**job_line(jid), "format": formats.get(fid).name})
        return {"default": default.id, "behind": behind,
                "formats": [{"id": f.id, "name": f.name, "font": f.font, "accent": f.accent, "blurb": f.blurb,
                             "max_pages": f.max_pages, "pages_word": f.pages_word} for f in formats.FORMATS.values()]}

    @app.get("/api/formats/{fid}/preview.pdf")
    def format_preview(fid: str, request: Request):
        return cached_file(request, preview_file(fid, "pdf"), "application/pdf")

    @app.get("/api/formats/{fid}/thumb.png")
    def format_thumb(fid: str, request: Request):
        return cached_file(request, preview_file(fid, "png"), "image/png")

    @app.put("/api/profile/format")
    async def put_format(body: dict):
        """Make a format the default: {"format": id, "redraw": bool}. With redraw, every résumé already made is
        drawn in it, except one that would go over its page limit or is being edited right now."""
        fid = str(body.get("format") or "")
        if not formats.known(fid):
            raise HTTPException(400, "Choose one of the résumé formats.")
        redraw = bool(body.get("redraw"))
        if redraw:
            not_mid_run()
        try:
            searches_file.save_resume_format(searches_path, fid)
        except searches_file.SearchError as e:
            raise HTTPException(400, str(e)) from None
        fmt, redrawn, over = formats.get(fid), 0, []
        if redraw:
            def draw_all():
                nonlocal redrawn
                with render.Printer() as pr:
                    for jid, ws in tailored_workspaces():
                        if edit_busy(jid):
                            continue
                        try:
                            r = ws.redraw(fmt, pr)
                        except (EditError, OSError, ValueError):
                            continue
                        if r["applied"]:
                            redrawn += r["pages"] is not None
                        else:
                            over.append({**job_line(jid), "pages": r["pages"]})
            await asyncio.to_thread(draw_all)
        return {**get_formats(), "redrawn": redrawn, "over": over}

    @app.post("/api/jobs/{jid}/resume/format")
    def redraw_resume(jid: str):
        """Draw this résumé in the default format. When it would go over the format's page limit it stays as it
        is, and the reply carries the chat message that trims it to fit."""
        if edit_busy(jid):
            raise HTTPException(409, "Claude is still answering; wait for the reply.")
        ws = workspace(jid)
        fmt = default_format()
        try:
            r = ws.redraw(fmt)
        except EditError as e:
            raise HTTPException(400, str(e)) from None
        return {**r, "trim": None if r["applied"] else ws.trim_instruction(fmt, r["pages"]),
                "trim_format": fmt.id, "resume": resume_payload(ws, jid)}

    # ---- markdown documents: the impact record and interview prep ---------------------------------
    # Edited in the app's Impact record and Interview prep tabs, which save as you type. Each save checks
    # the text it started from is still what's on disk, so an edit made elsewhere (another tab, an editor,
    # Claude) isn't overwritten unseen. The text a save replaces is kept in data/<name>-history, at most
    # one copy per IMPACT_SNAPSHOT_EVERY seconds, the newest IMPACT_SNAPSHOTS of them.
    def doc_version(text: str) -> str:
        return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()

    def markdown_doc(route: str, path: Path, label: str, *, allow_empty: bool = False, starter: str = "") -> None:
        history_dir = store.root / f"{route}-history"

        def read() -> str:
            if not path.exists() and starter:   # written on first open, so Claude can find and add to it
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(starter)
            return path.read_text() if path.exists() else ""

        def payload(text: Optional[str] = None) -> dict:
            text = read() if text is None else text
            return {"markdown": text, "version": doc_version(text), "path": str(path),
                    "updated": path.stat().st_mtime if path.exists() else None}

        def snapshot(text: str) -> None:
            history_dir.mkdir(parents=True, exist_ok=True)
            kept = sorted(history_dir.glob("*.md"))
            if kept and time.time() - kept[-1].stat().st_mtime < IMPACT_SNAPSHOT_EVERY:
                return
            (history_dir / f"{datetime.now():%Y%m%d-%H%M%S}.md").write_text(text)
            for old in sorted(history_dir.glob("*.md"))[:-IMPACT_SNAPSHOTS]:
                old.unlink()

        def get_doc():
            return payload()

        def save_doc(body: dict):
            """Save the whole document. `version` is the one the edit started from; a different one on disk
            is a 409 that carries it, unless `force` is set."""
            text = body.get("markdown")
            if not isinstance(text, str) or (not allow_empty and not text.strip()):
                raise HTTPException(400, f"The {label} can't be empty.")
            current = read()
            if doc_version(current) != body.get("version") and not body.get("force"):
                return JSONResponse({"detail": f"The {label} was changed outside this window since you opened it.",
                                     "current": payload(current)}, status_code=409)
            write(text, current)
            return payload(text)

        def write(text: str, current: str) -> None:
            if text == current:
                return
            if current:
                snapshot(current)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_text(text)
            os.replace(tmp, path)

        def preview_doc(body: dict):
            return {"html": md_html(str(body.get("markdown") or ""))}

        app.get(f"/api/{route}")(get_doc)
        app.put(f"/api/{route}")(save_doc)
        app.post(f"/api/{route}/preview")(preview_doc)
        return read, write, payload

    read_impact, write_impact, impact_payload = markdown_doc("impact-record", impact_path, "impact record")
    markdown_doc("interview-prep", prep_path, "interview prep", allow_empty=True, starter=INTERVIEW_PREP_STARTER)

    # ---- confirmed facts: answers to follow-up questions, kept in the impact record ---------------------
    @app.post("/api/impact-record/facts")
    def add_fact(body: dict):
        """Add a dated user-confirmed fact to the record's Confirmed facts section. `version` is the record
        the app has open; a different one on disk is a 409, as for a save."""
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "Write the fact to record.")
        current = read_impact()
        if doc_version(current) != body.get("version"):
            return JSONResponse({"detail": "The impact record was changed outside this window since you opened it.",
                                 "current": impact_payload(current)}, status_code=409)
        new = add_confirmed_fact(current, text, date.today(), config.candidate().first_name)
        write_impact(new, current)
        state["runner"] = None  # the cached system prompt includes the record
        return impact_payload(new)

    return app


def serve(cfg: Config, port: int = 8765, open_browser: bool = True) -> None:
    import webbrowser

    from ..pipeline import make_tracker

    import threading

    token = load_token(config.DATA)
    tracker = make_tracker(cfg, print)
    app = create_app(cfg, token=token, tracker=tracker)
    url = f"http://127.0.0.1:{port}/?token={token}"
    print(f"\n  jobpipe is running at {url}\n  (bookmark it; the token stays the same until you delete {config.DATA / 'web-token'})\n")
    if open_browser:
        webbrowser.open(url)
    runs = app.state.runs
    if "JOBSEARCH_APP_VERSION" in os.environ:
        # Started by the Mac app (mac/Launcher.swift): stop when it's gone, even if it was force-quit. That's
        # quitting, so the runs stop too (the Mac app asks first when it can).
        parent = os.getppid()

        def follow_app() -> None:
            while os.getppid() == parent:
                threading.Event().wait(2)
            runs.stop_all()
            os._exit(0)
        threading.Thread(target=follow_app, daemon=True).start()
    if cfg.startups.auto_refresh_hours:
        # The startup search on its own: a minute after start, then every few minutes, whenever the sources are older
        # than startups.auto_refresh_hours. Open roles matching your filters land in Find jobs each time.
        def keep_fresh() -> None:
            wait = 60.0
            while True:
                threading.Event().wait(wait)
                wait = 600.0
                run = app.state.auto_refresh()
                if run:
                    print(f"  Refresh startup sources started on its own (run {run.id}); it runs every "
                          f"{cfg.startups.auto_refresh_hours:g} hours while the app is open.")
        threading.Thread(target=keep_fresh, daemon=True).start()
    run_server(app, port)


def run_server(app: FastAPI, port: int) -> None:
    """Serve until stopped. Quitting stops the runs; a restart leaves them going (app_server says which is which)."""
    import uvicorn

    runs = app.state.runs
    server = app_server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"), runs)
    signal.signal(signal.SIGHUP, server.hang_up)
    try:
        server.run()
    except KeyboardInterrupt:        # uvicorn passes Ctrl+C on once it has shut down; as uvicorn.run does, end quietly
        pass
    finally:
        if server.quitting:
            n = runs.stop_all()
            if n:
                print(f"  Stopped {n} run{'s' if n != 1 else ''}.", flush=True)


def at_terminal() -> bool:
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (ValueError, OSError):    # stdin closed
        return False


def app_server(uvicorn_config, runs: RunManager):
    """uvicorn's server, which tells apart how it was stopped. Runs outlive the app (runs.py), so:
    - SIGTERM (`jobsearch restart`, `kill`) restarts: the runs going carry on, and the waiting ones wait on.
    - SIGINT (Ctrl+C, or quitting the Mac app) and SIGHUP (its terminal window closed) quit: `quitting` is set
      and serve() stops the runs. At a terminal, Ctrl+C with runs going says so first; a second Ctrl+C quits."""
    import uvicorn

    class Server(uvicorn.Server):
        quitting = False
        warned = 0.0

        def handle_exit(self, sig, frame):
            going = runs.going()
            if sig == signal.SIGTERM and not self.quitting and (going["running"] or going["waiting"]):
                print(f"\n  Stopping the app. {going['text'][0].upper() + going['text'][1:]}: they carry on, "
                      "and the app picks them up when it starts again.", flush=True)
            if sig == signal.SIGINT and not self.quitting and not self.should_exit:
                if (going["running"] or going["waiting"]) and at_terminal() and time.monotonic() - self.warned > 10:
                    self.warned = time.monotonic()
                    them = "it" if going["running"] + going["waiting"] == 1 else "them"
                    print(f"\n  {going['text'][0].upper() + going['text'][1:]}. Quitting stops {them}.\n"
                          f"  Press Ctrl+C again to quit and stop {them}, or leave it and the app keeps running.\n"
                          "  (To restart the app without stopping them: jobsearch restart)\n", flush=True)
                    return
                self.quitting = True
            super().handle_exit(sig, frame)

        def hang_up(self, sig, frame):
            self.quitting = True
            super().handle_exit(signal.SIGINT, frame)

    return Server(uvicorn_config)

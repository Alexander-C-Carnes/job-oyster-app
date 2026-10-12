"""Résumé versions and the chat with Claude about one tailored job.

Layout inside the job's run folder:
  versions/v1.md, v1.pdf      the pipeline's resume-final.md and its PDF
  versions/v2.md, v2.pdf ...  accepted edits
  versions/proposal.md/.pdf   a pending edit, until accepted or discarded
  versions/history.json       {"current": n, "format": id, "versions": [...], "proposal": {...} | null}
  versions/chat.json          the conversation: [{"role": "user"|"claude"|"note", "text", "at", "proposal"?}]

Each message to Claude carries the conversation so far. Claude always replies, and writes a revised
résumé only when the candidate asks for a change, so questions and discussion cost no edit.
Claude edits the markdown, never the PDF. Every proposal is re-rendered, run through
check_resume.py and scored before the candidate sees it, and nothing replaces the current version
until they accept it.

The candidate can also edit by hand: on the page (the résumé drawn in the PDF's layout, each line
click-to-edit; apply_page_edits maps the changed text back onto its markdown lines) or as the
markdown itself. A hand edit is saved straight away as a new version, re-rendered and checked.

Format: the résumé format its PDFs are drawn in (jobpipe/formats.py). A tailoring run records the candidate's
default in resume-format.txt; runs from before formats were Signature. Changing the default redraws a résumé
only when it still fits the new format's page limit (redraw); one that doesn't keeps its format until a trim
proposal made for the new format is accepted.

Scores: each version carries its ATS total (skills / experience / keywords, the tailoring run's math) and
résumé score. An edit, Claude's or a hand edit, is re-rated against the requirement rows in
ratings-final.json, alongside the render, so the scores move with every edit; accepting or restoring a
version makes its scores the job's.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import config, formats, render
from ..config import Config, ModelCfg
from ..llm import AgentCall, Runner, brief
from ..tailor import VALUE, exact_title, normalize_keywords, run_script, scorecard

EDIT_BRIEF = """# Resume chat

You're chatting with {name} about his tailored resume for one job, like a sharp recruiter who knows his record. The current resume, the job posting, and the match brief (evidence ledger, Do-not-claim list, posting keywords) are below; his impact record, user-confirmed notes and the resume rules are in the system prompt. The conversation so far and his new message come last.

Every turn, write `reply.md`: your chat message to him. Talk the way you would in a chat: direct, plain prose, short unless he asks for depth, no headings. Answer questions, give your honest opinion when he asks for it (how a line reads, what a recruiter would question, whether the resume fits the role), suggest changes, and ask a clarifying question when a request is ambiguous enough that guessing would waste an edit.

Scores: scores.md has the app's scores for this resume, exactly as he sees them in the job's header, the list and the Full score card: the resume score, the ATS total and its parts, the impact record score, and each requirement's rating. When he asks about a score, give those numbers. Never give a score of your own, not even an estimate: a second number contradicts what's on his screen. Explain a score from the requirement ratings (which rows are partial or missing, and what evidence would move them). If you think a rating is wrong, say which row and why, but the score is still the app's. When you propose an edit, don't predict its score: the app re-rates every proposal and shows its scores next to it. Scores mentioned earlier in the conversation may be out of date; scores.md is current.

Only when he asks for a change, or says yes to one you suggested, also write `resume-edited.md`: the complete revised resume. Otherwise don't write it. If a proposed edit is still pending, the resume he's looking at is that proposal; when he's refining it ("shorter", "undo the second bullet"), build on it. In `reply.md` say briefly what you changed, the evidence or ledger IDs behind it where there are any, and any risk worth knowing.

It's his resume, so his instruction is final: make the change he asks for, exactly as he words it, even where it goes past the evidence, the Do-not-claim list, or the resume rules (a title or level in the headline, a broader scope, a number). Never decline, water it down, or swap in a "closest supported" version. If you think a change carries a risk (a claim a recruiter or reference check could question, a title he hasn't held, a failed format check), make it anyway and add a one-line note on the risk in reply.md.

Rules for everything you change on your own (his instruction overrides them):
1. Every claim you add must trace to the impact record, user-confirmed notes, or the evidence ledger in 04-match.md, at exactly the scope the source states ("contributed to" stays "contributed to"). Follow the Do-not-claim list.
2. The headline starts with the posting's exact title, level included, whether or not he held it. Anywhere else, don't add a level or title he hasn't held.
3. Keep the exact markdown shape of the writer brief (headings, job lines, italic date lines, CORE SKILLS with Hard Skills / Soft Skills lines, plain characters only).
4. Keep to the length limit at the end of this brief.
5. Change only what he asks for. Keep the posting's own wording where the evidence supports it.
"""
CHAT_TURNS = 24     # earlier messages sent with each new one
TAILORED = "Tailored resume"    # the tailored résumé's source name in ratings-final.json

RERATE_BRIEF = """# Re-rate the edited resume

{name} just changed his tailored resume in a chat. Rate the revised resume (resume-edited.md) against the posting's requirements the way the tailoring run rated it, so his ATS scorecard and resume score stay current.

requirements.json lists every skills and experience row with its weight and the rating the previous version got, plus that version's 1-10 score. For each row, rate the revised resume literally on what it says: a resume only gets credit for what is on the page, because recruiters don't infer. strong = clearly demonstrated with scope or results; partial = adjacent or transferable; missing = not on the page. Keep the previous rating wherever the change (changes.md) doesn't touch what the page shows for that row, so a score moves only because the resume did. Then score the revised resume 1-10 with the rubric in scoring-and-report.md.

Write `ratings.json`: {{"skills": ["strong"|"partial"|"missing", ...], "experience": [...], "score": n}}, one rating per row, in the order of requirements.json.
"""


SCORE_KEYS = ("ats", "ats_parts", "resume_score", "ratings", "rerated", "rating")


class EditError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ResumeWorkspace:
    run_dir: Path
    exported_pdf: Optional[Path] = None  # the pipeline's deliverable PDF; kept in sync with the current version
    role: str = ""                       # the job's title and company, for the PDF's own title (else the posting's title)
    company: str = ""

    @property
    def pdf_title(self) -> str:
        """The PDFs' title, which browsers save them under: "<Role Title> - <Company>"."""
        return config.candidate().resume_title(self.role or self.title(), self.company)

    @property
    def vdir(self) -> Path:
        return self.run_dir / "versions"

    @property
    def history_path(self) -> Path:
        return self.vdir / "history.json"

    # ---- state ---------------------------------------------------------------
    def history(self) -> dict:
        if not self.history_path.exists():
            self._init()
        return json.loads(self.history_path.read_text())

    def _save(self, h: dict) -> None:
        self.history_path.write_text(json.dumps(h, indent=2))

    def _drawn_in(self) -> str:
        """The format the tailoring run drew this résumé in (Signature before there were formats)."""
        f = self.run_dir / "resume-format.txt"
        return formats.get(f.read_text().strip() if f.exists() else None).id

    def format_id(self) -> str:
        """The format its PDFs are drawn in, read without setting up the versions (which renders v1)."""
        if self.history_path.exists():
            return json.loads(self.history_path.read_text()).get("format") or self._drawn_in()
        return self._drawn_in()

    @property
    def format(self) -> formats.Format:
        self.history()
        return formats.get(self.format_id())

    def _init(self) -> None:
        src = self.run_dir / "resume-final.md"
        if not src.exists():
            raise EditError(f"No resume-final.md in {self.run_dir}")
        self.vdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, self.vdir / "v1.md")
        fid = self._drawn_in()
        if self.exported_pdf and self.exported_pdf.exists():
            shutil.copy(self.exported_pdf, self.vdir / "v1.pdf")
        else:
            render.render_pdf(src.read_text(), self.vdir / "v1.pdf", fmt=fid, title=self.pdf_title)
        check = self.check(self.vdir / "v1.md", formats.get(fid))
        md = (self.vdir / "v1.md").read_text()
        pdf_txt = self.run_dir / "resume-final.pdf.txt"     # the text the tailoring run's ATS total was measured on
        rows = self.requirements()
        upload = self.run_dir / "uploaded-from.txt"         # a résumé uploaded for the job (linked.save_upload)
        source, changes = ("upload", f"Uploaded {upload.read_text().strip()}.") if upload.exists() else ("pipeline", "")
        self._save({"current": 1, "format": fid, "proposal": None, "versions": [{
            "n": 1, "created": _now(), "source": source, "instruction": "", "changes": changes, "format": fid,
            "keywords_pct": self.keyword_pct(md), "check": check,
            **(self.scores(rows["ratings"], rows["score"], pdf_txt.read_text() if pdf_txt.exists() else md)
               if rows else {})}]})

    def md(self, n: Optional[int] = None) -> str:
        n = n or self.history()["current"]
        return (self.vdir / f"v{n}.md").read_text()

    @property
    def chat_path(self) -> Path:
        return self.vdir / "chat.json"

    def chat(self) -> list[dict]:
        return json.loads(self.chat_path.read_text()) if self.chat_path.exists() else []

    def _say(self, role: str, text: str, **extra) -> None:
        msgs = self.chat()
        msgs.append({"role": role, "text": text, "at": _now(), **extra})
        self.vdir.mkdir(parents=True, exist_ok=True)
        self.chat_path.write_text(json.dumps(msgs, indent=2))

    def clear_chat(self) -> list[dict]:
        self.chat_path.unlink(missing_ok=True)
        return []

    def pdf_path(self, which: str) -> Path:
        """which: a version number or 'proposal'."""
        self.history()
        p = self.vdir / ("proposal.pdf" if which == "proposal" else f"v{int(which)}.pdf")
        if not p.exists():
            raise EditError(f"No PDF for {which}")
        return p

    # ---- checks ----------------------------------------------------------------
    def title(self) -> str:
        obj = self.run_dir / "01-objectives.md"
        return exact_title(obj.read_text(), "") if obj.exists() else ""

    def keyword_hits(self, text: str) -> Optional[tuple[set[str], int]]:
        """The posting keywords the text has, and how many keywords there are (None without keywords.json)."""
        kw_path = self.run_dir / "keywords.json"
        if not kw_path.exists():
            return None
        kw = normalize_keywords(json.loads(kw_path.read_text()))[0]
        return {k for k, p in kw.items() if re.search(p, text, re.I)}, len(kw)

    def keyword_pct(self, text: str) -> Optional[float]:
        if (got := self.keyword_hits(text)) is None:
            return None
        hits, total = got
        return round(100 * len(hits) / max(1, total), 1)

    def compare(self, a: int, b: int) -> dict:
        """What changed from version a to version b, beside the scores each version keeps: the wording, the
        requirements whose rating moved (raised first, heaviest first) and the posting keywords gained or lost."""
        vs = {v["n"]: v for v in self.history()["versions"]}
        if a not in vs or b not in vs:
            raise EditError(f"No version {a if a not in vs else b}")
        md_a, md_b = self.md(a), self.md(b)
        moved = []
        if rows := self.requirements():
            for sec in ("skills", "experience"):
                ra, rb = ((vs[n].get("ratings") or {}).get(sec) for n in (a, b))
                if ra and rb and len(ra) == len(rb) == len(rows[sec]):
                    moved += [{"section": sec, "requirement": r["requirement"], "weight": r["weight"], "a": x, "b": y}
                              for r, x, y in zip(rows[sec], ra, rb) if x != y]
        rank, heavy = {"missing": 0, "partial": 1, "strong": 2}, {"high": 0, "med": 1, "medium": 1, "low": 2}
        moved.sort(key=lambda m: (rank.get(m["b"], 0) < rank.get(m["a"], 0), heavy.get(str(m["weight"]).lower(), 3)))
        hits_a, hits_b = self.keyword_hits(md_a), self.keyword_hits(md_b)
        return {"a": a, "b": b, "diff": diff(md_a, md_b), "moved": moved,
                "keywords": {"gained": sorted(hits_b[0] - hits_a[0]), "lost": sorted(hits_a[0] - hits_b[0])}
                if hits_a and hits_b else None}

    def requirements(self) -> Optional[dict]:
        """The requirement rows the tailoring run rated (ratings-final.json) with the tailored résumé's
        ratings and score: {"skills": [rows], "experience": [rows], "ratings": {...}, "score": n}."""
        try:
            d = json.loads((self.run_dir / "ratings-final.json").read_text())
            i = d["sources"].index(TAILORED)
            rows = {sec: [{"requirement": r.get("requirement", ""), "weight": r["weight"]} for r in d.get(sec) or []]
                    for sec in ("skills", "experience")}
            ratings = {sec: [r["ratings"][i].lower() for r in d.get(sec) or []] for sec in rows}
            return {**rows, "ratings": ratings, "score": (d.get("scores") or [None] * (i + 1))[i]}
        except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            return None

    def scores(self, ratings: dict, score: Optional[float], text: str, rerated: bool = True) -> dict:
        """A version's scores: the ATS total and its parts (keywords measured on `text`), the résumé score,
        and the ratings they came from. rerated=False: the ratings were carried over, not re-rated."""
        rows, kw_path = self.requirements(), self.run_dir / "keywords.json"
        if not rows or not kw_path.exists():
            return {}
        keywords = normalize_keywords(json.loads(kw_path.read_text()))[0]
        table = {"sources": [TAILORED], **{sec: [{**r, "ratings": [x]} for r, x in zip(rows[sec], ratings[sec])]
                                           for sec in ("skills", "experience")}}
        card = scorecard(table, keywords, {TAILORED: text})[TAILORED]
        r1 = lambda v: None if v is None else round(v, 1)
        return {"ats": r1(card["total"]), "ats_parts": {k: r1(card[k]) for k in ("skills", "experience", "keywords")},
                "resume_score": score, "ratings": ratings, "rerated": rerated}

    async def rerate(self, new_md: str, old_md: str, prior: dict, runner: Runner, cfg: Config) -> Optional[dict]:
        """Rate the revised résumé on every requirement row. None when there are no rows or the rating failed."""
        rows = self.requirements()
        if not rows:
            return None
        reqs = {sec: [{**r, "previous": x} for r, x in zip(rows[sec], prior["ratings"][sec])]
                for sec in ("skills", "experience")}
        changed = "\n".join(("- " + o["old"] if o["op"] == "del" else "+ " + o["new"] if o["op"] == "add"
                              else f"- {o['old']}\n+ {o['new']}") for o in diff(old_md, new_md) if o["op"] != "same")
        # A small model and none of the candidate materials: the rows, the page and the rubric are all it rates on.
        cand = config.candidate()
        call = AgentCall(label="resume-rerate", model=cfg.rescore_model, candidate=False,
                         instructions=cand.personalize(RERATE_BRIEF),
                         documents={"jd.md": (self.run_dir / "jd.md").read_text(),
                                    "scoring-and-report.md": cand.personalize(
                                        (config.SKILL / "references" / "scoring-and-report.md").read_text()),
                                    "requirements.json": json.dumps({**reqs, "previous_score": prior.get("score")},
                                                                    indent=2),
                                    "changes.md": changed or "(no line changed)", "resume-edited.md": new_md},
                         expect=["ratings.json"])
        try:
            d = json.loads((await runner.run(call)).files["ratings.json"])
            out = {sec: [str(x).lower() for x in d[sec]] for sec in ("skills", "experience")}
            if any(len(out[sec]) != len(rows[sec]) or any(x not in VALUE for x in out[sec]) for sec in out):
                return None
            score = d.get("score")
            return {"ratings": out, "score": float(score) if isinstance(score, (int, float)) else prior.get("score")}
        except Exception:  # noqa: BLE001 - a failed rating keeps the edit; its scores carry over the old ratings
            return None

    def prior(self, h: dict, base: Optional[int] = None) -> Optional[dict]:
        """The ratings a new edit is rated against: the pending proposal's (unless the edit is built on
        version `base`), else that version's (the current one by default), else the tailoring run's."""
        rows = self.requirements()
        if not rows:
            return None
        cur = next((v for v in h["versions"] if v["n"] == (base or h["current"])), {})
        for v in ((h.get("proposal") or {}) if base is None else {}, cur):
            if v.get("ratings"):
                return {"ratings": v["ratings"], "score": v.get("resume_score")}
        return {"ratings": rows["ratings"], "score": rows["score"]}

    def score_sheet(self, h: dict) -> Optional[str]:
        """The scores the app shows for the current version (and a pending proposal), for the chat, so Claude
        quotes them instead of scoring the résumé itself. None when the version has no scores."""
        rows = self.requirements()
        v = next((v for v in h["versions"] if v["n"] == h["current"]), {})
        if not rows or v.get("ats") is None:
            return None
        num = lambda x: "n/a" if x is None else f"{x:g}"
        parts = v.get("ats_parts") or {}
        lines = [f"# The app's scores (current version, v{v['n']})", "",
                 f"- Resume score: {num(v.get('resume_score'))}/10",
                 f"- ATS: {num(v['ats'])}% (skills {num(parts.get('skills'))}%, experience "
                 f"{num(parts.get('experience'))}%, keywords {num(parts.get('keywords'))}%; the target is 80%)"]
        try:
            d = json.loads((self.run_dir / "ratings-final.json").read_text())
            impact = d["scores"][d["sources"].index(config.IMPACT_SOURCE)]
            lines.append(f"- Impact record score: {num(impact)}/10 (his record against the posting, rated when the "
                         "resume was tailored; editing the record since doesn't change it)")
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            pass
        if v.get("rating"):
            lines.append("- A hand edit is still being re-rated: skills, experience and the resume score may change "
                         "in a few seconds.")
        elif v.get("rerated") is False:
            lines.append("- Re-rating this version failed, so skills, experience and the resume score are the "
                         "previous version's; only keywords were measured on it.")
        if (p := h.get("proposal")) and p.get("ats") is not None:
            lines.append(f"- Pending proposal (not accepted yet): resume score {num(p.get('resume_score'))}/10, "
                         f"ATS {num(p['ats'])}%")
        ratings = v.get("ratings") or rows["ratings"]
        for sec in ("skills", "experience"):
            lines += ["", f"## {sec.capitalize()} ratings", ""]
            lines += [f"- [{r['weight']}] {r['requirement']}: {x}" for r, x in zip(rows[sec], ratings[sec])]
        return "\n".join(lines) + "\n"

    def check(self, md_path: Path, fmt: Optional[formats.Format] = None) -> dict:
        title = self.title()
        if not title:
            return {"passed": None, "output": "No exact title found; check skipped."}
        code, out = run_script("check_resume.py", str(md_path), "--title", title, cwd=self.run_dir,
                               max_pages=(fmt or self.format).max_pages)
        return {"passed": code == 0, "output": out}

    def editable(self) -> bool:
        """Edits need the posting and the match brief; a saved résumé copied on its own has neither."""
        return (self.run_dir / "jd.md").exists() and (self.run_dir / "04-match.md").exists()

    # ---- edit loop ---------------------------------------------------------------
    async def propose(self, instruction: str, runner: Runner, cfg: Config, fmt: Optional[str] = None,
                      chat: Optional[tuple[Runner, ModelCfg, str]] = None) -> dict:
        """Send one chat message. Returns {"reply": str, "proposal": {...} | None}; a proposal only
        when Claude revised the résumé, which replaces any pending one. With `fmt` (the trim for a new
        default format), the proposal is drawn in and held to that format, and accepting it switches to it.
        `chat` is (runner, model, label) for the AI picked in the chat's model menu: it answers and its label is
        kept with the reply, while `runner` (the Profile's AI) still re-rates a proposed edit."""
        if not self.editable():
            raise EditError("This résumé was saved without its posting and match brief, so Claude can't edit it "
                            "here. Tailor the job in the app to get an editable copy.")
        # history() may render v1, and Playwright's sync API can't run on the event loop thread.
        h = await asyncio.to_thread(self.history)
        instruction = instruction.strip()
        if not instruction:
            raise EditError("Type a message first.")
        current = self.md(h["current"])
        cand = config.candidate()
        target = formats.get(fmt) if fmt else self.format
        docs = {"jd.md": (self.run_dir / "jd.md").read_text(),
                "04-match.md": (self.run_dir / "04-match.md").read_text(),
                f"resume-current.md (v{h['current']})": current,
                "writer brief (for the resume shape)": brief("05-resume-writer.md")}
        pending = (self.vdir / "proposal.md").read_text() if h.get("proposal") and (self.vdir / "proposal.md").exists() else ""
        if pending:
            docs["resume-proposed.md (pending, not accepted yet; the PDF on screen)"] = pending
        if sheet := self.score_sheet(h):
            docs["scores.md"] = sheet
        who = {"user": cand.first_name, "claude": "You", "note": "(app)"}
        convo = "\n\n".join(f"{who.get(m['role'], m['role'])}: {m['text']}" for m in self.chat()[-CHAT_TURNS:])
        convo = convo or f"(this is {cand.first_name}'s first message)"
        tail = (f"The conversation so far:\n{convo}\n\n"
                f"{cand.first_name}'s new message:\n{instruction}"
                + (f"\n\nThe posting's exact job title: {self.title()}" if self.title() else ""))
        chat_runner, model, via = chat or (runner, cfg.chat_model, "")
        call = AgentCall(label="resume-edit", model=model,
                         instructions=cand.personalize(EDIT_BRIEF) + "\n" + formats.length_rules(target),
                         documents=docs, expect=["reply.md"], tail=tail, prose_file="reply.md")
        # The message is kept before Claude answers, so a page reloaded meanwhile still shows it.
        self._say("user", instruction)
        try:
            return await self._answer(instruction, runner, cfg, call, h, current, pending, target, chat_runner, via)
        except BaseException:       # a failure, or the request cancelled: the message isn't left unanswered
            self._say("note", "Claude couldn't answer that message. Send it again to retry.")
            raise

    async def _answer(self, instruction: str, runner: Runner, cfg: Config, call: AgentCall,
                      h: dict, current: str, pending: str, target: formats.Format,
                      chat_runner: Runner, via: str) -> dict:
        res = await chat_runner.run(call)
        reply = res.files["reply.md"].strip()
        new_md = res.files.get("resume-edited.md", "")
        tag = {"via": via} if via else {}
        if not new_md.strip() or new_md.strip() == (pending or current).strip():
            self._say("claude", reply, **tag)
            return {"reply": reply, "proposal": None}
        prop_md = self.vdir / "proposal.md"
        prop_md.write_text(new_md)
        prior = self.prior(h)

        async def rate() -> Optional[dict]:
            return await self.rerate(new_md, pending or current, prior, runner, cfg) if prior else None
        (pages, pdf_text), check, rated = await asyncio.gather(
            asyncio.to_thread(render.render_pdf, new_md, self.vdir / "proposal.pdf", None, target, self.pdf_title),
            asyncio.to_thread(self.check, prop_md, target), rate())
        scores = {}
        if prior:
            r = rated or prior
            scores = self.scores(r["ratings"], r["score"], pdf_text or new_md, rerated=rated is not None)
        proposal = {"created": _now(), "instruction": instruction, "changes": reply,
                    "pages": pages, "check": check, "format": target.id,
                    "keywords_pct": self.keyword_pct(new_md), "base": h["current"], **scores}
        h["proposal"] = proposal
        self._save(h)
        self._say("claude", reply, proposal=True, **tag)
        return {"reply": reply, "proposal": {**proposal, "diff": diff(current, new_md)}}

    def accept(self) -> dict:
        h = self.history()
        p = h.get("proposal")
        if not p:
            raise EditError("No pending edit.")
        if p["base"] != h["current"]:
            raise EditError("The current version changed since this edit was proposed; ask again.")
        n = max(v["n"] for v in h["versions"]) + 1
        (self.vdir / "proposal.md").rename(self.vdir / f"v{n}.md")
        (self.vdir / "proposal.pdf").rename(self.vdir / f"v{n}.pdf")
        fid = p.get("format") or h.get("format") or self._drawn_in()
        h["versions"].append({"n": n, "created": _now(), "source": "edit", "instruction": p["instruction"],
                              "changes": p["changes"], "keywords_pct": p["keywords_pct"], "check": p["check"],
                              "format": fid, **{k: p[k] for k in SCORE_KEYS if k in p}})
        h["current"], h["proposal"], h["format"] = n, None, fid
        self._save(h)
        self._export(n)
        self._say("note", f"Accepted the proposed edit as v{n}.")
        return h

    def current_scores(self) -> dict:
        """The current version's ATS total and résumé score, for the job's row ({} when it has none)."""
        h = self.history()
        v = next((v for v in h["versions"] if v["n"] == h["current"]), {})
        return {"ats_total": v["ats"], "resume_score": v.get("resume_score")} if v.get("ats") is not None else {}

    def discard(self) -> dict:
        h = self.history()
        for f in ("proposal.md", "proposal.pdf"):
            (self.vdir / f).unlink(missing_ok=True)
        if h.get("proposal"):
            self._say("note", "Discarded the proposed edit.")
        h["proposal"] = None
        self._save(h)
        return h

    def restore(self, n: int) -> dict:
        """Make an earlier version current again (as a new version, so history stays linear)."""
        h = self.history()
        if not any(v["n"] == n for v in h["versions"]):
            raise EditError(f"No version {n}")
        new = max(v["n"] for v in h["versions"]) + 1
        shutil.copy(self.vdir / f"v{n}.md", self.vdir / f"v{new}.md")
        old = next(v for v in h["versions"] if v["n"] == n)
        if old.get("format") == h.get("format"):
            shutil.copy(self.vdir / f"v{n}.pdf", self.vdir / f"v{new}.pdf")
        else:                       # drawn in another format: redraw it in the résumé's current one
            render.render_pdf(self.md(n), self.vdir / f"v{new}.pdf", fmt=self.format, title=self.pdf_title)
        h["versions"].append({**old, "n": new, "created": _now(), "source": f"restored v{n}",
                              "instruction": "", "changes": f"Restored version {n}.", "format": h.get("format")})
        h["current"] = new
        self._save(h)
        self._export(new)
        self._say("note", f"Restored v{n} as v{new}.")
        return h

    # ---- edits by hand ------------------------------------------------------------------
    def save_page_edits(self, base: int, edits: list[dict]) -> dict:
        """Save changes made on the page: [{"key": "<line>:<field>", "old": text shown, "text": new text}]."""
        self._check_base(base)
        md, changed = apply_page_edits(self.md(base), edits)
        return self._save_by_hand(base, md, f"Edited on the page ({changed} change{'s' if changed != 1 else ''}).")

    def save_text(self, base: int, md: str) -> dict:
        """Save the whole résumé as edited in markdown."""
        self._check_base(base)
        return self._save_by_hand(base, md, "Edited as text.")

    def save_upload(self, md: str, filename: str) -> dict:
        """Save an uploaded résumé (already read into markdown) as the next version."""
        return self._save_by_hand(self.history()["current"], md, f"Uploaded {filename}.", source="upload")

    def _check_base(self, base: int) -> None:
        if base != self.history()["current"]:
            raise EditError("The résumé changed since you opened the editor. Reopen it and make your edit again.")

    def _save_by_hand(self, base: int, md: str, changes: str, source: str = "by hand") -> dict:
        if md.strip() == self.md(base).strip():
            raise EditError("Nothing changed.")
        try:
            render.render(md)
        except Exception:  # noqa: BLE001 - any parse failure means the shape is broken
            raise EditError("The résumé can't be laid out: keep the first three lines as # Name, "
                            "**headline** and the contact line.") from None
        h = self.history()
        n = max(v["n"] for v in h["versions"]) + 1
        md = md.rstrip("\n") + "\n"
        (self.vdir / f"v{n}.md").write_text(md)
        fmt = self.format
        pages, pdf_text = render.render_pdf(md, self.vdir / f"v{n}.pdf", fmt=fmt, title=self.pdf_title)
        (self.vdir / f"v{n}.pdf.txt").write_text(pdf_text or md)     # what rescore() measures keywords on
        for f in ("proposal.md", "proposal.pdf"):    # a pending proposal was built on the old version
            (self.vdir / f).unlink(missing_ok=True)
        # Keywords now; skills and experience carry over until rescore() re-rates them (the app calls it next).
        prior = self.prior(h, base)
        scores = {**self.scores(prior["ratings"], prior["score"], pdf_text or md, rerated=False),
                  "rating": True} if prior else {}
        h["versions"].append({"n": n, "created": _now(), "source": source, "instruction": "", "changes": changes,
                              "keywords_pct": self.keyword_pct(md), "check": self.check(self.vdir / f"v{n}.md"),
                              "pages": pages, "base": base, "format": fmt.id, **scores})
        dropped = bool(h.get("proposal"))
        h["current"], h["proposal"] = n, None
        self._save(h)
        self._export(n)
        self._say("note", f"{changes[:-1]}, saved as v{n}." + (" Claude's pending proposal was discarded." if dropped else ""))
        return {**h, "pages": pages}

    async def rescore(self, n: int, runner: Runner, cfg: Config) -> Optional[dict]:
        """Re-rate a hand-edited version against the one it was built on, and store its scores.
        Returns the version, or None when it has nothing waiting to be rated."""
        h = await asyncio.to_thread(self.history)
        v = next((v for v in h["versions"] if v["n"] == n), None)
        if not v or not v.get("rating"):
            return v
        base = v.get("base") or n - 1
        prior = self.prior(h, base)
        txt = self.vdir / f"v{n}.pdf.txt"
        r = await self.rerate(self.md(n), self.md(base), prior, runner, cfg) if prior else None
        h = self.history()                         # re-read: the chat may have saved meanwhile
        v = next(v for v in h["versions"] if v["n"] == n)
        if r:
            v.update(self.scores(r["ratings"], r["score"], txt.read_text() if txt.exists() else self.md(n)))
        v["rating"] = False
        self._save(h)
        return v

    # ---- formats -----------------------------------------------------------------------
    def redraw(self, fmt: formats.Format, printer: Optional[render.Printer] = None, force: bool = False) -> dict:
        """Draw the current version in `fmt` when it fits the format's page limit: {"applied", "pages"}.
        One that doesn't fit keeps its format; trim_instruction() is the chat message that fits it. `force` redraws
        one already in `fmt` too (its settings changed)."""
        h = self.history()
        if not force and h.get("format") == fmt.id and (self.vdir / f"v{h['current']}.pdf").exists():
            return {"applied": True, "pages": None, "format": fmt.id}
        n = h["current"]
        tmp = self.vdir / "redraw.pdf"
        pages, text = (printer.pdf if printer else render.render_pdf)(self.md(n), tmp, fmt=fmt, title=self.pdf_title)
        if pages > fmt.max_pages:
            tmp.unlink(missing_ok=True)
            return {"applied": False, "pages": pages, "format": h.get("format") or self._drawn_in()}
        tmp.replace(self.vdir / f"v{n}.pdf")
        (self.vdir / f"v{n}.pdf.txt").write_text(text)
        for v in h["versions"]:
            if v["n"] == n:
                v.update(format=fmt.id, pages=pages)
        h["format"] = fmt.id
        self._save(h)
        self._export(n)
        return {"applied": True, "pages": pages, "format": fmt.id}

    @staticmethod
    def trim_instruction(fmt: formats.Format, pages: int) -> str:
        """The chat message that trims a résumé to a format's page limit (deleting only, like Make résumé's trim)."""
        return (f"Trim this résumé to fit {fmt.pages_word} in the {fmt.name} format; it runs to {pages} pages there. "
                "Delete only: first whole bullets that prove Low-weight requirements, then Med ones, then any bullet "
                "that repeats another's proof, keeping every bullet that is the only proof of a High-weight "
                "requirement (04-match.md has the weights). Then shorten long bullets to two lines at most. "
                "Don't add or reword anything.")

    def _export(self, n: int) -> None:
        """Keep resume-final.md and the deliverable PDF equal to the current version."""
        shutil.copy(self.vdir / f"v{n}.md", self.run_dir / "resume-final.md")
        if self.exported_pdf:
            shutil.copy(self.vdir / f"v{n}.pdf", self.exported_pdf)


# ---- page edits → markdown ------------------------------------------------------------------------
# render(editable=True) tags each piece of text with its markdown line and a field naming which part
# of the line it shows. Each field's region is the part of the raw line its text comes from.
FIELD_RE = {
    "name": r"^(\s*#\s+)(.*?)(\s*)$",
    "bullet": r"^(\s*-\s+)(.*?)(\s*)$",
    "company": r"^(\s*\*\*)(.+?)(\*\*\s*\|.*)$",
    "role": r"^(\s*\*\*.+?\*\*\s*\|\s*)(.+?)(\s*)$",
}
WHOLE_LINE = r"^(\s*)(.*?)(\s*)$"     # headline, contact, dates, paragraphs, a bullet's continuation lines
REQUIRED = {"name": "name", "headline": "headline", "contact": "contact line", "company": "company", "role": "job title"}
QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "\u00a0": " "})


def _shown(raw: str) -> str:
    """The text render.inline() shows for a raw markdown fragment (markers dropped)."""
    s = re.sub(r"`([^`]+)`", r"\1", raw)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    return re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"\1", s)


def _norm(s: str) -> str:
    s = re.sub(r"\s+", " ", s.translate(QUOTES)).strip()
    return re.sub(r" ?\| ?", " | ", s)


def _align(raw: str, shown: str) -> Optional[list[int]]:
    """For each character of `shown` (plus its end), the index in `raw` it comes from. Markdown
    markers and spacing differences are skipped; None if the two don't line up."""
    pos, i = [], 0
    for ch in shown:
        while i < len(raw) and raw[i].translate(QUOTES) != ch and (raw[i] in "*`" or raw[i].isspace()):
            i += 1
        if i < len(raw) and raw[i].translate(QUOTES) == ch:
            pos.append(i)
            i += 1
        elif ch.isspace():
            pos.append(i)
        else:
            return None
    end = pos[-1] + 1 if pos else 0
    return pos + [end]


def _patch(raw: str, old: str, new: str) -> str:
    """Apply the change old -> new (both as shown) to the raw fragment, keeping markdown markers the
    edit doesn't touch. Falls back to the plain new text when the markers can't be kept."""
    pos = _align(raw, old)
    if pos is None or not old:
        return new
    out = raw
    ops = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in reversed(ops):
        if tag == "equal":
            continue
        a, b = (pos[i1], pos[i2 - 1] + 1) if i2 > i1 else (pos[i1], pos[i1])
        out = out[:a] + new[j1:j2] + out[b:]
    return out if _norm(_shown(out)) == new else new


def apply_page_edits(md: str, edits: list[dict]) -> tuple[str, int]:
    """Apply page edits to the markdown. Returns (new markdown, number of fields changed).
    Emptying a bullet, paragraph or date line deletes it (a bullet with its continuation lines)."""
    lines = md.split("\n")
    gone: set[int] = set()
    changed = 0
    # role before company: the role's region sits after the company's on the same line
    order = {"role": 0, "company": 1}
    for e in sorted(edits, key=lambda e: order.get(str(e.get("key", "")).partition(":")[2], 2)):
        line_s, _, field = str(e.get("key", "")).partition(":")
        if not line_s.isdigit() or int(line_s) >= len(lines):
            raise EditError("That edit points past the end of the résumé; reopen the editor.")
        n = int(line_s)
        old, new = _norm(e.get("old", "")), _norm(e.get("text", ""))
        if old == new:
            continue
        m = re.match(FIELD_RE.get(field, WHOLE_LINE), lines[n])
        if not m or _norm(_shown(m.group(2))) != old:
            raise EditError(f"Line {n + 1} of the résumé changed since the editor opened; reopen it.")
        changed += 1
        if not new:
            if field in REQUIRED:
                raise EditError(f"The {REQUIRED[field]} can't be empty.")
            gone.add(n)
            if field == "bullet":
                k = n + 1
                while k < len(lines) and lines[k].startswith(" ") and lines[k].strip() and not lines[k].lstrip().startswith("- "):
                    gone.add(k)
                    k += 1
            continue
        lines[n] = lines[n][:m.start(2)] + _patch(m.group(2), old, new) + lines[n][m.end(2):]
    return "\n".join(l for k, l in enumerate(lines) if k not in gone), changed


def diff(old: str, new: str) -> list[dict]:
    """Line-level diff with word-level detail for changed lines.
    Returns [{"op": "same"|"add"|"del"|"change", "old": str, "new": str, "words": [[op, text], ...]}]."""
    out: list[dict] = []
    a, b = old.splitlines(), new.splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            out += [{"op": "same", "old": l, "new": l} for l in a[i1:i2]]
        elif tag == "delete":
            out += [{"op": "del", "old": l, "new": ""} for l in a[i1:i2]]
        elif tag == "insert":
            out += [{"op": "add", "old": "", "new": l} for l in b[j1:j2]]
        else:
            olds, news = a[i1:i2], b[j1:j2]
            for k in range(max(len(olds), len(news))):
                o = olds[k] if k < len(olds) else ""
                n = news[k] if k < len(news) else ""
                if not o:
                    out.append({"op": "add", "old": "", "new": n})
                elif not n:
                    out.append({"op": "del", "old": o, "new": ""})
                else:
                    out.append({"op": "change", "old": o, "new": n, "words": word_diff(o, n)})
    return out


def word_diff(old: str, new: str) -> list[list[str]]:
    a, b = re.findall(r"\S+|\s+", old), re.findall(r"\S+|\s+", new)
    ops: list[list[str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            ops.append(["same", "".join(a[i1:i2])])
        else:
            if i2 > i1:
                ops.append(["del", "".join(a[i1:i2])])
            if j2 > j1:
                ops.append(["add", "".join(b[j1:j2])])
    return ops

"""A prep pack for one role, made when that role is marked Interviewing.

One chat-model call reads the posting, the unconfirmed gaps, the evidence ledger and (when the
app has the text) the résumé that was sent. It writes likely questions, a story for each cited
from the impact record or the ledger, and questions to ask them. A story whose cite isn't in
those sources, or whose figures aren't in the cited source, is dropped; the question stays.

The debrief is the candidate's own notes. Lines they confirm from it go into the impact record
the same way the setup interview's lines do (onboarding.checked_lines, then record_import.apply
into "From your interviews"). The blank page at data/interview-prep.md is a different file:
notes that aren't about one role.

Company research is a separate call, started only when the candidate asks for it. It is saved on
this same pack. Building or rebuilding the questions does not run it, and running the research
again replaces only that research.

Stored at data/interview-packs/<job-id>.json.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import evidence, onboarding
from .llm import AgentCall, Runner
from .config import ModelCfg
from .tailor import unconfirmed_gaps

PACK_BRIEF = """You are preparing {first} for an interview for one role. Write pack.md from the documents only.

posting.md is the job. gaps.md lists unconfirmed gaps, or says there are none. ledger.md is the evidence ledger (ids such as E1, and what each may say). resume.md is the résumé {first} sent, or says there is no text. The impact record is in your materials.

Write pack.md in exactly this shape, and nothing else:

## Questions

### <the gap, in the gap's own words>
Ask: <one likely interview question about that gap>
Story: <one or two sentences {first} can tell, using only the impact record, the ledger, or resume.md; or none>
Cite: <the ledger id, such as E1, or the impact-record heading the story comes from; or none>

One ### block per gap when gaps.md lists gaps (every gap, at most 6). When gaps.md says there are none, write 3 to 5 ### blocks and start each heading with "the posting". A story must name one cite. Do not invent a number, tool, team size or outcome. If you cannot cite a story, write Story: none and Cite: none.

## Questions to ask them

- <a question {first} could ask the interviewer, drawn from the posting>
- <another>

Write 3 to 5. No stories in this section."""

DEBRIEF_BRIEF = """{first} wrote a debrief after an interview for one role. Write the lines it adds to {first}'s impact record.

Write lines.md: a bullet list ("- " each), 1 to 6 lines, each one result or responsibility in {first}'s own facts, in résumé style (an action verb first, no "I"). Use only what the debrief says: never add a number, tool, team size or outcome it doesn't state. Keep each figure exactly as {first} gave it. Leave out anything the impact record and résumés in your materials already say. If the debrief gives nothing new, write "- none"."""

RESEARCH_BRIEF = """You are researching the company behind one role so {first} can tailor "Why do you want to work here?" and prepare questions for the interviewer. Write research.md from the documents only.

posting.md is the job. company.md is the funding and startup record already stored for this company, or says there is none. You have no web search, no reviews site, and no news feed.

Write research.md in short sections. Each section starts with ## and a heading. Cover what helps in an interview: history, mission, values, culture, challenges, how the company defines success and its next few years, management style, diversity and inclusion, growth and how performance is measured, recent news, market position, products, funding history, growth, layoffs, and review themes. Use a heading when you have something to say about it. End with ## Why this role and ## Questions to ask, a few lines {first} could use.

This is background for the interview, not a fact from {first}'s files. A round amount, a layoff count, a review score, or any other precise figure may be stated only when that figure is written in posting.md or company.md. You may still discuss a topic the documents don't quantify: say what to listen for or what to ask, and do not invent the number or say the posting stated it."""

_NONE = {"", "none", "n/a", "-", "—"}
_FIELD = re.compile(r"^(ask|story|cite):\s*(.*)$", re.I)
_LEDGER_ID = re.compile(r"E\d+[a-z]?", re.I)
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`#]", "", s or "")).strip().lower()


def _blank(s: str) -> bool:
    return _plain(s).rstrip(".") in _NONE


@dataclass
class Materials:
    posting: str
    gaps: list[str]
    ledger: dict[str, str]
    impact: str
    resume_md: str = ""
    pdf_only: str = ""

    @property
    def from_gaps(self) -> bool:
        return bool(self.gaps)


def collect(posting: str, match_md: str, full_score: Optional[dict], triage: Optional[dict],
            impact: str, resume_md: str = "", pdf_only: str = "") -> Materials:
    """Gaps from the match file, else the full score, else the signal score. The ledger is the match file's."""
    gaps = unconfirmed_gaps(match_md) if match_md else []
    if not gaps:
        gaps = [str(x).strip() for x in ((full_score or {}).get("gaps") or []) if str(x).strip()]
    if not gaps:
        gaps = [str(x).strip() for x in ((triage or {}).get("likely_gaps") or []) if str(x).strip()]
    return Materials(posting=(posting or "")[:15000], gaps=gaps[:8], ledger=evidence.ledger(match_md or ""),
                     impact=impact or "", resume_md=(resume_md or "")[:15000], pdf_only=pdf_only or "")


def parse(md: str) -> tuple[list[dict], list[str]]:
    """(questions as {gap, question, story, cite}, questions to ask them) from pack.md."""
    questions: list[dict] = []
    ask: list[str] = []
    section, block = "", None

    def flush() -> None:
        nonlocal block
        if not block:
            return
        q = (block.get("ask") or "").strip()
        if q and not _blank(q):
            questions.append({"gap": (block.get("gap") or "").strip(), "question": q,
                              "story": (block.get("story") or "").strip(), "cite": (block.get("cite") or "").strip()})
        block = None

    for raw in (md or "").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            flush()
            title = _plain(line[3:])
            section = "ask" if "ask them" in title or "questions to ask" in title else "questions" if "question" in title else ""
            continue
        if section == "questions" and line.startswith("### "):
            flush()
            gap = re.sub(r"^gap:\s*", "", re.sub(r"[*_`]", "", line[4:]).strip(), flags=re.I)
            block = {"gap": gap, "ask": "", "story": "", "cite": "", "_last": ""}
            continue
        if section == "questions" and block is not None:
            cleaned = re.sub(r"[*_`]", "", line).strip()
            m = _FIELD.match(cleaned)
            if m:
                block[m.group(1).lower()] = m.group(2).strip()
                block["_last"] = m.group(1).lower()
            elif block.get("_last") and cleaned:
                block[block["_last"]] = (block[block["_last"]] + " " + cleaned).strip()
            continue
        if section == "ask":
            item = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", line).strip()
            if item != line.strip() and item and not _blank(item):
                ask.append(item)
    flush()
    return questions[:8], ask[:8]


def company_text(funding: Optional[dict] = None, startup: Optional[dict] = None) -> str:
    """Plain lines from the funding block and the startup record already stored. Empty when neither has facts."""
    s = startup or {}
    f = funding if isinstance(funding, dict) else {}
    rnd = s.get("round") if isinstance(s.get("round"), dict) else {}
    lines: list[str] = []
    seen: set[str] = set()

    def add(label: str, value) -> None:
        if isinstance(value, dict):
            value = "; ".join(f"{k} {value[k]}" for k in value if value[k] not in (None, "", [], {}))
        elif isinstance(value, (list, tuple)):
            value = ", ".join(str(x) for x in value if x not in (None, ""))
        elif isinstance(value, bool):
            value = "yes" if value else "no"
        elif isinstance(value, float) and value.is_integer():
            value = int(value)
        if value in (None, ""):
            return
        text = str(value).strip()
        if not text or text.lower() in seen:
            return
        seen.add(text.lower())
        lines.append(f"{label}: {text[:500]}")

    add("name", s.get("name"))
    add("one liner", s.get("one_liner"))
    add("website", s.get("website") or s.get("domain"))
    add("hq", s.get("hq"))
    add("industries", s.get("industries"))
    stage = s.get("stage") if s.get("stage") not in (None, "", "Unknown") else ""
    add("stage", stage or (f.get("stage") if f.get("stage") not in (None, "", "Unknown") else ""))
    add("round", rnd.get("name") or f.get("round"))
    add("amount", f.get("amount") or rnd.get("amount"))
    if isinstance(rnd.get("amount_usd"), (int, float)):
        add("amount usd", rnd["amount_usd"])
    add("date", rnd.get("date") or f.get("date"))
    add("headline", rnd.get("headline") or f.get("headline"))
    add("source", rnd.get("source") or f.get("source"))
    add("url", rnd.get("url") or f.get("url"))
    add("batch", s.get("batch") or f.get("batch"))
    add("yc stage", s.get("yc_stage") or f.get("yc_stage"))
    team = s.get("team_size") if s.get("team_size") not in (None, "") else f.get("team_size")
    add("team size", team)
    if s.get("hiring") not in (None, ""):
        add("hiring", s.get("hiring"))
    for name in ("pdl", "theirstack", "fundable"):
        block = s.get(name)
        if isinstance(block, dict):
            add(name, block)
    sources = []
    for src in s.get("sources") or []:
        if isinstance(src, dict):
            bit = " ".join(str(src[k]) for k in ("kind", "name", "url") if src.get(k)).strip()
            if bit:
                sources.append(bit)
    add("sources", "; ".join(sources))
    line = f.get("line") or ""
    if line and line.lower() not in seen:
        add("funding", line)
    return "\n".join(lines)


def parse_research(md: str) -> list[dict]:
    """[{heading, text}] from research.md. A file with no ## headings is one untitled section."""
    sections: list[dict] = []
    heading, buf = "", []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if heading or text:
            sections.append({"heading": heading[:160], "text": text[:4000]})

    for raw in (md or "").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            flush()
            heading = re.sub(r"[*_`]", "", line[3:]).strip()
            buf = []
            continue
        if line.startswith("# ") and not heading and not any(x.strip() for x in buf):
            continue
        buf.append(raw.rstrip())
    flush()
    return [s for s in sections if s["heading"] or s["text"]][:24]


def _ledger_text(ledger: dict[str, str], cite: str) -> str:
    m = _LEDGER_ID.search(cite or "")
    if not m:
        return ""
    want = m.group(0).lower()
    for key, text in ledger.items():
        if key.lower() == want:
            return text or ""
    return ""


def _section_text(impact: str, cite: str) -> str:
    """The heading that matches cite, and the text under it until the next heading of the same or higher level."""
    want = _plain(cite)
    if len(want) < 2:
        return ""
    lines = (impact or "").splitlines()
    heads = [(i, len(m.group(1)), m.group(2)) for i, line in enumerate(lines) if (m := _HEADING.match(line.strip()))]
    for n, (i, level, text) in enumerate(heads):
        got = _plain(text)
        if got == want or got.startswith(want + ",") or got.startswith(want + " (") or got.startswith(want + ":"):
            end = len(lines)
            for j, lv, _ in heads[n + 1:]:
                if lv <= level:
                    end = j
                    break
            return "\n".join(lines[i:end])
    return ""


def honest(questions: list[dict], ledger: dict[str, str], impact: str) -> list[dict]:
    """Keep each question. Keep its story only when the cite is a ledger id or an impact-record heading,
    and every figure in the story is in that source."""
    out = []
    for q in questions:
        story, cite = (q.get("story") or "").strip(), (q.get("cite") or "").strip()
        if _blank(story) or _blank(cite):
            story, cite = "", ""
        else:
            src = _ledger_text(ledger, cite) or _section_text(impact, cite)
            missing = [f for f in evidence.figures(story) if f not in set(evidence.figures(src))]
            if not src or missing:
                story, cite = "", ""
            else:
                found = _LEDGER_ID.search(cite)
                if found and _ledger_text(ledger, found.group(0)):
                    cite = next(k for k in ledger if k.lower() == found.group(0).lower())
        out.append({"gap": (q.get("gap") or "")[:300], "question": (q.get("question") or "")[:500],
                    "story": story[:600], "cite": (cite if story else "")[:80]})
    return out


def _docs(m: Materials) -> dict[str, str]:
    gaps = "\n".join(f"- {g}" for g in m.gaps) if m.gaps else "(no gaps on file; write the questions from the posting)"
    ledger = "\n".join(f"{k} — {v}" for k, v in m.ledger.items()) or "(no evidence ledger on file)"
    resume = m.resume_md or "(no résumé text on file)"
    return {"posting.md": m.posting or "(no posting text on file)", "gaps.md": gaps, "ledger.md": ledger, "resume.md": resume}


async def make(runner: Runner, model: ModelCfg, brief: str, materials: Materials) -> dict:
    """The pack body (questions, ask_them, from_gaps, pdf_only, gaps). Raises if the model doesn't answer."""
    res = await runner.run(AgentCall(
        label="interview-pack", model=model, instructions=brief, documents=_docs(materials),
        expect=["pack.md"], prose_file="pack.md"))
    questions, ask = parse(res.files.get("pack.md") or "")
    return {"questions": honest(questions, materials.ledger, materials.impact), "ask_them": [a[:400] for a in ask],
            "from_gaps": materials.from_gaps, "pdf_only": materials.pdf_only, "gaps": list(materials.gaps)}


async def research(runner: Runner, model: ModelCfg, brief: str, posting: str, company: str) -> dict:
    """One chat-model call for company background. Returns {"sections"}. Does not build the role pack."""
    res = await runner.run(AgentCall(
        label="company-research", model=model, instructions=brief,
        documents={"posting.md": (posting or "")[:15000] or "(no posting text on file)",
                   "company.md": company or "(no funding or startup record on file)"},
        expect=["research.md"], prose_file="research.md"))
    return {"sections": parse_research(res.files.get("research.md") or "")}


async def draft_lines(runner: Runner, model: ModelCfg, brief: str, debrief: str) -> list[dict]:
    """Lines the debrief supports, each {"text", "ok", "why"}. A figure the debrief doesn't contain is flagged."""
    text = (debrief or "").strip()
    if not text:
        return []
    res = await runner.run(AgentCall(
        label="interview-pack-draft", model=model, instructions=brief, documents={"debrief.md": text[:8000]},
        expect=["lines.md"], prose_file="lines.md", tail="The debrief is in debrief.md."))
    return onboarding.checked_lines(res.files.get("lines.md") or "", text, where="your debrief")


def safe_id(jid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", jid or "") or "job"


def research_public(raw) -> dict:
    """The company-research block the app sends. Missing research is status none."""
    if not isinstance(raw, dict):
        raw = {}
    sections = []
    for s in raw.get("sections") or []:
        if not isinstance(s, dict):
            continue
        heading, text = str(s.get("heading") or "").strip()[:160], str(s.get("text") or "").strip()[:4000]
        if heading or text:
            sections.append({"heading": heading, "text": text})
    status = raw.get("status") or "none"
    if status not in ("none", "making", "ready", "failed"):
        status = "none"
    return {"status": status, "sections": sections, "error": str(raw.get("error") or ""),
            "made_at": str(raw.get("made_at") or "")}


def public(pack: Optional[dict], jid: str = "") -> dict:
    """What the app sends for one pack. No file yet is status none."""
    if not pack:
        return {"job_id": jid, "status": "none", "company": "", "title": "", "error": "", "from_gaps": False,
                "pdf_only": "", "gaps": [], "questions": [], "ask_them": [], "debrief": "", "added": 0, "made_at": "",
                "company_research": research_public(None)}
    return {"job_id": pack.get("job_id") or jid, "status": pack.get("status") or "none",
            "company": pack.get("company") or "", "title": pack.get("title") or "", "error": pack.get("error") or "",
            "from_gaps": bool(pack.get("from_gaps")), "pdf_only": pack.get("pdf_only") or "",
            "gaps": list(pack.get("gaps") or []), "questions": list(pack.get("questions") or []),
            "ask_them": list(pack.get("ask_them") or []), "debrief": pack.get("debrief") or "",
            "added": int(pack.get("added") or 0), "made_at": pack.get("made_at") or "",
            "company_research": research_public(pack.get("company_research"))}


class Packs:
    """data/interview-packs/<id>.json. The lock covers a read-modify-write, never the model call,
    so a debrief saved while a pack is being made is still there when the pack lands."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.RLock()

    def path(self, jid: str) -> Path:
        return self.root / f"{safe_id(jid)}.json"

    def _read(self, jid: str) -> Optional[dict]:
        try:
            return json.loads(self.path(jid).read_text())
        except (OSError, ValueError):
            return None

    def _write(self, pack: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path(pack["job_id"])
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(pack, indent=1))
        os.replace(tmp, path)

    def get(self, jid: str) -> Optional[dict]:
        with self._lock:
            return self._read(jid)

    def save(self, pack: dict) -> dict:
        with self._lock:
            self._write(pack)
            return pack

    def finish(self, jid: str, **fields) -> dict:
        """Write the built pack. A debrief or an added-line count saved while it was making stays."""
        with self._lock:
            cur = self._read(jid) or {"job_id": jid}
            debrief, added = cur.get("debrief") or "", int(cur.get("added") or 0)
            cur.update(fields)
            cur["job_id"] = jid
            cur["debrief"] = debrief
            cur["added"] = added
            self._write(cur)
            return cur

    def begin_role(self, jid: str, **fields) -> dict:
        """Write the role pack as making. The debrief, the added-line count, and company research stay."""
        with self._lock:
            cur = self._read(jid) or {"job_id": jid}
            debrief, added = cur.get("debrief") or "", int(cur.get("added") or 0)
            research = cur.get("company_research")
            cur.update(fields)
            cur["job_id"] = jid
            cur["debrief"] = debrief
            cur["added"] = added
            if research:
                cur["company_research"] = research
            self._write(cur)
            return cur

    def begin_research(self, jid: str, *, company: str = "", title: str = "") -> tuple[dict, bool]:
        """Mark company research as making. Returns (pack, started). Started is false when a call is already going.
        Questions, stories, and the debrief are left as they are. A role with no pack yet gets a shell."""
        with self._lock:
            cur = self._read(jid)
            if not cur:
                cur = {"job_id": jid, "status": "none", "company": company, "title": title, "error": "",
                       "from_gaps": False, "pdf_only": "", "gaps": [], "questions": [], "ask_them": [],
                       "debrief": "", "added": 0, "made_at": ""}
            research = dict(cur.get("company_research") or {})
            if research.get("status") == "making":
                return cur, False
            research["status"] = "making"
            research["error"] = ""
            cur["company_research"] = research
            cur["job_id"] = jid
            self._write(cur)
            return cur, True

    def put_research(self, jid: str, **fields) -> Optional[dict]:
        """Replace company-research fields only. Questions, stories, and the debrief stay."""
        with self._lock:
            cur = self._read(jid)
            if not cur:
                return None
            research = dict(cur.get("company_research") or {})
            research.update(fields)
            cur["company_research"] = research
            self._write(cur)
            return cur

    def put_debrief(self, jid: str, text: str) -> Optional[dict]:
        with self._lock:
            cur = self._read(jid)
            if not cur:
                return None
            cur["debrief"] = text
            self._write(cur)
            return cur

    def add_lines(self, jid: str, n: int) -> Optional[dict]:
        with self._lock:
            cur = self._read(jid)
            if not cur:
                return None
            cur["added"] = int(cur.get("added") or 0) + n
            self._write(cur)
            return cur

    def summaries(self) -> list[dict]:
        with self._lock:
            out = []
            if not self.root.exists():
                return out
            for f in self.root.glob("*.json"):
                try:
                    d = json.loads(f.read_text())
                except (OSError, ValueError):
                    continue
                if d.get("job_id"):
                    out.append({k: d.get(k) or "" for k in ("job_id", "company", "title", "status", "made_at")})
            out.sort(key=lambda r: r["made_at"], reverse=True)
            return out

"""The setup wizard's two helpers: reading a LinkedIn export, and interviewing the candidate about a past job.

LinkedIn hides most of a public profile and blocks scraping, so a profile address alone gives nothing to read. Two
exports do carry the whole profile, and the wizard takes either:

linkedin_zip()   the data export (Settings › Data privacy › Get a copy of your data): Positions.csv, Profile.csv,
                 Skills.csv, Education.csv, Email Addresses.csv and PhoneNumbers.csv, read with no AI.
linkedin_pdf()   the profile's More › Save to PDF, read from its text. Its layout is fixed, so this also needs no AI;
                 a PDF it can't place goes to resume_import.shape() like any résumé.

Both give résumé markdown in the app's shape, so the same review as "Compare a résumé" offers each line for the impact
record, plus the profile fields the export names (to fill blanks on the Profile, never to overwrite).

The interview asks one question at a time about one role, through the candidate's own AI (the chat model), then
drafts impact-record lines from the answers only. A drafted figure the answers don't contain is flagged and left
unticked. Nothing reaches the record until the candidate adds it (record_import.apply, "From your interviews").
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

from . import evidence
from .llm import AgentCall, Runner
from .config import ModelCfg

MAX_ZIP = 50 * 1024 * 1024          # the export with messages can be large; only a few small files are read
MAX_CSV = 5 * 1024 * 1024
QUESTIONS = 6                       # the most an interview asks before it drafts
INTERVIEW_SECTION = "From your interviews"
INTERVIEW_INTRO = ("Lines from the setup interviews, each put here after you checked it. They count as evidence like "
                   "the rest of this record; edit or move them as you like.")


class ExportError(ValueError):
    """Why an export can't be read, in words the candidate can act on."""


@dataclass
class Position:
    company: str
    title: str
    start: str = ""
    end: str = ""                    # "" while it's current
    location: str = ""
    lines: list[str] = field(default_factory=list)

    @property
    def dates(self) -> str:
        if not self.start:
            return self.end
        return f"{self.start}–{self.end or 'Present'}"


@dataclass
class Profile:
    name: str = ""
    headline: str = ""
    city: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    summary: str = ""
    positions: list[Position] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)

    def fields(self) -> dict:
        """What the Profile form can take from it."""
        return {k: getattr(self, k) for k in ("name", "city", "email", "phone", "linkedin") if getattr(self, k)}

    def roles(self) -> list[dict]:
        return [{"company": p.company, "title": p.title, "dates": p.dates, "location": p.location,
                 "lines": len(p.lines)} for p in self.positions]


def markdown(p: Profile) -> str:
    """The profile as résumé markdown in the app's shape (resume_import.SHAPE)."""
    contact = " | ".join(x for x in (p.city, p.phone, p.email, p.linkedin) if x)
    out = [f"# {p.name or 'Your Name'}", f"**{p.headline or 'LinkedIn profile'}**", contact or "LinkedIn", ""]
    if p.summary:
        out += ["## SUMMARY", p.summary, ""]
    out += ["## PROFESSIONAL EXPERIENCE", ""]
    for pos in p.positions:
        where = " | ".join(x for x in (pos.dates, pos.location) if x)
        out += [f"**{pos.company}** | {pos.title}"] + ([f"*{where}*"] if where else []) + [""]
        out += [f"- {l}" for l in pos.lines] + ([""] if pos.lines else [])
    if p.skills:
        out += ["## CORE SKILLS", "Hard Skills: " + " | ".join(p.skills), ""]
    if p.education:
        out += ["## EDUCATION", *p.education, ""]
    return "\n".join(out).rstrip() + "\n"


def paragraphs(text: str) -> list[str]:
    """A description as its items: a bullet starts one, a blank line ends one, and a line that wraps (no closing
    punctuation before it) joins the one before."""
    items: list[str] = []
    joinable = False
    for raw in text.replace("\r", "").split("\n"):
        ln = raw.strip()
        if not ln:
            joinable = False
            continue
        bullet = re.match(r"^[•●▪◦‣∙·*–—-]\s*", ln)
        ln = ln[bullet.end():] if bullet else ln
        if not ln:
            continue
        if joinable and not bullet and not re.search(r"[.!?:;]$", items[-1]):
            items[-1] += " " + ln
        else:
            items.append(ln)
        joinable = True
    return [re.sub(r"\s+", " ", i) for i in items]


# ---- the data export (a ZIP of CSV files) ----------------------------------------------------------------
def _rows(z: zipfile.ZipFile, name: str, key: str) -> list[dict]:
    """The rows of one CSV in the export, found by its file name in any folder. Some files open with notes
    before the header, so the header is the first line naming `key`."""
    info = next((i for i in z.infolist() if i.filename.rsplit("/", 1)[-1].lower() == name.lower()), None)
    if info is None:
        return []
    if info.file_size > MAX_CSV:
        raise ExportError(f"{name} in that file is too large to be a LinkedIn export.")
    text = z.read(info).decode("utf-8-sig", errors="replace")
    lines = text.splitlines(keepends=True)
    start = next((k for k, l in enumerate(lines) if key.lower() in l.lower()), None)
    if start is None:
        return []
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()}
            for r in csv.DictReader(io.StringIO("".join(lines[start:])))]


def _month(s: str) -> str:
    """'Jan 2020', '2020-01' or '01/2020' as the shape writes it: Jan 2020."""
    names = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    s = s.strip()
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", s):
        return f"{names[int(m.group(2)) - 1]} {m.group(1)}" if 1 <= int(m.group(2)) <= 12 else m.group(1)
    if m := re.fullmatch(r"(\d{1,2})/(\d{4})", s):
        return f"{names[int(m.group(1)) - 1]} {m.group(2)}" if 1 <= int(m.group(1)) <= 12 else m.group(2)
    if m := re.fullmatch(r"([A-Za-z]{3})[a-z]*\.? (\d{4})", s):
        return f"{m.group(1).title()} {m.group(2)}"
    return s


def linkedin_zip(data: bytes) -> Profile:
    if len(data) > MAX_ZIP:
        raise ExportError(f"That file is {len(data) / 1e6:.0f} MB. Ask LinkedIn for the smaller export "
                          "(Download larger data archive, unticked) or save your profile as a PDF instead.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ExportError("That isn't a ZIP file. Add the LinkedIn export as LinkedIn sent it, or a profile PDF.") from None
    with z:
        positions = _rows(z, "Positions.csv", "Company Name")
        if not positions and not _rows(z, "Profile.csv", "First Name"):
            raise ExportError("That ZIP has no Positions.csv or Profile.csv, so it isn't a LinkedIn data export.")
        prof = (_rows(z, "Profile.csv", "First Name") or [{}])[0]
        p = Profile(name=" ".join(x for x in (prof.get("First Name", ""), prof.get("Last Name", "")) if x),
                    headline=prof.get("Headline", ""), summary=" ".join(paragraphs(prof.get("Summary", ""))),
                    city=_city(prof.get("Geo Location", "")))
        for r in positions:
            if not (r.get("Company Name") or r.get("Title")):
                continue
            p.positions.append(Position(company=r.get("Company Name", ""), title=r.get("Title", ""),
                                        start=_month(r.get("Started On", "")), end=_month(r.get("Finished On", "")),
                                        location=r.get("Location", ""), lines=paragraphs(r.get("Description", ""))))
        p.skills = [r["Name"] for r in _rows(z, "Skills.csv", "Name") if r.get("Name")][:40]
        for r in _rows(z, "Education.csv", "School Name"):
            years = "–".join(x for x in (r.get("Start Date", "")[:4], r.get("End Date", "")[:4]) if x)
            line = ", ".join(x for x in (r.get("Degree Name", ""), r.get("School Name", "")) if x)
            if line:
                p.education.append(line + (f" ({years})" if years else ""))
        emails = _rows(z, "Email Addresses.csv", "Email Address")
        primary = next((r for r in emails if r.get("Primary", "").lower() == "yes"), emails[0] if emails else {})
        p.email = primary.get("Email Address", "")
        p.phone = next((r.get("Number", "") for r in _rows(z, "PhoneNumbers.csv", "Number") if r.get("Number")), "")
    return p


def zip_text(p: Profile) -> str:
    """What the export said, as plain text: the source the review checks the markdown against."""
    out = [p.name, p.headline, p.city, p.email, p.phone, p.summary]
    for pos in p.positions:
        out += [pos.company, pos.title, pos.start, pos.end, pos.location, *pos.lines]
    return "\n".join(x for x in out + p.skills + p.education if x)


def _city(geo: str) -> str:
    """'Seattle, Washington, United States' -> 'Seattle, Washington'; LinkedIn's region names stay as they are."""
    parts = [x.strip() for x in geo.split(",") if x.strip()]
    if len(parts) >= 3 and parts[-1] in ("United States", "USA", "US"):
        parts = parts[:-1]
    return ", ".join(parts[:2])


# ---- Save to PDF ---------------------------------------------------------------------------------------
MONTHS = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
WHEN = rf"(?:{MONTHS} )?\d{{4}}"
DATE_LINE = re.compile(rf"^({WHEN})\s*[-–—]\s*({WHEN}|Present)\s*(?:\(([^)]*)\))?$")
DURATION = re.compile(r"^(?:(\d+) years?)?\s*(?:(\d+) months?)?$")
SIDEBAR = {"contact", "top skills", "languages", "certifications", "honors-awards", "publications", "patents",
           "courses", "projects", "volunteer experience"}
MAIN = {"summary", "experience", "education"}
PAGE = re.compile(r"^Page \d+ of \d+$")


def looks_like_linkedin(text: str) -> bool:
    lines = {l.strip().lower() for l in text.splitlines()}
    return "experience" in lines and ("contact" in lines or "top skills" in lines) and bool(
        re.search(r"linkedin\.com/in/|\(LinkedIn\)|Page 1 of \d", text))


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _months(s: str) -> int:
    m = DURATION.match(s.strip())
    return (int(m.group(1) or 0) * 12 + int(m.group(2) or 0)) if m and s.strip() else 0


def _ym(w: str) -> int:
    """A LinkedIn date as months since year 0 (a bare year is its January)."""
    if w == "Present":
        return 10 ** 6
    parts = w.split()
    names = "January February March April May June July August September October November December".split()
    return int(parts[-1]) * 12 + (names.index(parts[0]) if len(parts) == 2 else 0)


def _short(w: str) -> str:
    return w if w == "Present" or " " not in w else f"{w[:3]} {w.split()[-1]}"


def linkedin_pdf(text: str) -> Profile:
    """The profile in a Save to PDF file's text. The sidebar (Contact, Top Skills…) comes first, then the name,
    headline and place, then Summary, Experience and Education. In Experience a role is its title over its date line;
    a company with several roles names itself once, over its total time there."""
    lines = [l.strip() for l in text.replace("\xa0", " ").splitlines()]
    lines = [l for l in lines if l and not PAGE.match(l)]
    low = [l.lower() for l in lines]
    p = Profile()
    if m := re.search(r"(?:www\.)?linkedin\.com/in/[\w%-]+", text):
        p.linkedin = m.group(0).removeprefix("www.")
    if m := re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text):
        p.email = m.group(0)
    head_at = next((k for k, l in enumerate(low) if l in MAIN), len(lines))
    # The skills: the lines under Top Skills, up to the next sidebar heading.
    if "top skills" in low[:head_at]:
        k = low.index("top skills") + 1
        while k < head_at and low[k] not in SIDEBAR and len(p.skills) < 5:
            p.skills.append(lines[k])
            k += 1
    # Name, headline, place: from the main heading back. The place has commas or "Area"; the headline is long.
    block = lines[:head_at]
    k = len(block) - 1
    if k >= 0 and ("," in block[k] or block[k].endswith("Area")) and len(block[k]) < 60:
        p.city, k = _city(block[k]), k - 1
    # The name: the line whose words are the profile address's (linkedin.com/in/jordan-rivera), else the line over
    # the headline, which is long or wraps onto a short second line.
    slug = set(re.findall(r"[a-z]+", p.linkedin.rsplit("/", 1)[-1].lower())) if p.linkedin else set()
    named = next((j for j in range(k, -1, -1) if slug and low[j] not in SIDEBAR
                  and set(re.findall(r"[a-z]+", _ascii(low[j]))) <= slug | {""} and len(block[j].split()) >= 2), None)
    if named is None:
        named = k
        while named > 0 and (len(block[named]) > 30 or "|" in block[named] or " at " in block[named]
                             or len(block[named - 1]) > 30 and not re.search(r"[.!?]$", block[named - 1])):
            named -= 1
    if 0 <= named <= k and low[named] not in SIDEBAR and not re.search(r"[@/\d]", block[named]) \
            and 1 < len(block[named].split()) <= 5:
        p.name, p.headline = block[named], " ".join(block[named + 1:k + 1])
    sections: dict[str, list[str]] = {}
    current = ""
    for l, lw in zip(lines[head_at:], low[head_at:]):
        if lw in MAIN:
            current = lw
            sections.setdefault(current, [])
        elif current:
            sections[current].append(l)
    p.summary = " ".join(paragraphs("\n".join(sections.get("summary", []))))
    p.positions = _experience(sections.get("experience", []))
    edu = sections.get("education", [])
    for k, l in enumerate(edu):
        if "·" in l or re.search(r"\(\d{4}", l):
            school = edu[k - 1] if k else ""
            p.education.append(", ".join(x for x in (l.replace(" · ", " ").strip(), school) if x))
    return p


def _experience(lines: list[str], today: Optional[date] = None) -> list[Position]:
    """The roles in Experience. A role is its title over its date line, then its place and description. A company
    with one role names itself over the title; one with several names itself once, over its total time there, and
    the roles under it follow with no company line, until their dates fill that time."""
    today = today or date.today()
    now = today.year * 12 + today.month - 1
    dates = [k for k, l in enumerate(lines) if DATE_LINE.match(l) and k]
    found = []                                # (date line, where its heading starts, company)
    group, group_start = "", 0
    for k in dates:
        m = DATE_LINE.match(lines[k])
        start, end = _ym(m.group(1)), (now if m.group(2) == "Present" else _ym(m.group(2)))
        over = k - 2                          # the line over the title
        if over >= 1 and _months(lines[over]) and not DATE_LINE.match(lines[over]):
            group, group_start = lines[over - 1], end - _months(lines[over]) + 1
            found.append((k, over - 1, group))
        elif group:
            found.append((k, k - 1, group))
        else:
            found.append((k, max(over, 0) if over >= 0 and not DATE_LINE.match(lines[over]) else k - 1,
                          lines[over] if over >= 0 and not DATE_LINE.match(lines[over]) else ""))
        if group and start <= group_start + 1:
            group = ""                        # its roles now fill the company's time: the next role is elsewhere
    out: list[Position] = []
    for n, (k, _, company) in enumerate(found):
        m = DATE_LINE.match(lines[k])
        body = lines[k + 1:found[n + 1][1] if n + 1 < len(found) else len(lines)]
        location = ""
        if body and len(body[0]) < 70 and not re.search(r"[.!?]$", body[0]) and (
                "," in body[0] or body[0].endswith(("Area", "Remote")) or body[0] in ("Remote", "Hybrid", "On-site")):
            location, body = body[0], body[1:]
        out.append(Position(company=company, title=lines[k - 1], start=_short(m.group(1)),
                            end="" if m.group(2) == "Present" else _short(m.group(2)), location=location,
                            lines=paragraphs("\n".join(body))))
    return out


# ---- the interview -------------------------------------------------------------------------------------
ASK_BRIEF = """You are interviewing {first} about one past job, to build {first}'s impact record: the evidence every
résumé the app writes is drawn from. You ask one question at a time, like a good career coach: short, specific,
friendly, and never more than one question per turn.

Aim for what a résumé needs and the record lacks for this role: the scope (team, budget, users, systems),
what {first} owned personally, results with numbers (before and after, money, time, scale, quality), one hard
problem and how it was solved, and the tools and methods used. Read the impact record and résumés in your materials
first and don't ask for what they already say about this role; build on the earlier answers instead. When an answer is
vague, ask for the figure or the example. If {first} doesn't know a number, accept an estimate and say it will be
marked as one.

Write reply.md: only your next question (one or two sentences, no preamble). If the answers so far already cover
this role well, write exactly DONE instead."""

DRAFT_BRIEF = """You interviewed {first} about one past job. Now write the lines it adds to {first}'s impact record.

Write lines.md: a bullet list ("- " each), 3 to 8 lines, each one result or responsibility in {first}'s own
facts, in résumé style (an action verb first, no "I", past tense for past roles). Use only what the answers and
the role's LinkedIn text say: never add a number, tool, team size or outcome they don't state. Keep each figure
exactly as {first} gave it, and keep "about" or "roughly" when the answer said it. Leave out anything the impact
record and résumés in your materials already say. If the answers give nothing new, write "- none"."""


@dataclass
class Turn:
    q: str
    a: str = ""


def _role_doc(role: dict) -> str:
    out = [f"Company: {role.get('company', '')}", f"Title: {role.get('title', '')}"]
    if role.get("dates"):
        out.append(f"Dates: {role['dates']}")
    if role.get("notes"):
        out.append("What LinkedIn or the résumé says about it:\n" + str(role["notes"])[:4000])
    return "\n".join(out)


def _convo(turns: list[Turn], first: str) -> str:
    return "\n\n".join(f"You: {t.q}\n{first}: {t.a or '(no answer yet)'}" for t in turns) or "(no questions yet)"


async def ask(runner: Runner, model: ModelCfg, role: dict, turns: list[Turn], first: str, brief: str) -> Optional[str]:
    """The next question, or None when the interview has enough (or has asked QUESTIONS already)."""
    if len([t for t in turns if t.a.strip()]) >= QUESTIONS:
        return None
    res = await runner.run(AgentCall(
        label="setup-interview", model=model, instructions=brief, documents={"role.md": _role_doc(role)},
        expect=["reply.md"], prose_file="reply.md",
        tail=f"The interview so far:\n{_convo(turns, first)}"))
    q = res.files["reply.md"].strip().strip('"').strip()
    return None if not q or q.upper().rstrip(".") == "DONE" else q


async def draft(runner: Runner, model: ModelCfg, role: dict, turns: list[Turn], first: str, brief: str) -> list[dict]:
    """The lines the answers support, each {"text", "ok", "why"}. A line with a figure the answers and the role's
    text don't have is flagged (not ok), so the candidate checks it before adding."""
    answered = [t for t in turns if t.a.strip()]
    if not answered:
        return []
    res = await runner.run(AgentCall(
        label="setup-interview-draft", model=model, instructions=brief, documents={"role.md": _role_doc(role)},
        expect=["lines.md"], prose_file="lines.md", tail=f"The interview:\n{_convo(answered, first)}"))
    said = " ".join(t.a for t in answered) + " " + str(role.get("notes") or "")
    have = set(evidence.figures(said))
    out = []
    for raw in res.files["lines.md"].splitlines():
        text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw).strip()
        if not text or text.lower().rstrip(".") == "none" or not raw.lstrip().startswith(("-", "*", "•")) and not re.match(r"^\s*\d+[.)]", raw):
            continue
        missing = [f for f in evidence.figures(text) if f not in have]
        out.append({"text": text, "ok": not missing,
                    "why": (f"{', '.join(missing)} isn't in your answers: check it." if missing else "")})
    return out


def turns_from(data: list) -> list[Turn]:
    return [Turn(q=str(t.get("q") or "")[:2000], a=str(t.get("a") or "")[:6000]) for t in data or [] if isinstance(t, dict)]


def turns_json(turns: list[Turn]) -> list[dict]:
    return [asdict(t) for t in turns]

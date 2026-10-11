"""What a résumé says that the impact record doesn't, offered as suggestions the candidate accepts one by one.

The impact record is what every résumé is built from, so an uploaded résumé's roles and results matter most there.
Records aren't laid out one way (by role, by project, by theme), so this never edits the candidate's own sections:
suggest() checks each résumé line against the whole record, and apply() writes an accepted line to one section the
app keeps at the end, "# From your résumés", under a heading per role. Nothing is added without a click.

A line is covered when its figures are all in the record and either a record passage shares most of its words or,
with the local evidence models, the nearest passages entail it (evidence.py's NLI model). Covered lines aren't
offered; lines the record has with less detail are offered unticked; the rest are offered ticked. A role whose
company the record never names is offered whole, as a new role.

The models take gigabytes, so the web app runs this in a process of its own that ends when it's done:
    python -m jobpipe.record_import resume.md --record impact-record.md     (JSON on stdout)
JOBPIPE_EVIDENCE_MODELS=off, or `evidence: enabled: false` in searches.yaml, checks words and figures only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from . import evidence

SECTION = "From your résumés"
SECTION_HEADING = re.compile(rf"^#\s+{re.escape(SECTION)}\s*#*\s*$", re.I)
INTRO = ("Lines from résumés you added, each put here after you checked it. They count as evidence like the rest of "
         "this record; edit or move them as you like.")
ROLE_SECTIONS = {"PROFESSIONAL EXPERIENCE", "EXPERIENCE", "ADDITIONAL EXPERIENCE", "WORK EXPERIENCE", "WORK HISTORY"}
COVERED, DETAIL = 0.7, 0.4       # share of a line's words found in one record passage
WITHIN = 0.6                     # share of a passage's words found in the line: the line is that passage, with more
ENTAILED = 0.5                   # NLI probability that the nearest passages state the line
STOP = set("""a an and are as at be been but by for from has have in into is it its of on or our over that the their
them they this to was were which while with within across after before during per than then through up via""".split())


@dataclass
class Line:
    text: str
    status: str = "new"          # new | detail | covered
    why: str = ""
    overlap: float = 0.0         # the most of its words found in one record passage
    within: float = 0.0          # the most of one record passage's words found in it
    entailed: Optional[float] = None


@dataclass
class Role:
    company: str
    title: str
    dates: str
    lines: list[Line] = field(default_factory=list)
    known: bool = True           # the record names the company
    id: str = ""


def roles(md: str) -> list[Role]:
    """Each role in a résumé's experience sections, with its bullets and plain lines."""
    out: list[Role] = []
    section, role = "", None
    for raw in md.splitlines():
        ln = raw.strip()
        if ln.startswith("## "):
            section, role = ln[3:].strip().upper(), None
            continue
        if section not in ROLE_SECTIONS or not ln or ln == "---":
            continue
        m = re.match(r"^\*\*(.+?)\*\*\s*\|\s*(.+)$", ln)
        if m:
            role = Role(m.group(1).strip(), m.group(2).strip(), "")
            out.append(role)
        elif role and re.fullmatch(r"\*[^*].*\*", ln) and not role.dates and not role.lines:
            role.dates = ln.strip("*").split("|")[0].strip()
        elif role and ln.startswith("- "):
            role.lines.append(Line(ln[2:].strip()))
        elif role and role.lines and raw.startswith("  "):
            role.lines[-1].text += " " + ln               # a bullet's wrapped line
        elif role:
            role.lines.append(Line(ln))
    for k, r in enumerate(out):
        r.id = f"r{k}"
    return [r for r in out if r.lines]


def _stem(w: str) -> str:
    """A light English stem, so "services" meets "service" and "migrated" meets "migrating"."""
    if len(w) > 5 and w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("sses", "xes", "zes", "ches", "shes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    for end in ("ing", "ed", "e"):
        if w.endswith(end) and len(w) - len(end) >= 4:
            return w[: -len(end)]
    return w


def words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower().replace("’", "'"))
            if (len(w) > 2 or w.isdigit()) and w not in STOP}


def _plain(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def suggest(record_md: str, resume_md: str, models=None) -> list[Role]:
    """The résumé's roles, each line marked new, detail (the record has it with less) or covered."""
    passages = evidence.split_markdown("Impact record", record_md)
    pwords = [words(p.text) for p in passages]
    record_plain = f" {_plain(record_md)} "
    record_figs = set(evidence.figures(record_md))
    out = roles(resume_md)
    flat = [(r, l) for r in out for l in r.lines]
    entail: dict[int, float] = {}
    if models is not None and passages and flat:
        vecs = models.embed([p.doc for p in passages])
        q = models.embed([l.text for _, l in flat], evidence.LINE_SEARCH)
        import numpy as np
        near = [list(np.argsort(-row)[:5]) for row in q @ vecs.T]
        pairs = [(passages[int(i)].text, l.text) for (_, l), idx in zip(flat, near) for i in idx]
        scores = models.entail(pairs)
        for k in range(len(flat)):
            entail[k] = max(scores[k * 5:(k + 1) * 5] or [0.0])
    for k, (role, line) in enumerate(flat):
        lw = words(line.text)
        line.overlap = round(max((len(lw & p) / len(lw) for p in pwords), default=0.0) if lw else 1.0, 2)
        line.within = round(max((len(lw & p) / len(p) for p in pwords if len(p) >= 4), default=0.0), 2)
        line.entailed = round(entail[k], 2) if k in entail else None
        missing = [f for f in evidence.figures(line.text) if f not in record_figs]
        if missing:
            said = missing[0] if len(missing) == 1 else ", ".join(missing[:-1]) + " and " + missing[-1]
            line.status, line.why = "new", f"{said} {'is' if len(missing) == 1 else 'are'}n't in your record."
        elif (line.entailed is not None and line.entailed >= ENTAILED) or line.overlap >= COVERED:
            line.status, line.why = "covered", "Already in your record."
        elif line.overlap >= DETAIL or line.within >= WITHIN:
            line.status, line.why = "detail", "Your record has this, with less detail."
        else:
            line.status, line.why = "new", "Not in your record."
    for role in out:
        name = _plain(role.company)
        role.known = bool(name) and f" {name} " in record_plain
    return out


def apply(record_md: str, company: str, title: str, dates: str, lines: list[str], source: str, day: date) -> str:
    """The record with these lines added under the role's heading in the "From your résumés" section (both made
    when they aren't there yet). A line already there isn't added twice."""
    clean = [re.sub(r"\s+", " ", l).strip() for l in lines if l and l.strip()]
    head = f"## {company}, {title}" + (f" ({dates})" if dates else "")
    note = f"<!-- added from {source.replace('--', '-')}, {day:%b} {day.day}, {day.year} -->"
    md = record_md.rstrip("\n")
    lines_ = md.split("\n")
    start = next((i for i, l in enumerate(lines_) if SECTION_HEADING.match(l)), None)
    if start is None:
        return md + f"\n\n# {SECTION}\n\n{INTRO}\n\n{head}\n\n{note}\n" + "".join(f"- {l}\n" for l in clean)
    end = next((i for i in range(start + 1, len(lines_)) if re.match(r"^#\s", lines_[i])), len(lines_))
    sec = lines_[start:end]
    key = _plain(f"{company} {title}")
    at = next((i for i, l in enumerate(sec) if l.startswith("## ") and _plain(l[3:]).startswith(key)), None)
    if at is None:
        while sec and not sec[-1].strip():
            sec.pop()
        sec += ["", head, "", note, *(f"- {l}" for l in clean)]
    else:
        stop = next((i for i in range(at + 1, len(sec)) if sec[i].startswith("#")), len(sec))
        have = {_plain(l[2:]) for l in sec[at:stop] if l.startswith("- ")}
        new = [l for l in clean if _plain(l) not in have]
        if not new:
            return record_md
        last = stop
        while last > at + 1 and not sec[last - 1].strip():
            last -= 1
        sec[last:last] = [note, *(f"- {l}" for l in new)]
    after = lines_[end:]
    return "\n".join([*lines_[:start], *sec, *([""] + after if after else [])]).rstrip("\n") + "\n"


def payload(found: list[Role], used_models: bool, reason: str = "") -> dict:
    return {"models": used_models, "reason": reason, "roles": [asdict(r) for r in found]}


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="What a résumé says that the impact record doesn't (JSON on stdout).")
    ap.add_argument("resume", help="the résumé markdown, or - for stdin")
    ap.add_argument("--record", required=True)
    ap.add_argument("--no-models", action="store_true")
    a = ap.parse_args(argv)
    resume = sys.stdin.read() if a.resume == "-" else Path(a.resume).read_text()
    record = Path(a.record).read_text()
    models, reason = None, ""
    if a.no_models or os.environ.get("JOBPIPE_EVIDENCE_MODELS") == "off":
        reason = "switched off"
    else:
        from . import config
        try:
            models = evidence.load(config.load(config.searches_path()).evidence)
        except Exception as e:  # noqa: BLE001 - Unavailable, a bad config: words and figures only
            reason = str(e)
    print(json.dumps(payload(suggest(record, resume, models), models is not None, reason)))


if __name__ == "__main__":
    main()

"""Résumés made outside the app, shown for a tracked job.

The resume-job-fit skill (run in a Claude chat) leaves its work in `resume-runs/<company>-<role>/`,
and finished résumés kept for reuse live in `references/resume-*.md` (both in the profile folder). A tracked job that
the app never tailored can show one of these: the job's marks remember which (`resume_source`),
or, until one is picked, the source whose name matches the job's company and title.

The chosen résumé is copied into `data/linked/<job>/` (with the run's posting and match brief,
when it has them), so the app's version history and edits never write into the repo.

A résumé uploaded for one job (read into markdown by resume_import.py) is that job's own source, kept in
`data/uploads/<job>/` with the file it came from; it is offered for that job only, under the key "upload".
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

from .. import config

# Files copied from a skill run folder, so the résumé editor and checks have what they read.
RUN_FILES = ("resume-final.md", "jd.md", "04-match.md", "01-objectives.md", "keywords.json", "uploaded-from.txt",
             "ratings.json", "ratings-final.json")
# What scoring an upload copies in from the role's full score: the posting, its title, the match brief, the
# keywords and the requirement rows (the upload's own ratings follow, in ratings-final.json).
ANALYSIS_FILES = ("jd.md", "01-objectives.md", "04-match.md", "keywords.json", "ratings.json")
ABBREV = {"tpm": "technical program manager", "pm": "product manager", "em": "engineering manager",
          "sr": "senior", "eng": "engineering", "mgr": "manager"}
STOP = {"and", "of", "the", "for", "a", "an", "to", "in", "at", "resume", "final"}
NONE = "none"   # the job's marks say: show no outside résumé, even if one matches


def tokens(text: str) -> set[str]:
    text = re.sub(r"ci\s*/\s*cd", "cicd", (text or "").lower())
    words = re.findall(r"[a-z0-9]+", text)
    out: set[str] = set()
    for w in words:
        out.update(ABBREV.get(w, w).split())
    return out - STOP


def _headline(md: Path) -> str:
    """The résumé's second line, its target title: '**Program Manager, Quality**'."""
    try:
        lines = [l.strip() for l in md.read_text().splitlines()[:4] if l.strip()]
    except OSError:
        return ""
    return lines[1].strip("*# ").split("|")[0].strip() if len(lines) > 1 else ""


def _is_resume_md(md: Path) -> bool:
    try:
        first = next((l.strip() for l in md.read_text().splitlines() if l.strip()), "")
    except OSError:
        return False
    return first.startswith("# ") and "rules" not in first.lower()


def sources(root: Optional[Path] = None) -> list[dict]:
    """Every résumé the app can show: skill runs first, then the reference résumés (in the profile folder)."""
    root = root or config.PROFILE
    out = []
    for d in sorted((root / "resume-runs").glob("*/")):
        md = d / "resume-final.md"
        if md.exists():
            out.append({"key": f"run:{d.name}", "label": f"{d.name} (skill run)", "headline": _headline(md),
                        "path": d, "kind": "run"})
    for md in sorted((root / "references").glob("resume-*.md")):
        # Only résumés in the markdown shape the PDF renderer reads ("# Your Name" first);
        # the original plain-text variants and the rules file aren't.
        if not _is_resume_md(md):
            continue
        out.append({"key": f"ref:{md.stem}", "label": f"{md.name} (saved résumé)", "headline": _headline(md),
                    "path": md, "kind": "ref"})
    return out


def rank(title: str, company: str, srcs: list[dict]) -> list[tuple[float, dict]]:
    """Sources by how well their name matches the job, best first, each with a 0-1 score: 0 unless
    the company is in the source's name, then the share of the name's other words found in the title
    (1.0 when every word of "acme-staff-tpm" is in the job's title)."""
    want_co, want_title = tokens(company), tokens(title)
    scored = []
    for s in srcs:
        have = tokens(s["key"].split(":", 1)[1])
        role = have - want_co
        ok = bool(want_co) and want_co <= have and role
        scored.append((round(len(role & want_title) / len(role), 3) if ok else 0.0, s))
    scored.sort(key=lambda x: -x[0])
    return scored


def best_match(title: str, company: str, srcs: list[dict]) -> Optional[dict]:
    """The one source whose whole name matches the job, if exactly one does."""
    full = [s for score, s in rank(title, company, srcs) if score == 1.0]
    return full[0] if len(full) == 1 else None


UPLOAD = "upload"   # the source key of the résumé uploaded for this job


def job_dir(kind: str, jid: str, data: Optional[Path]) -> Path:
    return (data or config.DATA) / kind / re.sub(r"[^a-zA-Z0-9_-]", "_", jid)


def upload_source(jid: str, data: Optional[Path] = None) -> Optional[dict]:
    """The résumé uploaded for this job, if there is one."""
    d = job_dir("uploads", jid, data)
    md = d / "resume-final.md"
    if not md.exists():
        return None
    name = (d / "uploaded-from.txt").read_text().strip() if (d / "uploaded-from.txt").exists() else "your file"
    return {"key": UPLOAD, "label": f"{name} (uploaded for this role)", "headline": _headline(md), "path": d, "kind": "run"}


def save_upload(jid: str, md: str, filename: str, original: Optional[Path] = None,
                data: Optional[Path] = None) -> dict:
    """Keep an uploaded résumé as the job's own source (replacing an earlier upload), with the file it was read from."""
    d = job_dir("uploads", jid, data)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / "resume-final.md").write_text(md.rstrip("\n") + "\n", encoding="utf-8")
    (d / "uploaded-from.txt").write_text(Path(filename).name + "\n", encoding="utf-8")
    if original and original.exists():
        shutil.copy(original, d / original.name)
    return upload_source(jid, data)


def workspace_dir(jid: str, src: dict, data: Optional[Path] = None) -> Path:
    """The job's copy of the source, made (or remade, if the job now points elsewhere) on first use."""
    d = job_dir("linked", jid, data)
    marker = d / "source.txt"
    if marker.exists() and marker.read_text().strip() == src["key"] and (d / "resume-final.md").exists():
        return d
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    if src["kind"] == "run":
        for name in RUN_FILES:
            if (src["path"] / name).exists():
                shutil.copy(src["path"] / name, d / name)
    else:
        shutil.copy(src["path"], d / "resume-final.md")
    marker.write_text(src["key"] + "\n")
    return d

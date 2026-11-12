#!/usr/bin/env python3
"""Check that a commit's files hold nothing personal. This repo is public, so tools/ci/checks.sh runs it on every PR.

Every text file is checked for the profile's personal details: the candidate's name, phone, email, LinkedIn and
city, the Notion IDs, and each line of <profile>/publish-deny.txt (employers, projects, anything else that must
not go public). The terms, and any exceptions (publish-allow.txt), come from the profile folder on the Mac running
the check, never from this repo.

    .venv/bin/python tools/privacy_check.py              # HEAD
    .venv/bin/python tools/privacy_check.py --rev <rev>  # any commit; exit 1 and the matching lines if any
"""
from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobpipe import config  # noqa: E402

NAME_ALLOWED_IN: set[str] = set()


def allowed_terms() -> dict[str, set[str]]:
    """Deny-list terms a file may hold anyway, from <profile>/publish-allow.txt: lines of "path: term, term".

    For a file that needs a term for its own reasons (a list of countries holds every country). The exceptions live
    in the profile folder with the terms, since naming one here would publish it.
    """
    out: dict[str, set[str]] = {}
    path = config.PROFILE / "publish-allow.txt"
    for line in path.read_text().splitlines() if path.exists() else []:
        rel, sep, terms = line.partition(":")
        if sep and not line.startswith("#"):
            out.setdefault(rel.strip(), set()).update(x.strip().lower() for x in terms.split(",") if x.strip())
    return out


def deny_terms() -> tuple[list[str], str]:
    """Phrases that must not be public, and the candidate's name."""
    import yaml
    path = config.searches_path()
    raw = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
    cand, notion = raw.get("candidate") or {}, raw.get("notion") or {}
    terms = [str(cand.get(k) or "") for k in ("phone", "email", "linkedin", "city")]
    terms += [re.sub(r"^\+?1[ -]?", "", str(cand.get("phone") or ""))]          # the phone without +1
    for k in ("data_source_id", "project_page_id"):
        v = str(notion.get(k) or "")
        terms += [v, v.replace("-", "")]
    extra = config.PROFILE / "publish-deny.txt"
    if extra.exists():
        terms += [l.strip() for l in extra.read_text().splitlines() if l.strip() and not l.startswith("#")]
    return sorted({t for t in terms if len(t) >= 3}), str(cand.get("name") or "")


def pattern(term: str) -> re.Pattern:
    edge = lambda c: r"\b" if c.isalnum() else ""
    return re.compile(edge(term[0]) + re.escape(term) + edge(term[-1]), re.I)


def scan(tree: Path, terms: list[str], name: str, allowed: dict[str, set[str]] | None = None) -> list[str]:
    allowed = allowed or {}
    pats = [(t, pattern(t)) for t in terms]
    name_pat = pattern(name) if name else None
    hits = []
    for f in sorted(p for p in tree.rglob("*") if p.is_file()):
        data = f.read_bytes()
        if b"\0" in data[:4096]:
            continue                                                        # fonts, images
        rel = f.relative_to(tree).as_posix()
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            found = [t for t, p in pats if p.search(line) and t.lower() not in allowed.get(rel, ())]
            if name_pat and rel not in NAME_ALLOWED_IN and name_pat.search(line):
                found.append(name)
            if found:
                hits.append(f"{rel}:{n}: {', '.join(found)}")
    return hits


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rev", default="HEAD")
    a = ap.parse_args()
    archive = subprocess.run(["git", "archive", a.rev], cwd=ROOT, capture_output=True)
    if archive.returncode:
        sys.exit(f"git archive {a.rev} failed:\n{archive.stderr.decode().strip()}")
    tree = Path(tempfile.mkdtemp(prefix="job-oyster-privacy-"))
    try:
        tarfile.open(fileobj=io.BytesIO(archive.stdout)).extractall(tree, filter="data")
        terms, name = deny_terms()
        hits = scan(tree, terms, name, allowed_terms())
        count = sum(1 for p in tree.rglob("*") if p.is_file())
    finally:
        shutil.rmtree(tree, ignore_errors=True)
    if hits:
        print(f"Not clean: {len(hits)} line(s) hold personal details.")
        print("\n".join("  " + h for h in hits[:60]))
        sys.exit(1)
    print(f"Checked {count} files against {len(terms) + (1 if name else 0)} personal terms: clean.")


if __name__ == "__main__":
    main()

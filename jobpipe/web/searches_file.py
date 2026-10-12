"""Read and write the saved searches in searches.yaml, keeping its comments."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from .. import config, regions

FIELDS = ("id", "name", "titles", "exclude_titles", "country", "posted_within_days", "min_salary_usd",
          "remote", "work_arrangement", "locations", "seniority", "limit", "api_filters")
LIST_FIELDS = {"titles", "exclude_titles", "work_arrangement", "locations", "seniority"}
INT_FIELDS = {"posted_within_days", "min_salary_usd", "limit"}


class SearchError(ValueError):
    pass


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    # Write null as `null` (as the file has it), not as an empty value.
    y.representer.add_representer(type(None), lambda r, _: r.represent_scalar("tag:yaml.org,2002:null", "null"))
    return y


def load(path: Path) -> tuple[Any, list[dict], dict]:
    doc = _yaml().load(path.read_text())
    searches = [dict(s) for s in (doc.get("searches") or [])]
    for s in searches:
        for k in LIST_FIELDS:
            if k in s and s[k] is not None:
                s[k] = list(s[k])
        if isinstance(s.get("country"), list):
            s["country"] = list(s["country"])
    return doc, searches, dict(doc.get("defaults") or {})


def unique_id(name: str, taken: set[str]) -> str:
    """An id for a new search, from its name: 'Director, remote' -> director-remote (-2, -3 if taken)."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].rstrip("-") or "search"
    sid, n = base, 2
    while sid in taken:
        sid, n = f"{base}-{n}", n + 1
    return sid


def clean(raw: dict) -> dict:
    """Validate one search from the UI. Empty values are dropped so defaults apply."""
    out: dict[str, Any] = {}
    sid = str(raw.get("id", "")).strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", sid):
        raise SearchError("id: lowercase letters, numbers and hyphens (e.g. tpm-remote)")
    out["id"] = sid
    out["name"] = str(raw.get("name") or sid).strip()
    for k in LIST_FIELDS:
        v = raw.get(k)
        if isinstance(v, str):
            v = [x.strip() for x in v.split("\n")]
        v = [str(x).strip() for x in (v or []) if str(x).strip()]
        if v:
            out[k] = v
    if not out.get("titles"):
        raise SearchError("titles: at least one title phrase")
    for k in INT_FIELDS:
        v = raw.get(k)
        if v in (None, ""):
            continue
        try:
            out[k] = int(v)
        except (TypeError, ValueError):
            raise SearchError(f"{k}: a whole number") from None
        if out[k] < 0 or (k == "limit" and not 1 <= out[k] <= 100):
            raise SearchError(f"{k}: out of range")
    if raw.get("country"):
        try:
            out["country"] = regions.canonical(raw["country"])
        except ValueError as e:
            raise SearchError(f"Where: {e}") from None
    if raw.get("remote") is True:
        out["remote"] = True
    if out.get("remote") and out.get("work_arrangement"):
        raise SearchError("choose remote or a work arrangement such as hybrid, not both")
    if raw.get("api_filters"):
        if not isinstance(raw["api_filters"], dict):
            raise SearchError("api_filters: an object")
        out["api_filters"] = raw["api_filters"]
    return out


def _to_yaml(s: dict) -> CommentedMap:
    m = CommentedMap()
    for k in FIELDS:
        if k not in s:
            continue
        v = s[k]
        if isinstance(v, list):
            seq = CommentedSeq([DoubleQuotedScalarString(x) if isinstance(x, str) else x for x in v])
            seq.fa.set_flow_style()
            v = seq
        m[k] = v
    return m


def save(path: Path, searches: list[dict]) -> None:
    ids = [s["id"] for s in searches]
    if len(ids) != len(set(ids)):
        raise SearchError("search ids must be unique")
    doc, _, _ = load(path)
    seq = doc["searches"]
    old = {s["id"]: s for s in seq}
    seq.clear()
    for s in searches:
        m = _to_yaml(s)
        # Keep keys the UI doesn't edit (and their comments) on an existing entry.
        prev = old.get(s["id"])
        if prev is not None:
            for k in list(prev.keys()):
                if k not in FIELDS:
                    m[k] = prev[k]
        seq.append(m)
    tmp = path.with_suffix(".yaml.tmp")
    with tmp.open("w") as f:
        _yaml().dump(doc, f)
    tmp.replace(path)


# ---- the candidate (searches.yaml's `candidate:` section, edited on the Profile tab) ----------------
CANDIDATE_TEXT = ("name", "pronouns", "city", "phone", "email", "linkedin", "pdf_prefix")
PRONOUNS = ("he/him", "she/her", "they/them")
RESUME_FILE = re.compile(r"resume-[a-z0-9][a-z0-9-]*\.md")


def load_candidate(path: Path) -> Optional[dict]:
    """The `candidate:` section as plain values, with résumés as an ordered list; None if there isn't one."""
    c = _yaml().load(path.read_text()).get("candidate")
    if c is None:
        return None
    out = {k: str(c[k]) if c.get(k) is not None else "" for k in (*CANDIDATE_TEXT, "evidence")}
    out["resumes"] = [{"name": str(k), "file": str(v)} for k, v in (c.get("resumes") or {}).items()]
    out["resume_format"] = str(c.get("resume_format") or "signature")
    return out


def is_example(path: Path) -> bool:
    """The candidate is still example-profile's made-up one (`example: true`, dropped on the first save)."""
    return bool((_yaml().load(path.read_text()).get("candidate") or {}).get("example"))


def is_resume_file(name: str) -> bool:
    return bool(RESUME_FILE.fullmatch(name)) and name != "resume-rules.md"


def clean_candidate(raw: dict, references: Path) -> dict:
    """Validate the profile from the UI. Raises SearchError with a message for the form."""
    one_line = lambda k, n=160: re.sub(r"\s+", " ", str(raw.get(k) or "")).strip()[:n]
    out = {k: one_line(k) for k in CANDIDATE_TEXT}
    if not out["name"]:
        raise SearchError("Your name is required.")
    if out["pronouns"] not in PRONOUNS:
        raise SearchError("Pronouns: choose he/him, she/her or they/them.")
    li = re.sub(r"^(https?://)?(www\.)?", "", out["linkedin"]).rstrip("/")
    if li and "linkedin.com/in/" not in li:
        raise SearchError("LinkedIn: use your profile's address, like linkedin.com/in/your-name.")
    out["linkedin"] = li
    out["pdf_prefix"] = re.sub(r"[^A-Za-z0-9]+", "-", out["pdf_prefix"]).strip("-")
    out["evidence"] = one_line("evidence", 600)
    resumes, names = [], set()
    for r in raw.get("resumes") or []:
        name, f = re.sub(r"\s+", " ", str(r.get("name") or "")).strip()[:60], str(r.get("file") or "")
        if not is_resume_file(f) or not (references / f).is_file():
            raise SearchError(f"Résumé file not found in your profile's references folder: {f}")
        if not name:
            raise SearchError(f"Give {f} a name (it labels the résumé's column in the heat map).")
        if name.lower() in names:
            raise SearchError(f"Two résumés are named “{name}”; names must differ.")
        names.add(name.lower())
        resumes.append({"name": name, "file": f})
    if not resumes:
        raise SearchError("Tick at least one résumé to score against.")
    out["resumes"] = resumes
    return out


def save_resume_format(path: Path, fid: str) -> None:
    """Set `candidate.resume_format`, keeping the file's comments. The profile needs saving first."""
    doc = _yaml().load(path.read_text())
    m = doc.get("candidate")
    if m is None:
        raise SearchError("Save your profile first, then choose a résumé format.")
    if "resume_format" not in m:
        keys = list(m)
        at = keys.index("pdf_prefix") + 1 if "pdf_prefix" in keys else len(keys)
        m.insert(at, "resume_format", fid, comment="how résumé PDFs look (Profile › Résumé format)")
    else:
        m["resume_format"] = fid
    tmp = path.with_suffix(".yaml.tmp")
    with tmp.open("w") as f:
        _yaml().dump(doc, f)
    tmp.replace(path)


def save_candidate(path: Path, cand: dict) -> None:
    """Write the `candidate:` section, keeping the file's comments (adding the section at the top if needed)."""
    doc = _yaml().load(path.read_text())
    m = doc.get("candidate")
    if m is None:
        m = CommentedMap()
        doc.insert(0, "candidate", m, comment="Who the résumés are for (edited on the app's Profile tab).")
    for k in (*CANDIDATE_TEXT, "evidence"):
        m[k] = cand.get(k, "")
    m.pop("example", None)
    old = m.get("resumes")
    new = CommentedMap((r["name"], r["file"]) for r in cand["resumes"])
    # A comment line after the last résumé (the one above the next key) is stored on that item: move it.
    if isinstance(old, CommentedMap) and old and old.ca.items.get(list(old)[-1]):
        new.ca.items[list(new)[-1]] = old.ca.items[list(old)[-1]]
    m["resumes"] = new
    tmp = path.with_suffix(".yaml.tmp")
    with tmp.open("w") as f:
        _yaml().dump(doc, f)
    tmp.replace(path)


# ---- the AI (searches.yaml's `models:` section and the key in the profile's .env, edited on the Profile tab) ----
MODEL_STAGES = ("triage", "analysis", "writer", "chat", "rescore")
EFFORT_CHOICES = ("low", "medium", "high")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,119}")


def load_models(path: Path) -> dict:
    m = _yaml().load(path.read_text()).get("models") or {}
    stages = {s: {"model": str((m.get(s) or {}).get("model") or ""), "effort": str((m.get(s) or {}).get("effort") or "")}
              for s in MODEL_STAGES}
    backend = str(m.get("backend") or "claude-code")
    if not m.get("chat"):               # a profile from before the chat had its own model
        stages["chat"] = config.chat_default(backend, stages["writer"])
    if not m.get("rescore"):            # a profile from before live scores
        stages["rescore"] = config.rescore_default(backend, stages["triage"])
    saved = {}                          # each provider's last saved picks, restored when you switch back to it
    for b, picks in (m.get("saved") or {}).items():
        if isinstance(picks, dict):
            saved[str(b)] = {"base_url": str(picks.get("base_url") or ""),
                             "stages": {s: {"model": str((picks.get(s) or {}).get("model") or ""),
                                            "effort": str((picks.get(s) or {}).get("effort") or "")}
                                        for s in MODEL_STAGES if picks.get(s)}}
    return {"backend": backend, "base_url": str(m.get("base_url") or ""), "stages": stages, "saved": saved}


def _flow(model: str, effort: str) -> CommentedMap:
    cur = CommentedMap(model=model, effort=effort)
    cur.fa.set_flow_style()
    return cur


def save_models(path: Path, backend: str, base_url: str, stages: dict) -> None:
    """Write models.backend, base_url and each stage's model and effort, keeping the file's comments."""
    doc = _yaml().load(path.read_text())
    m = doc.get("models")
    if m is None:
        m = doc["models"] = CommentedMap()
    before = load_models(path)            # the AI being switched away from, kept below if it isn't yet
    m["backend"] = backend
    if base_url:
        m["base_url"] = base_url
    else:
        m.pop("base_url", None)
    for s in MODEL_STAGES:
        cur = m.get(s)
        if not isinstance(cur, CommentedMap):
            cur = CommentedMap()
            cur.fa.set_flow_style()
            m[s] = cur
        cur["model"], cur["effort"] = stages[s]["model"], stages[s]["effort"]
    # Remember this provider's picks too, so switching to another AI and back doesn't lose them.
    saved = m.get("saved")
    if not isinstance(saved, CommentedMap):
        saved = m["saved"] = CommentedMap()
        m.yaml_set_comment_before_after_key("saved", before="Each AI's last picks, so switching back to it restores them.",
                                            indent=2)
    def remember(b: str, url: str, chosen: dict) -> None:
        picks = CommentedMap()
        if url:
            picks["base_url"] = url
        for s in MODEL_STAGES:
            if chosen.get(s, {}).get("model"):
                picks[s] = _flow(chosen[s]["model"], chosen[s]["effort"] or "medium")
        saved[b] = picks
    if before["backend"] != backend and before["backend"] not in saved:
        remember(before["backend"], before["base_url"], before["stages"])
    remember(backend, base_url, stages)
    tmp = path.with_suffix(".yaml.tmp")
    with tmp.open("w") as f:
        _yaml().dump(doc, f)
    tmp.replace(path)


def clean_models(raw: dict, backends) -> tuple[str, str, dict]:
    backend = str(raw.get("backend") or "")
    if backend not in backends:
        raise SearchError("Choose which AI to use.")
    base_url = str(raw.get("base_url") or "").strip()
    if base_url and not re.fullmatch(r"https?://[^\s]+", base_url):
        raise SearchError("The address must start with http:// or https://.")
    if backend == "openai-compatible" and not base_url:
        raise SearchError("Give the address of the OpenAI-compatible API, e.g. http://localhost:8000/v1.")
    stages = {}
    for s in MODEL_STAGES:
        r = (raw.get("stages") or {}).get(s) or {}
        if s == "chat" and not r:       # a page from before the chat had its own model
            r = config.chat_default(backend, stages["writer"])
        if s == "rescore" and not r:    # a page from before live scores
            r = config.rescore_default(backend, stages["triage"])
        model, effort = str(r.get("model") or "").strip(), str(r.get("effort") or "medium")
        if not MODEL_ID.fullmatch(model):
            raise SearchError(f"Choose a model for {s}.")
        if effort not in EFFORT_CHOICES:
            raise SearchError(f"Effort for {s}: low, medium or high.")
        stages[s] = {"model": model, "effort": effort}
    return backend, base_url, stages


def set_env(path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file (replacing the line if there is one), readable only by you."""
    import os
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or "\n" in value:
        raise SearchError("That key can't be saved.")
    lines = path.read_text().splitlines() if path.exists() else []
    line = f"{key}={value}"
    for i, l in enumerate(lines):
        if re.match(rf"\s*{key}\s*=", l):
            lines[i] = line
            break
    else:
        lines.append(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)

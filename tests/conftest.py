import json
import os
import re
import shutil
import sys
from pathlib import Path

import httpx
import jobspipe
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobpipe import config, evidence, postings  # noqa: E402
from jobpipe.llm import AgentResult  # noqa: E402

TITLE = "Staff Technical Program Manager"
# The tests run the pipeline for example-profile's made-up candidate, whose impact record and résumé
# are in example-profile/references, so they never depend on (or send) a real person's evidence.
EXAMPLE_REFS = ROOT / "example-profile" / "references"
BASE = "Platform resume"
CANDIDATE = config.Candidate(
    name="Jordan Rivera", pronouns="they/them", city="Portland, OR", phone="+1 555-010-0199",
    email="jordan.rivera@example.com", linkedin="linkedin.com/in/jordan-rivera-example",
    resumes={BASE: "resume-platform.md"})
BASE_SOURCES = [config.IMPACT_SOURCE, *CANDIDATE.resumes]
DESCRIPTION = ("About the role. You will lead cross-functional technical programs across platform engineering, "
               "own delivery planning, milestones, dependencies and risks, and communicate status to executives. "
               "Minimum qualifications: 8+ years of technical program management; experience with CI/CD and "
               "developer productivity; excellent written communication. Preferred: experience with AI evaluation. "
               ) * 3


def make_job(jid="j1", title=TITLE, company="Acme", remote=True, hybrid=False, lo=280000, hi=360000, **kw):
    return {"id": jid, "job_title": title, "company": company, "url": f"https://boards.greenhouse.io/acme/jobs/{jid}?gh_src=x",
            "final_url": f"https://boards.greenhouse.io/acme/jobs/{jid}", "location": "New York, NY",
            "remote": remote, "hybrid": hybrid, "min_annual_salary_usd": lo, "max_annual_salary_usd": hi,
            "description": DESCRIPTION, "date_posted": "2026-09-29", **kw}


class FakeJobsAPI:
    """httpx transport standing in for POST /v1/jobs/search."""
    def __init__(self, jobs_by_title=None, fail_400=False):
        self.jobs_by_title = jobs_by_title or {}
        self.fail_400 = fail_400
        self.bodies = []
        self.paid = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.fail_400:
            return httpx.Response(400, json={"error": "unknown field: work_arrangement_or"})
        jobs = []
        for phrase in body.get("job_title_or", []):
            jobs += self.jobs_by_title.get(phrase, [])
        jobs = jobs[: body.get("limit", 10)]
        new = [j for j in jobs if j["id"] not in self.paid]
        self.paid.update(j["id"] for j in jobs)
        meta = {"total_results": len(jobs) if body.get("include_total_results") else None,
                "next_cursor": None, "credits_charged": len(new), "jobs_already_paid": len(jobs) - len(new)}
        return httpx.Response(200, json={"metadata": meta, "data": jobs})


def jobspipe_client(fake: FakeJobsAPI) -> jobspipe.Jobspipe:
    http = httpx.Client(base_url="https://api.jobspipe.dev", transport=httpx.MockTransport(fake))
    return jobspipe.Jobspipe(api_key="jp_test", http_client=http, max_retries=0)


def _resume(title=TITLE) -> str:
    md = (EXAMPLE_REFS / "resume-platform.md").read_text()
    return md.replace("Senior Technical Program Manager, Platform", title)


def _trace(md: str) -> str:
    rows = ["| Line | Starts with | Posting phrases | Ledger IDs | Requirement IDs |", "|---|---|---|---|---|"]
    for i, l in enumerate(md.splitlines()):
        if l.strip().startswith("- "):
            rows.append(f"| {i} | {' '.join(l.strip()[2:].split()[:8])} | - | E1 | S1 |")
    return "\n".join(rows) + "\n"


def _ratings(sources, score=8):
    rows = lambda: [{"requirement": "Technical program management", "weight": "High",
                     "ratings": ["strong"] * len(sources), "notes": ["x"] * len(sources)},
                    {"requirement": "AI evaluation", "weight": "Low",
                     "ratings": ["partial"] * len(sources), "notes": ["y"] * len(sources)}]
    return {"title": f"{TITLE} — Acme", "sources": sources, "scores": [score] * len(sources),
            "skills": rows(), "experience": rows()}


def files(**kw):
    return AgentResult(files={k.replace("__", "-").replace("_DOT_", "."): v for k, v in kw.items()}, summary="ok")


class FakeRunner:
    """Returns canned agent outputs keyed on the call label; records every call."""
    def __init__(self, fit_by_job=None):
        self.calls = []
        self.fit_by_job = fit_by_job or {}

    async def run(self, call):
        self.calls.append(call)
        label = call.label
        out = {}
        if label.startswith("triage:"):
            jid = label.split(":", 1)[1]
            out["triage.json"] = json.dumps({
                "fit_score": self.fit_by_job.get(jid, 8), "band_reason": "b", "role_family": "TPM",
                "level_match": "match", "hard_requirement_issues": [], "strongest_matches": ["m"],
                "likely_gaps": ["g"], "recommended_base": BASE, "one_line": "strong TPM fit"})
        elif label == "01-objectives":
            out["01-objectives.md"] = (f"# Role objectives\n\n## Exact job title\n{TITLE}\n\n## Job summary\nAcme.\n\n"
                                       "## Hard constraints stated in the posting\nNone stated\n")
        elif label in ("02-skills", "03-experience"):
            out[f"{label}.md"] = f"# {label}\n"
        elif label == "04-matcher":
            out["04-match.md"] = "# Match\n\n## Unconfirmed gaps\n- AI evaluation depth\n\n## Scores\n8\n"
            out["ratings.json"] = json.dumps(_ratings(BASE_SOURCES))
            out["keywords.json"] = json.dumps({"Technical Program Manager": "technical program manag",
                                               "CI/CD": r"CI/CD", "dependencies": "dependenc",
                                               "Kubernetes": "kubernetes"})
            out["recommended-base.txt"] = BASE
        elif label.startswith("05-writer-"):
            L = label[-1]
            md = _resume()
            out[f"resume-draft-{L}.md"] = md
            out[f"trace-{L}.md"] = _trace(md)
        elif label == "rate-drafts":
            out["ratings-drafts.json"] = json.dumps(_ratings(BASE_SOURCES + ["Draft A", "Draft B", "Draft C"]))
        elif label in ("merge", "merge-fix", "merge-restore", "honesty-fix", "trim"):
            md = _resume()
            out["resume-final.md"] = md
            out["trace-final.md"] = _trace(md)
            out["merge-notes.md"] = "# Merge notes\nAll from Draft A.\n"
        elif label.startswith("rate-final-"):
            out[f"ratings-final-{label[-1]}.json"] = json.dumps(_ratings(BASE_SOURCES + ["Tailored resume"], score=9))
        elif label == "report":
            out["report.md"] = f"# {TITLE} — Acme\n\nReport.\n"
            out["notion-summary.txt"] = "Acme wants a TPM. The role leads platform programs."
        else:
            raise AssertionError(f"unexpected agent call {label}")
        res = AgentResult(files=out, summary="ok")
        if call.run_dir:
            from jobpipe.llm import write_files
            write_files(call.run_dir, res.files)
        return res


class FakeNotion:
    """httpx transport standing in for the Notion API."""
    def __init__(self, rows=None):
        self.rows = {r["id"]: r for r in (rows or [])}
        self.requests = []
        self.uploads = {}     # file upload id -> (name, bytes)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "files.example":           # a signed download link
            fid = path.split("/")[1]
            return httpx.Response(200, content=self.uploads[fid][1])
        if path.endswith("/send"):                         # a multipart file upload
            fid = path.split("/")[-2]
            self.requests.append((request.method, path, None))
            data = request.content
            start = data.index(b"%PDF-")
            self.uploads[fid] = (self.uploads[fid][0], data[start:data.rindex(b"%%EOF") + 5])
            return httpx.Response(200, json={"id": fid, "status": "uploaded"})
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        if request.method == "POST" and path.endswith("/file_uploads"):
            fid = f"fu{len(self.uploads) + 1}"
            self.uploads[fid] = (body["filename"], b"")
            return httpx.Response(200, json={"id": fid, "status": "pending"})
        if path.endswith("/query"):
            f = body["filter"]
            if f["property"] == "Job URL":
                needle = f["url"]["contains"]
                res = [r for r in self.rows.values() if needle in (r["properties"]["Job URL"]["url"] or "")]
            else:
                res = list(self.rows.values())
            return httpx.Response(200, json={"results": res, "has_more": False})
        if request.method == "POST" and path.endswith("/pages"):
            pid = f"page{len(self.rows) + 1}"
            props = body["properties"]
            row = {"id": pid, "url": f"https://notion.so/{pid}", "properties": {
                "Job URL": {"url": props["Job URL"]["url"]},
                "Status": {"status": props["Status"]["status"]},
                "Fit Score": {"number": props.get("Fit Score", {}).get("number")},
                "Notes": {"rich_text": [{"plain_text": props["Notes"]["rich_text"][0]["text"]["content"]}]}},
                "_create": body}
            self.rows[pid] = row
            return httpx.Response(200, json=row)
        pid = path.rsplit("/", 1)[-1]
        row = self.rows[pid]
        if request.method == "PATCH":
            for k, v in body["properties"].items():
                if k == "Notes":
                    v = {"rich_text": [{"plain_text": v["rich_text"][0]["text"]["content"]}]}
                if k == "Resume Used":
                    v = {"files": [{"name": f["name"], "type": "file", "file": {
                        "url": f"https://files.example/{f['file_upload']['id']}/{f['name']}?sig=1"}} for f in v["files"]]}
                row["properties"][k] = v
        return httpx.Response(200, json=row)


def notion_row(pid, url, status="Not started", fit=None, notes=""):
    return {"id": pid, "url": f"https://notion.so/{pid}", "properties": {
        "Job URL": {"url": url}, "Status": {"status": {"name": status} if status else None},
        "Fit Score": {"number": fit}, "Notes": {"rich_text": [{"plain_text": notes}] if notes else []}}}


@pytest.fixture(autouse=True)
def no_himalayas():
    """Searches don't ask Himalayas in tests (a real web call); a test that wants it passes a Himalayas with a fake client."""
    env = os.environ.get("JOBPIPE_HIMALAYAS")
    os.environ["JOBPIPE_HIMALAYAS"] = "off"
    yield
    if env is None:
        os.environ.pop("JOBPIPE_HIMALAYAS", None)
    else:
        os.environ["JOBPIPE_HIMALAYAS"] = env


@pytest.fixture(autouse=True)
def no_evidence_models():
    """The local evidence models are off in tests (they'd load gigabytes); a test that wants them swaps in fakes.
    Set by hand, not with monkeypatch, so a test's monkeypatch.undo() can't switch the real models on."""
    real, env = evidence.load, os.environ.get("JOBPIPE_EVIDENCE_MODELS")

    def off(cfg):
        raise evidence.Unavailable("off in tests")
    evidence.load = off
    os.environ["JOBPIPE_EVIDENCE_MODELS"] = "off"         # for the checks that run in a process of their own
    yield
    evidence.load = real
    if env is None:
        os.environ.pop("JOBPIPE_EVIDENCE_MODELS", None)
    else:
        os.environ["JOBPIPE_EVIDENCE_MODELS"] = env


@pytest.fixture(autouse=True)
def no_posting_keeper(monkeypatch):
    """The app's background keeper of applied postings would read pages from the web mid-test; it's off,
    and a test that wants it calls PostingKeeper.run_once() itself."""
    monkeypatch.setattr(postings.PostingKeeper, "kick", lambda self: None)


@pytest.fixture(autouse=True)
def fixture_candidate(tmp_path_factory, monkeypatch):
    """Every test runs for CANDIDATE, from a copy of example-profile in a temp folder (never the real
    ~/JobSearch): its references are the example impact record and résumé."""
    profile = tmp_path_factory.mktemp("profile")
    shutil.copytree(EXAMPLE_REFS, profile / "references")
    for name, path in (("PROFILE", profile), ("REFERENCES", profile / "references"), ("RUNS", profile / "resume-runs"),
                       ("DATA", profile / "data"), ("OUTPUTS", profile / "outputs")):
        monkeypatch.setattr(config, name, path)
    monkeypatch.setenv("JOBPIPE_CONFIG", str(ROOT / "example-profile" / "searches.yaml"))
    monkeypatch.setattr(config, "candidate", lambda path=None: CANDIDATE)
    return CANDIDATE


@pytest.fixture
def tmp_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUTS", tmp_path / "outputs")
    return tmp_path

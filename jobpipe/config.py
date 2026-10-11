"""Load searches.yaml and resolve the repo's and the profile's paths.

The repo holds the code and the method (skill/). Everything about the person using it lives in
their profile folder, outside the repo: ~/JobSearch, or $JOBPIPE_HOME. A new profile starts as a
copy of example-profile/ (ensure_profile)."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skill"                       # the method: agent briefs, rules, scoring rubric, check scripts
EXAMPLE_PROFILE = ROOT / "example-profile"   # what a new profile starts as (a made-up candidate)


def _env_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name) or default).expanduser()


def set_profile(home: Path) -> None:
    """Point every profile path at this folder (the env-var overrides still win)."""
    global PROFILE, REFERENCES, RUNS, DATA, OUTPUTS
    PROFILE = Path(home).expanduser()
    REFERENCES = PROFILE / "references"     # impact-record.md, resume-*.md
    RUNS = PROFILE / "resume-runs"           # runs of the resume-job-fit skill done in Claude chats
    DATA = _env_path("JOBPIPE_DATA_DIR", PROFILE / "data")
    OUTPUTS = _env_path("JOBPIPE_OUTPUTS_DIR", PROFILE / "outputs")


set_profile(_env_path("JOBPIPE_HOME", Path.home() / "JobSearch"))


def searches_path() -> Path:
    """searches.yaml: the candidate, saved searches, Notion IDs and models ($JOBPIPE_CONFIG overrides)."""
    return _env_path("JOBPIPE_CONFIG", PROFILE / "searches.yaml")


def ensure_profile(log=print) -> bool:
    """Make the profile folder if it has no searches.yaml yet: from the files a pre-profile checkout kept
    in the repo, if this one has them, or else from example-profile/. True if it made one."""
    if searches_path().exists():
        return False
    from . import legacy
    if legacy.found(ROOT):
        legacy.move(ROOT, PROFILE, log)
        return True
    shutil.copytree(EXAMPLE_PROFILE, PROFILE, dirs_exist_ok=True)
    if not (PROFILE / ".env").exists():
        shutil.copy(ROOT / ".env.example", PROFILE / ".env")
        os.chmod(PROFILE / ".env", 0o600)
    log(f"Made your profile folder at {PROFILE}, starting from a made-up candidate.\n"
        f"Put yourself in it on the app's Profile and Impact record tabs (see README: Get started).")
    return True

IMPACT_SOURCE = "Impact record"


@dataclass
class Candidate:
    """Who the résumés are for: searches.yaml's `candidate:` section.

    The skill's briefs and rules, and the app's own prompts, say {name}, {first}, {contact},
    {city}, {linkedin} and {pdf_prefix} and call the candidate he/him; personalize() fills those in
    and swaps the pronouns as they are loaded as prompts."""
    name: str
    pronouns: str = "they/them"          # he/him, she/her or they/them
    city: str = ""                       # "City, ST": the contact-line check requires it
    phone: str = ""
    email: str = ""
    linkedin: str = ""                   # linkedin.com/in/... (the check requires a visible URL)
    pdf_prefix: str = ""                 # résumé PDFs are <prefix>-<Company>-<Title>.pdf
    resume_format: str = "signature"     # how résumé PDFs look: an id in jobpipe/formats.py (Profile › Résumé format)
    # Resume variants the matcher and triage score, as "source name" -> file in the profile's references/.
    # The names become the columns of ratings.json and the heat map.
    resumes: dict[str, str] = field(default_factory=dict)
    # What the triage prompt calls the candidate's evidence.
    evidence: str = "the impact record, including every user-confirmed note, plus the resume variants"
    # Figures the impact record withdrew (e.g. "41.2%"): check_resume.py fails a résumé that shows one.
    withdrawn: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.pdf_prefix = self.pdf_prefix or re.sub(r"[^A-Za-z0-9]+", "-", self.name).strip("-") + "-Resume"

    @property
    def first_name(self) -> str:
        return (self.name.split() or [""])[0]

    def resume_title(self, role: str = "", company: str = "") -> str:
        """A résumé PDF's title, which browsers save it under: "<Role Title> - <Company>"."""
        role, company = (re.sub(r'[\\/:*?"<>|\s]+', " ", x or "").strip(" .") for x in (role, company))
        return " - ".join(x for x in (role, company) if x) or f"{self.name} Resume"

    @property
    def contact(self) -> str:
        return " | ".join(x for x in (self.city, self.phone, self.email, self.linkedin) if x)

    def personalize(self, text: str) -> str:
        """A prompt with {name}, {first}, {contact} and the like, filled in for this candidate."""
        for key, value in (("{contact}", self.contact), ("{linkedin}", self.linkedin), ("{city}", self.city),
                           ("{pdf_prefix}", self.pdf_prefix), ("{name}", self.name), ("{first}", self.first_name)):
            text = text.replace(key, value)
        return swap_pronouns(text, self.pronouns)


# The candidate before a profile names one: the Profile tab asks for the details.
PLACEHOLDER = Candidate(name="Your Name")

_PRONOUNS = {
    "she": {"he": "she", "he's": "she's", "him": "her", "his": "her", "himself": "herself"},
    "they": {"he": "they", "he's": "they're", "him": "them", "his": "their", "himself": "themselves"},
}
_THEY_VERBS = {"is": "are", "was": "were", "has": "have", "does": "do", "isn't": "aren't",
               "wasn't": "weren't", "hasn't": "haven't", "doesn't": "don't"}


def _plural_verb(word: str) -> str:
    """'lacks' -> 'lack', 'applies' -> 'apply', 'has' -> 'have': the verb after 'they' instead of 'he'."""
    if word.lower() in _THEY_VERBS:
        return _THEY_VERBS[word.lower()]
    if word.endswith("ies"):
        return word[:-3] + "y"
    if re.search(r"(ss|sh|ch|x|z)es$", word):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def swap_pronouns(text: str, pronouns: str) -> str:
    """Text that calls the candidate he/him/his, with these pronouns instead."""
    key = pronouns.split("/")[0].strip().lower()
    table = _PRONOUNS.get(key)
    if not table:
        return text

    def sub(m: re.Match) -> str:
        word, verb = m.group(1), m.group(2)
        new = table[word.lower()]
        if key == "they" and word.lower() == "he's" and verb and verb.endswith(("ed", "held", "done", "been")):
            new = "they've"                               # "tools he's used"; "the role he's targeting" keeps they're
        new = new.capitalize() if word[0].isupper() else new
        if verb is None:
            return new
        return f"{new} {_plural_verb(verb) if key == 'they' and word.lower() == 'he' else verb}"

    return re.sub(r"\b(he's|he|him|his|himself)\b(?: ([a-z]+'?[a-z]*))?", sub, text, flags=re.I)


def parse_candidate(raw: Optional[dict]) -> Candidate:
    """searches.yaml's `candidate:` section (PLACEHOLDER without one)."""
    if raw is None:
        return PLACEHOLDER
    if not raw.get("name"):
        raise ValueError("searches.yaml: candidate.name is required")
    resumes = {str(k): str(v) for k, v in (raw.get("resumes") or {}).items()}
    if not resumes:
        raise ValueError("searches.yaml: candidate.resumes needs at least one résumé "
                         "(a source name mapped to a file in your profile's references/)")
    known = {f.name for f in Candidate.__dataclass_fields__.values()} - {"resumes", "withdrawn"}
    withdrawn = [str(x) for x in raw.get("withdrawn") or []]
    return Candidate(**{k: str(v) for k, v in raw.items() if k in known and v not in (None, "")},
                     resumes=resumes, withdrawn=withdrawn)


_cache: dict[str, Any] = {}


def candidate(path: Optional[Path] = None) -> Candidate:
    """The candidate from searches.yaml (re-read when the file changes)."""
    path = path or searches_path()
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return PLACEHOLDER
    if _cache.get("key") != key:
        raw = yaml.safe_load(path.read_text()) or {}
        _cache.update(key=key, value=parse_candidate(raw.get("candidate")))
    return _cache["value"]

@dataclass
class Search:
    id: str
    name: str
    titles: list[str] = field(default_factory=list)
    exclude_titles: list[str] = field(default_factory=list)
    country: Optional[str] = "US"
    posted_within_days: Optional[int] = 7
    min_salary_usd: Optional[int] = None
    remote: Optional[bool] = None
    work_arrangement: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    seniority: list[str] = field(default_factory=list)
    limit: int = 10
    api_filters: dict[str, Any] = field(default_factory=dict)

    def request_body(self, *, limit: Optional[int] = None) -> dict[str, Any]:
        """The JSON body for POST /v1/jobs/search. Only set fields are sent."""
        body: dict[str, Any] = {
            "limit": self.limit if limit is None else limit,
            "job_title_or": self.titles or None,
            "job_title_not": self.exclude_titles or None,
            "job_country_code_or": [self.country] if self.country else None,
            "posted_at_max_age_days": self.posted_within_days,
            "min_salary_usd": self.min_salary_usd,
            "remote": self.remote,
            "work_arrangement_or": self.work_arrangement or None,
            "job_location_or": self.locations or None,
            "job_seniority_or": self.seniority or None,
        }
        body = {k: v for k, v in body.items() if v is not None}
        body.update(self.api_filters)
        return body


@dataclass
class ModelCfg:
    model: str = "claude-opus-5-5"
    effort: str = "medium"


@dataclass
class StartupsCfg:
    """searches.yaml's `startups:` section: what the Startups tab reads and keeps."""
    days: int = 90                                   # a raise this recent counts
    feeds: dict[str, str] = field(default_factory=dict)      # funding-news RSS feeds, name -> URL (default: the module's)
    news_queries: Optional[list[str]] = None         # Google News searches (default: the module's)
    yc_hiring_only: bool = True
    yc_batches: list[str] = field(default_factory=list)      # e.g. ["Summer 2026"]; empty = every batch
    yc_industries: list[str] = field(default_factory=list)
    yc_regions: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)        # keep only startups mentioning one of these; empty = all
    vc_boards: Optional[dict[str, str]] = None       # VC portfolio job boards, firm -> URL (default: the module's); {} = none
    enrich_per_refresh: int = 0                      # round lookups per refresh, for startups whose round is unknown
    roles_auto: bool = True                          # each refresh reads startups' careers boards into Find jobs
    roles_max_per_refresh: int = 300                 # how many startups' boards a refresh reads (newest raise first)
    roles_exclude_titles: list[str] = field(default_factory=list)   # extra title words that rule a role out
    roles_signal_score: bool = False                 # score new startup roles against the impact record each refresh
    roles_score_per_refresh: int = 30                # at most this many scores (Claude calls) a refresh
    roles_track_min_fit: Optional[int] = None        # a startup role scoring this or more goes to the tracker; None = notion.log_triaged_min_fit
    check_postings: bool = True                      # each refresh re-checks roles not yet applied to are still open
    auto_refresh_hours: float = 24                   # the app refreshes the sources on its own this often; 0 = never


@dataclass
class EvidenceCfg:
    """searches.yaml's `evidence:` section: the local Hugging Face models that check resume text against the
    candidate's evidence (jobpipe/evidence.py)."""
    enabled: bool = True
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"
    verifier_model: str = "MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli"
    device: Optional[str] = None                     # "mps", "cpu", ...; None picks the GPU when there is one


@dataclass
class Config:
    searches: list[Search]
    defaults: dict[str, Any] = field(default_factory=dict)     # searches.yaml's defaults, for one-off searches
    max_credits_per_run: int = 40
    allowance_amount: int = 1000
    allowance_period: str = "once"  # "once" or "monthly"
    shortlist_min_fit: int = 7
    tailor_top: int = 3
    enforce_min_salary: bool = True
    notion_enabled: bool = True
    notion_data_source_id: str = ""
    notion_project_page_id: str = ""
    notion_log_triaged_min_fit: Optional[int] = None
    triage_model: ModelCfg = field(default_factory=lambda: ModelCfg(effort="low"))
    analysis_model: ModelCfg = field(default_factory=ModelCfg)
    writer_model: ModelCfg = field(default_factory=lambda: ModelCfg(effort="high"))
    # Re-rating each résumé edit in the chat (live ATS and résumé scores): frequent, so a mid-priced model.
    rescore_model: ModelCfg = field(default_factory=lambda: ModelCfg(model=LIVE_SCORE_MODEL, effort="medium"))
    fallbacks: bool = True
    # Which AI does the work: "claude-code" (Claude subscription), "api" (Claude API credits), or an
    # OpenAI-compatible provider from llm.PROVIDERS (openai, gemini, kimi, openrouter, ollama, lmstudio,
    # openai-compatible). base_url and api_key_env override a provider's address and key variable.
    backend: str = "claude-code"
    claude_bin: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    startups: StartupsCfg = field(default_factory=StartupsCfg)
    evidence: EvidenceCfg = field(default_factory=EvidenceCfg)

    @property
    def title_phrases(self) -> list[str]:
        """Every title phrase in the saved searches: what counts as a matching role on a startup's board."""
        out: list[str] = []
        for s in self.searches:
            for t in s.titles:
                if t and t.lower() not in {x.lower() for x in out}:
                    out.append(t)
        return out

    def track_min_fit(self, job_id: str = "") -> Optional[int]:
        """The signal score at which a scored job goes to the tracker on its own (None: it waits in Find jobs).
        Startup roles use startups.roles.track_min_fit when it's set, every other job notion.log_triaged_min_fit."""
        if job_id.startswith("startup-") and self.startups.roles_track_min_fit is not None:
            return self.startups.roles_track_min_fit
        return self.notion_log_triaged_min_fit

    def search(self, search_id: str) -> Search:
        for s in self.searches:
            if s.id == search_id:
                return s
        raise KeyError(f"No search with id {search_id!r} in searches.yaml "
                       f"(have: {', '.join(s.id for s in self.searches)})")


def _search(raw: dict, defaults: dict) -> Search:
    merged = {**defaults, **raw}
    wa = merged.get("work_arrangement") or []
    if isinstance(wa, str):
        wa = [wa]
    return Search(
        id=merged["id"],
        name=merged.get("name", merged["id"]),
        titles=list(merged.get("titles") or []),
        exclude_titles=list(merged.get("exclude_titles") or []),
        country=merged.get("country"),
        posted_within_days=merged.get("posted_within_days"),
        min_salary_usd=merged.get("min_salary_usd"),
        remote=merged.get("remote"),
        work_arrangement=list(wa),
        locations=list(merged.get("locations") or []),
        seniority=list(merged.get("seniority") or []),
        limit=int(merged.get("limit", 10)),
        api_filters=dict(merged.get("api_filters") or {}),
    )


LIVE_SCORE_MODEL = "claude-sonnet-5-5"


def rescore_default(backend: str, triage: dict) -> dict:
    """The live-score model when searches.yaml names none: Sonnet on Claude, else the quick-score model
    (another provider has its own names for models)."""
    if backend in ("claude-code", "api"):
        return {"model": LIVE_SCORE_MODEL, "effort": "medium"}
    return {"model": triage.get("model") or ModelCfg.model, "effort": triage.get("effort") or "low"}


def _model(raw: Optional[dict], default: ModelCfg) -> ModelCfg:
    raw = raw or {}
    return ModelCfg(model=raw.get("model", default.model), effort=raw.get("effort", default.effort))


def load(path: Optional[Path] = None) -> Config:
    path = path or searches_path()
    raw = yaml.safe_load(path.read_text()) or {}
    defaults = raw.get("defaults") or {}
    budget = raw.get("budget") or {}
    allowance = budget.get("allowance") or {}
    pipe = raw.get("pipeline") or {}
    notion = raw.get("notion") or {}
    models = raw.get("models") or {}
    su = raw.get("startups") or {}
    yc = su.get("yc") or {}
    ev = raw.get("evidence") or {}
    _list = lambda v: [str(x) for x in (v or [])] if isinstance(v, (list, tuple)) else ([str(v)] if v else [])
    startups = StartupsCfg(
        days=int(su.get("days", 90)),
        feeds={str(k): str(v) for k, v in (su.get("feeds") or {}).items()},
        news_queries=_list(su["news_queries"]) if su.get("news_queries") is not None else None,
        yc_hiring_only=bool(yc.get("hiring_only", True)), yc_batches=_list(yc.get("batches")),
        yc_industries=_list(yc.get("industries")), yc_regions=_list(yc.get("regions")),
        keywords=_list(su.get("keywords")),
        vc_boards={str(k): str(v) for k, v in su["vc_boards"].items()} if isinstance(su.get("vc_boards"), dict) else None,
        enrich_per_refresh=int((su.get("enrich") or {}).get("per_refresh", 0) or 0),
        roles_auto=bool((su.get("roles") or {}).get("auto", True)),
        roles_max_per_refresh=int((su.get("roles") or {}).get("max_per_refresh", 300) or 0),
        roles_exclude_titles=_list((su.get("roles") or {}).get("exclude_titles")),
        roles_signal_score=bool((su.get("roles") or {}).get("signal_score", False)),
        roles_score_per_refresh=int((su.get("roles") or {}).get("score_per_refresh", 30) or 0),
        roles_track_min_fit=(None if (su.get("roles") or {}).get("track_min_fit") is None
                             else int(su["roles"]["track_min_fit"])),
        check_postings=bool(su.get("check_postings", True)),
        auto_refresh_hours=float(su.get("auto_refresh_hours", 24) or 0))
    return Config(
        searches=[_search(s, defaults) for s in raw.get("searches") or []],
        defaults=dict(defaults),
        max_credits_per_run=int(budget.get("max_credits_per_run", 40)),
        allowance_amount=int(allowance.get("amount", 1000)),
        allowance_period=allowance.get("period", "once"),
        shortlist_min_fit=int(pipe.get("shortlist_min_fit", 7)),
        tailor_top=int(pipe.get("tailor_top", 3)),
        enforce_min_salary=bool(pipe.get("enforce_min_salary", True)),
        notion_enabled=bool(notion.get("enabled", True)),
        notion_data_source_id=notion.get("data_source_id", ""),
        notion_project_page_id=notion.get("project_page_id", ""),
        notion_log_triaged_min_fit=notion.get("log_triaged_min_fit"),
        triage_model=_model(models.get("triage"), ModelCfg(effort="low")),
        analysis_model=_model(models.get("analysis"), ModelCfg(effort="medium")),
        writer_model=_model(models.get("writer"), ModelCfg(effort="high")),
        rescore_model=_model(models.get("rescore"), ModelCfg(**rescore_default(
            models.get("backend", "claude-code"), models.get("triage") or {}))),
        fallbacks=bool(models.get("fallbacks", True)),
        backend=models.get("backend", "claude-code"),
        claude_bin=models.get("claude_bin"),
        base_url=models.get("base_url") or None,
        api_key_env=models.get("api_key_env") or None,
        startups=startups,
        evidence=EvidenceCfg(enabled=bool(ev.get("enabled", True)),
                             embedding_model=ev.get("embedding_model", EvidenceCfg.embedding_model),
                             reranker_model=ev.get("reranker_model", EvidenceCfg.reranker_model),
                             verifier_model=ev.get("verifier_model", EvidenceCfg.verifier_model),
                             device=ev.get("device")),
    )


def one_off_search(raw: dict, cfg: Config) -> Search:
    """A search given in full (the web app's Search once), with searches.yaml's defaults for blank fields."""
    return _search({**raw, "id": "one-off", "name": raw.get("name") or "Search once"}, cfg.defaults)


def adhoc_search(*, title: list[str], remote: bool, hybrid: bool, min_salary: Optional[int],
                 locations: list[str], days: Optional[int], limit: int, country: Optional[str],
                 defaults: Optional[Search] = None) -> Search:
    """A one-off search from CLI flags (not saved to searches.yaml)."""
    return Search(
        id="adhoc",
        name="Ad-hoc: " + ", ".join(title),
        titles=title,
        exclude_titles=defaults.exclude_titles if defaults else [],
        country=country,
        posted_within_days=days,
        min_salary_usd=min_salary,
        remote=True if remote else None,
        work_arrangement=["hybrid"] if hybrid else [],
        locations=locations,
        limit=limit,
    )

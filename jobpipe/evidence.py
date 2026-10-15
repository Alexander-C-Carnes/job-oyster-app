"""Check resume text against the candidate's evidence, with small Hugging Face models run locally.

The evidence is the impact record, every resume variant and the confirmed facts, split into short passages. Passages
under "Do not claim", withdrawn metrics and the record's methodology notes are not evidence and are left out.

Keyword evidence (keyword_support): for each posting keyword a resume is missing, Qwen3-Embedding-0.6B finds the
closest passages and Qwen3-Reranker-0.6B judges whether each one shows work that phrase directly describes. A
keyword the reranker rates supported must also be stated: the NLI model below has to find that a top passage entails
"This work involved <keyword>." Relevant but not stated is weak. The reranker alone rated "Mentoring program
managers" 1.00 against mentoring junior analysts; the NLI model gave it 0.00. The verdicts go to the merge's
keyword-restore pass (add only what is supported) and to the report.

Line checks (check_lines): each bullet's claims (the part after its posting lead phrase, split at semicolons) against
the ledger entries its trace cites plus the passages closest to it, one at a time and all together.
- Figures: every number in the line must appear in that evidence or the confirmed facts. Without the models, or
  for a summary sentence (which combines several claims), anywhere in the evidence.
- Support: the probability the evidence entails the claim, from an NLI model (DeBERTa-v3-large-mnli-fever-anli-
  ling-wanli). The reranker can't do this: it scores whether evidence is on topic, so "Realized $12M" scored 0.998
  against "modeled up to $12M". Calibrated on three past runs (Oct 2026): 2 of 69 reviewed lines flagged, both
  paraphrases just under the threshold (0.14, 0.18), and 6 of 7 made-up lines (three also by their figures; the
  miss was the vaguest, a vague made-up claim of company-wide impact).
Flagged lines go to one honesty-fix pass (tailor.py), where Claude, with the full record, keeps or rescopes each.

The models are optional. With `evidence: enabled: false` in searches.yaml, or without sentence-transformers,
load() raises Unavailable and only the figure check runs.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Sequence

from . import config
from .config import EvidenceCfg

# Reranker probabilities. Keywords: supported = the evidence shows that work; weak = adjacent, ask first.
KEYWORD_SUPPORTED, KEYWORD_WEAK = 0.7, 0.3
# Entailment probability a supported keyword's evidence must reach. On four past runs (Oct 2026) the scores were
# near 0 or 1: 34 of 37 keywords the final resumes used passed (the 3 that didn't were the posting's own title,
# "Staff TPM"), and 1 of 3 missing keywords the reranker rated supported ("business impact", 0.99).
KEYWORD_ENTAILED = 0.5
KEYWORD_CLAIM = "This work involved {keyword}."
# Entailment probability: a line whose best evidence scores below this is flagged as not backed by it.
LINE_UNSUPPORTED = 0.2

KEYWORD_SEARCH = ("Given a phrase from a job posting, retrieve passages from a candidate's work history that "
                  "describe work the phrase refers to")
KEYWORD_JUDGE = ("Judge whether the Document describes specific work the candidate did that is a direct example of "
                 "the job-posting phrase in the Query. Answer no if the Document only relates to the topic, is a "
                 "general statement, or would need inference.")
LINE_SEARCH = "Given a line from a resume, retrieve the passages of the candidate's work history it is based on"

EXCLUDE_HEADING = re.compile(r"do not claim|withdrawn|data limitations|source inventory|methodology", re.I)
EXCLUDE_BLOCK = re.compile(r"^\W*(do not claim|withdrawn|invalid)\b", re.I)
CONFIRMED = re.compile(r"(^|› )(other )?(user-)?confirmed facts\b", re.I)   # the record's section, or user-notes.md
PASSAGE_WORDS = 120


# ---------------------------------------------------------------------------
# passages

@dataclass(frozen=True)
class Passage:
    source: str             # "Impact record › 2. Fabrikam Games › Build farm"
    text: str

    @property
    def doc(self) -> str:
        return f"{self.source}\n{self.text}"


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`#]|\\(?=\.)", "", s)).strip()


def split_markdown(name: str, md: str) -> list[Passage]:
    """Passages of one markdown document: each list item, and each paragraph in windows of about 120 words,
    labelled with its heading path. A resume's "**Company** | Title" line counts as a heading, and a paragraph
    ending in a colon ("The strongest statement is:") leads each item of the list after it."""
    heads: list[tuple[int, str]] = []
    out: list[Passage] = []
    intro = ""

    def add(text: str) -> None:
        text = _clean(text)
        if len(text.split()) < 4 or EXCLUDE_BLOCK.match(text) or any(EXCLUDE_HEADING.search(h) for _, h in heads):
            return
        path = [h for _, h in heads]
        label = " › ".join([name] + (path[:1] + path[-1:] if len(path) > 2 else path))
        words = text.split()
        for i in range(0, len(words), PASSAGE_WORDS):
            out.append(Passage(label, " ".join(words[i:i + PASSAGE_WORDS])))

    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)        # notes, such as where an added line came from
    for block in re.split(r"\n\s*\n", md):
        items: list[str] = []
        for line in block.splitlines():
            m = re.match(r"^(#{1,6})\s+(.*)", line)
            if m:
                items and [add(x) for x in items]
                items = []
                level = len(m.group(1))
                heads = [h for h in heads if h[0] < level] + [(level, _clean(m.group(2)))]
            elif re.match(r"^\*\*[^*]+\*\*\s*\|", line):           # resume job line: "**Company** | Title"
                items and [add(x) for x in items]
                items = []
                heads = [h for h in heads if h[0] < 7] + [(7, _clean(line))]
            elif re.match(r"^\s*([-*+]|\d+[.)])\s+", line) or not items:
                items.append(re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", line))
            else:
                items[-1] += " " + line.strip()                     # a wrapped line continues the item
        if len(items) == 1 and items[0].rstrip().endswith(":") and not block.lstrip().startswith(("-", "*", "+")):
            intro = items[0].strip() + " "
            continue
        for x in items:
            add(intro + x)
        intro = ""
    return out


def materials() -> dict[str, str]:
    """The candidate's evidence by source name: the impact record, each resume variant, the confirmed facts."""
    ref = config.REFERENCES
    out = {config.IMPACT_SOURCE: (ref / "impact-record.md").read_text()}
    for source, fname in config.candidate().resumes.items():
        out[source] = (ref / fname).read_text()
    if (ref / "user-notes.md").exists():       # where an older version of the app kept them
        out["Confirmed facts"] = (ref / "user-notes.md").read_text()
    return out


def candidate_passages() -> list[Passage]:
    return [p for name, md in materials().items() for p in split_markdown(name, md)]


# ---------------------------------------------------------------------------
# models

class Models(Protocol):
    name: str

    def embed(self, texts: Sequence[str], instruction: Optional[str] = None):
        """One L2-normalized row (numpy) per text; queries take an instruction, passages don't."""

    def judge(self, instruction: str, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """For each (query, document) pair, the probability the answer to the instruction is yes."""

    def entail(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """For each (evidence, claim) pair, the probability the evidence entails the claim."""


class Unavailable(RuntimeError):
    pass


class LocalModels:
    """The embedding model and reranker (sentence-transformers) and the NLI verifier (transformers) that
    searches.yaml names, on the Mac's GPU when there is one."""

    def __init__(self, cfg: EvidenceCfg):
        import torch
        from sentence_transformers import CrossEncoder, SentenceTransformer
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self._torch = torch
        self.device = cfg.device or ("mps" if torch.backends.mps.is_available()
                                     else "cuda" if torch.cuda.is_available() else "cpu")
        self.name = f"{cfg.embedding_model}|{cfg.reranker_model}"
        self.names = [cfg.embedding_model, cfg.reranker_model, cfg.verifier_model]
        self._embedder = SentenceTransformer(cfg.embedding_model, device=self.device)
        self._reranker = CrossEncoder(cfg.reranker_model, device=self.device)
        self._sigmoid = torch.nn.Sigmoid()
        self._nli_tok = AutoTokenizer.from_pretrained(cfg.verifier_model)
        self._nli = AutoModelForSequenceClassification.from_pretrained(cfg.verifier_model).to(self.device).eval()
        labels = {v.lower(): int(k) for k, v in self._nli.config.id2label.items()}
        self._entailment = labels.get("entailment", labels.get("supported", 1))

    def embed(self, texts, instruction=None):
        # "" rather than None: a model with a default prompt would otherwise apply it to passages too
        return self._embedder.encode(list(texts), prompt=f"Instruct: {instruction}\nQuery:" if instruction else "",
                                     normalize_embeddings=True, convert_to_numpy=True, batch_size=16)

    def judge(self, instruction, pairs):
        if not pairs:
            return []
        scores = self._reranker.predict(list(pairs), prompt=instruction, activation_fn=self._sigmoid, batch_size=8)
        return [float(s) for s in scores]

    def entail(self, pairs):
        out: list[float] = []
        for i in range(0, len(pairs), 16):
            batch = pairs[i:i + 16]
            enc = self._nli_tok([e for e, _ in batch], [c for _, c in batch], truncation="only_first", max_length=512,
                                padding=True, return_tensors="pt").to(self.device)
            with self._torch.no_grad():
                probs = self._torch.softmax(self._nli(**enc).logits.float(), dim=-1)
            out += [float(p) for p in probs[:, self._entailment]]
        return out


def not_installed() -> str:
    """Why the models are missing, and what adds them. The Mac app leaves them out on purpose (mac/build.sh)."""
    if "OYSTER_APP_VERSION" in os.environ:
        return ("the Mac app doesn't include them, since they need PyTorch (a few GB); running the app from its "
                "code adds them (docs/setup-from-code.md)")
    return ("sentence-transformers isn't installed; `pip install -r requirements.txt` in the app's folder adds it "
            "(with PyTorch, a few GB)")


_lock = threading.Lock()
_loaded: dict[tuple, Models] = {}


def load(cfg: EvidenceCfg) -> Models:
    """The models, loaded once per process (the first run downloads them). Raises Unavailable, with the reason,
    when they are switched off, not installed, or fail to load."""
    if not cfg.enabled:
        raise Unavailable("switched off in searches.yaml")
    key = (cfg.embedding_model, cfg.reranker_model, cfg.verifier_model, cfg.device)
    with _lock:
        if key not in _loaded:
            try:
                _loaded[key] = LocalModels(cfg)
            except ImportError:
                raise Unavailable(not_installed()) from None
            except Exception as e:  # noqa: BLE001 - a failed download or load must not stop the run
                raise Unavailable(f"couldn't load the evidence models: {e}") from e
        return _loaded[key]


class Index:
    """Passage vectors, cached on disk until the evidence or the models change."""

    def __init__(self, models: Models, passages: list[Passage], cache_dir: Optional[Path] = None):
        import numpy as np
        self.models, self.passages = models, passages
        sig = hashlib.sha256("\0".join([models.name, *(p.doc for p in passages)]).encode()).hexdigest()[:16]
        path = cache_dir / f"index-{sig}.npy" if cache_dir else None
        if path and path.exists():
            self.vectors = np.load(path)
        else:
            self.vectors = models.embed([p.doc for p in passages])
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                for old in path.parent.glob("index-*.npy"):
                    old.unlink()
                np.save(path, self.vectors)

    def nearest(self, queries: list[str], instruction: str, k: int) -> list[list[int]]:
        import numpy as np
        if not queries or not self.passages:
            return [[] for _ in queries]
        sims = self.models.embed(queries, instruction) @ self.vectors.T
        return [[int(i) for i in np.argsort(-row)[:k]] for row in sims]


# ---------------------------------------------------------------------------
# keyword evidence

@dataclass
class KeywordSupport:
    keyword: str
    score: float                                    # the reranker's best
    passages: list[tuple[Passage, float]]           # best first
    entailment: Optional[float] = None              # NLI, checked when the reranker rates it supported
    stated_by: Optional[Passage] = None             # the passage that states it

    @property
    def verdict(self) -> str:
        if self.score >= KEYWORD_SUPPORTED and (self.entailment or 0) >= KEYWORD_ENTAILED:
            return "supported"
        return "weak" if self.score >= KEYWORD_WEAK else "none"

    @property
    def evidence(self) -> Optional[Passage]:
        """The passage to cite: the one that states it, else the most relevant."""
        return self.stated_by or (self.passages[0][0] if self.passages else None)


def keyword_support(index: Index, labels: list[str], k: int = 12, top: int = 3) -> list[KeywordSupport]:
    """How well the evidence supports each keyword label, best supported first. The query is the label alone:
    adding the posting sentence it came from made the judge rate the sentence, not the keyword ("benchmarks"
    scored 0.92 against an onboarding passage)."""
    near = index.nearest(labels, KEYWORD_SEARCH, k)
    scores = iter(index.models.judge(KEYWORD_JUDGE, [(q, index.passages[i].doc) for q, ids in zip(labels, near)
                                                     for i in ids]))
    out = []
    for label, ids in zip(labels, near):
        ranked = sorted(((index.passages[i], next(scores)) for i in ids), key=lambda x: -x[1])[:top]
        out.append(KeywordSupport(label, ranked[0][1] if ranked else 0.0, ranked))
    # the reranker scores relevance; supported also needs a passage that states the work
    check = [s for s in out if s.score >= KEYWORD_SUPPORTED]
    pairs = [(p.text, KEYWORD_CLAIM.format(keyword=s.keyword)) for s in check for p, _ in s.passages]
    entail = iter(index.models.entail(pairs) if pairs else [])
    for s in check:
        stated = max(((p, next(entail)) for p, _ in s.passages), key=lambda x: x[1])
        s.stated_by, s.entailment = (stated[0] if stated[1] >= KEYWORD_ENTAILED else None), stated[1]
    return sorted(out, key=lambda s: (s.verdict != "supported", -s.score))


def keyword_table(results: list[KeywordSupport], models: str = "") -> str:
    lines = ["# Keyword evidence", "",
             "Posting keywords the resume is missing, checked against the impact record, resume variants and "
             f"confirmed facts{f' by {models}' if models else ''}. supported: a passage is relevant (score "
             f"{KEYWORD_SUPPORTED:.2f}+) and states the work (entailment {KEYWORD_ENTAILED:.2f}+), so the phrase can "
             "truthfully go in where that evidence is used. weak: relevant, but no passage states it; a question for "
             "the candidate, not an add. none: don't add.", "",
             "| Keyword | Verdict | Relevance | Stated | Evidence |", "|---|---|---|---|---|"]
    for r in results:
        stated = "" if r.entailment is None else f"{r.entailment:.2f}"
        lines.append(f"| {r.keyword} | {r.verdict} | {r.score:.2f} | {stated} | "
                     f"{r.evidence.source if r.evidence else ''} |")
    for r in results:
        if r.verdict != "none":
            lines += ["", f"## {r.keyword} ({r.verdict})"]
            if r.stated_by:
                lines.append(f"- Stated by {r.stated_by.source}: {r.stated_by.text[:400]}")
            elif r.entailment is not None:
                lines.append("- Relevant passages, but none states this work:")
            lines += [f"- [{s:.2f}] {p.source}: {p.text[:400]}" for p, s in r.passages
                      if s >= KEYWORD_WEAK and p is not r.stated_by]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# line checks

FIGURE = re.compile(r"\d[\d,]*(?:\.\d+)?")
SKIP_SECTIONS = {"CORE SKILLS", "EDUCATION", "CERTIFICATIONS", "INTERESTS"}


def figures(text: str) -> list[str]:
    """The numbers in a text, with thousands separators dropped: "1,200 repos, 99.5%" -> ["1200", "99.5"]."""
    out = []
    for m in FIGURE.findall(text):
        n = m.rstrip(",").replace(",", "")
        if n not in out:
            out.append(n)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", s)).strip().lower()


def resume_lines(md: str) -> list[tuple[str, str]]:
    """(section, line) for every claim line of a resume: summary sentences, then the bullets and paragraphs under
    the experience headings. Headings, job and date lines, CORE SKILLS and EDUCATION are not claims to check."""
    out, sec = [], ""
    for raw in md.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            sec = _clean(line[3:]).upper()
            continue
        if not line or line.startswith("#") or sec in SKIP_SECTIONS or not sec:
            continue
        if re.match(r"^(\*\*[^*]+\*\*\s*\||\*[^*].*\*$)", line):   # "**Company** | Title", "*dates | place*"
            continue
        text = _clean(re.sub(r"^[-*+]\s+", "", line))
        if sec == "SUMMARY":
            out += [(sec, s) for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) >= 4]
        elif len(text.split()) >= 4:
            out.append((sec, text))
    return out


def trace_rows(trace_md: str) -> list[tuple[str, str, list[str]]]:
    """(line label, starts-with words, ledger IDs) for each row of a trace table."""
    rows = []
    for line in trace_md.splitlines():
        if not line.strip().startswith("|") or re.match(r"^\|[\s|:-]+\|?$", line.strip()):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[1].lower().startswith("starts with"):
            continue
        rows.append((cells[0], _norm(cells[1]), re.findall(r"\b([EU]\d+[a-z]?)\b", line)))
    return rows


def ledger(match_md: str) -> dict[str, str]:
    """The evidence ledger in 04-match.md: {"E1": "Impact record §1 ... allowed verb/scope: ...", ...}."""
    m = re.search(r"^##\s+Evidence ledger\s*\n(.*?)(?=^##\s|\Z)", match_md, re.S | re.M)
    if not m:
        return {}
    parts = re.split(r"^[\W_]*(E\d+[a-z]?)[*_\s]*[—–:|-]", m.group(1), flags=re.M)     # "E1 —", "- **E1** —"
    return {parts[i]: _clean(parts[i + 1]) for i in range(1, len(parts) - 1, 2)}


def _cited(line: str, rows: list[tuple[str, str, list[str]]]) -> Optional[list[str]]:
    """The ledger IDs of the trace row that starts like this line (first six words), or None."""
    words = _norm(line).split()
    for label, start, ids in rows:
        sw = start.split()
        n = min(6, len(sw), len(words))
        if n and sw[:n] == words[:n]:
            return ids
    return None


def claims(line: str) -> list[str]:
    """The factual clauses of a resume line. A bullet leads with the posting's phrase ("Drove operational
    excellence: built ..."); the lead phrase is the keyword, checked by keyword evidence, so it is dropped here.
    Semicolons separate claims."""
    lead, sep, rest = line.partition(": ")
    if sep and len(lead.split()) <= 8 and len(rest.split()) >= 4:
        line = rest
    out: list[str] = []
    for part in re.split(r";\s+", line):
        if out and len(part.split()) < 4:
            out[-1] += "; " + part
        elif part.strip():
            out.append(part.strip())
    return out


@dataclass
class LineCheck:
    section: str
    line: str
    cited: list[str]
    missing_figures: list[str]
    support: Optional[float] = None         # None when the models are unavailable
    best: Optional[str] = None              # the source of the evidence for the least supported clause
    weakest: Optional[str] = None           # that clause

    @property
    def flagged(self) -> bool:
        """Summary sentences combine several claims and are only checked for figures."""
        return bool(self.missing_figures) or (self.section != "SUMMARY" and self.support is not None
                                              and self.support < LINE_UNSUPPORTED)


def check_lines(md: str, trace_md: str, match_md: str, passages: list[Passage],
                index: Optional[Index] = None, k: int = 4) -> list[LineCheck]:
    """Check every claim line of a resume. Each clause's evidence is the line's cited ledger entries plus, with the
    models, the k passages closest to that clause. A line's figures must appear in its evidence or the confirmed
    facts (without the models, or for a summary sentence, anywhere in the evidence). Its support is its weakest
    clause's best entailment score."""
    led = ledger(match_md)
    everywhere = set(figures("\n".join([*(p.text for p in passages), *led.values()])))
    confirmed = set(figures(" ".join(p.text for p in passages if CONFIRMED.search(p.source))))
    rows = trace_rows(trace_md)
    checks, summary_ids = [], None
    for sec, line in resume_lines(md):
        ids = _cited(line, rows)
        if sec == "SUMMARY":                    # one trace row covers the summary; it starts like its first sentence
            ids = summary_ids = ids if ids is not None else (
                summary_ids or next((i for label, _, i in rows if "summary" in label.lower()), []))
        checks.append(LineCheck(sec, line, ids or [], []))
    parts = [(n, cl) for n, c in enumerate(checks) for cl in claims(c.line)] if index else []
    near = index.nearest([cl for _, cl in parts], LINE_SEARCH, k) if parts else []
    part_docs = []
    for (n, _), ids in zip(parts, near):
        d = ([(f"Evidence ledger {i}", led[i]) for i in checks[n].cited if i in led]
             + [(index.passages[i].source, index.passages[i].text) for i in ids])
        # and all of it together: a detail can come from one passage and the rest from another
        part_docs.append(d + [("Combined evidence", " ".join(t for _, t in d))] if len(d) > 1 else d)
    for n, c in enumerate(checks):
        if index and c.section != "SUMMARY":
            known = confirmed | set(figures(" ".join(t for (m, _), d in zip(parts, part_docs) if m == n for _, t in d)))
        else:
            known = everywhere
        c.missing_figures = [f for f in figures(c.line) if f not in known]
    if parts:
        scores = iter(index.models.entail([(text, cl) for (_, cl), d in zip(parts, part_docs) for _, text in d]))
        for (n, cl), d in zip(parts, part_docs):
            best = max(((src, next(scores)) for src, _ in d), key=lambda x: x[1], default=None)
            c = checks[n]
            if best and (c.support is None or best[1] < c.support):
                c.best, c.support, c.weakest = best[0], best[1], cl
    return checks


def honesty_table(checks: list[LineCheck], models: str = "") -> str:
    flagged = [c for c in checks if c.flagged]
    support = (f"Support is the probability{f' ({models})' if models else ''} that the cited ledger entries or one "
               f"of the closest passages entails the line; under {LINE_UNSUPPORTED:.2f} is flagged." if any(
                   c.support is not None for c in checks) else
               "The support models were not available for this run, so only figures were checked.")
    lines = ["# Honesty check", "",
             f"{len(checks)} resume lines checked, {len(flagged)} flagged. Every figure in a line must appear in the "
             f"evidence (impact record, resume variants, confirmed facts, evidence ledger). {support}", "",
             "| Line | Cited | Figures not in evidence | Support | Best evidence | Flag |", "|---|---|---|---|---|---|"]
    for c in checks:
        sup = "" if c.support is None else f"{c.support:.2f}"
        lines.append(f"| {c.line[:90].replace('|', '/')} | {', '.join(c.cited)} | {', '.join(c.missing_figures)} | "
                     f"{sup} | {(c.best or '')[:60]} | {'FLAG' if c.flagged else ''} |")
    if flagged:
        lines += ["", "## Flagged lines", ""]
        for c in flagged:
            why = []
            if c.missing_figures:
                why.append("figures not found in any evidence: " + ", ".join(c.missing_figures))
            if c.support is not None and c.support < LINE_UNSUPPORTED and c.section != "SUMMARY":
                why.append(f"support {c.support:.2f}" + (f' for "{c.weakest}"' if c.weakest != c.line else ""))
            lines.append(f"- {c.line}\n  ({'; '.join(why)})")
    return "\n".join(lines) + "\n"

"""Read the pay a posting states, when the listing itself didn't give any.

The triage model returns a small JSON reading. A number is kept only when the quote it came from
is in the posting, so a guessed salary never lands on the job. Pay the source already gave
(min/max yearly USD, or a salary string) is left as it is.

needs_parse_pay_run is 1 when an automatic reading is still due, and 0 after one finished attempt.
A missing flag is due too, so jobs stored before the flag existed are read. A new job starts at 1
unless it already has source pay. A changed description sets it back to 1, and clears pay that an
earlier reading wrote. Find pay (force=True) reads again anyway.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any, Optional
from urllib.parse import quote

from . import config
from .jd import MIN_PASTED_CHARS, posting_url
from .jobs_api import salary_text
from .llm import AgentCall, AgentError
from .store import now_iso

WATCH = True                         # the app's background reader; tests turn it off
PERIODS = ("year", "hour", "day", "week", "month")
BASES = ("base", "total", "ote", "unknown")
PAY_FIELDS = ("min_annual_salary_usd", "max_annual_salary_usd", "salary_string")
# What a finished reading leaves on the job, kept when a search stores the same posting again.
KEEP = (*PAY_FIELDS, "pay_origin", "pay_parse", "pay_parsed_at", "pay_parse_for", "needs_parse_pay_run")
CLEAR = (*PAY_FIELDS, "pay_origin", "pay_parse", "pay_parsed_at", "pay_parse_for")
RETRY_AFTER = 900                    # a failed call is left for the next pass, not retried at once

INSTRUCTIONS = """You read one job posting and report the pay it states. Return one file and nothing else.

<file name="pay.json">
{"stated": false, "currency": null, "period": null, "min": null, "max": null, "basis": null, "quote": ""}
</file>

Rules:
- Copy only a pay figure or range the posting itself writes. If it gives none, stated is false and every other field is null and quote is "".
- quote is the posting's own line that states the numbers, copied verbatim.
- min and max are numbers with commas removed (220000, not 220,000). A single figure goes in both min and max.
- currency is a 3-letter code such as USD, EUR or GBP. period is year, hour, day, week or month.
- basis is base (salary), total (the posting says the range includes bonus, equity or benefits), ote (on-target earnings), or unknown.
- When the posting gives several ranges, use the one for the job's location. If you cannot tell which, use the first range it states.
- Do not infer pay from the title, the level, or words like competitive or commensurate.
"""

NOT_STATED = {"stated": False, "currency": None, "period": None, "min": None, "max": None, "basis": None, "quote": ""}
_UNIT = {"hour": " / hour", "day": " / day", "week": " / week", "month": " / month"}


class NoPosting(ValueError):
    """The job has no posting text long enough to read."""


def listed_pay(job: dict) -> bool:
    """The job carries a pay figure or a pay string, wherever it came from."""
    if job.get("min_annual_salary_usd") or job.get("max_annual_salary_usd"):
        return True
    return bool((job.get("salary_string") or "").strip())


def has_source_pay(job: dict) -> bool:
    """Pay the listing gave. A figure this reading wrote (pay_origin parse) is not source pay."""
    return job.get("pay_origin") != "parse" and listed_pay(job)


def long_enough(text: str) -> bool:
    return len((text or "").strip()) >= MIN_PASTED_CHARS


def description_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ").replace("\u2009", " ")).strip().lower()


def quote_in(quote: str, description: str) -> bool:
    q = _norm(quote)
    return bool(q) and q in _norm(description)


def number_in_quote(n: float, quote: str) -> bool:
    """The figure, as digits or as thousands (220,000 or 220k), appears in the quote."""
    if n is None:
        return True
    if float(n) != int(n):
        return re.search(rf"(?<![\d.]){re.escape(str(n))}(?!\d)", quote.lower()) is not None
    i = int(n)
    digits = quote.lower().replace(",", "")
    if re.search(rf"(?<![\d.]){i}(?!\d)", digits):
        return True
    if i >= 1000 and i % 1000 == 0:
        return re.search(rf"(?<![\d.]){i // 1000}k(?!\d)", quote.lower()) is not None
    return False


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    n = float(v)
    if n != n:                       # NaN
        return None
    return int(n) if n == int(n) else n


def normalize(raw: Any, description: str) -> dict:
    """The reading to keep. A quote that isn't in the posting, or numbers that aren't in the quote,
    count as the posting stating no pay. A reading that isn't the shape we asked for raises ValueError,
    so that attempt can be tried again."""
    if not isinstance(raw, dict) or "stated" not in raw:
        raise ValueError("pay.json has no stated field")
    if not raw.get("stated"):
        return dict(NOT_STATED)
    quote = str(raw.get("quote") or "").strip()
    period = str(raw.get("period") or "").lower()
    basis = str(raw.get("basis") or "unknown").lower()
    currency = str(raw.get("currency") or "").upper()
    if not quote_in(quote, description) or period not in PERIODS or basis not in BASES \
            or not re.fullmatch(r"[A-Z]{3}", currency):
        return dict(NOT_STATED)
    try:
        lo, hi = _num(raw.get("min")), _num(raw.get("max"))
    except (TypeError, ValueError):
        return dict(NOT_STATED)
    if lo is None and hi is None:
        return dict(NOT_STATED)
    lo, hi = lo if lo is not None else hi, hi if hi is not None else lo
    if lo > hi:
        lo, hi = hi, lo
    if not number_in_quote(lo, quote) or not number_in_quote(hi, quote):
        return dict(NOT_STATED)
    if period == "year" and not (10_000 <= lo <= 5_000_000 and 10_000 <= hi <= 5_000_000):
        return dict(NOT_STATED)
    if period != "year" and (lo <= 0 or hi <= 0):
        return dict(NOT_STATED)
    return {"stated": True, "currency": currency, "period": period, "min": lo, "max": hi, "basis": basis, "quote": quote}


def pay_string(reading: dict) -> str:
    """How a non-yearly-USD reading is shown: '$45–$60 / hour', 'EUR 90k–110k'."""
    lo, hi = reading["min"], reading["max"]
    cur, period = reading["currency"], reading["period"]

    def one(n: float) -> str:
        if period == "year" and n >= 1000 and n % 1000 == 0:
            return f"{n / 1000:,.0f}k"
        return f"{n:,.0f}" if float(n) == int(n) else f"{n:g}"

    def usd(n: float) -> str:
        return f"${one(n)}"

    if cur == "USD":
        body = usd(lo) if lo == hi else f"{usd(lo)}–{usd(hi)}"
        return body + _UNIT.get(period, "")
    body = one(lo) if lo == hi else f"{one(lo)}–{one(hi)}"
    return f"{cur} {body}{_UNIT.get(period, '')}"


def apply_fields(job: dict, reading: dict, text: str) -> dict:
    """Fields to write after a finished attempt. Source pay is left in place; the reading is kept beside it.
    None removes a field a previous reading wrote."""
    fields: dict[str, Any] = {
        "pay_parse": reading, "pay_parsed_at": now_iso(), "pay_parse_for": description_hash(text),
        "needs_parse_pay_run": 0,
    }
    if has_source_pay(job):
        return fields
    if not reading["stated"]:
        if job.get("pay_origin") == "parse":
            fields.update({k: None for k in (*PAY_FIELDS, "pay_origin")})
        return fields
    fields["pay_origin"] = "parse"
    if reading["period"] == "year" and reading["currency"] == "USD":
        fields["min_annual_salary_usd"] = reading["min"]
        fields["max_annual_salary_usd"] = reading["max"]
        fields["salary_string"] = None
    else:
        fields["salary_string"] = pay_string(reading)
        fields["min_annual_salary_usd"] = None
        fields["max_annual_salary_usd"] = None
    return fields


def merge_save(old: dict, job: dict) -> dict:
    """The job about to be stored. A new job with no source pay needs a reading. A search that finds the
    same posting again keeps the reading it already has. A changed description needs a new reading, and
    drops pay an earlier reading wrote. Source pay is never dropped."""
    job = dict(job)
    if not old:
        if has_source_pay(job):
            job["pay_origin"] = "source"
            job["needs_parse_pay_run"] = 0
        else:
            job["needs_parse_pay_run"] = 1
        return job
    if (job.get("description") or "").strip() == (old.get("description") or "").strip():
        if has_source_pay(job):
            job["pay_origin"] = "source"
            job["needs_parse_pay_run"] = 0
            for k in ("pay_parse", "pay_parsed_at", "pay_parse_for"):
                if k in old and k not in job:
                    job[k] = old[k]
            return job
        for k in KEEP:
            if k in old:
                job[k] = old[k]
        return job
    if has_source_pay(job) or (has_source_pay(old) and old.get("pay_origin") != "parse"):
        if not has_source_pay(job):
            for k in PAY_FIELDS:
                if old.get(k) not in (None, ""):
                    job[k] = old[k]
        job["pay_origin"] = "source"
        job["needs_parse_pay_run"] = 0
        for k in ("pay_parse", "pay_parsed_at", "pay_parse_for"):
            job.pop(k, None)
        return job
    for k in CLEAR:
        job.pop(k, None)
    job["needs_parse_pay_run"] = 1
    return job


def note_change(job: dict) -> None:
    """The stored job's description just changed. Source pay stays; parsed pay is cleared so the next
    reading can fill it."""
    if has_source_pay(job):
        job["needs_parse_pay_run"] = 0
        job.setdefault("pay_origin", "source")
        return
    if job.get("pay_origin") == "parse":
        for k in CLEAR:
            job.pop(k, None)
    job["needs_parse_pay_run"] = 1


def automatic_due(job: dict) -> bool:
    """An automatic reading is due: no source pay, a real posting, and the flag is missing or 1.
    Flag 0 is a finished attempt and is not read again until the description changes."""
    if has_source_pay(job) or not long_enough(job.get("description") or ""):
        return False
    return job.get("needs_parse_pay_run") != 0


def _jobs(store: Any):
    d = store.jobs_dir
    if not d.exists():
        return
    for path in sorted(d.glob("*.json")):
        try:
            raw = path.read_text()
            job = json.loads(raw)
        except (OSError, ValueError):
            continue
        if isinstance(job, dict):
            yield job


def jobs_due(store: Any, *, limit: int, mark_source: bool = True) -> list[str]:
    """Job ids an automatic pass should read: flag 1 first, then jobs that have no flag yet.
    Flag 0 is skipped. A job that already has source pay is marked done instead of read, unless
    mark_source is off."""
    flagged, missing = [], []
    for job in _jobs(store):
        jid = str(job.get("id") or "")
        if not jid:
            continue
        if has_source_pay(job):
            if mark_source and job.get("needs_parse_pay_run") != 0:
                store.update_job(jid, needs_parse_pay_run=0, pay_origin=job.get("pay_origin") or "source")
            continue
        if not automatic_due(job):
            continue
        (flagged if job.get("needs_parse_pay_run") == 1 else missing).append(jid)
    return (flagged + missing)[:limit]


class Status:
    """Which jobs a reading is running for, and how many have finished, for the page to follow."""

    def __init__(self) -> None:
        self.active: set[str] = set()
        self.finished = 0
        self.failed_until: dict[str, float] = {}
        self.lock = asyncio.Lock()

    def public(self) -> dict:
        return {"active": sorted(self.active), "finished": self.finished}


async def ask(runner: Any, model: Any, job: dict, text: str) -> dict:
    call = AgentCall(
        label=f"pay:{job.get('id')}", model=model, instructions=INSTRUCTIONS, candidate=False,
        documents={"job.txt": "\n".join((
            f"Title: {job.get('job_title') or ''}",
            f"Company: {job.get('company') or ''}",
            f"Location: {job.get('location') or ''}",
        )), "posting.txt": text},
        expect=["pay.json"])
    res = await runner.run(call)
    try:
        raw = json.loads(res.files["pay.json"])
    except (KeyError, json.JSONDecodeError) as e:
        raise AgentError(f"pay: bad pay.json ({e})") from e
    return normalize(raw, text)


def _with_fields(job: dict, fields: dict) -> dict:
    merged = dict(job)
    for key, value in fields.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


async def parse_job(store: Any, runner: Any, model: Any, jid: str, *, force: bool = False,
                    dry_run: bool = False) -> dict:
    """Read one job's pay and store it. force is Find pay and `parse-pay --job`: it reads even when a
    reading already finished, and still does not replace source pay. A failed call leaves the flag at 1.
    dry_run asks the model and returns the reading without writing. Returns {id, pay, stated, kept_source}
    or {id, skipped} or {id, error}; a dry run also includes reading."""
    job = store.job(jid)
    if not job:
        raise KeyError(jid)
    text = (job.get("description") or "").strip()
    if force and not long_enough(text):
        raise NoPosting("There's no posting text to read pay from.")
    if not force:
        if has_source_pay(job):
            if not dry_run and job.get("needs_parse_pay_run") != 0:
                store.update_job(jid, needs_parse_pay_run=0, pay_origin=job.get("pay_origin") or "source")
            return {"id": jid, "skipped": "source"}
        if not automatic_due(job):
            return {"id": jid, "skipped": "not due"}
    try:
        reading = await ask(runner, model, job, text)
    except Exception as e:  # noqa: BLE001 - the caller retries an automatic miss; Find pay shows it
        if force:
            raise
        return {"id": jid, "error": str(e).splitlines()[0][:200] if str(e) else "no reply"}
    source = has_source_pay(job)
    fields = apply_fields(job, reading, text)
    if dry_run:
        return {"id": jid, "pay": salary_text(_with_fields(job, fields)), "stated": bool(reading["stated"]),
                "kept_source": source, "reading": reading}
    store.update_job(jid, **fields)
    job = store.job(jid)
    return {"id": jid, "pay": salary_text(job), "stated": bool(reading["stated"]), "kept_source": source}


def next_flagged(store: Any, status: Status) -> Optional[str]:
    """The next job whose flag is 1, or missing. Flag 0 is skipped. Flag 1 is taken before a missing
    flag, so a job just stored isn't stuck behind the older library. Source pay is marked done.
    Short text and a recent failure are skipped."""
    now = time.time()
    missing = None
    for job in _jobs(store):
        if job.get("needs_parse_pay_run") == 0:
            continue
        jid = str(job.get("id") or "")
        if not jid or jid in status.active or status.failed_until.get(jid, 0) > now:
            continue
        if has_source_pay(job):
            store.update_job(jid, needs_parse_pay_run=0, pay_origin=job.get("pay_origin") or "source")
            continue
        if not long_enough(job.get("description") or ""):
            continue
        if job.get("needs_parse_pay_run") == 1:
            return jid
        missing = missing or jid
    return missing


async def run_locked(status: Status, jid: str, fn) -> Any:
    """One reading at a time. A skip (source pay, or nothing due) doesn't count as finished."""
    async with status.lock:
        status.active.add(jid)
        try:
            result = await fn()
        finally:
            status.active.discard(jid)
        if isinstance(result, dict) and not result.get("skipped") and not result.get("error"):
            status.finished += 1
        return result


async def watch(store: Any, runner_get, model: Any, status: Status) -> None:
    """One reading at a time, for jobs whose flag is missing or 1. Flag 0 waits until the description
    changes. While the app is open this keeps going; it is not a batch at startup."""
    while True:
        try:
            if status.active:
                await asyncio.sleep(0.5)
                continue
            jid = next_flagged(store, status)
            if not jid:
                await asyncio.sleep(3)
                continue

            async def once(jid=jid):
                return await parse_job(store, runner_get(), model, jid)

            result = await run_locked(status, jid, once)
            if result.get("error"):
                status.failed_until[jid] = time.time() + RETRY_AFTER
            else:
                status.failed_until.pop(jid, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(3)


async def run_many(store: Any, runner: Any, model: Any, *, job: Optional[str] = None, limit: int = 50,
                   dry_run: bool = False, log=print) -> int:
    """Read pay for one job (--job forces it) or for up to `limit` jobs that need it. dry_run prints each
    reading and does not write. Returns how many readings finished."""
    if job:
        try:
            result = await parse_job(store, runner, model, job, force=True, dry_run=dry_run)
        except KeyError:
            log(f"No stored job {job}.")
            return 0
        except NoPosting as e:
            log(str(e))
            return 0
        except Exception as e:  # noqa: BLE001
            log(f"  {job}: {str(e).splitlines()[0][:200]}")
            return 0
        if dry_run:
            log("Dry run. Nothing is saved.")
        log(_report(store, job, result, dry_run=dry_run))
        return 0 if result.get("error") else 1
    ids = jobs_due(store, limit=limit, mark_source=not dry_run)
    if not ids:
        log("No jobs need a pay reading.")
        return 0
    if dry_run:
        log(f"Dry run: reading pay for {len(ids)} job(s). Nothing is saved.")
    else:
        log(f"Reading pay for {len(ids)} job(s).")
    done = 0
    for jid in ids:
        try:
            result = await parse_job(store, runner, model, jid, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            log(f"  {jid}: {str(e).splitlines()[0][:200]}")
            continue
        log(_report(store, jid, result, dry_run=dry_run))
        if not result.get("error") and not result.get("skipped"):
            done += 1
    return done


def app_link(jid: str) -> str:
    """Open this job in the app that is serving this profile. Empty when that app is not running:
    the address is whatever that process wrote, not a fixed host or port."""
    try:
        origin = (config.DATA / "web-address").read_text().strip().rstrip("/")
    except OSError:
        return ""
    if not origin.startswith(("http://", "https://")):
        return ""
    return f"{origin}/#job={quote(jid, safe='')}&tab=posting"


def _where(job: dict, jid: str) -> list[str]:
    """The posting, then this job in the app, when each is known."""
    lines = []
    link = posting_url(job).strip()
    if link:
        lines.append(f"    {link}")
    here = app_link(jid)
    if here:
        lines.append(f"    {here}")
    return lines


def _report(store: Any, jid: str, result: dict, *, dry_run: bool = False) -> str:
    job = store.job(jid) or {}
    who = f"{job.get('job_title') or jid} @ {job.get('company') or ''}".strip(" @")
    if result.get("error"):
        return f"  {who}: {result['error']}"
    if result.get("skipped") == "source":
        return f"  {who}: already has pay"
    reading = result.get("reading")
    if dry_run and reading is not None:
        lines = [f"  {who}", *_where(job, jid)]
        quote = str(reading.get("quote") or "").strip()
        if quote:
            lines.append(f"    {quote}")
        lines.append("    " + json.dumps(reading, ensure_ascii=False))
        if result.get("stated"):
            kept = " (would keep the pay already on the job)" if result.get("kept_source") else ""
            lines.append(f"    would show {result.get('pay')}{kept}")
        else:
            lines.append("    no pay stated")
        return "\n".join(lines)
    if result.get("stated"):
        kept = " (kept the pay already on the job)" if result.get("kept_source") else ""
        head = f"  {who}: {result.get('pay')}{kept}"
    else:
        head = f"  {who}: no pay stated"
    return "\n".join([head, *_where(job, jid)])

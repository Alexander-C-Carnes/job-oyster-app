"""Command line: python -m jobpipe <command> ...

  preflight            Check filters: count matches for each saved search (at most 1 credit per search)
  search               Fetch jobs (saved searches, or an ad-hoc query from flags); no Claude calls
  run                  search -> signal score -> make résumés for the best -> Notion -> digest
  tailor JOB_ID        Make résumé for one stored job (or --jd-file for a pasted posting)
  jobs                 List stored jobs and their signal scores
  startups refresh     Read the funding feeds, the YC directory and VC boards into data/startups.json, then the
                       startups' careers boards: roles matching your filters land in Search Jobs (free)
  startups scan        Just the careers boards (--limit N startups, newest raise first)
  startups score       Signal-score the startup roles in Search Jobs against the impact record (--limit N)
  startups list        Stored startups, newest raise first (--stage "Series B", --hiring, --days N)
  startups roles ID    A startup's open roles from its careers board; --add stores the matching ones as jobs
  startups enrich ID   Look up a startup's latest round (needs FUNDABLE_API_KEY or PDL_API_KEY)
  check-postings       Re-check that roles are still open; closed ones in Search Jobs go to Dismissed, tracked ones are tagged Closed
  credits              JobsPipe credit ledger
  serve                Local web app (job board, searches, runs, résumé review and edits)
  show-request         Print the request body each saved search would send (free)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import config
from .config import adhoc_search
from .jobs_api import salary_text, work_mode


def _load_env() -> None:
    """Read KEY=VALUE lines from the profile's .env (then the repo's, if there is one) into the
    environment, without overriding real env vars."""
    import os
    for env in (config.PROFILE / ".env", config.ROOT / ".env"):
        if not env.exists():
            continue
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                # Skip blanks: an empty ANTHROPIC_API_KEY would shadow an `ant auth login` profile.
                if v:
                    os.environ.setdefault(k.strip(), v)
    config.set_profile(config.PROFILE)   # picks up JOBPIPE_DATA_DIR / JOBPIPE_OUTPUTS_DIR set in .env


def _need_jobspipe_key() -> None:
    import os
    if not os.environ.get("JOBSPIPE_API_KEY"):
        sys.exit(f"JOBSPIPE_API_KEY is not set. Put it in {config.PROFILE / '.env'} or export it.")


def _searches(cfg, args):
    if getattr(args, "search_json", None):
        return [config.one_off_search(json.loads(args.search_json), cfg)]
    if getattr(args, "title", None):
        base = cfg.searches[0] if cfg.searches else None
        return [adhoc_search(title=args.title, remote=args.remote, hybrid=args.hybrid,
                             min_salary=args.min_salary, locations=args.location or [],
                             days=args.days, limit=args.limit, country=args.country, defaults=base)]
    if getattr(args, "search", None):
        return [cfg.search(s) for s in args.search]
    if getattr(args, "hiringcafe", None):     # only those HiringCafe searches
        return []
    return cfg.searches


def _hiringcafe(cfg, args):
    """The HiringCafe searches a run checks: the ones named, else every one not paused (none for a one-off search)."""
    if getattr(args, "search_json", None) or getattr(args, "title", None):
        return []
    if getattr(args, "hiringcafe", None):
        return [cfg.hiringcafe_search(s) for s in args.hiringcafe]
    return [s for s in cfg.hiringcafe if not s.paused]


def _add_hiringcafe_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument("--hiringcafe", action="append", metavar="ID",
                   help="check only this HiringCafe search from searches.yaml (repeatable); by default a run checks "
                        "every one not paused")


def _add_query_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--search", action="append", help="saved search id from searches.yaml (repeatable)")
    p.add_argument("--search-json", help="one search, not saved, as JSON with searches.yaml's fields "
                                         "(the web app's Search once); blank fields use the defaults")
    g = p.add_argument_group("ad-hoc query (instead of saved searches)")
    g.add_argument("--title", action="append", help='title phrase, e.g. "Director of Engineering" (repeatable)')
    g.add_argument("--remote", action="store_true")
    g.add_argument("--hybrid", action="store_true")
    g.add_argument("--location", action="append", help='e.g. "New York" (repeatable)')
    g.add_argument("--min-salary", type=int, default=300000)
    g.add_argument("--days", type=int, default=7, help="posted within N days")
    g.add_argument("--limit", type=int, default=10, help="max jobs (= max credits) for this query")
    g.add_argument("--country", default="US", help='a country code, a region (EU, Schengen, EEA, Europe, EMEA), '
                                                  'several joined by commas ("EU,Schengen"), or Any')


def cmd_show_request(cfg, args):
    for s in _searches(cfg, args):
        print(f"# {s.id}: {s.name}")
        print(json.dumps(s.request_body(), indent=2))


def cmd_preflight(cfg, args):
    from .jobs_api import JobsClient
    from .store import Store
    import jobspipe
    client = JobsClient(cfg, Store())
    for s in _searches(cfg, args):
        try:
            r = client.size(s)
        except jobspipe.BadRequestError as e:
            print(f"{s.id}: API rejected the request (1 credit): {e.body or e.message}")
            continue
        if r.skipped_reason:
            print(f"{s.id}: skipped, {r.skipped_reason}")
            continue
        sample = r.jobs[0] if r.jobs else None
        print(f"{s.id}: {r.total_results if r.total_results is not None else '?'} matching jobs "
              f"(charged {r.credits_charged if r.credits_charged is not None else len(r.jobs)})"
              + (f"; e.g. {sample['job_title']} @ {sample.get('company')}, {salary_text(sample)}, {work_mode(sample)}"
                 if sample else ""))


def cmd_search(cfg, args):
    from .pipeline import Pipeline
    p = Pipeline(cfg, use_notion=False, runner=_NoLLM())
    cands, skipped = p.search(_searches(cfg, args), _hiringcafe(cfg, args))
    for c in cands:
        print(f"{c.id}  {c.meta.title} @ {c.meta.company} | {c.meta.location} {work_mode(c.job)} | "
              f"{c.meta.salary} | {c.meta.url}" + (f"  [{', '.join(c.flags)}]" if c.flags else ""))
    for s in skipped:
        print("skipped:", s)
    print(f"credits charged: {p.jobs.budget.spent_this_run}")


def cmd_run(cfg, args):
    from .pipeline import Pipeline
    p = Pipeline(cfg, use_notion=not args.no_notion, fetch_pages=not args.no_fetch)
    asyncio.run(p.run(_searches(cfg, args), top=args.top, tailor=not args.no_tailor, retriage=args.retriage,
                      hiringcafe=_hiringcafe(cfg, args)))
    _usage(p.runner)


def cmd_tailor(cfg, args):
    from .pipeline import Pipeline
    from .store import Store
    p = Pipeline(cfg, use_notion=not args.no_notion, fetch_pages=not args.no_fetch)
    if args.jd_file:
        text = Path(args.jd_file).read_text()
        job = {"id": args.job_id or "manual", "job_title": args.job_title or "Role", "company": args.company or "Company",
               "url": args.url or "", "description": text}
    else:
        job = Store().job(args.job_id)
        if not job:
            sys.exit(f"No stored job {args.job_id}. Run `search` first, or pass --jd-file.")
    c = asyncio.run(p.tailor_job(job))
    _usage(p.runner)
    if not c.tailored:   # a failed run must not look done (the web app's Runs tab goes by the exit status)
        sys.exit(f"\nMake résumé didn't finish: {c.error or 'no résumé was produced'}")
    t = c.tailored
    print(f"\nPDF: {t.pdf}\nReport: {t.report}\nHeat map: {t.heatmap}\nRun folder: {t.run_dir}")


def cmd_serve(cfg, args):
    from .web.server import serve
    serve(cfg, port=args.port, open_browser=not args.no_open)


def cmd_jobs(cfg, args):
    from .store import Store
    st = Store()
    rows = []
    for jid, s in st.state().items():
        j = st.job(jid)
        tri = s.get("triage") or {}
        rows.append((tri.get("fit_score", 0), jid, j.get("job_title"), j.get("company"), work_mode(j),
                     salary_text(j), ",".join(s.get("searches", [])), "tailored" if s.get("tailored_at") else ""))
    for r in sorted(rows, key=lambda r: -r[0]):
        print(f"{r[0] or '-':>2}  {r[1]}  {r[2]} @ {r[3]} | {r[4]} | {r[5]} | {r[6]} {r[7]}")


def cmd_credits(cfg, args):
    from .store import Store
    st = Store()
    c = st.credits()
    used = st.credits_used(cfg.allowance_period)
    print(f"Allowance: {cfg.allowance_amount} ({cfg.allowance_period}); used: {used}; "
          f"left: {cfg.allowance_amount - used}; per-run cap: {cfg.max_credits_per_run}")
    for m, n in sorted(c.get("months", {}).items()):
        print(f"  {m}: {n}")
    for call in c.get("calls", [])[-10:]:
        print(f"  {call['at']} {call['search']}: {call['status']}, {call['returned']} jobs, "
              f"charged {call['credits_charged']}")


def cmd_startups(cfg, args):
    import os
    from . import startups as su
    from .store import Store
    store = su.StartupStore(config.DATA / "startups.json")
    keys = {"fundable": os.environ.get("FUNDABLE_API_KEY", ""), "theirstack": os.environ.get("THEIRSTACK_API_KEY", ""),
            "pdl": os.environ.get("PDL_API_KEY", "")}
    exclude = cfg.startups.roles_exclude_titles
    if args.what == "refresh":
        new, upd = su.refresh(cfg.startups, store, keys, log=print, days=args.days, phrases=cfg.title_phrases,
                              searches=cfg.searches, jobs_store=Store(), tracked=_tracked())
        print(f"Startups: {new} new, {upd} seen again; {len(store.read())} stored in {store.path}")
        if cfg.startups.roles_signal_score and cfg.startups.roles_score_per_refresh:
            asyncio.run(su.score_startup_roles(cfg, Store(), limit=cfg.startups.roles_score_per_refresh, log=print))
    elif args.what == "scan":
        su.store_known_roles(store, cfg.searches, Store(), log=print, exclude=exclude)
        su.scan_boards(store, cfg.searches, Store(), limit=args.limit, log=print, exclude=exclude)
    elif args.what == "score":
        n = asyncio.run(su.score_startup_roles(cfg, Store(), limit=args.limit, log=print))
        print(f"Scored {n} startup role(s)")
    elif args.what == "track":
        floor = args.min_fit if args.min_fit is not None else cfg.track_min_fit("startup-")
        if floor is None:
            raise SystemExit("Set startups.roles.track_min_fit in searches.yaml, or pass --min-fit N")
        n = su.track_scored_roles(cfg, Store(), min_fit=floor, log=print)
        print(f"Added {n} startup role(s) scoring {floor}+ to the tracker")

    elif args.what == "list":
        rows = store.list()
        if args.stage:
            rows = [s for s in rows if (s.get("stage") or "Unknown").lower() == args.stage.lower()]
        if args.hiring:
            rows = [s for s in rows if s.get("hiring")]
        if args.days:
            since = su.since_date(args.days)
            rows = [s for s in rows if ((s.get("round") or {}).get("date") or "") >= since]
        for s in rows:
            print(f"{s['id']:40} {s.get('stage') or 'Unknown':10} {su.funding_line(su.funding_block(s)) or '-':44} {s['name']}"
                  + (f"  {s['website']}" if s.get("website") else ""))
        print(f"{len(rows)} startups")
    elif args.what in ("roles", "enrich"):
        s = store.get(args.id)
        if not s:
            sys.exit(f"No stored startup {args.id!r}. See `startups list`.")
        if args.what == "enrich":
            got, said = su.enrich(s, keys)
            if not got:
                sys.exit(f"Couldn't look it up: {said}")
            store.merge([{**got, "name": s["name"], "website": s.get("website", "")}])
            print(f"{s['name']}: {su.funding_line(su.funding_block(store.get(args.id)))} (from {said})")
            return
        roles = su.open_roles(s, cfg.title_phrases, log=print, searches=cfg.searches, exclude=exclude)
        store.update(args.id, roles=roles, ats=roles["board"], careers_url=roles["careers_url"] or s.get("careers_url", ""))
        if not roles["board"]:
            sys.exit(f"Couldn't find a Greenhouse, Lever or Ashby board for {s['name']}"
                     + (f"; its careers page is {roles['careers_url']}" if roles["careers_url"] else ""))
        print(f"{s['name']}: {len(roles['jobs'])} open roles on {roles['board']['url']}")
        for j in roles["jobs"]:
            print(f"  {'*' if j['match'] else ' '} {j['title']} | {j['location']}{' | remote' if j['remote'] else ''} | {j['url']}")
        if args.add:
            st = Store()
            n = 0
            for j in roles["jobs"]:
                if j["match"]:
                    st.save_job(su.role_as_job(s, j), "startups")
                    n += 1
            print(f"Added {n} matching role(s) to Search Jobs.")


def _tracked() -> dict[str, str]:
    """Stored job id -> tracker status, for the jobs in the tracker (a closed posting there is tagged, not dismissed)."""
    import sqlite3
    from contextlib import closing
    path = config.DATA / "tracker.db"
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path)) as con:
        return {r[0]: r[1] for r in con.execute("SELECT job_id, status FROM tracker WHERE job_id IS NOT NULL")}


def cmd_check_postings(cfg, args):
    from .postings import check_closed
    from .store import Store
    check_closed(Store(), tracked=_tracked(), limit=args.limit, log=print)


class _NoLLM:
    async def run(self, call):  # search-only commands never call the AI
        raise RuntimeError("no AI calls in this command")


def _usage(runner) -> None:
    log = getattr(runner, "usage_log", None)
    if not log:
        return
    tot = {k: sum(u[k] for u in log) for k in ("input", "output", "cache_read", "cache_write")}
    via = getattr(runner, "label", None) or ("Claude Code (your subscription)" if type(runner).__name__ == "ClaudeCodeRunner"
                                             else "Claude API (credits)")
    cost = [u["cost"] for u in log if "cost" in u]               # what OpenRouter says it charged
    print(f"AI usage via {via}: {len(log)} calls, {tot['input']:,} input + {tot['cache_write']:,} cache-write + "
          f"{tot['cache_read']:,} cache-read input tokens, {tot['output']:,} output tokens"
          + (f", ${sum(cost):.2f} charged" if cost else ""))


def main(argv=None) -> None:
    config.ensure_profile()
    _load_env()
    ap = argparse.ArgumentParser(prog="python -m jobpipe", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, help="searches.yaml path")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn, extra in (("show-request", cmd_show_request, True), ("preflight", cmd_preflight, True),
                            ("search", cmd_search, True)):
        p = sub.add_parser(name)
        _add_query_flags(p)
        if name == "search":
            _add_hiringcafe_flag(p)
        p.set_defaults(fn=fn)

    p = sub.add_parser("run")
    _add_query_flags(p)
    _add_hiringcafe_flag(p)
    p.add_argument("--top", type=int, help="how many résumés to make (default: pipeline.tailor_top)")
    p.add_argument("--no-tailor", action="store_true", help="signal score only, no résumés")
    p.add_argument("--no-notion", action="store_true")
    p.add_argument("--no-fetch", action="store_true", help="don't fetch employer pages for short listings")
    p.add_argument("--retriage", action="store_true", help="signal score again the jobs scored on earlier runs")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("tailor")
    p.add_argument("job_id", nargs="?", help="stored job id (from `search` or `jobs`)")
    p.add_argument("--jd-file", help="a pasted job description instead of a stored job")
    p.add_argument("--job-title")
    p.add_argument("--company")
    p.add_argument("--url")
    p.add_argument("--no-notion", action="store_true")
    p.add_argument("--no-fetch", action="store_true")
    p.set_defaults(fn=cmd_tailor)

    p = sub.add_parser("serve", help="local web app")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true", help="don't open the browser")
    p.set_defaults(fn=cmd_serve)

    sub.add_parser("jobs").set_defaults(fn=cmd_jobs)
    sub.add_parser("credits").set_defaults(fn=cmd_credits)

    p = sub.add_parser("check-postings", help="move roles in Search Jobs whose posting has closed to Dismissed; tag tracked ones not yet applied to Closed")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(fn=cmd_check_postings)

    p = sub.add_parser("startups", help="startups that just raised, and their open roles")
    sp = p.add_subparsers(dest="what", required=True)
    q = sp.add_parser("refresh", help="read the funding feeds and the YC directory (free)")
    q.add_argument("--days", type=int, help="how recent a raise counts (default: startups.days in searches.yaml)")
    q = sp.add_parser("scan", help="read startups' careers boards; roles matching your filters go to Find jobs")
    q.add_argument("--limit", type=int, default=300, help="how many startups (newest raise first)")
    q = sp.add_parser("score", help="signal-score startup roles in Search Jobs against the impact record")
    q.add_argument("--limit", type=int, default=30, help="at most this many (one Claude call each)")
    q = sp.add_parser("track", help="add the scored startup roles in Find jobs at or above a fit score to the tracker")
    q.add_argument("--min-fit", type=int, help="default: startups.roles.track_min_fit in searches.yaml")
    q = sp.add_parser("list")
    q.add_argument("--stage", help='e.g. "Series A"')
    q.add_argument("--hiring", action="store_true")
    q.add_argument("--days", type=int, help="raised within N days")
    q = sp.add_parser("roles", help="open roles from the startup's careers board")
    q.add_argument("id")
    q.add_argument("--add", action="store_true", help="store the roles matching your filters' titles as jobs")
    q = sp.add_parser("enrich", help="look up the latest round (Fundable or People Data Labs key in .env)")
    q.add_argument("id")
    p.set_defaults(fn=cmd_startups)

    args = ap.parse_args(argv)
    if args.cmd == "tailor" and not (args.job_id or args.jd_file):
        ap.error("tailor needs a JOB_ID or --jd-file")
    if args.cmd in ("preflight", "search", "run") and not (getattr(args, "hiringcafe", None) and not args.search):
        _need_jobspipe_key()            # a check of HiringCafe searches alone needs no JobsPipe key
    cfg = config.load(args.config)
    args.fn(cfg, args)


if __name__ == "__main__":
    main()

# Reference

## Commands

```bash
python -m jobpipe show-request                 # the exact API body for each saved search (free)
python -m jobpipe preflight                    # how many jobs match each search (≤ 1 credit each)
python -m jobpipe run                          # everything: search → signal score → make résumés for top 3 → Notion → digest
python -m jobpipe run --search tpm-remote --top 1
python -m jobpipe run --no-tailor              # signal score only (cheap), then make résumés by hand:
python -m jobpipe tailor <job id>              # Make résumé for one stored job
python -m jobpipe tailor --jd-file posting.txt --job-title "Staff TPM" --company Acme --url https://...
python -m jobpipe search --title "Director of Engineering" --hybrid --location "Portland" --limit 5
python -m jobpipe jobs                         # stored jobs with signal scores
python -m jobpipe startups refresh             # funding news + YC directory + VC boards -> data/startups.json, then their
                                               # careers boards: roles matching your filters land in Find jobs (free)
python -m jobpipe startups scan --limit 100    # just the careers boards
python -m jobpipe startups score --limit 30    # signal-score startup roles in Find jobs (one Claude call each)
python -m jobpipe startups track --min-fit 7   # add the scored startup roles at 7+ to the tracker
python -m jobpipe check-postings               # move roles whose posting has closed to Dismissed (tracked: tag Closed)
python -m jobpipe startups list --stage "Series B" --days 30
python -m jobpipe startups roles <startup id> --add   # its open roles; --add stores the ones matching your filters
python -m jobpipe startups enrich <startup id>  # look up its latest round (FUNDABLE_API_KEY, THEIRSTACK_API_KEY or PDL_API_KEY)
python -m jobpipe credits                      # JobsPipe credit ledger
python -m jobpipe serve                        # local web app (see Using the app)
```

Outputs go to `outputs/<date>/`: the résumé PDFs, `report-*.md`, `heatmap-*.html`, `apply-*.md` and `digest.md`. Each job's working files (`jd.md`, `01-objectives.md` … `resume-final.md`, scorecards, checks) are in `outputs/<date>/runs/<job>/`. Both are in your profile folder, out of git.

## Filters (saved searches)

The app calls these filters; the file and the command line call them searches.

Edit your `searches.yaml`. The example profile's four saved searches:

| id | Titles | Where | Pay |
|---|---|---|---|
| `tpm-remote` | Technical Program Manager, Principal Program Manager, Program Manager, Technical | remote | $200k+ |
| `em-remote` | Engineering Manager, Manager, Engineering, Manager, Software Engineering | remote | $200k+ |
| `tpm-hybrid` | same TPM titles | hybrid, Portland | $200k+ |
| `em-hybrid` | same EM titles | hybrid, Portland | $200k+ |

Add a search by copying a block. Anything the file doesn't model can go under `api_filters:` and is sent to the API as-is. Ad-hoc queries (`--title`, `--remote`, `--hybrid`, `--location`, `--min-salary`, `--days`, `--limit`) aren't saved.

**Himalayas, free, beside JobsPipe.** Each remote filter (and each that doesn't name a work arrangement) also searches [Himalayas](https://himalayas.app/api), a free remote-jobs board with a public API and no key. A filter with countries asks once per country, worldwide jobs left out, plus once for jobs open worldwide; titles are checked as for startup roles (its search also matches descriptions), and the newest that answer the filter are kept, up to its jobs per run. Two queries at a time, at most 400 a run, waiting and asking again after a "slow down" (429); about two minutes for six filters, two of them across the EU. A job it lists links to its Himalayas page and is tagged Himalayas, as its terms ask, and a job JobsPipe also found is stored once. Pay in another currency is kept as stated and flagged "pay not in USD" rather than checked against the floor. Turn it off with `pipeline.himalayas: false`.

**HiringCafe searches, free, on every run.** Add a [HiringCafe](https://hiringcafe.com) search on the Filters tab from its share link (`hiringcafe.com/s/…` or `hiring.cafe/s/…`) or the address of its search page. The link is read once: `searches.yaml`'s `hiringcafe:` list keeps it, the name you give it and `search_state`, the search's every filter as HiringCafe's `?searchState=` has them, so a run doesn't need the link. Every run (`run`, `search`, the tab's buttons) checks each one not paused, after JobsPipe and Himalayas; `--hiringcafe ID` checks just that one, and **Check now** on its card does that with a signal score of what's new. A job's link is the employer's own posting (its apply link), so one JobsPipe or Himalayas found too, or one you added by hand, stays one job and only gains the search. Each card shows the last check and how many jobs were new. HiringCafe's pages sit behind Cloudflare's bot check, which turns away plain HTTP clients and headless browsers; the app reads them with `curl_cffi` (Chrome's TLS fingerprint), and a check the bot check turns away is shown on the card and skipped, while the run goes on.

**Check these once with `preflight`:** `work_arrangement_or`, `job_location_or` and `min_salary_usd` come from JobsPipe's own integration code, not its Python SDK. A request the API rejects costs 1 credit and is never retried; the error shows in `preflight` and `credits`.

## Startup sources

What the Startups tab reads, and what was looked at and left out (checked October 2026). Everything used is free and needs no key, except the round lookups.

| Source | Gives | How |
|---|---|---|
| Google News RSS searches (`"raises" "Series A"`, …) | Round headlines from every outlet it indexes, including the wires that block scripts (FinSMEs, Business Wire, PR Newswire, Pulse 2.0) | `news.google.com/rss/search?q=…`, 100 newest items a query; the publisher after " - " in the title is kept as the source |
| FinSMEs, TechCrunch (venture), Crunchbase News, AlleyWatch (New York) feeds | FinSMEs has the most regular "Acme Raises $8M in Series A Funding" headlines; AlleyWatch is a daily New York funding report | RSS. FinSMEs answers a feed reader's User-Agent only, which the app sends when a feed refuses it |
| YC directory via [yc-oss/api](https://github.com/yc-oss/api) | Every YC company: batch, hiring flag, team size, industries, regions, website. No round (YC's own label is Early/Growth) | Daily JSON mirror of YC's public index; keyless, unlike YC's own search index, whose key rotates |
| VC portfolio boards on Getro (General Catalyst, Techstars, Accel, Insight Partners, Khosla Ventures) | Roles across each portfolio with the company's stage (pre-seed … series H, IPO, acquired, undisclosed) | `api.getro.com/api/v2/collections/<network>/search/jobs`, 20 a page; the network id is read from the board's page. Add any `jobs.<firm>.com` board that runs on Getro under `startups.vc_boards` |
| Greenhouse, Lever (its US and EU hosts), Ashby public job-board APIs | A startup's open roles, with location, remote flag and the posting text; on Ashby the pay too, with its yearly USD figures across the posting's tiers when it states them | Found from a link on the company's website or careers page, else by trying the company's name as the board name (on Lever's EU host, `jobs.eu.lever.co`, too: European companies' boards answer only there). Each refresh reads up to 300 boards (newest raise first; a board is re-read after a day, a site with no board after two weeks) and the roles answering your filters go to Find jobs |
| Fundable, TheirStack, People Data Labs (optional, keys) | The latest round of a company the news didn't cover | One lookup a click, or up to `startups.enrich.per_refresh` a refresh |

Looked at and not used: **Crunchbase** (no API for individuals since the Basic API ended; enterprise only), **Harmonic**, **Dealroom's full API**, **PitchBook**, **CB Insights**, **Tracxn**, **Specter** (all sales-gated, thousands a year); **Wellfound**, **Built In** and **Work at a Startup** job pages (no API, and their terms forbid scraping); **Consider**-run VC boards such as jobs.lsvp.com (free but need a session token, and only say Seed / Series A / Growth); **SEC EDGAR Form D** (free and authoritative for US raises, with the amount sold and date, but it never names the round and half the filings are funds; a good later addition); **Hacker News "Who is hiring?"** (free, stage only when the comment says it); **Dealroom's free feed** (a few stale items); **VentureBeat** (blocks scripts), **Axios Pro Rata**, **Term Sheet** and **StrictlyVC** (no deal data in their feeds or dead); **Wikidata** (no funding data). The round in a headline is read by a pattern, so a company named oddly in a headline ("Neocloud PaleBlueDot AI") can land under the wrong name; the headline is kept beside every round so you can check.

## JobsPipe credits

One credit buys one job for the rest of the calendar month; re-running a search only charges for jobs you haven't had this month, and an empty result is free. So the cost is the number of distinct jobs your filters let through, not how often you run. The pipeline:

- caps each search at its `limit` and each run at `budget.max_credits_per_run` (40), lowering `limit` when the budget runs short;
- tracks every call in `data/credits.json` against `budget.allowance` (the free plan's 1,000 credits are one-time; set `period: monthly` on a paid plan). In the app, click the credits count in the header for the ledger: used and left, use by month, and each call with its filter, result, jobs returned, credits charged and the request it sent;
- filters on JobsPipe's side before you pay; the local filters (remote/hybrid, pay floor, closed postings) only drop jobs already paid for.

## JobsPipe in Claude

- **Claude Code:** `.mcp.json` registers the JobsPipe MCP server (`https://mcp.jobspipe.dev/mcp`) using `JOBSPIPE_API_KEY` from your environment.
- **claude.ai / Claude desktop:** Settings → Connectors → Add custom connector → URL `https://mcp.jobspipe.dev/mcp`, then sign in.

## Tests

```bash
python -m pytest -q
```

They run offline: JobsPipe, Notion and Claude are mocked (a stand-in `claude` executable for the Claude Code runner; the real SDK with a mock transport for the API runner), while the real `check_resume.py`, `heatmap.py` and PDF renderer run. The evidence models are switched off, and `tests/test_evidence.py` uses word-overlap stand-ins for them. They run for the example profile's made-up candidate (`example-profile/`), in a temporary folder, never your profile.

## Making a Mac app release (for maintainers)

Published releases are on the public repo's [Releases page](https://github.com/Alexander-C-Carnes/job-oyster-app/releases).

`mac/build.sh` builds `build/Job Oyster.app` and a DMG on the Mac you run it on: it downloads a standalone Python ([python-build-standalone](https://github.com/astral-sh/python-build-standalone)), installs `requirements.txt` into it (without pytest and sentence-transformers), adds headless Chromium for the PDFs, copies `jobpipe/`, `skill/` and `example-profile/`, and compiles the launcher (`mac/Launcher.swift`), which starts the server and opens the page. The DMG opens on a laid-out window, the app on the left and Applications on the right with an arrow and a line on what to do, drawn by `mac/make_dmg_background.swift` and placed by [dmgbuild](https://github.com/dmgbuild/dmgbuild) from `mac/dmg_settings.py` (no Finder scripting, so it works on GitHub's Macs). With a "Developer ID Application" certificate in your keychain it signs everything; `--notarize` also sends it to Apple, so it opens with no warning (`mac/build.sh --help` lists the options).

Releases go out on their own once a day (07:00 UTC) when the code changed since the last one: the **Mac app** workflow (`.github/workflows/mac-app.yml`) tags the next version (v0.1.0 → v0.1.1), builds the Apple silicon and Intel DMGs on GitHub's Macs, and publishes them as a GitHub Release once both builds pass, which is free for a public repo. Installed apps offer the new version the next time they open. To release sooner, push a tag (`git tag v0.2.0 && git push origin v0.2.0`), or use **Actions → Mac app → Run workflow** and tick *release*; without the tick it's a test build. The workflow publishes them unsigned, and people click **Open Anyway** once, until they're signed.

To sign a release, run `mac/sign-release.sh` on a Mac that has a "Developer ID Application" certificate in its keychain and an App Store Connect API key with the Developer role, named in `.notary.env` in your profile folder (`~/JobOyster`): `NOTARY_KEY=/path/to/AuthKey_XXXXXXXXXX.p8`, `NOTARY_KEY_ID=XXXXXXXXXX` and `NOTARY_ISSUER=<issuer UUID>`. (A `jobsearch-notary` keychain profile made with `xcrun notarytool store-credentials` works too, but keychain sync can purge it between releases; the file stays.) It downloads the latest release's DMGs (or the tag you name), signs the app inside each, has Apple notarize the new DMGs, replaces them on the release and takes the Open Anyway step out of its notes. `--no-upload` stops before the upload and leaves the signed DMGs in `build/signed/`. Signing on your own Mac keeps the certificate and the notary key off GitHub. The workflow can also sign and notarize on GitHub's Macs if the repo has the secrets listed at the top of `.github/workflows/mac-app.yml`, but then the private key sits on a machine that installs unpinned packages during the build.

To have every release signed without remembering to, run `mac/auto-sign.sh --install` once, and again after the signing scripts change: macOS doesn't let a background job read `~/Documents`, so it copies `mac/` to `~/Library/Application Support/Job Oyster/signing/` and adds a LaunchAgent running that copy that checks once an hour, and at login, whether the latest release still says Open Anyway, and if so runs `mac/sign-release.sh` on it. It skips the hour while the Mac is on an iPhone hotspot or another signing run is going, gives a release three tries, and shows a notification when one is signed or fails (the signed DMGs stay in that folder's `build/signed/`); its log is `~/Library/Logs/Job Oyster/auto-sign.log`. `mac/auto-sign.sh --status` shows whether it's installed and the end of that log, and `--uninstall` removes it. The Mac has to be awake and logged in, and the notary key is best in `.notary.env`, since the keychain profile can vanish.

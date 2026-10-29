# Run it from the code

Most people should [download the Mac app](https://github.com/Alexander-C-Carnes/job-oyster-app/releases/latest) instead ([install steps](../README.md#get-started)). These steps are for running it from a clone of the repo, on a Mac (Linux may work). Steps 3 and 4 are the real work: the pipeline is only as good as the evidence you give it.

## 1. What you need

- **A Mac** (the scripts are written for macOS; Linux may work) with **Python 3.10+** and **git**.
- **An AI to do the work.** The default, and the one the app is tuned on, is **Claude through a Pro or Max subscription** with the **Claude Code CLI** ([install guide](https://docs.claude.com/en/docs/claude-code/setup)): run `claude` once and sign in with `/login`, and calls count toward your plan's usage limits instead of costing anything per call. You can use ChatGPT, Gemini, Kimi, OpenRouter or a free model on your own Mac (Ollama, LM Studio) instead: see [Which AI does the work](choosing-an-ai.md).
- **A JobsPipe account** from [jobspipe.dev](https://jobspipe.dev). The free plan gives 1,000 credits once; one credit buys one job.
- **Notion** (optional): a workspace where you can add an integration. Without it the tracker still works, on your Mac only.

## 2. Get the code and start the app

```bash
git clone https://github.com/Alexander-C-Carnes/job-oyster-app.git job-oyster && cd job-oyster
./oyster install                  # once: puts `oyster` on your PATH
oyster run                        # first time: builds .venv, installs packages and the PDF browser, opens the app
```

The first start makes your profile folder, `~/JobOyster`, as a copy of `example-profile/`: a made-up candidate, Jordan Rivera, so you can look around the app before you've added anything. (To keep it somewhere else, set `JOBPIPE_HOME` before the first start, e.g. `export JOBPIPE_HOME=~/Dropbox/JobOyster` in `~/.zshrc`.) The app opens in your browser at `http://127.0.0.1:8765/?token=…`. Bookmark that URL.

You never edit files in the repo itself, so `oyster update` can always pull the latest code.

## 3. Add your evidence

Replace the example candidate's impact record and résumé with your own. [Your evidence](your-evidence.md) says what goes in them; it's the part that matters most.

## 4. Say who you are

Open the app's **Profile** tab, fill in your name, pronouns and contact line, tick the résumés to score against, and click **Save profile**. Until you do, it says you're looking at the example profile.

The tab writes the `candidate:` section at the top of your `searches.yaml`, which you can also edit by hand:

```yaml
candidate:
  name: Your Name
  pronouns: she/her                # he/him, she/her or they/them
  city: Portland, OR               # résumés fail check_resume.py without it on the contact line
  phone: +1 555-555-5555
  email: you@example.com
  linkedin: linkedin.com/in/you
  pdf_name: "{name} resume {YYYY}-{MM} {company} - {title} {vv}"   # what PDFs are named (default: {title} - {company})
  resumes:                         # your files from step 3: a name (a heat-map column) -> file
    Platform resume: resume-platform.md
    Leadership resume: resume-leadership.md
  evidence: the impact record, every user-confirmed note, and the resume variants
  withdrawn: ["41.2%"]             # figures your impact record withdrew; a résumé showing one fails the check
```

Every prompt, the PDF file names and the contact-line check read it: the method's briefs and rules in `skill/` say `{name}`, `{first}`, `{contact}` and `{city}`, and call the candidate he/him, and those are filled in with your details and pronouns as they're loaded. `pdf_name` names every résumé PDF: the one a run saves, the one you download, and the copy kept as sent. It can say `{name}`, `{first}`, `{company}` and `{title}`, the date as `{YYYY}`, `{YY}`, `{MM}` and `{DD}` (the day it's saved), and the version as `{v}` (v1, v2…) or `{vv}` (nothing for v1, then v2, v3…). A run's own PDF always holds the latest version, so it leaves the version out. An older profile's `pdf_prefix` still works: a prefix you changed names files `<prefix>-{company}-{title}` until you set `pdf_name`. `evidence` is how the quick triage prompt describes your materials, so name a section there if your impact record has one worth pointing at.

`jobpipe/render.py` (`CSS`) sets the PDF's look: red accent (`#E50815`), font, spacing. Changing it is optional.

`skill/SKILL-*.md` are the skill files for running the same method in a Claude chat. The app only reads the Stage 4 section of `SKILL-resume-job-fit.md` (personalized like the briefs), so you need them only if you also want to use the skill in Claude.

## 5. Add your keys

The first start copied `.env.example` to `~/JobOyster/.env`. Fill in:

| Key | Where to get it |
|---|---|
| `JOBSPIPE_API_KEY` | The jobspipe.dev dashboard. It starts with `jp_live_` |
| `NOTION_TOKEN` | Optional. notion.so/profile/integrations → **New integration** (internal) → copy the secret. Then connect it to your tracker database: open the database → `…` → **Connections** → add the integration |
| `FUNDABLE_API_KEY`, `THEIRSTACK_API_KEY`, `PDL_API_KEY` | Optional, for the Search Startups tab's **Look up the round**. Any one is enough. [Fundable](https://www.tryfundable.ai): 200 free lookups, then $20/month. [TheirStack](https://theirstack.com): 50 free company lookups a month. [People Data Labs](https://www.peopledatalabs.com): 100 free a month |

Leave `ANTHROPIC_API_KEY` empty unless you switch to the API backend; if it's set, the default backend removes it from Claude Code's environment anyway, so your subscription is used.

## 6. Set up the Notion tracker (optional)

Skip this if you don't use Notion: it's off (`notion.enabled: false`) in a new profile.

The app writes to a Notion database with these properties. Names and types must match exactly:

| Property | Type | Notes |
|---|---|---|
| `Name` | Title | Written as "Job title — Company" |
| `Company` | Text | |
| `Job URL` | URL | Rows are matched by this, so it must be unique per job |
| `Status` | Status | Options: `Not started`, `In progress`, `Blocked`, `Applied`, `Interviewing`, `Offer`, `Denied`, `Ghosted`, `Done`, `Not Applying`. Notion's API can't add options, so add any that are missing by hand |
| `Type` | Select | The app only reads and writes rows where this is `Apply to Job`, so the database can hold other tasks too |
| `Fit Score` | Number | |
| `Notes` | Text | The app updates its own score line and keeps anything else you write |
| `Priority` | Select | Read only; any options |
| `Due/Submitted` | Date | Read only |
| `Resume Used` | Files & media | The PDF you sent, uploaded from the app's **Sent résumé** tab |
| `Project` | Relation | Optional: links each row to a project page |

Then, in your `searches.yaml` under `notion:`:

- set `data_source_id` to your database's data source ID (in Notion: open the database → `…` → **Manage data sources** → copy the ID);
- set `project_page_id` to the page the `Project` relation should point to, or leave it empty to skip it;
- set `enabled: true`.

## 7. Write your searches

Edit your `searches.yaml`:

- **`defaults`**: country (a code like `US` or `GB`, a region such as `EU`, `Schengen`, `EEA`, `Europe` or `EMEA`, a list like `[EU, Schengen]`, or `Any`; a search can set its own), how recent (`posted_within_days`), salary floor (`min_salary_usd`), the most jobs per search (`limit`), and title words to exclude.
- **`searches`**: one block per search, each with an `id`, a `name`, the `titles` to match, and `remote: true` or `work_arrangement: ["hybrid"]` plus `locations`. The example profile's four (TPM and Engineering Manager, remote and hybrid in Portland) are there to copy from. Replace them.
- **`budget`**: `max_credits_per_run`, and your plan's `allowance` (`{amount: 1000, period: once}` on the free plan; on a paid plan, its monthly credits and `period: monthly`).
- **`pipeline`**: the lowest signal score that gets a résumé (`shortlist_min_fit`), how many résumés to make per run (`tailor_top`), and the score at or below which a job is dismissed as soon as it's scored (`dismiss_at_or_below_fit`, off by default).

You can also edit searches in the web app's **Filters** tab, which writes back to this file.

## 8. First run

Start small, and check each step before spending more credits or Claude usage. From a Terminal window in the repo:

```bash
.venv/bin/python -m jobpipe show-request          # the API request each search sends (free)
.venv/bin/python -m jobpipe preflight             # matches per search (at most 1 credit each); confirms the API accepts the filters
.venv/bin/python -m jobpipe run --no-tailor       # search and signal score only
.venv/bin/python -m jobpipe jobs                  # what it found, with scores
.venv/bin/python -m jobpipe tailor <job id>       # Make résumé for one job
```

Everything here can also be done from the app: run searches in **Filters**, score jobs in **Search Jobs**, and watch progress in **Logs**. Read the first fit report closely. Weak scores or thin résumés usually mean the impact record is missing something; add it, or answer the report's questions with **Add a confirmed fact**, and make the résumé again.

## 9. Run the tests

```bash
.venv/bin/python -m pytest -q
```

They run offline (JobsPipe, Notion and Claude are mocked), so they're safe to run any time. They run as the example profile's made-up candidate in a temporary folder, so they never read or change your profile.

## Keeping it updated

`oyster update` pulls the latest code and reinstalls packages if `requirements.txt` changed; `oyster go` does that and restarts the app. Your profile folder isn't in the repo, so updates never conflict with your files.

**Or let it update itself.** Add `OYSTER_FOLLOW_MAIN=1` to `~/JobOyster/.env` and restart the app once. From then on, while the app runs, it looks for new commits on `main` every five minutes. When there are some and no run is going, it pulls them (`oyster update`) and restarts in the same window. It leaves the checkout alone when it's on another branch, has changes you haven't committed, or has commits of its own.

**Back up your profile folder.** It's the only copy of your evidence and tracker. Keep it in a synced folder (set `JOBPIPE_HOME`), or make it a private git repo of its own: `cd ~/JobOyster && git init`.

**From a checkout made before profile folders,** where your files were in the repo: the first start after updating copies them (`searches.yaml`, `.env`, `skill/references/`, `resume-runs/`, `data/`, `outputs/`) into `~/JobOyster` and points the stored paths at the new folders. It leaves the originals; delete them from the repo once everything looks right. To run it yourself: `.venv/bin/python -m jobpipe.legacy /path/to/old/checkout`.


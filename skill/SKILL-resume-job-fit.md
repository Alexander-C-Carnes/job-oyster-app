---
name: "resume-job-fit"
description: "Tailor and score {name}'s resume for a job posting with a multi-agent pipeline — an orchestrator runs sub-agents that extract the role's objectives, required skills, and required experience, match them to his impact record, write three independently reviewed tailored resumes in the posting's own language, merge them into one resume, and render it as a PDF with resume-format — plus a 1-10 fit score, ATS scorecard (Skills %, Experience %, Keywords %, Total % vs. 80% target), heat map, keyword gaps, and follow-up questions, then logs the job in his Notion job tracker. Use this skill whenever the user shares a job URL or pasted job description, even with no other words, or says things like \"score me against this job\", \"how good a fit am I\", \"tailor my resume\", \"make me a resume for this role\", \"which resume should I use\", \"ATS score\", \"keyword match\", or \"fix my headline\"."
---

# Resume vs. Job Fit — Orchestrated Pipeline

You are the **orchestrator**. You don't do the analysis or writing yourself: you set up the run, launch sub-agents with narrow jobs, check their files, merge the three resume drafts, and hand the result to resume-format for the PDF. Then you report and log the job in the Notion tracker.

```
Stage 0  Orchestrator: fetch posting → jd.md
Stage 1  01 Objectives ─┐
         02 Skills     ─┼─ in parallel
         03 Experience ─┘
Stage 2  04 Matcher → 04-match.md (+ ratings.json, keywords.json)
Stage 3  05 Writer A ─┐
         05 Writer B ─┼─ in parallel → resume-draft-A/B/C.md + trace-A/B/C.md
         05 Writer C ─┘
Stage 4  Orchestrator: merge → resume-final.md
Stage 5  resume-format → PDF
Stage 6  Orchestrator: fit report, heat map, questions
Stage 7  Orchestrator: add/update the row in the Notion job tracker
```

## How to run a sub-agent

**If you have a sub-agent tool** (e.g. Task/Agent in Claude Code or Cowork): launch each agent with a short prompt that names its brief and its files, for example:

> Read `<skill>/agents/02-skills.md` and follow it exactly. Run folder: `<run>`. Input: `<run>/jd.md`. Write your output to `<run>/02-skills.md` and reply with a one-paragraph summary.

Launch the agents within a stage in a single turn so they run in parallel (Stage 1: three agents; Stage 3: three agents, each told its lens letter). Wait for a stage to finish before starting the next. Pass absolute paths; sub-agents have no access to this conversation.

**If you have no sub-agent tool** (e.g. claude.ai chat): run each agent as a separate, isolated pass. Before each pass, read that agent's brief, work only from the inputs the brief lists, write its output file, then move on. For the three writers, write each draft without re-reading the other drafts, so the three stay independent. Tell the user once, briefly, that the agents ran in sequence.

After every stage, open the output files and confirm they exist and follow their brief's shape. If one is missing or clearly broken, rerun that agent once with a note on what to fix.

## Four standing rules (every stage, every agent)

1. **Only relevant experience.** Cite {first}'s experience only where it maps to a requirement in this job. Don't list impressive but unrelated work, don't pad evidence lists, and don't write a general career summary. If a heat-map note, bullet, or suggestion doesn't tie to a specific job requirement, leave it out.
2. **Only the job's actual text — no inference about the job.** Every statement about the role (summary, responsibilities, requirements, weights) must come from the posting's own words. Don't guess at team context, seniority, day-to-day work, success metrics, tech stack, or what a phrase "really means". If the posting doesn't state something, write "Not stated in the posting". When quoting helps, quote the posting directly.
3. **Ask before calling something a gap.** {first} may have experience that isn't in any bundled document. Any requirement rated missing or partial in *all* sources is an *unconfirmed gap*, not a fact — end the report with follow-up questions about each one, and re-score once he answers.
4. **Mirror the posting's language wherever it truthfully fits.** Applications go through digital job boards and ATS keyword filters, so every resume rewrite, draft bullet, summary line, title line, and skills list should reuse as much of the posting's exact wording as the evidence supports — its nouns, verbs, section headings, and tool names — merged with {first}'s real experience. Truth sets the limit: a posting phrase goes in only where the evidence (impact record, resumes, or user-confirmed answers) supports it. See "Mirroring the posting's language" below.

The full rule set, which every writer reads, is in `references/resume-rules.md`.

## Bundled candidate materials

These are always loaded — the user shouldn't have to re-upload them:

| File | What it is | How to use it |
|---|---|---|
| `references/impact-record.md` | Evidence-backed record of {first}'s work: roles and projects with results, any self-reported sections, and dated **user-confirmed** notes; ideally ends with what can and cannot be claimed | The **source of truth** for what {first} has actually done. Much richer than any resume. |
| `references/resume-*.md` | {first}'s existing resume variants, each tuned for a kind of role | Score each as a document a recruiter would read. |

Some variants may mirror older PDFs and keep their original headings ("–– PROFESSIONAL EXPERIENCE", "CORE CAPABILITIES"). Score them as written. Never carry those decorative characters or that heading into a tailored resume; the ATS check flags both.

The orchestrator doesn't need to read them itself: the matcher and writer agents read them. If the user uploads a newer resume, copy it into the run folder and list it for the matcher as an extra source (or instead of an outdated variant if he says so).

Other bundled files:

| File | Used by |
|---|---|
| `agents/01-objectives.md`, `02-skills.md`, `03-experience.md` | Stage 1 analysis agents |
| `agents/04-matcher.md` | Stage 2 matcher |
| `agents/05-resume-writer.md` | Stage 3 writers A, B, C |
| `references/resume-rules.md` | Standing rules, resume best practices, posting-language mirroring, required elements, ATS practices, honesty guardrails |
| `references/scoring-and-report.md` | 1–10 rubric, ATS scorecard, report template, heat map |
| `scripts/check_resume.py` | Mechanical pre-delivery checks on each draft and the final |
| `scripts/ats_score.py`, `scripts/heatmap.py` | Scorecard and heat map |

Any self-reported section of the impact record and every line marked **user-confirmed** are {first}'s own statements: count them as evidence (they are, in effect, earlier answers to follow-up questions), but keep the claim at exactly the scope he stated — "helped with" stays "contributed to", "we had a dashboard" is not "built a dashboard". Don't invent detail beyond any source. Check posting-specific location rules against the location the impact record states.

## Stage 0: Set up the run (orchestrator)

Fetch the URL with `web_fetch`. Many job sites (LinkedIn, Workday, some Greenhouse/Lever pages) render with JavaScript or require login, so the fetch may return only a title or boilerplate. If that happens:
1. Try `web_search` with the job title + company to find the same posting on another page (company careers site, Greenhouse/Lever mirror, job aggregator), and fetch that.
2. If that still fails, tell the user and ask them to paste the job description.

Never score from a job title alone.

Detect the applicant tracking system (ATS) from the URL or page:
- `gh_jid=`, `boards.greenhouse.io`, or `job-boards.greenhouse.io` → Greenhouse
- `jobs.lever.co` → Lever
- `myworkdayjobs.com` → Workday
- `icims.com` → iCIMS

If nothing identifies it, write "ATS: not identified". Then:
1. Create a run folder, `resume-runs/<company>-<role>/` in the working directory (`/home/claude/` in chat).
2. Save the full posting text verbatim to `jd.md`, with `ATS: <name or not identified>` and the source URL as its first two lines. Sub-agents may not have web access, so everything they need about the job is in this file.
3. If {first} has answered questions in this conversation or earlier runs, write those answers verbatim to `user-notes.md`. Sub-agents don't see the conversation.
4. Resolve `<skill>` to this skill's absolute folder path and use absolute paths in every agent prompt.

## Stage 1: Analysis agents (parallel)

Launch 01 Objectives, 02 Skills, and 03 Experience together. Each reads only `jd.md`. Outputs: `01-objectives.md`, `02-skills.md`, `03-experience.md`.

Orchestrator check: every row carries a source quote from the posting; nothing is added that the posting doesn't say; weights follow the High/Med/Low signals. If the user only wants a quick fit score, you may stop the resume stages here and after Stage 2 go straight to Stage 6 (Stage 7 still runs).

## Stage 2: Matcher agent

Launch 04 Matcher with the Stage 1 files, the impact record, all resume variants (plus any upload), and `user-notes.md`. Outputs: `04-match.md`, `ratings.json`, `keywords.json`.

Orchestrator check: every "Use" keyword and every CORE SKILLS item points to ledger IDs; nothing from the impact record's "Do not claim" list appears as evidence; unconfirmed gaps have questions. Then run the scorecard on the existing variants so there is a baseline:
`python <skill>/scripts/ats_score.py ratings.json keywords.json --resume "<source>=<path>" ...`

## Stage 3: Writer/reviewer agents A, B, C (parallel)

Launch three copies of 05 Resume writer, lens A (posting language), B (evidence fidelity), and C (recruiter scorecard). Each reads `jd.md`, `04-match.md`, the impact record, the best existing resume (for the frame only: contact line, job lines, education, interests), and `references/resume-rules.md`, reviews the brief against the posting and evidence, builds every bullet fresh from the evidence ledger rather than editing an existing resume, and writes `resume-draft-X.md` plus `trace-X.md`.

Orchestrator check, per draft:
- `python <skill>/scripts/check_resume.py resume-draft-X.md --title "<exact posting title>" --trace trace-X.md`
- Add each draft as a source to a copy of `ratings.json`, rated literally on what the draft says, and run `ats_score.py` on all three.

## Stage 4: Merge (orchestrator)

Combine the three drafts into one `resume-final.md`, in the same markdown shape the writers use (see `agents/05-resume-writer.md`). Work section by section:

1. **Pool the issues.** Read the three trace files. Anything a writer flagged in `04-match.md` (an overstated ledger item, an unsupported Use phrase) is resolved against the impact record before merging: if the writer was right, that claim is out of every version.
2. **Headline and summary.** Pick the strongest version, or combine clauses from two drafts only when each clause traces to ledger IDs. The headline starts with the exact posting title, character for character, whether or not he held it.
3. **Bullets.** For each role, group the three drafts' bullets by requirement ID (from the traces). For each requirement keep one bullet, choosing by, in order: (a) traces to ledger items at the allowed scope; (b) uses the most High-weight posting phrases verbatim; (c) carries a real figure; (d) reads as a natural mini-STAR. You may splice the posting phrase from one draft onto the evidence from another only if the result still matches the ledger exactly. Never introduce a claim, figure, or tool that none of the drafts traced.
4. **Coverage.** Every met High-weight must-have appears at least once in a bullet in the posting's words. Order bullets within a role by weight.
5. **CORE SKILLS** from `04-match.md`'s ranked lists, adjusted only if a writer showed an item was unsupported.
6. **Length.** Cut Low-weight and unrelated lines until it fits the résumé format's page limit: two pages (roughly 1,000 words at most), or one page (roughly 600 words) in the Compact format.
7. **Verify.** Run `check_resume.py` with the merged trace (write `trace-final.md` listing each line's source draft and ledger IDs) and `ats_score.py`. The merged resume's Total % must be at least the best draft's; if it's lower, find the keywords the merge dropped and restore them where the evidence allows. Check the "Honesty guardrails" in `references/resume-rules.md` line by line.

Also write `merge-notes.md`: which draft each line came from, and every posting phrase held back and why. These feed the report.

## Stage 5: PDF with resume-format

Read the resume-format skill's SKILL.md and follow it to render `resume-final.md` to a PDF. The wording comes only from `resume-final.md`. Handoff notes, since that skill was written for older resumes:
- The section is **CORE SKILLS**, not Core Capabilities. Put it where that skill puts Core Capabilities, with the heavy red rule above it, and render the `Hard Skills:` and `Soft Skills:` lines as two separate paragraphs.
- Each job renders as one row (`Employer | Title` left, `Mon YYYY–Mon YYYY | Location` right), so it parses as the single job line the rules require.
- File name: `{pdf_prefix}-[Company]-[Role].pdf` in the outputs folder.
- To make the PDF, use headless Chromium via Playwright if available, otherwise `wkhtmltopdf`.

After rendering: confirm the page count (two pages; one in the Compact format) and run `pdftotext -layout` on the PDF, then `check_resume.py`-style spot checks on the text (exact title present, headings in order, no stray dashes) and `ats_score.py` with the PDF text as the resume. Report the final Total %.

## Stage 6: Report (orchestrator)

Write the report using the template and rubric in `references/scoring-and-report.md`, built from the agents' files: Job summary and What the job will be doing from `01-objectives.md`; scores, heat map, strongest fit, unconfirmed gaps, and scorecard stories from `04-match.md`; the ATS scorecard now including the final tailored resume as a row; "Posting-language keywords" and "Held back" from `merge-notes.md`. Build the heat map with `scripts/heatmap.py`.

Rate the final tailored resume three times before the scorecard: launch three raters in a single turn, each adding it as the source "Tailored resume" to its own copy of `ratings.json` and scoring it 1-10, without seeing the others. Combine them into `ratings-final.json`: each row takes the rating at least two raters gave (partial when all three differ), and the score is the middle of the three. A single rating differs from the majority on about 3-7% of rows.

Deliver: the PDF, `resume-final.md`, and the heat map HTML (share with the environment's file tool). Keep the run folder's intermediate files out of the outputs unless the user asks for them. End with the numbered questions about unconfirmed gaps.

## Stage 7: Log the job in the Notion job tracker (orchestrator)

Every run ends by recording the job in {first}'s Notion job tracker, automatically, without asking first. Do it as soon as the Stage 6 fit score is set (also in quick-score runs), then mention it in one line of the reply.

**Where:** the **Tasks** database, data source `collection://<DATA_SOURCE_ID>` (put your tracker's ID here: `notion.data_source_id` in your profile's searches.yaml). Job rows are the ones with Type = "Apply to Job". Use the Notion connector tools (search/fetch, create pages, update page); load them with ToolSearch if they're deferred.

**1. Check for an existing row first.** Search that data source for the posting URL (and, if nothing matches, the company + title). Compare against the `Job URL` property, ignoring tracking parameters like `utm_*` or `gh_src`. If a row for this posting exists, update it (step 3) instead of creating a duplicate.

**2. New posting → create one row** in that data source with:

| Property | Value |
|---|---|
| `Name` | `<exact posting title> — <Company>` |
| `Company` | Company name |
| `Job URL` | The posting URL the user gave (the source URL on line 2 of `jd.md`) |
| `Status` | `In progress` |
| `Type` | `Apply to Job` |
| `Fit Score` | The **impact-record** fit score (actual experience), as a number 1–10 |
| `Project` | Relation to your job-search project page, if you use one (`notion.project_page_id`) |
| `Notes` | One line: `Scored <YYYY-MM-DD> · Tailored resume <X>/10 · ATS <Total>% (Skills <S>% / Experience <E>% / Keywords <K>%) · <location / pay if the posting states them>` |

Leave `Due/Submitted`, `Priority`, `Contact` and `Resume Used` empty — those are his to fill when he applies. In the page body, add a short summary in the same shape as earlier job rows: **Posting:** link, two-sentence role summary from `01-objectives.md`, **Fit:** score line, **Resume:** the PDF file name, **Unconfirmed gaps:** the requirements the follow-up questions cover.

**3. Existing row → update it:**
- Set `Fit Score` to the new impact-record score and refresh the scores in `Notes` (keep any text he added).
- Set `Status` to `In progress` only if it's currently `Not started` or empty. Never move a row that is `Applied`, `Denied`, `Ghosted`, `Done`, `Blocked` or `Not Applying` back to In progress.
- Fill `Job URL` if it's empty; don't overwrite a different URL he entered.

**4. Confirm.** Fetch the row back and check that Job URL, Status and Fit Score saved. In the reply, add one line: "Added to your job tracker (In progress, fit X/10)" or "Updated your job tracker row (fit X/10)", with a link to the row.

If the Notion tools aren't connected or the write fails, don't stop the run: deliver the report and files, then say the tracker wasn't updated and list the values (title, company, URL, status, fit score) so he can paste them in.

## After {first} answers the follow-up questions

- Treat his answers as a fourth evidence source: **User-confirmed**. It raises the impact-record score, not the resume scores — the resumes still say what they say.
- Update the heat map (rerun the script with the changed ratings and a note like "User-confirmed: ..."), the scores, and the suggestions.
- Anything he confirms having moves into **Resume rewrites**, with a draft bullet built only from what he told you — don't embellish scope, numbers, or outcomes he didn't state.
- Anything he confirms lacking moves into **Skills to build**.
- If he says he doesn't know or skips a question, keep it as unconfirmed.
- Add every answer verbatim to `user-notes.md`.
- Offer to record each confirmed answer in `references/impact-record.md` as a dated **user-confirmed** note at exactly the scope he stated, so future runs don't re-ask. (The installed skill folder is read-only: write an updated copy and tell him to re-upload the skill.)
- Rerun `scripts/ats_score.py` after any resume edit and report the new Total %.
- If answers change what can be claimed, rerun from Stage 2 (matcher) through Stage 5 so the PDF reflects them; Stage 1 files stay valid.
- After any re-score, rerun Stage 7 so the tracker row's `Fit Score` and `Notes` show the new numbers (update the existing row; don't create a new one).

## Multiple jobs

If the user gives several job URLs, produce the full report for each and run Stage 7 for each (one tracker row per posting), then finish with a ranked table: company, title, best resume, resume score, impact-record score, best resume's ATS Total %, one-line reason.
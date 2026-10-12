# How it works

```
searches.yaml ──► JobsPipe search ──► local filters ──► skip jobs already triaged / Applied in Notion
                  (credit-capped)                        │
                                                         ▼
                                   signal score (1 Claude call per job, 1-10 fit)
                                                         │  best N with fit ≥ 7
                                                         ▼
          resume-job-fit (Stage 1 analysts ×3 → matcher → writers A/B/C → rater → merge
                          → PDF in your résumé format → heat map + report)
                                                         │
                                                         ▼
              Notion Tasks row · apply-*.md checklist · digest.md          ──►  you apply
```

1. **Find jobs.** `searches.yaml` holds saved searches (titles, remote or hybrid, locations, salary floor, how recent). Each one becomes a request to the JobsPipe API. JobsPipe charges one credit per job returned, so every search has a `limit` and each run has a credit budget. Jobs are stored in `data/` so they're never paid for twice.
2. **Skip what you've seen.** Jobs already scored, or already marked Applied, Interviewing, Offer, Denied, Done or Not Applying in the tracker, are dropped.
3. **Signal score.** One short Claude call per job gives it a 1–10 fit score against your **impact record**, a detailed document of what you've done and what it achieved (`references/impact-record.md` in your profile folder). If the listing is only a stub, the full posting is fetched from the employer's site first. A job is never scored on its title alone.
4. **Make résumés for the best ones.** Jobs scoring 7 or higher (the top 3 by default) get **Make résumé**: the full `resume-job-fit` pipeline, about 14 Claude calls per job (the median of measured runs):
   - three analysts each pull one thing out of the posting: its objectives, the skills it requires, and the experience it requires;
   - a matcher rates every requirement against the impact record and each of your existing résumés, and builds an evidence ledger and a "do not claim" list;
   - three writers draft résumés independently, each with a different emphasis, using the posting's own wording wherever your evidence supports it;
   - the drafts are rated and merged into one résumé, which is checked by `skill/scripts/check_resume.py` (format and honesty rules) and an ATS keyword scorer;
   - small Hugging Face models running on your Mac check the merged résumé against your evidence: whether every line is backed by what it cites (flagged lines get one review pass), and which posting keywords it's missing that your record does support (those go into a keyword pass) — see [Evidence checks](#evidence-checks-local-models);
   - the résumé is rendered to a PDF in your résumé format (two pages; one in Compact), with a fit report and an interactive heat map.
5. **Track it.** The job is recorded in a local tracker (`data/tracker.db`) and copied to a Notion database. You also get an `apply-*.md` checklist with the link, the PDF to attach, and the posting's hard requirements, plus a `digest.md` for the run.
6. **You apply.** The pipeline never submits an application. You review the résumé, apply, and set the job to Applied.

**Honesty rules.** Every claim in a résumé has to trace back to the impact record, your existing résumés, or answers you've confirmed. When a requirement isn't covered anywhere, the report doesn't call it a gap. It ends with a question about it, and the answers you give (with **Add a confirmed fact** in the app's **Impact record** tab, which files them under the record's **Confirmed facts** section) count as evidence on later runs.

**Where things live.** The repo:

| Path | What's in it |
|---|---|
| `jobpipe/` | The Python app: CLI (`cli.py`), pipeline (`pipeline.py`), JobsPipe client (`jobs_api.py`), triage, tailoring orchestrator (`tailor.py`), PDF renderer (`render.py`), tracker and Notion sync, the startup search (`startups.py`), and the web app (`web/`) |
| `skill/` | The `resume-job-fit` method: agent briefs (`agents/`), rules and scoring rubric (`references/`), check scripts. Nobody's personal details: the prompts say `{name}`, `{contact}` and the like, filled in from your profile as they load |
| `example-profile/` | What a new profile starts as: a made-up candidate (Jordan Rivera) with an impact record, a résumé and example searches. The tests run as Jordan too |
| `jobsearch`, `start.sh` | Scripts that set up `.venv` and start the web app |

Your profile folder (`~/JobSearch`, or `$JOBPIPE_HOME`), which never goes in git:

| Path | What's in it |
|---|---|
| `searches.yaml` | Who you are (`candidate:`), saved searches, credit budget, Notion IDs, and which Claude model each stage uses |
| `.env` | Your API keys |
| `references/` | Your impact record (with your confirmed facts at the end) and your résumés (`resume-*.md`) |
| `data/` | Stored jobs, the tracker database, credit ledger, startups (`startups.json`), interview prep notes, web-app token |
| `outputs/<date>/` | Résumé PDFs, reports, heat maps, apply checklists and each run's working files |
| `resume-runs/` | Runs of the `resume-job-fit` skill done in Claude chats; the web app can show these résumés too |

Paths in these docs like `data/` or `references/` are in your profile folder. `JOBPIPE_DATA_DIR`, `JOBPIPE_OUTPUTS_DIR` and `JOBPIPE_CONFIG` move one part of it somewhere else.

## Evidence checks (local models)

Three small open models from Hugging Face run on your Mac, on its GPU (`jobpipe/evidence.py`). They are downloaded once, about 4 GB in all, into `~/.cache/huggingface`, and nothing is sent anywhere. Your impact record, résumé variants and confirmed facts are split into short passages; anything under "Do not claim", the withdrawn metrics and the record's methodology notes are left out. The passages' vectors are cached in `data/evidence/` until the evidence changes.

| Model | Size | What it does here |
|---|---|---|
| [Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | 0.6B, Apache-2.0 | finds the passages closest to a keyword or a résumé line |
| [Qwen/Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) | 0.6B, Apache-2.0 | judges whether a passage shows work a posting keyword directly describes |
| [MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli](https://huggingface.co/MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli) | 0.4B, MIT | judges whether the evidence states what a résumé line or a supported keyword claims (natural-language inference) |

- **Honesty check** (`honesty.md` in the run folder). Every figure in a bullet must appear in the ledger entries its trace cites, the passages closest to it, or your confirmed facts. Each bullet's claims (the part after its posting lead phrase, split at semicolons) must be entailed by that evidence. Summary sentences combine several claims, so they are checked for figures only. If a line fails, one Claude pass reviews the flagged lines with your full record: it keeps a line the evidence supports and says where, rescopes one that claims too much, or cuts it. Lines still flagged after that show up in the run's warnings and the report's "Lines to check".
- **Keyword evidence** (`keyword-evidence.md`). Each posting keyword the merged résumé is missing gets the passages that best support it and a verdict: supported, weak or none. Supported needs two things: the reranker finds a relevant passage, and the NLI model finds that a passage states the work ("This work involved mentoring program managers"). Relevant but not stated is weak: a record of mentoring junior analysts is relevant to "mentoring program managers" (1.00) but doesn't state it (0.00). Supported keywords go to the keyword pass, which adds a phrase only to a line that already uses that evidence. The report's "truthful adds vs. don't add" split comes from the verdicts on the final PDF.

How well it works, on three earlier Make résumé runs (Oct 2026): 2 of 69 résumé lines that had been reviewed were flagged, both paraphrases just under the threshold. The review pass is there to keep lines like these. 6 of 7 made-up lines were flagged, such as a "Realized $12 million in savings" line where the record says the savings were only *modeled*. The check that needs no model also caught invented figures. The reranker alone couldn't do the honesty check: it scores whether evidence is on topic, so that $12 million line scored 0.998. On a run these checks add about a minute, plus one Claude call when a line is flagged or a keyword is supported.

Settings are in the `evidence:` section of `searches.yaml`: the three model names, `device`, and `enabled`. With `enabled: false`, or without the `sentence-transformers` package, the pipeline runs as before, and only the figure check runs. That check uses all your evidence, not each line's own.

## How the skill was ported

`skill/` holds the `resume-job-fit` method (the app fills in the `candidate:` from your `searches.yaml` as it loads the briefs): the five agent briefs, `resume-rules.md`, `scoring-and-report.md`, and the three scripts (`check_resume.py`, `ats_score.py`, `heatmap.py`). The Python orchestrator (`jobpipe/tailor.py`) does what the skill's orchestrator does:

| Skill stage | Here |
|---|---|
| 0: fetch posting | `jd.md` from the JobsPipe description, falling back to the employer's page when the listing is a stub; never scored from a title alone |
| 1: agents 01-03 in parallel | same briefs; they see only `jd.md` |
| 2: matcher | same brief; `ratings.json` / `keywords.json` are validated and re-requested once if malformed |
| 3: writers A/B/C in parallel | same brief and lenses; each draft goes through `check_resume.py`; a rater call rates each draft literally for the draft scorecard |
| 4: merge | the skill's Stage 4 text as the merge brief; `check_resume.py` on the result, one fix pass on FAIL; the local [evidence checks](#evidence-checks-local-models), with one honesty review pass for flagged lines; one keyword-restore pass if Total % drops below the best draft or the evidence supports a missing keyword |
| 5: PDF | `jobpipe/render.py` in the format chosen on the Profile tab (`jobpipe/formats.py`); tightens spacing once, then trims (deleting only) if over the format's page limit (two pages; one in Compact); scorecard re-run on the PDF text |
| 6: report | `heatmap.py` + the report template |
| 7: Notion | `jobpipe/tracker.py` records the job locally with the same update rules (never moves Blocked, Applied or any later stage back, keeps your Notes text) and `jobpipe/notion.py` copies it to Notion with the same properties, now or on the next sync |

Agents have no tools: the orchestrator passes each file they are told to read as a named document and parses the files they return.

### References

The matcher and triage score the impact record and every résumé listed under `candidate.resumes`, all from your profile's `references/`. Answers to follow-up questions go in the record's `# Confirmed facts` section (the **Impact record** tab's **Add a confirmed fact** puts them there); every run treats them as user-confirmed evidence. `skill/references/` holds only the method: `resume-rules.md` and `scoring-and-report.md`.

## How close the signal score is to the full score

`python tools/calibrate_signal.py --repeat 2` runs the signal scorer on the jobs in `data/calibration/` (postings whose full score from Make résumé is known from the tracker) and prints the gap for each. The aim is an average gap within half a point. On 13 tracker jobs in October 2026 the average gap was −0.3 points (it was −1.4 before the scorer was changed to rate requirements the way the matcher does) and every job was within one point; three jobs Make résumé scored 9 came out 8. All 13 score 6–9 there, so weak fits are checked only loosely. It makes real Claude calls, stores nothing and does not touch Notion.

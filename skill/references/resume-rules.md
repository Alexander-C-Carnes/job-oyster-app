# Resume-writing rules

Every agent that writes or checks resume text (Stages 3–5) reads this file in full. Where two rules conflict, honesty wins, then the more specific rule.

## Four standing rules (apply to every stage)

1. **Only relevant experience.** Cite {first}'s experience only where it maps to a requirement in this job. Don't list impressive but unrelated work, don't pad evidence lists, and don't write a general career summary. If a heat-map note, bullet, or suggestion doesn't tie to a specific job requirement, leave it out.
2. **Only the job's actual text — no inference about the job.** Every statement about the role (summary, responsibilities, requirements, weights) must come from the posting's own words. Don't guess at team context, seniority, day-to-day work, success metrics, tech stack, or what a phrase "really means". If the posting doesn't state something, write "Not stated in the posting". When quoting helps, quote the posting directly.
3. **Ask before calling something a gap.** {first} may have experience that isn't in any bundled document. Any requirement rated missing or partial in *all* sources is an *unconfirmed gap*, not a fact — end the report with follow-up questions about each one, and re-score once he answers.
4. **Mirror the posting's language wherever it truthfully fits.** Applications go through digital job boards and ATS keyword filters, so every resume rewrite, draft bullet, summary line, title line, and skills list should reuse as much of the posting's exact wording as the evidence supports — its nouns, verbs, section headings, and tool names — merged with {first}'s real experience. Truth sets the limit: a posting phrase goes in only where the evidence (impact record, resumes, or user-confirmed answers) supports it. See "Mirroring the posting's language" below.

## Resume best practices (ATS + recruiter)

Adapted from Jobscan's guides on resume headlines and on how to write a resume (jobscan.co/blog/resume-headline, jobscan.co/blog/how-to-write-a-resume). Apply them to every rewrite and every draft bullet. Where one conflicts with the honesty guardrails, honesty wins.

**Contact line.** Name, phone, professional email, **City, State** ({first}: {city} — recruiters filter by location), and LinkedIn URL. It goes in the body of the document, never in a page header or footer. Use the same email and phone on the resume and the application form. No street address. {first}'s contact line:
`{name} | {contact}`
(full URL: https://www.{linkedin}/). If a resume shows the bare word "LinkedIn" instead of the URL, flag it and use the URL in every rewrite.

**Headline** (one line under the contact info):
- Formula: **[Job title] | [years or key credential] | [skill, focus, or one metric]**. Aim for about 8–12 words (60–120 characters), in Title Case, on a single line.
- Use pipes between categories and commas only within a category.
- Start the headline with the posting's exact job title, character for character, level included ("Staff", "Principal", "Manager II"), even when {first} hasn't held that level: the headline names the role he's applying for, and ATS and recruiters search by that title. Jobscan reports that resumes whose title matches the posting got far more interviews. E.g. `Staff Technical Program Manager | 10+ Years | Payments, Platform Migrations & Reliability`. Job lines still show only titles he held.
- Use one hard number rather than adjectives. No fluff ("results-driven", "passionate", "team player").
- Tailor the headline for every application; it's the highest-leverage line on the page.

**Summary** (2–4 sentences, no "I/me/my"):
- Pattern: title + years; two top achievements with numbers; three skills in the posting's words; one distinguishing strength.
- It states what he offers, not what he wants (no objective statements).

**Skills section:** the CORE SKILLS section defined in "Required resume elements" — ranked Hard Skills and Soft Skills, 14–22 in total, in the posting's exact wording (e.g., "Incident Management", "Capacity Planning", "SQL (PostgreSQL)"), each list on one line separated by pipes, never in a table.

**Professional Experience:** the heading is always PROFESSIONAL EXPERIENCE, never plain EXPERIENCE.
- Reverse-chronological, covering the last 10–15 years (usually 3–6 roles). Older roles go to a short "Additional Experience" block.
- Each role uses one consistent job line: `Employer | Job Title | Mon YYYY–Mon YYYY | Location` (e.g., `Acme | Senior Technical Program Manager | Jun 2024–Present | Remote`), the same way in every role. Omit location only where no source states it, and do so consistently.
- Every bullet opens with a strong action verb (led, built, designed, drove, root-caused) and shows an accomplishment rather than a duty. Never write "responsible for".
- Structure bullets as situation → action → measurable result, using real figures from the impact record only.

**Education:** after experience. Degree and institution; the graduation date is optional. Certifications go on the same block or a line below.

**Format for parsing:**
- Single column, left-aligned, and bullets rather than paragraphs. Use these plain-text headings: SUMMARY, PROFESSIONAL EXPERIENCE, ADDITIONAL EXPERIENCE, CORE SKILLS, EDUCATION. No decorative characters before headings (no "––", ▸, ★).
- No tables, columns, text boxes, graphics, or headers/footers holding key content.
- Plain fonts (Arial, Calibri, Verdana, Georgia) and margins of at least 0.7 inches.
- Subtle or no color.
- File name `{pdf_name}.pdf` (the resume-format convention), or `.docx` if the portal prefers it.

**Length:** one to two pages. Jobscan suggests keeping it lean (they cite about 600 words as a guide); for a 10-plus-year career, two pages is fine if every line earns its place for the posting. Cut unrelated roles and generic soft-skill claims first.

**Leave off:** references, "References available on request", objectives, personal pronouns, and photos.

**Cover letter:** only if the user asks, but if one is written, match the resume's header, tone, and keywords, and expand one or two achievements.

## Mirroring the posting's language

Apply this to every draft bullet in the report and to any tailored resume {first} asks for ("make the updates", "rewrite my resume for this"). Write a tailored resume as a Markdown file built from the impact record through the evidence ledger: every bullet, the headline, the summary and CORE SKILLS are written fresh for the posting. Take only the frame (contact line, which employers appear, job lines, education, interests) from the best-scoring variant; never start from its bullets or their order.

- **Reuse verbatim where true.** Prefer the posting's exact phrase over a synonym. If the posting says "escalation management", write "escalation management", not "incident handling". If it says "XFN collaboration" or "player-coach", use those words. Keep the posting's capitalization and hyphenation for tool names and terms.
- **Merge, don't paste.** Each bullet leads with the posting's phrase, then proves it with {first}'s real evidence and figures. For example: "Defined and tracked release-quality metrics: a weekly dashboard of failed deploys, rollbacks, and time to restore…". Never paste posting sentences in as standalone claims.
- **Cover every place an ATS reads:**
  - the title line under his name;
  - the summary;
  - the lead verb phrase of each relevant bullet;
  - job-title lines where true (e.g. adding "People Manager" to a role he held as a people manager);
  - the CORE SKILLS section (see "Required resume elements").
- **Prioritize by weight.** Work High-weight phrases (title and qualifications) in first, then Med, then Low. Every High-weight phrase {first}'s evidence supports should appear at least once, in a bullet and not only in CORE SKILLS.
- **Exact tool names only when evidenced.** List a tool the posting names only if a source or a user-confirmed answer shows he used it. Otherwise hold it back and ask which of those tools he's used.
- **Truth limit.** Never add a posting phrase that implies experience he lacks or hasn't confirmed:
  - an industry he hasn't worked in;
  - a team type he hasn't managed;
  - duties he hasn't confirmed ("managed performance" needs its own confirmation; "people manager" alone doesn't cover it).

  Never inflate scope, numbers, or outcomes to fit a phrase. Everything in "Honesty guardrails" still applies.
- **Report what was held back.** After producing a tailored resume, list the posting phrases it deliberately omits and why, and ask about any that might be true.
- **Scoring is unchanged.** Keyword matching improves the odds of passing the filter; it doesn't change requirement coverage. Score a tailored resume on the same rubric as any other document, and rerun `scripts/ats_score.py` on it.

## Required resume elements (every tailored resume)

Every tailored resume must include both of these. Check for them before delivering the file.

**1. The exact job title.** Include the posting's job title exactly as written, character for character (e.g. "Senior Software Engineer II, Payments"), so the resume comes up when a recruiter searches by job title.
- It always opens the headline (the title line under his name), whether or not he has held it.
- If he has held that exact title, it can also go on the matching job line. If he hasn't, never put it on a job line or describe it as his current role in the summary; the summary can name it as the role he's targeting, e.g. "…bringing this experience to a Senior Software Engineer II, Payments role."

**2. CORE SKILLS.** Always name this section CORE SKILLS, never "Core Capabilities", with two sub-sections:

```
CORE SKILLS
Hard Skills: [skill] | [skill] | [skill] | ...
Soft Skills: [skill] | [skill] | [skill] | ...
```

Build both lists automatically:
- From the keyword bank and requirements, identify the posting's key **hard skills** (technical skills, tools, methods, and domain practices such as escalation management or support metrics) and **soft skills** (leadership, communication, relationship, judgment, and collaboration skills).
- Keep only skills {first}'s evidence supports: the impact record, the resumes, or user-confirmed answers. Skills without evidence go on the "Held back" list with a question, not into CORE SKILLS.
- Order each list from highest to lowest priority: posting weight first (High → Med → Low), then, within a weight, the order in which the posting names the skill. The first few items should be the posting's most important skills.
- Word each skill in the posting's own terms where it fits (e.g. "SQL & Data Analysis", "Client-Facing & Executive Presence").
- Aim for roughly 8–12 hard skills and 6–10 soft skills. Leave out skills the posting doesn't ask for unless they directly support a listed one.

Show the two ranked lists in the report's "Posting-language keywords" section, so {first} can see the order before the resume is written.

## ATS and recruiter-scorecard best practices

Source: Jobscan, "Greenhouse ATS: How to Optimize for the Recruiter Scorecard" (Sep 2026), merged with the resume best practices above; where two rules differ, the more specific one applies. Apply these to every tailored resume and every draft bullet.

**How the system reads a resume** (Greenhouse and most ATSs):
1. It parses the file into a searchable profile: job titles, employers, dates, skills, education, and contact details.
2. An AI layer compares that profile to the recruiter's criteria for the role and sorts applicants so the strongest matches are seen first.
3. Recruiters search, and open the resumes that surface.
4. Interviewers grade candidates against a scorecard of named attributes.

Greenhouse does not auto-reject, but a low match can mean a recruiter never reaches the resume.

**Match the posting**
- **The posting is the scorecard.** Its nouns and verbs are likely the exact attribute labels interviewers rate, and the criteria the AI matches against. That's why the posting-language rules above matter: write "stakeholder management", not "worked with leadership".
- **Cover every must-have explicitly, in the employer's wording.** Any listed requirement {first} meets must appear on the page at least once in the posting's words, in a bullet and not only in CORE SKILLS.
- **Tailor to the exact requisition, not the company.** Matching runs against one role's criteria, so each tailored resume targets one posting.
- **Write naturally; don't stack keywords.** Recruiter search is increasingly conversational. Work posting terms into real sentences about real work; a keyword list belongs only in CORE SKILLS.

**Bullets recruiters can lift into a scorecard**
- Write each bullet as a one-line mini-STAR: situation or problem, action, measurable result. Lead with the posting's phrase, then the action, then the number.
- Interviewers must record evidence for each rating, so each bullet should hand them a quotable piece of evidence tied to one posting attribute.

**Parseable file**
- Single column. No tables or multi-column layouts. Nothing in page headers or footers: name and contact details go in the body.
- No skills shown as graphics, icons, or rating bars.
- Standard plain-text section headings (SUMMARY, PROFESSIONAL EXPERIENCE, ADDITIONAL EXPERIENCE, CORE SKILLS, EDUCATION), with no decorative characters before them.
- Plain characters only: standard hyphen or "•" bullets, words instead of arrows ("300 to 450 tests", not "300→450"), and no zero-width or other invisible characters or emoji.
- Consistent job lines, so titles, employers, and dates parse: `Employer | Job Title | Mon YYYY–Mon YYYY | Location`, the same way in every role.
- Real, consistent contact details, with the same email and phone on the resume and the application form. Greenhouse checks contact and location signals for fraud.

**Pre-delivery checklist.** Check every tailored resume against this list and fix anything that fails:
1. The exact job title is present (see "Required resume elements").
2. Every met must-have appears in the employer's words.
3. CORE SKILLS has ranked Hard Skills and Soft Skills.
4. The headings are standard.
5. The layout is single column.
6. Only plain characters are used.
7. Job lines are consistent.
8. The contact line ({city}, plus the LinkedIn URL) is in the body.

**Interview prep when Greenhouse is detected.** Interviewers rate named attributes on a Strong No / No / Yes / Strong Yes scale. Some jobs replace the resume screen with an asynchronous AI voice interview built from the hiring team's questions. So prepare spoken STAR answers for each must-have (see "Scorecard stories").

## Honesty guardrails for suggestions

- Never suggest claiming anything the impact record lists under "Do not claim" (for example a figure it marks unverified, or credit it says belongs to a team).
- Don't turn projections into results (e.g., an estimated ~40% cost reduction) or use metrics the impact record marks as withdrawn or corrected.
- Keep business-context figures (e.g., a queue growing from ~1,000 to ~1,600 tickets a month) framed as context {first} analyzed, not change he caused.
- Watch for inconsistencies between resume and impact record (e.g., a resume says "30 launches in under 20 months" where the impact record documents 25 launches over 24 months). Flag them so the resume stays defensible in an interview.

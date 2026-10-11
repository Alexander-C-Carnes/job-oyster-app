# Your evidence: the impact record and résumés

Everything Claude knows about you comes from your profile's `references/` folder.

**The impact record** (`references/impact-record.md`). Replace Jordan's with your own. This matters more than anything else in the setup. Write it as a long markdown document, one section per role or major project, covering:

- what the problem was, what you did, and what changed because of it;
- numbers wherever you have them (team sizes, percentages, money, time saved, counts);
- the tools, methods and domains involved, in the words a job posting would use;
- your location, work authorization and anything else postings ask about;
- at the end, what can and can't be claimed: figures you've withdrawn or can't back up, and things not to overstate.

It can be long (10,000 words is fine). Claude only uses what's written down, so detail you leave out can't appear in a résumé. A good way to build one is to ask Claude to interview you about each job, then edit the result. You can write and edit it in the app: the **Impact record** tab shows it formatted, like Typora, and saves it back as markdown. If a résumé of yours says things the record doesn't, **Compare a résumé…** in that tab (or **Add what's new to my impact record** when you add one) lists its lines the record lacks, role by role. You tick the ones to keep, and they go in a section at the end, **From your résumés**. Nothing is added unless you press Add, and your own sections are never changed.

**Your existing résumés** (`references/resume-<name>.md`, any number). These are scored alongside the impact record and give the writers a format to follow. Add them in the app with **Profile → Add a résumé**: a PDF or Word file (saved from the program you wrote it in, not a scan), Markdown or plain text. Claude sorts it into the markdown shape below using only your file's words, the app checks that word by word, and you see your file beside the result, fix anything it flags, and save; the original is kept in `references/originals/`. Or put the files there yourself. How the PDFs look is a separate choice: **Profile → Résumé format** (Signature, Executive, Modern, Minimal, or Compact on one page). At least one should use the markdown shape the PDF renderer reads:

```markdown
# Your Name
**Headline | Years | Focus areas**
City, ST | +1 555-555-5555 | you@example.com | linkedin.com/in/you

## SUMMARY
Two or three sentences.

## PROFESSIONAL EXPERIENCE

**Company** | Job Title
*Jan 2022–Present | Remote*

- Bullet with a result and a number.

## CORE SKILLS
Hard Skills: Skill | Skill | Skill
Soft Skills: Skill | Skill | Skill
```

Delete Jordan's `resume-platform.md` once yours are in.

**Confirmed facts** (the impact record's `# Confirmed facts` section). Leave this out at first. It's added when you answer a fit report's follow-up questions with **Add a confirmed fact** in the **Impact record** tab: each answer goes at the end of the record, dated and marked user-confirmed. (A `references/user-notes.md` from an older version still counts; move its lines into the record whenever you like.)

**Interview prep** (`data/interview-prep.md`). Created the first time you open the app's **Interview prep** tab: a page for stories you need to find, examples and numbers to dig up, and questions to get ready for. Edit it formatted like the impact record; `- [ ] ` makes a checkbox you can tick. It's a plain markdown file, so you can also ask Claude to add to it, and the open tab shows the change. It isn't used for scoring or résumés.

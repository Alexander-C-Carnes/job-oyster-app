---
name: "resume-format"
description: "Render {name}'s résumé markdown into his two-page, red-accent résumé layout (HTML/PDF). Use whenever the user supplies a résumé .md and asks to \"make it look like my résumé\", \"format this\", or \"create a new version\" of the résumé."
---

# Résumé Format

Turn a résumé markdown file into a two-page US Letter document with a fixed visual spec. **The content comes only from the markdown**: never rewrite, reorder, add, or drop wording. Keep the text verbatim, apart from typographic quotes (' → ’).

## Formats
The app has five résumé formats; the candidate picks one on the Profile tab (`candidate.resume_format` in searches.yaml). This file specifies **Signature**, the default and the original layout. The others are CSS over the same markup in `jobpipe/formats.py`, and keep every ATS rule below:

| Format | Font | Accent | Pages | What differs from Signature |
|---|---|---|---|---|
| Signature | Helvetica Neue | #E50815 red | 2 | (this spec) |
| Executive | Georgia | #1F3A5F navy | 2 | Centred caps name, headings centred between hairlines, company bold and role italic |
| Modern | Arial | #0E7C74 teal | 2 | Large name, teal heading bars, each role's title on its own line |
| Minimal | Helvetica | none | 2 | Black and greys only, dates under each role, en-dash bullets |
| Compact | Arial | #1D4ED8 blue | **1** | 0.5 in margins, tighter spacing, CORE SKILLS straight after SUMMARY |

Render any of them with `python -m jobpipe.render resume.md out.pdf --format <signature|executive|modern|minimal|compact>`.

The candidate can also have formats of their own (`own-<name>` ids, saved in the profile's `references/formats/`): settings read from an uploaded résumé's look, or copied from a built-in format and changed (`jobpipe/own_formats.py`). They change only fonts, sizes, colours, alignment, rules, bullets, margins and page limit. The page is always this one-column structure, so every ATS rule here still holds. In every format, heading letter-spacing stays at .06em or less and ligatures are off, so each heading and word comes out of the PDF as plain text.

## Page
- US Letter (8.5 × 11 in), exactly **2 pages**, 0.625 in margins on all sides.
- **No page numbers, headers, or footers.** ATS parsers skip or garble header/footer text, and page numbers pollute parsed content.
- Page 1: header → Summary → Professional Experience (the most recent role, plus more roles if they fit). Page 2: remaining roles → Additional Experience → Core Capabilities → Education → Interests.
- Never split a role's header from its first bullet. Nothing may overflow a page. If a page is tight, cut section-header top margins (16pt → 12pt) before touching font sizes.

## ATS / Greenhouse rules (non-negotiable)
Source: Jobscan, "Greenhouse ATS: How to Optimize for the Recruiter Scorecard" (2026). Greenhouse parses the file into a profile, AI-ranks the match, then a human reads the original.
- **Single column.** No tables, multi-column layouts, text boxes, or sidebars. Put the job line and dates on one row with flexbox, never a table.
- **All content is real, selectable text in the body.** Name and contact go in the body, never in a page header/footer. No images, icons, skill bars, or ratings graphics.
- **Standard section headings** in plain words: SUMMARY, PROFESSIONAL EXPERIENCE, ADDITIONAL EXPERIENCE, SKILLS/CORE CAPABILITIES, EDUCATION, INTERESTS. No decorative characters in headings.
- **Decorative rules must not be text characters.** Draw dividers with CSS borders so the parser doesn't pick up strings of dashes.
- **Consistent, parseable dates:** `Mon YYYY–Mon YYYY` on the same line as the company and title.
- **Skills as plain text**, separated by pipes.
- **Contact details written out in full** (email, phone, LinkedIn URL as visible text such as `linkedin.com/in/…`). A parser can't read a hyperlink hidden behind the word "LinkedIn".
- **Standard fonts only**, embedded in the PDF: Helvetica Neue, Helvetica or Arial.
- **Deliver as a text-based PDF** (not a scan or image) with a descriptive filename, e.g. `{pdf_name}.pdf`.
- Content: mirror the posting's exact nouns and verbs; one evidence-backed, measurable result per bullet; cover every listed must-have in the employer's wording. (Content still comes from the md; flag missing must-haves rather than inventing them.)

## Type
- Family: **Helvetica Neue**. Fallback stack: `'Helvetica Neue', Helvetica, Arial, sans-serif`.
- Weights: Light 300 (body), Bold 700 (headings, job lines).
- Body line-height: 12pt on 10pt text.

| Element | Size | Weight | Color | Notes |
|---|---|---|---|---|
| Name | 18pt | 700 | #171717 | line-height 1.0 |
| Title line | 10pt | 700 | #E50815 | 5pt below name |
| Contact line | 10pt | 300 | #666666 | `email \| phone \| linkedin.com/in/…` (full URL as visible text, also linked) |
| Section header | 11.5pt | 700 | #E50815 | UPPERCASE, plain standard wording |
| Job line | 10.5pt | 700 | #171717-ish (inherit) | `Company \| Title`, left |
| Job dates | 10pt | 700 italic | #999999 | `Mon YYYY–Mon YYYY \| Location`, right-aligned on the same baseline |
| Body / bullets | 10pt | 300 | #434343 | |
| Bullet marker | 10pt | 300 | #000000 | a hyphen `-` |

## Colors (only these)
- Accent red `#E50815`: title line, section headers, heavy rules
- Name `#171717` · Body `#434343` · Contact `#666666` · Dates `#999999` · Light rules `#CCCCCC`

## Rules (dividers)
Draw rules with a CSS `border-bottom` on an empty div, never with runs of dash characters.
- **Heavy red rule** (1.5pt #E50815): under the contact line, and above CORE CAPABILITIES.
- **Light grey rule** (0.5pt #CCCCCC): between roles inside Professional Experience, and before each Additional Experience entry. About 6pt of space above and below.

## Structure & spacing
1. Name / title / contact → heavy red rule.
2. `SUMMARY` (11pt above) → paragraph (4pt below the header).
3. `PROFESSIONAL EXPERIENCE` (16pt above). Each role:
   - A flex row: job line on the left, dates on the right (`justify-content: space-between; align-items: baseline`).
   - Bullets 2pt below: indent 18pt, then an 18pt column for the `-` marker, then text with a hanging indent.
   - Nested items (a markdown sub-list) sit under the parent bullet's text with a `·` marker in a 14pt column.
   - A light grey rule between roles.
4. `ADDITIONAL EXPERIENCE`: a light rule, then for each entry a job row (dates without location if the md has none) and one plain paragraph 2pt below (no bullet).
5. Heavy red rule → `CORE CAPABILITIES` (6pt above) → one paragraph of pipe-separated skills (`A | B | C`).
6. `EDUCATION` (or the md's exact heading, e.g. EDUCATION & CERTIFICATIONS): one line per credential.
7. `INTERESTS`: pipe-separated items, when the md has them; otherwise leave the section out.

## Markdown mapping
- `# Name` → Name. The first bold line → Title line. The next line → Contact.
- `## HEADING` → section header (uppercase, no decorative prefix).
- `**Company** | Title` + an italic date line → job row.
- `- item` → bullet. Indented `- item` → nested `·` item.
- `---` in the md is structural only. Place the rules as described above, not wherever `---` appears.
- Inline `code` → monospace at 9pt.

## Don'ts
- No other colors, icons, photos, columns, sidebars, boxes, or shading.
- No bold inside bullets and no emphasis that isn't in the md.
- Don't invent sections or content. Ask if something is missing (except Interests, above).
- Don't shrink the body below 10pt to force a fit.

## Reference example
`examples/Example_Resume_PDF.pdf` is a finished two-page résumé in this format. Open it (or render its pages to images) to check layout, spacing, rule placement, and the page-1/page-2 split before delivering. Match its look, but take all wording only from the user's markdown, never from the example.

## Final check
- Exactly 2 pages with no clipped text, and no header, footer, or page-number text.
- Copy-paste the whole page as plain text: it should read top to bottom in order, with no dash strings or stray symbols.
- Every word matches the source md.
- Headings are standard names, dates follow `Mon YYYY–Mon YYYY`, and the LinkedIn URL is visible text.
- Red appears only on the title, section headers, and heavy rules.

"""Résumé formats: how the PDF looks, never what it says.

Each format is CSS laid over the markup jobpipe.render builds (render.CSS is Signature, the original
layout), plus a page margin, a page limit and an optional section order. Every format keeps the ATS rules
in skill/SKILL-resume-format.md: one column, all real text, rules drawn as CSS borders, nothing in a page
header or footer, a standard font. Heading letter-spacing stays at .06em or less: wider, and the PDF's text
reads "S U M M A R Y", so a parser can miss the section (tests/test_formats.py checks every heading).

Formats of the candidate's own (jobpipe/own_formats.py) have "own-" ids and live in the profile; get() finds them
too. The candidate picks one as the Profile default (searches.yaml `candidate.resume_format`). A résumé records the
format its PDF was drawn in, so changing the default never silently pushes one over its page limit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT = "signature"
OWN = "own-"            # the id prefix of a format of the candidate's own


@dataclass(frozen=True)
class Format:
    id: str
    name: str
    font: str
    accent: str                 # the colour that marks the format ("" for none), also the page editor's hover
    blurb: str
    max_pages: int = 2
    margin_in: float = 0.625
    order: tuple[str, ...] = ()  # `## ` sections drawn first, in this order; the rest follow as written
    css: str = ""               # laid over render.CSS
    tighten: str = ""           # added once when a résumé runs over max_pages, before the trim step
    words: int = 950            # about how many words fit in max_pages, for the writers and the trim step
    heading_case: str = "upper"  # "title": section headings drawn as "Professional Experience"
    pages_word: str = field(init=False, default="")

    def __post_init__(self):
        object.__setattr__(self, "pages_word", {1: "one page", 2: "two pages"}.get(self.max_pages, f"{self.max_pages} pages"))


FORMATS: dict[str, Format] = {f.id: f for f in (
    Format(
        "signature", "Signature", "Helvetica Neue", "#E50815",
        "Clean sans, red accent, two pages. The original layout; fits most tech and business roles.",
        tighten="h2 { margin-top: 12pt; } h2.first { margin-top: 9pt; } .rule-light { margin: 4pt 0; }"),
    Format(
        "executive", "Executive", "Georgia", "#1F3A5F",
        "Serif and centred, navy accent. For director-and-up, finance, law, consulting and government.",
        css="""
body { font-family: Georgia, 'Times New Roman', serif; font-weight: 400; font-size: 10pt; line-height: 12.5pt; color: #2B2B2B; }
.name { text-align: center; font-size: 21pt; font-weight: 400; letter-spacing: .06em; text-transform: uppercase; color: #1F3A5F; }
.title { text-align: center; color: #1F3A5F; font-style: italic; font-weight: 400; font-size: 11pt; margin-top: 6pt; }
.contact { text-align: center; color: #555; font-size: 9.5pt; margin-top: 4pt; }
.contact a { color: #555; text-decoration: none; }
.rule-heavy { border-bottom: 2.5pt double #1F3A5F; }
.after-contact { margin-top: 9pt; }
.pre-skills { display: none; }
h2 { display: flex; align-items: center; gap: 10pt; font-size: 11pt; font-weight: 700; letter-spacing: .05em;
     color: #1F3A5F; margin: 15pt 0 6pt; }
h2::before, h2::after { content: ""; flex: 1; border-top: .5pt solid #9AA7B8; }
h2.first { margin-top: 11pt; }
h2.skills { margin-top: 15pt; }
.jobline { font-size: 10.5pt; color: #1A1A1A; }
.jobline .co { font-weight: 700; }
.jobline .role { font-style: italic; }
.dates { font-weight: 400; font-style: normal; color: #555; font-size: 9.5pt; }
.rule-light { border-bottom: .5pt solid #D5DAE1; margin: 7pt 0; }
ul.b { padding-left: 12pt; margin-top: 3pt; }
ul.b > li { padding-left: 12pt; text-indent: -12pt; margin-bottom: 1.5pt; }
ul.b > li::before { content: "\\2022"; width: 12pt; color: #1F3A5F; }
""",
        tighten="h2, h2.skills { margin-top: 11pt; } .rule-light { margin: 5pt 0; } ul.b > li { margin-bottom: 0; }",
        words=900),
    Format(
        "modern", "Modern", "Arial", "#0E7C74",
        "Bold name, teal heading bars, each role's title on its own line. For product, design and startups.",
        css="""
body { font-family: Arial, Helvetica, sans-serif; font-weight: 400; font-size: 10pt; line-height: 13pt; color: #333; }
.name { font-size: 26pt; font-weight: 700; letter-spacing: -.01em; color: #111; }
.title { color: #0E7C74; font-size: 11pt; margin-top: 7pt; }
.contact { color: #555; margin-top: 4pt; }
.contact a { color: #555; }
.rule-heavy { border-bottom: 3pt solid #0E7C74; width: 48pt; }
.after-contact { margin-top: 12pt; }
.pre-skills { display: none; }
h2 { font-size: 11pt; letter-spacing: .06em; color: #0E7C74; border-left: 3pt solid #0E7C74; padding-left: 7pt;
     line-height: 12pt; margin: 16pt 0 7pt; }
h2.first { margin-top: 11pt; }
h2.skills { margin-top: 16pt; }
.rule-light { border-bottom: 0; margin: 9pt 0 0; }
.jobrow { align-items: flex-start; }
.jobline .co { font-size: 11pt; font-weight: 700; color: #111; }
.jobline .sep { display: none; }
.jobline .role { display: block; font-size: 10pt; font-weight: 700; color: #0E7C74; margin-top: 1pt; }
.dates { font-style: normal; font-weight: 400; color: #666; font-size: 9.5pt; }
ul.b { padding-left: 0; margin-top: 4pt; }
ul.b > li { padding-left: 13pt; text-indent: -13pt; margin-bottom: 2pt; }
ul.b > li::before { content: ""; width: 4pt; height: 4pt; margin-right: 9pt; vertical-align: 2pt; border-radius: 50%; background: #0E7C74; }
""",
        tighten="h2, h2.skills { margin-top: 11pt; } .rule-light { margin-top: 6pt; } ul.b > li { margin-bottom: 0; }",
        words=880),
    Format(
        "minimal", "Minimal", "Helvetica", "",
        "Black and grey only, quiet headings, dates under each role. Prints well in black and white.",
        css="""
body { font-family: Helvetica, Arial, sans-serif; font-weight: 400; font-size: 10pt; line-height: 13pt; color: #222; }
.name { font-size: 20pt; font-weight: 400; letter-spacing: .02em; color: #000; }
.title { color: #000; font-weight: 400; margin-top: 6pt; }
.contact { color: #6B6B6B; font-size: 9.5pt; margin-top: 2pt; }
.contact a { color: #6B6B6B; text-decoration: none; }
.rule-heavy { border-bottom: .5pt solid #000; }
.after-contact { margin-top: 12pt; }
.pre-skills { display: none; }
h2 { font-size: 9pt; font-weight: 700; letter-spacing: .06em; color: #6B6B6B; margin: 18pt 0 7pt; }
h2.first { margin-top: 12pt; }
h2.skills { margin-top: 18pt; }
.rule-light { border-bottom: 0; margin: 10pt 0 0; }
.jobrow { display: block; }
.jobline { font-size: 10.5pt; font-weight: 700; color: #000; }
.dates { font-weight: 400; font-style: normal; color: #6B6B6B; font-size: 9.5pt; margin-top: 1pt; }
ul.b { padding-left: 0; margin-top: 4pt; }
ul.b > li { padding-left: 12pt; text-indent: -12pt; margin-bottom: 1.5pt; }
ul.b > li::before { content: "\\2013"; width: 12pt; color: #6B6B6B; }
""",
        tighten="h2, h2.skills { margin-top: 12pt; } .rule-light { margin-top: 6pt; } ul.b > li { margin-bottom: 0; }",
        words=880),
    Format(
        "compact", "Compact", "Arial", "#1D4ED8",
        "One page. Skills straight after the summary, tight spacing. For technical roles, early careers and "
        "career changers.",
        max_pages=1, margin_in=0.5, order=("SUMMARY", "CORE SKILLS"), words=600,
        css="""
body { font-family: Arial, Helvetica, sans-serif; font-weight: 400; font-size: 10pt; line-height: 11.5pt; color: #262626; }
.name { font-size: 18pt; font-weight: 700; color: #1D4ED8; }
.title { font-size: 10pt; color: #262626; margin-top: 2pt; }
.contact { color: #555; margin-top: 2pt; }
.contact a { color: #555; }
.rule-heavy { border-bottom: 1pt solid #262626; }
.after-contact { margin-top: 4pt; }
.pre-skills { display: none; }
h2 { font-size: 10.5pt; color: #262626; border-bottom: 1pt solid #262626; padding-bottom: 1pt; margin: 6pt 0 2pt; }
h2.first { margin-top: 5pt; }
h2.skills { margin-top: 6pt; }
.rule-light { border-bottom: 0; margin: 3pt 0 0; }
.jobline { font-size: 10pt; }
.dates { font-size: 9.5pt; font-style: normal; color: #555; }
ul.b { padding-left: 0; margin-top: 0; }
ul.b > li { padding-left: 11pt; text-indent: -11pt; }
ul.b > li::before { content: "\\2022"; width: 11pt; color: #1D4ED8; }
.skills-p + .skills-p { margin-top: 2pt; }
""",
        tighten="h2, h2.skills { margin-top: 4pt; } .rule-light { margin-top: 2pt; }"),
)}


def get(fid: str | None) -> Format:
    """The format with this id; the default for an unknown or empty one (a format later removed, an old run)."""
    if fid in FORMATS:
        return FORMATS[fid]
    if fid and fid.startswith(OWN):
        from . import own_formats
        own = own_formats.load(fid)
        if own:
            return own
    return FORMATS[DEFAULT]


def known(fid: str) -> bool:
    return fid in FORMATS or get(fid).id == fid


def all_formats() -> list[Format]:
    """The built-in formats, then the candidate's own, oldest first."""
    from . import own_formats
    return [*FORMATS.values(), *(own_formats.load(r["id"]) for r in own_formats.records())]


def length_rules(fmt: Format) -> str:
    """The page limit as the writers, the merge and the trim step are told it. render_pdf decides the actual
    page count."""
    if fmt.max_pages == 1:
        bullets = "The most recent role has at most 5 bullets and each earlier role at most 3"
    else:
        bullets = "The most recent role has at most 8 bullets and each earlier role at most 4"
    return (f"Hard length limit: the resume must fit {fmt.pages_word} in the {fmt.name} format, about {fmt.words} "
            f"words at most. {bullets}; every bullet is at most two lines (about 35 words): the posting's lead "
            "phrase, the action, the strongest figure. ADDITIONAL EXPERIENCE entries are one line each.")

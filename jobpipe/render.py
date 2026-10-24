#!/usr/bin/env python3
"""Render a resume markdown (resume-job-fit writer shape) into a résumé PDF (headless Chromium via
Playwright), in one of the formats in jobpipe/formats.py. CSS below is the Signature format, the original
two-page red-accent layout; the other formats are laid over it.

Usage: python -m jobpipe.render resume.md out.pdf [--html out.html] [--format compact]
"""
import argparse
import html
import os
import re
import textwrap
import time
from io import BytesIO
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

from . import formats

# Playwright's own Chromium by default (`playwright install chromium`); set
# PLAYWRIGHT_CHROMIUM_EXECUTABLE to use another build (e.g. /opt/pw-browsers/chromium).
CHROMIUM = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
# A Mac checks a browser it hasn't run before (the app's is 150 MB, downloaded with it) the first time, which
# can take longer than Playwright's 30-second default.
LAUNCH_TIMEOUT_MS = 120_000
# After the browser fails to start, asks in the next minute fail at once rather than each waiting on it.
RETRY_AFTER = 60
_no_browser = {"at": 0.0, "why": ""}


class BrowserUnavailable(RuntimeError):
    """The PDF renderer (headless Chromium) couldn't start. `tried` is False when this ask didn't try again
    because one failed in the last minute (that failure is already in the log)."""

    def __init__(self, why: str, tried: bool):
        super().__init__(f"The PDF renderer (headless Chromium) couldn't start: {why}")
        self.why, self.tried = why, tried


def _first_line(e: BaseException) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0]


def unavailable_svg(why: str) -> bytes:
    """A page-shaped picture that says the preview couldn't be drawn, shown where it would go (the Profile's
    format thumbnails, a format's live preview) instead of a browser's broken-image mark. US Letter proportions."""
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    lines = textwrap.wrap(" ".join(why.split()), 42)[:5]
    text = "".join(f'<text x="80" y="{470 + i * 44}" font-size="30" fill="#434343">{esc(l)}</text>' for i, l in enumerate(lines))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 816 1056" width="816" height="1056" '
            f'font-family="Helvetica Neue, Helvetica, Arial, sans-serif">'
            f'<rect width="816" height="1056" fill="#fff"/>'
            f'<text x="80" y="400" font-size="40" font-weight="700" fill="#171717">This preview couldn\'t be drawn.</text>'
            f"{text}"
            f'<text x="80" y="{470 + len(lines) * 44 + 20}" font-size="30" fill="#666">The app\'s log says more.</text>'
            f"</svg>").encode()

CSS = """
@page { size: 8.5in 11in; margin: 0.625in; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #fff; }
body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
       font-weight: 300; font-size: 10pt; line-height: 12pt; color: #434343; }
/* No ligatures: "fl" drawn as one glyph comes out of the PDF as "ﬂ", and an ATS then misses the word. */
body { font-variant-ligatures: none; font-feature-settings: "liga" 0, "clig" 0; }
.name { font-size: 18pt; line-height: 1.0; font-weight: 700; color: #171717; margin: 0; }
.title { font-size: 10pt; font-weight: 700; color: #E50815; margin-top: 5pt; }
.contact { font-size: 10pt; color: #666666; margin-top: 3pt; }
.contact a { color: #666666; }
.rule-heavy { border-bottom: 1.5pt solid #E50815; height: 0; }
.rule-light { border-bottom: 0.5pt solid #CCCCCC; height: 0; margin: 6pt 0; }
.pre-skills { margin-top: 12pt; }
.after-contact { margin-top: 8pt; }
h2 { font-size: 11.5pt; font-weight: 700; color: #E50815; text-transform: uppercase;
     margin: 16pt 0 4pt 0; line-height: 13pt; }
h2.first { margin-top: 11pt; }
h2.skills { margin-top: 6pt; }
p { margin: 0; }
/* A role may run onto the next page, but never leaves its header without its first bullet, and a page
   never ends on a section heading. */
.jobrow, h2, .rule-light { break-after: avoid; page-break-after: avoid; }
ul.b > li, ul.n > li { break-inside: avoid; page-break-inside: avoid; }
.jobrow { display: flex; justify-content: space-between; align-items: baseline; gap: 12pt; }
.jobline { font-size: 10.5pt; font-weight: 700; color: #171717; }
.dates { font-size: 10pt; font-weight: 700; font-style: italic; color: #999999; white-space: nowrap; }
ul.b { list-style: none; margin: 2pt 0 0 0; padding: 0 0 0 18pt; }
/* Markers sit in the line (a hanging indent), never positioned: Chromium writes positioned boxes into the PDF
   after everything else, so the text read out of it listed every bullet after the last section. */
ul.b > li { padding-left: 18pt; text-indent: -18pt; }
ul.b > li::before { content: "-"; display: inline-block; width: 18pt; text-indent: 0; color: #000; }
ul.n { list-style: none; margin: 0; padding: 0; }
ul.n > li { padding-left: 14pt; text-indent: -14pt; }
ul.n > li::before { content: "\\00B7"; display: inline-block; width: 14pt; text-indent: 0; color: #000; }
.addl-p { margin-top: 2pt; }
.skills-p + .skills-p { margin-top: 3pt; }
code { font-family: Menlo, Consolas, monospace; font-size: 9pt; }
"""


def inline(s):
    s = s.replace("'", "’")
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)  # no bold inside body text
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"\1", s)
    return s


def contact_html(s):
    parts = [p.strip() for p in s.split("|")]
    out = []
    for p in parts:
        if p.startswith("linkedin.com/"):
            out.append(f'<a href="https://www.{html.escape(p)}">{html.escape(p)}</a>')
        else:
            out.append(inline(p))
    return " | ".join(out)


# Sections whose `**Company** | Title` lines are job rows. Plain EXPERIENCE is from résumés tailored
# before the heading became PROFESSIONAL EXPERIENCE; any other "... EXPERIENCE" heading (a pinned
# FOUNDER EXPERIENCE, say: jobpipe/pinned.py) has job rows too.
ROLE_SECTIONS = ("PROFESSIONAL EXPERIENCE", "EXPERIENCE", "ADDITIONAL EXPERIENCE")


def role_section(section: str) -> bool:
    return section in ROLE_SECTIONS or section.endswith(" EXPERIENCE")


# The page editor (option `editable`): every piece of text the candidate can change is wrapped in a
# span that names its markdown line and which part of that line it is. Saving maps each changed
# span back onto that line (jobpipe/web/resumes.py, apply_page_edits).
# A letter-size sheet on a grey desk: the page editor, and previews in the app.
SHEET_CSS = """
html { background: #e6e6e6; }
body { width: 8.5in; margin: 24px auto; padding: var(--margin); background: #fff; position: relative;
       box-shadow: 0 1px 4px rgba(0,0,0,.25); }
"""
EDIT_CSS = SHEET_CSS + """
[data-ed] { border-radius: 2px; outline: none; cursor: text; }
[data-ed]:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); }
[data-ed]:focus { background: #fff4c2; box-shadow: 0 0 0 1px #e0c25a; }
[data-ed].changed { background: #fff4c2; }
.page-guide { position: absolute; left: 0; right: 0; border-top: 1px dashed #b0b0b0; pointer-events: none; }
.page-guide span { position: absolute; right: 6px; top: -9px; padding: 0 4px; background: #fff;
                   font: 400 8pt -apple-system, Helvetica, sans-serif; color: #888; }
"""

# Option `mark`: lines picked out on the page (the Profile's pinned-section preview).
MARK_CSS = """
mark.pin { background: #fff1a8; color: inherit; box-shadow: 0 0 0 2px #fff1a8; border-radius: 1px; }
h2.pin { background: #fff1a8; box-shadow: 0 0 0 2px #fff1a8; }
"""


def render(md, editable=False, fmt=None, title=None, mark=()):
    """The résumé as an HTML page in format `fmt` (an id or a Format; the default when None). With
    `editable`, every piece of text is a click-to-edit span for the page editor. `title` is the page's
    title, and so the PDF's, which browsers save it under (default "<Name> Resume"). The text of the
    markdown lines in `mark` (0-based numbers) is highlighted."""
    fmt = fmt if isinstance(fmt, formats.Format) else formats.get(fmt)
    lines = md.splitlines()
    i = 0
    body = []
    name = headline = contact = None
    at = {}

    def ed(text_html, line, field):
        if line in mark:
            text_html = f'<mark class="pin">{text_html}</mark>'
        if not editable:
            return text_html
        return f'<span data-ed="{line}:{field}" contenteditable="plaintext-only" spellcheck="true">{text_html}</span>'

    # header
    while i < len(lines):
        ln = lines[i].strip()
        i += 1
        if not ln:
            continue
        if ln.startswith("# ") and name is None:
            name, at["name"] = ln[2:].strip(), i - 1
        elif headline is None and ln.startswith("**"):
            headline, at["headline"] = ln.strip("*").strip(), i - 1
        elif contact is None:
            contact, at["contact"] = ln, i - 1
            break
    body.append(f'<div class="name">{ed(inline(name), at.get("name"), "name")}</div>')
    body.append(f'<div class="title">{ed(inline(headline), at.get("headline"), "headline")}</div>')
    body.append(f'<div class="contact">{ed(contact_html(contact), at.get("contact"), "contact")}</div>')
    body.append('<div class="rule-heavy after-contact"></div>')

    section = None
    first_section = True
    job_open = False
    pending_head = None
    list_open = nested_open = False
    roles_in_section = 0

    def close_lists():
        nonlocal list_open, nested_open
        if nested_open:
            body.append("</ul></li>")
            nested_open = False
        elif list_open:
            body.append("</li>")
        if list_open:
            body.append("</ul>")
            list_open = False

    def close_job():
        nonlocal job_open
        close_lists()
        if job_open:
            body.append("</div>")
            job_open = False

    pending_li = False
    starts = []         # (heading, index in body) where each `## ` section's markup begins
    while i < len(lines):
        raw = lines[i]
        ln = raw.strip()
        i += 1
        if not ln or ln == "---":
            continue
        if ln.startswith("## "):
            close_job()
            section = ln[3:].strip().upper()
            starts.append((section, len(body)))
            roles_in_section = 0
            cls = ""
            if first_section:
                cls = "first"
                first_section = False
            if section == "CORE SKILLS":
                body.append('<div class="rule-heavy pre-skills"></div>')
                cls = "skills"
            if i - 1 in mark:
                cls = (cls + " pin").strip()
            head = f'<h2 class="{cls}">{inline(section if fmt.heading_case == "upper" else title_case(section))}</h2>'
            if role_section(section):
                # emitted inside the first entry's block so it can't be orphaned at a page end
                pending_head = head
            else:
                body.append(head)
            continue
        m = re.match(r"^\*\*(.+?)\*\*\s*\|\s*(.+)$", ln)
        if m and role_section(section):
            close_job()
            rule = roles_in_section > 0 or section == "ADDITIONAL EXPERIENCE"
            roles_in_section += 1
            jobline = (f'<span class="co">{ed(inline(m.group(1)), i - 1, "company")}</span><span class="sep"> | </span>'
                       f'<span class="role">{ed(inline(m.group(2)), i - 1, "role")}</span>')
            dates = ""
            # the italic date line follows
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines) and re.match(r"^\*[^*].*\*$", lines[i].strip()):
                dates = ed(inline(lines[i].strip().strip("*").strip()), i, "dates")
                i += 1
            body.append('<div class="job">')
            job_open = True
            if pending_head:
                body.append(pending_head)
                pending_head = None
            if rule:
                body.append('<div class="rule-light"></div>')
            body.append(f'<div class="jobrow"><div class="jobline">{jobline}</div>'
                        f'<div class="dates">{dates}</div></div>')
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if ln.startswith("- "):
            text = ed(inline(ln[2:]), i - 1, "bullet")
            if indent >= 2 and list_open:
                if not nested_open:
                    body.append('<ul class="n">')
                    nested_open = True
                body.append(f"<li>{text}</li>")
            else:
                if nested_open:
                    body.append("</ul></li>")
                    nested_open = False
                elif list_open:
                    body.append("</li>")
                if not list_open:
                    body.append('<ul class="b">')
                    list_open = True
                body.append(f"<li>{text}")
            continue
        if indent >= 2 and list_open:
            # continuation of the current bullet
            if nested_open:
                body.append("</ul>")
                nested_open = False
                body.append(f" {ed(inline(ln), i - 1, 'line')}</li>")
                # reopen a hidden li so close_lists stays balanced
                body.append('<li style="display:none">')
            else:
                body.append(f" {ed(inline(ln), i - 1, 'line')}")
            continue
        # plain paragraph
        close_lists()
        text = ed(inline(ln), i - 1, "line")
        if section == "ADDITIONAL EXPERIENCE":
            body.append(f'<p class="addl-p">{text}</p>')
        elif section == "CORE SKILLS":
            body.append(f'<p class="skills-p">{text}</p>')
        else:
            body.append(f"<p>{text}</p>")
    close_job()
    body = _reorder(body, starts, fmt.order)
    title = title or f"{name} Resume"
    page = (f"@page {{ margin: {fmt.margin_in}in; }}\n"
            f":root {{ --margin: {fmt.margin_in}in; --accent: {fmt.accent or '#000000'}; }}\n")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{CSS}{page}{fmt.css}{EDIT_CSS if editable else ''}{MARK_CSS if mark else ''}</style></head><body>{''.join(body)}</body></html>")


SMALL_WORDS = {"and", "of", "the", "for", "in", "on", "to", "a", "an", "&"}


def title_case(heading):
    """PROFESSIONAL EXPERIENCE -> Professional Experience; small words stay lower-case after the first."""
    words = heading.lower().split()
    return " ".join(w if k and w in SMALL_WORDS else w[:1].upper() + w[1:] for k, w in enumerate(words))


def _reorder(body, starts, order):
    """Move the named sections' markup to the front of the body, in that order. Done on the drawn markup, not
    the markdown, so the page editor's line numbers still point at the right lines."""
    if not order or not starts:
        return body
    cuts = [s for _, s in starts] + [len(body)]
    head, chunks = body[:cuts[0]], {}
    for k, (name, _) in enumerate(starts):
        chunks.setdefault(name, []).append(body[cuts[k]:cuts[k + 1]])
    lead = [c for name in order for c in chunks.get(name, [])]
    rest = [body[cuts[k]:cuts[k + 1]] for k, (name, _) in enumerate(starts) if name not in order]
    return head + [x for c in lead + rest for x in c]


# The page on screen as it prints: a letter-size sheet with the format's margins.
THUMB_CSS = "html, body { background: #fff; } body { width: 8.5in; padding: var(--margin); }"


class Printer:
    """One Chromium for many PDFs (redrawing every résumé in a new format); `with Printer() as pr: pr.pdf(...)`."""

    def __enter__(self):
        if time.time() - _no_browser["at"] < RETRY_AFTER:
            raise BrowserUnavailable(_no_browser["why"], tried=False)
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(executable_path=CHROMIUM, timeout=LAUNCH_TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001 - whatever kept it from starting: hold a minute, say so, keep the cause
            self._pw.stop()
            _no_browser.update(at=time.time(), why=_first_line(e))
            raise BrowserUnavailable(_no_browser["why"], tried=True) from e
        return self

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()

    def print(self, doc, pdf_path):
        pg = self._browser.new_page()
        try:
            pg.set_content(doc, wait_until="load")
            pg.pdf(path=str(pdf_path), format="Letter", prefer_css_page_size=True,
                   display_header_footer=False, print_background=True)
        finally:
            pg.close()

    def png(self, doc, png_path, scale=0.6):
        """The first page as an image, `scale` × 96 dpi (the Profile tab's format thumbnails)."""
        w, h = 816, 1056                       # US Letter at 96 px per inch
        pg = self._browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=scale)
        try:
            pg.set_content(doc.replace("</style>", THUMB_CSS + "</style>"), wait_until="load")
            pg.screenshot(path=str(png_path), clip={"x": 0, "y": 0, "width": w, "height": h})
        finally:
            pg.close()

    def pdf(self, md_text, pdf_path, html_path=None, fmt=None, title=None):
        """Render to PDF. Returns (pages, extracted_text). Tightens spacing once if over the format's page
        limit (the skill's fix for a tight page: smaller gaps before headings, never smaller text)."""
        fmt = fmt if isinstance(fmt, formats.Format) else formats.get(fmt)
        doc = render(md_text, fmt=fmt, title=title)
        self.print(doc, pdf_path)
        pages = len(PdfReader(str(pdf_path)).pages)
        if pages > fmt.max_pages and fmt.tighten:
            doc = doc.replace("</style>", fmt.tighten + "</style>")
            self.print(doc, pdf_path)
            pages = len(PdfReader(str(pdf_path)).pages)
        if html_path:
            Path(html_path).write_text(doc, encoding="utf-8")
        text = "\n".join(pg.extract_text() or "" for pg in PdfReader(str(pdf_path)).pages)
        return pages, text


def render_pdf(md_text, pdf_path, html_path=None, fmt=None, title=None):
    """Render to PDF in format `fmt`, titled `title`. Returns (pages, extracted_text)."""
    with Printer() as pr:
        return pr.pdf(md_text, pdf_path, html_path, fmt, title)


def retitled(path, title):
    """The PDF's bytes with its Title set to `title` (what browsers save it under); None if pypdf can't read it."""
    try:
        w = PdfWriter(clone_from=str(path))
        w.add_metadata({"/Title": title})
        buf = BytesIO()
        w.write(buf)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - not a PDF pypdf can read
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("pdf")
    ap.add_argument("--html")
    ap.add_argument("--format", default=formats.DEFAULT, choices=list(formats.FORMATS))
    a = ap.parse_args()
    pages, _ = render_pdf(open(a.md, encoding="utf-8").read(), a.pdf, a.html, a.format)
    print(f"wrote {a.pdf} ({pages} pages)")


if __name__ == "__main__":
    main()

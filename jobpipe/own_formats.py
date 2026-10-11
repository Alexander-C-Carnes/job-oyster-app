"""Résumé formats of the candidate's own: a résumé's look read into settings, drawn by the same renderer.

read_look() reads an uploaded résumé's look: fonts (matched to the nearest standard font), sizes, colours, how the
name and headings are set, the rules drawn, where the dates sit, the bullet, the margins. A PDF is read from its
own drawing (pypdf), a Word file from its styles. What a one-column, real-text page can't keep (columns,
sidebars, photos, shaded boxes) is listed as not copied, with why.

The settings (TOKENS, kept in range by clean()) make CSS for the same markup as every format (css()), so the
structure, and with it the ATS rules, never comes from the upload. Each is saved in the profile as
references/formats/<slug>.json, as many as the candidate wants; formats.get() finds them by their "own-" id.
"""
from __future__ import annotations

import io
import json
import math
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Optional
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from . import config
from .formats import FORMATS, OWN, Format

# ---- the settings -----------------------------------------------------------------------------------------
FONTS = {   # the standard fonts a format may use, with their fallbacks
    "Helvetica Neue": "'Helvetica Neue', Helvetica, Arial, sans-serif",
    "Helvetica": "Helvetica, Arial, sans-serif",
    "Arial": "Arial, Helvetica, sans-serif",
    "Avenir Next": "'Avenir Next', Avenir, Helvetica, Arial, sans-serif",
    "Georgia": "Georgia, 'Times New Roman', serif",
    "Times New Roman": "'Times New Roman', Times, Georgia, serif",
    "Palatino": "Palatino, 'Palatino Linotype', 'Book Antiqua', Georgia, serif",
    "Baskerville": "Baskerville, 'Baskerville Old Face', Georgia, serif",
}
BULLETS = ("•", "–", "-", "▪", "◦")
CHOICES = {
    "name_align": ("left", "center"), "name_case": ("as-is", "upper"), "name_color": ("ink", "accent"),
    "headline_color": ("accent", "ink", "muted"), "heading_case": ("upper", "title"),
    "heading_align": ("left", "center"), "heading_color": ("accent", "ink"),
    "heading_style": ("plain", "rule", "bar", "lines"), "header_rule": ("none", "thin", "thick", "double"),
    "dates": ("right", "under"), "bullet": BULLETS,
}
RANGES = {"body_size": (9.5, 11.5), "name_size": (14, 30), "heading_size": (9, 14), "margin_in": (0.5, 1.0)}
TOKENS = {  # Signature's settings: where a new format starts, and what a missing value falls back to
    "body_font": "Helvetica Neue", "heading_font": "Helvetica Neue", "name_font": "Helvetica Neue",
    "body_size": 10.0, "name_size": 18.0, "heading_size": 11.5, "ink": "#434343", "accent": "#E50815",
    "name_align": "left", "name_case": "as-is", "name_color": "ink", "headline_color": "accent", "headline_italic": False,
    "heading_case": "upper", "heading_align": "left", "heading_color": "accent", "heading_style": "plain",
    "header_rule": "thick", "role_rule": True, "dates": "right", "bullet": "-", "margin_in": 0.625, "max_pages": 2,
    "skills_first": False,
}
BUILTIN = {   # each built-in format as settings, so Customise starts from it (tests read them back from the PDFs)
    "signature": TOKENS,
    "executive": {**TOKENS, "body_font": "Georgia", "heading_font": "Georgia", "name_font": "Georgia", "name_size": 21.0,
                  "heading_size": 11.0, "ink": "#2B2B2B", "accent": "#1F3A5F", "name_align": "center", "name_case": "upper",
                  "name_color": "accent", "headline_italic": True, "heading_align": "center", "heading_style": "lines",
                  "header_rule": "double", "bullet": "•"},
    "modern": {**TOKENS, "body_font": "Arial", "heading_font": "Arial", "name_font": "Arial", "name_size": 26.0,
               "heading_size": 11.0, "ink": "#333333", "accent": "#0E7C74", "heading_style": "bar", "header_rule": "none",
               "role_rule": False, "bullet": "•"},
    "minimal": {**TOKENS, "body_font": "Helvetica", "heading_font": "Helvetica", "name_font": "Helvetica",
                "name_size": 20.0, "heading_size": 9.0, "ink": "#222222", "accent": "", "headline_color": "ink",
                "heading_color": "ink", "header_rule": "thin", "role_rule": False, "dates": "under", "bullet": "–"},
    "compact": {**TOKENS, "body_font": "Arial", "heading_font": "Arial", "name_font": "Arial", "ink": "#262626",
                "heading_size": 10.5,
                "accent": "#1D4ED8", "name_color": "accent", "headline_color": "ink", "heading_color": "ink",
                "heading_style": "rule", "header_rule": "thin", "role_rule": False, "bullet": "•", "margin_in": 0.5,
                "max_pages": 1, "skills_first": True},
}
HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def clean(raw: dict) -> dict:
    """Settings from the form or a reader, each kept to its allowed values or range; unknown keys dropped."""
    out = dict(TOKENS)
    for k, v in (raw or {}).items():
        if k not in TOKENS:
            continue
        if k.endswith("_font"):
            out[k] = v if v in FONTS else out[k]
        elif k in CHOICES:
            out[k] = v if v in CHOICES[k] else out[k]
        elif k in RANGES:
            try:
                lo, hi = RANGES[k]
                out[k] = round(min(hi, max(lo, float(v))) * 4) / 4 if k != "margin_in" else round(min(hi, max(lo, float(v))) * 40) / 40
            except (TypeError, ValueError):
                pass
        elif k == "ink":
            out[k] = v.upper() if isinstance(v, str) and HEX.fullmatch(v) and contrast(v) >= 7 else out[k]
        elif k == "accent":
            out[k] = v.upper() if isinstance(v, str) and HEX.fullmatch(v) else ""
        elif k == "max_pages":
            out[k] = 1 if str(v) == "1" else 2
        elif k in ("headline_italic", "role_rule", "skills_first"):
            out[k] = bool(v)
    if not out["accent"]:                   # nothing coloured: the accent's places use the ink
        out.update(name_color="ink", heading_color="ink", headline_color="ink" if out["headline_color"] == "accent" else out["headline_color"])
    return out


def contrast(hex_color: str, against: str = "#FFFFFF") -> float:
    """WCAG contrast ratio of two colours."""
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    a, b = sorted((lum(hex_color), lum(against)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def mix(a: str, b: str, share_b: float) -> str:
    ca, cb = ([int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in (a, b))
    return "#" + "".join(f"{round(x + (y - x) * share_b):02X}" for x, y in zip(ca, cb))


def css(t: dict) -> str:
    """The format's CSS, laid over render.CSS like every built-in format's."""
    t = clean(t)
    accent = t["accent"] or t["ink"]
    text_accent = accent if contrast(accent) >= 3 else t["ink"]     # a pale accent stays on rules and bullets
    color = {"ink": t["ink"], "accent": text_accent, "muted": "#666666"}
    upper = lambda on: "text-transform: uppercase; letter-spacing: .04em;" if on else "text-transform: none; letter-spacing: 0;"
    bs, hs = t["body_size"], t["heading_size"]
    heading = {
        "plain": "",
        "rule": f"border-bottom: .75pt solid {accent}; padding-bottom: 2pt;",
        "bar": f"border-left: 3pt solid {accent}; padding-left: 7pt; line-height: {hs + 1}pt;",
        "lines": "display: flex; align-items: center; gap: 10pt;",
    }[t["heading_style"]]
    lines = ""
    if t["heading_style"] == "lines":      # a paler accent, mixed here: transparency would draw them in a layer of their own
        pale = mix(accent, "#FFFFFF", 0.45)
        lines = (f"h2::after {{ content: ''; flex: 1; border-top: .5pt solid {pale}; }}\n"
                 + (f"h2::before {{ content: ''; flex: 1; border-top: .5pt solid {pale}; }}\n"
                    if t["heading_align"] == "center" else ""))
    rule = {"none": "border-bottom: 0;", "thin": f"border-bottom: .75pt solid {accent};",
            "thick": f"border-bottom: 1.5pt solid {accent};", "double": f"border-bottom: 2.5pt double {accent};"}[t["header_rule"]]
    dates_under = t["dates"] == "under"
    return f"""
body {{ font-family: {FONTS[t['body_font']]}; font-weight: 400; font-size: {bs}pt; line-height: {round(bs * 1.27, 1)}pt; color: {t['ink']}; }}
.name {{ font-family: {FONTS[t['name_font']]}; font-size: {t['name_size']}pt; font-weight: 700; color: {color[t['name_color']]};
        text-align: {t['name_align']}; {upper(t['name_case'] == 'upper')} }}
.title {{ text-align: {t['name_align']}; color: {color[t['headline_color']]}; font-weight: 600; font-size: {bs + .5}pt;
         font-style: {'italic' if t['headline_italic'] else 'normal'}; margin-top: 5pt; }}
.contact {{ text-align: {t['name_align']}; color: #666666; font-size: {bs - .25}pt; margin-top: 3pt; }}
.contact a {{ color: #666666; }}
.rule-heavy {{ {rule} }}
.after-contact {{ margin-top: {9 if t['header_rule'] != 'none' else 6}pt; }}
.pre-skills {{ display: none; }}
h2 {{ font-family: {FONTS[t['heading_font']]}; font-size: {hs}pt; font-weight: 700; color: {color[t['heading_color']]};
     text-align: {t['heading_align']}; {upper(t['heading_case'] == 'upper')} margin: 14pt 0 5pt; {heading} }}
{lines}h2.first {{ margin-top: 10pt; }}
h2.skills {{ margin-top: 14pt; }}
.rule-light {{ {'border-bottom: .5pt solid #CCCCCC; margin: 6pt 0;' if t['role_rule'] else 'border-bottom: 0; margin: 8pt 0 0;'} }}
.jobrow {{ {'display: block;' if dates_under else ''} }}
.jobline {{ font-size: {bs + .5}pt; font-weight: 700; color: #1A1A1A; }}
.dates {{ font-weight: 400; font-style: normal; color: #666666; font-size: {bs - .25}pt; {'margin-top: 1pt;' if dates_under else ''} }}
ul.b {{ padding-left: 0; margin-top: 3pt; }}
ul.b > li {{ padding-left: 12pt; text-indent: -12pt; margin-bottom: 1pt; }}
ul.b > li::before {{ content: "{t['bullet']}"; width: 12pt; color: {accent}; }}
"""


def to_format(fid: str, name: str, tokens: dict, source: str = "") -> Format:
    t = clean(tokens)
    return Format(fid, name, t["body_font"], t["accent"], f"Your own{', from ' + source if source else ''}.",
                  max_pages=t["max_pages"], margin_in=t["margin_in"], heading_case=t["heading_case"],
                  order=("SUMMARY", "CORE SKILLS") if t["skills_first"] else (), css=css(t),
                  tighten="h2, h2.skills { margin-top: 10pt; } .rule-light { margin: 4pt 0; } ul.b > li { margin-bottom: 0; }",
                  words=600 if t["max_pages"] == 1 else 900)


# ---- storage: references/formats/<slug>.json ----------------------------------------------------------------
def folder() -> Path:
    return config.REFERENCES / "formats"


def _path(fid: str) -> Optional[Path]:
    slug = fid[len(OWN):] if fid.startswith(OWN) else ""
    return folder() / f"{slug}.json" if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", slug) else None


def load_record(fid: str) -> Optional[dict]:
    p = _path(fid)
    if not p or not p.exists():
        return None
    try:
        return {**json.loads(p.read_text()), "id": fid}
    except (OSError, ValueError):
        return None


def load(fid: str) -> Optional[Format]:
    rec = load_record(fid)
    return to_format(fid, rec["name"], rec.get("tokens") or {}, rec.get("source", "")) if rec else None


def records() -> list[dict]:
    if not folder().exists():
        return []
    out = [load_record(OWN + p.stem) for p in sorted(folder().glob("*.json"))]
    return sorted((r for r in out if r), key=lambda r: r.get("created", ""))


def save(name: str, tokens: dict, source: str = "", notes: Optional[dict] = None, fid: Optional[str] = None) -> dict:
    """Save a format (a new one when fid is None, named uniquely). Returns its record."""
    name = re.sub(r"\s+", " ", name or "").strip()[:40] or "My format"
    taken = {r["name"].lower() for r in records() if r["id"] != fid}
    if name.lower() in taken or name.lower() in {f.name.lower() for f in FORMATS.values()}:
        raise ValueError(f"There's already a format called “{name}”. Give this one another name.")
    if fid is None:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "format"
        slug, k = base, 2
        while (folder() / f"{slug}.json").exists():
            slug, k = f"{base}-{k}", k + 1
        fid, old = OWN + slug, {}
    else:
        old = load_record(fid) or {}
        if not old:
            raise ValueError("That format isn't there any more.")
    rec = {"name": name, "tokens": clean(tokens), "source": source or old.get("source", ""),
           "notes": notes if notes is not None else old.get("notes", {}),
           "created": old.get("created") or datetime.now(timezone.utc).isoformat(timespec="seconds")}
    folder().mkdir(parents=True, exist_ok=True)
    _path(fid).write_text(json.dumps(rec, indent=2) + "\n")
    return {**rec, "id": fid}


def delete(fid: str) -> bool:
    p = _path(fid)
    if p and p.exists():
        p.unlink()
        return True
    return False


# ---- reading a résumé's look ---------------------------------------------------------------------------------
SERIF = ("georgia", "times", "garamond", "cambria", "bookantiqua", "palatino", "baskerville", "caslon", "minion",
         "didot", "bodoni", "merriweather", "playfair", "charter", "hoefler", "constantia", "century", "crimson",
         "lora", "cormorant", "spectral", "ptserif", "sourceserif", "librebaskerville", "serif")
MONO = ("courier", "mono", "consolas", "menlo", "inconsolata")
HEADINGS = re.compile(r"^(professional |work |relevant |career )?(summary|profile|experience|employment|history|"
                      r"additional experience|education|skills|core skills|technical skills|competencies|certifications|"
                      r"projects|interests|awards|publications|volunteering|languages|about me|about)( & \w+)?:?$", re.I)
DATE = re.compile(r"\b(?:\d{4}|\d{1,2}/\d{4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{4})\s*(?:[–—-]|to)\s*"
                  r"(?:\d{4}|\d{1,2}/\d{4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{4}|Present|Current|Now)", re.I)


def match_font(raw: str) -> tuple[str, str]:
    """A font name from a file (e.g. "ABCDEF+Garamond-Bold") -> (a standard font, the file's family name)."""
    fam = re.sub(r"^[A-Z]{6}\+", "", (raw or "").lstrip("/")).split(",")[0]
    fam = re.sub(r"[-_](Bold|Italic|Oblique|Regular|Light|Medium|Semibold|SemiBold|Black|Book|Roman|BoldItalic|"
                 r"LightItalic|BoldMT|MT|PSMT|PS)+.*$", "", fam)
    fam = re.sub(r"(PSMT|MT|PS)$", "", fam)
    key = re.sub(r"[^a-z]", "", fam.lower())
    if "helveticaneue" in key:
        return "Helvetica Neue", fam
    for k, std in (("helvetica", "Helvetica"), ("arial", "Arial"), ("avenir", "Avenir Next"), ("georgia", "Georgia"),
                   ("times", "Times New Roman"), ("palatino", "Palatino"), ("bookantiqua", "Palatino"),
                   ("baskerville", "Baskerville")):
        if k in key:
            return std, fam
    if any(m in key for m in MONO):
        return "Helvetica", fam
    if any(s in key for s in SERIF) and "sans" not in key:
        return "Georgia", fam
    return "Arial", fam


@dataclass
class Span:
    text: str
    x: float
    y: float            # from the top of the page, in points
    size: float
    font: str
    color: str
    page: int

    @property
    def bold(self) -> bool:
        return bool(re.search(r"bold|black|heavy|semibold", self.font, re.I))

    @property
    def italic(self) -> bool:
        return bool(re.search(r"italic|oblique", self.font, re.I))

    @property
    def width(self) -> float:
        return len(self.text) * self.size * (0.6 if self.text.isupper() else 0.5)


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float
    color: str
    page: int


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02X}" for c in rgb)


def _grey(h: str) -> bool:
    c = [int(h[i:i + 2], 16) for i in (1, 3, 5)]
    return max(c) - min(c) < 24


def _pdf_marks(data: bytes) -> tuple[list[Span], list[Rect], float, list[str]]:
    pdf = PdfReader(io.BytesIO(data))
    spans, rects, notes = [], [], []
    width = float(pdf.pages[0].mediabox.width)
    for n, page in enumerate(pdf.pages[:2]):
        height = float(page.mediabox.height)
        fill, pending = ["#000000"], []
        res = page.get("/Resources") or {}
        xo = res.get("/XObject") if hasattr(res, "get") else None
        if xo and any((xo[k].get_object().get("/Subtype") == "/Image") for k in xo):
            notes.append("images")

        def point(cm, x, y):
            return cm[0] * x + cm[2] * y + cm[4], height - (cm[1] * x + cm[3] * y + cm[5])

        def before(op, args, cm, tm):
            nums = [float(a) for a in args if isinstance(a, (int, float)) or hasattr(a, "as_numeric")]
            if op == b"rg" and len(nums) == 3:
                fill[0] = _hex(nums)
            elif op == b"g" and nums:
                fill[0] = _hex(nums * 3)
            elif op == b"k" and len(nums) == 4:
                c, m, y, k = nums
                fill[0] = _hex([(1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)])
            elif op in (b"sc", b"scn") and len(nums) == 3:
                fill[0] = _hex(nums)
            elif op == b"re" and len(nums) == 4:
                x0, y0 = point(cm, nums[0], nums[1])
                x1, y1 = point(cm, nums[0] + nums[2], nums[1] + nums[3])
                pending.append(Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), fill[0], n))
            elif op in (b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"):     # painted: a rule, a bar, a box
                rects.extend(r for r in pending if r.color != "#FFFFFF"       # on the page, not a layer's own space
                             and r.x >= -1 and r.x + r.w <= width + 1 and 0 <= r.y <= height)
                pending.clear()
            elif op in (b"n", b"W", b"W*", b"S", b"s"):                   # a clip or an outline, not a fill
                pending.clear()

        def text(t, cm, tm, font, size):
            if not t.strip() or not font:
                return
            x, y = point(cm, tm[4], tm[5])
            scale = math.hypot(tm[2], tm[3]) * math.hypot(cm[2], cm[3]) or 1
            spans.append(Span(t.strip(), x, y, round(size * scale, 1), str(font.get("/BaseFont", "")), fill[0], n))
        page.extract_text(visitor_operand_before=before, visitor_text=text)
    if len(pdf.pages) == 1:
        notes.append("one page")
    return spans, rects, width, notes


def _lines(spans: list[Span]) -> list[list[Span]]:
    """Spans grouped into lines (same page, baselines within 2pt), top to bottom, left to right."""
    out: list[list[Span]] = []
    for s in sorted(spans, key=lambda s: (s.page, s.y, s.x)):
        if out and out[-1][0].page == s.page and abs(out[-1][0].y - s.y) < 2:
            out[-1].append(s)
        else:
            out.append([s])
    return [sorted(l, key=lambda s: s.x) for l in out]


def _look_from_marks(spans: list[Span], rects: list[Rect], width: float, notes: list[str]) -> tuple[dict, dict]:
    if not spans:
        raise ValueError("No text to read a look from.")
    t, found, dropped = {}, [], []
    lines = _lines(spans)
    left = min(s.x for s in spans)
    wide = [r for r in rects if r.w > width * 0.5 and r.h < 4]
    right = max([r.x + r.w for r in wide] or [width - left])
    cw = right - left
    centred = lambda l: (l[0].x - left) > cw * 0.12 and abs((l[0].x + (l[-1].x + l[-1].width - l[0].x) / 2) - (left + cw / 2)) < cw * 0.15
    text_of = lambda l: " ".join(s.text for s in l).strip()

    name_line = max(lines[:6], key=lambda l: max(s.size for s in l))
    name = name_line[0]
    heads = [l for l in lines if HEADINGS.match(text_of(l)) and len(text_of(l)) < 40]
    body = [s for l in lines if l is not name_line and l not in heads for s in l if len(s.text) > 3]
    if not body:
        raise ValueError("Not enough text to read a look from.")
    chars = Counter()
    for s in body:
        chars[(s.size, s.font, s.color)] += len(s.text)
    (bs, bfont, ink), _ = chars.most_common(1)[0]
    t["body_size"], t["ink"] = bs, ink
    t["body_font"], fam = match_font(bfont)
    found.append(f"Body text: {fam} → {t['body_font']}" if fam.lower().replace(" ", "") != t["body_font"].lower().replace(" ", "")
                 else f"Body text: {t['body_font']}")
    t["name_font"], nfam = match_font(name.font)
    t["name_size"] = name.size
    t["name_case"] = "upper" if text_of(name_line).isupper() else "as-is"
    t["name_align"] = "center" if centred(name_line) else "left"
    colors = Counter()
    if heads:
        h = heads[0][0]
        t["heading_font"], _ = match_font(h.font)
        t["heading_size"] = median(l[0].size for l in heads)
        t["heading_case"] = "upper" if all(text_of(l).isupper() for l in heads) else "title"
        t["heading_align"] = "center" if sum(map(centred, heads)) > len(heads) / 2 else "left"
        for l in heads:
            if not _grey(l[0].color):
                colors[l[0].color] += 2
        # what is drawn around a heading: a line under it, a bar before it, lines either side
        styles = Counter()
        for l in heads:
            hy, hx, size = l[0].y, l[0].x, l[0].size
            hx2 = l[-1].x + l[-1].width
            near = [r for r in rects if r.page == l[0].page and abs(r.y - hy) < size * 1.6]
            beside = [r for r in near if r.h < 3 and r.w > cw * 0.08 and hy - size * 0.9 < r.y < hy
                      and (r.x + r.w <= hx + 1 or r.x >= hx2 - size)]       # a line at mid-height, left or right
            if beside:
                styles["lines"] += 1
            elif any(r.x < hx and r.w < 6 and r.h > l[0].size * 0.5 for r in near):
                styles["bar"] += 1
            elif any(r.y > hy and r.w > cw * 0.5 and r.h < 3 for r in near):
                styles["rule"] += 1
            else:
                styles["plain"] += 1
        t["heading_style"] = styles.most_common(1)[0][0]
        heading_colored = not _grey(heads[0][0].color)
        t["heading_color"] = "accent" if heading_colored else "ink"
    if not _grey(name.color):
        colors[name.color] += 1
    t["name_color"] = "accent" if not _grey(name.color) else "ink"
    for r in rects:
        if not _grey(r.color) and not (r.w < 6 and r.h < 6):
            colors[r.color] += 1
    i = lines.index(name_line)
    headline = lines[i + 1] if i + 1 < len(lines) else None
    if headline:
        t["headline_italic"] = headline[0].italic
        hc = headline[0].color
        t["headline_color"] = "accent" if not _grey(hc) else "muted" if contrast(hc) < contrast(ink) - 1.5 else "ink"
    first_head_y = heads[0][0].y if heads and heads[0][0].page == 0 else 200
    first_size = heads[0][0].size if heads else 12
    top_rules = [r for r in wide if r.page == 0 and name.y < r.y < first_head_y - first_size]
    if top_rules:
        r = max(top_rules, key=lambda r: r.h)
        t["header_rule"] = "double" if len(top_rules) >= 2 and abs(top_rules[0].y - top_rules[-1].y) < 4 else \
            "thick" if r.h >= 1.2 else "thin"
    else:
        t["header_rule"] = "none"
    head_ys = {(l[0].page, round(l[0].y)) for l in heads}
    loose = [r for r in wide if not any(p == r.page and abs(y - r.y) < 18 for p, y in head_ys) and r.y > first_head_y]
    t["role_rule"] = len(loose) >= 2
    dated = [l for l in lines if DATE.search(text_of(l))]
    if dated:
        far = sum(1 for l in dated if next((s for s in l if DATE.search(s.text)), l[-1]).x > left + cw * 0.5)
        t["dates"] = "right" if far >= len(dated) / 2 else "under"
    starts = [l[0] for l in lines if l[0].text and l[0].text[0] in "•●▪◦–-·"]
    marks = Counter(s.text[0] for s in starts)
    dots = [r for r in rects if r.w < 6 and r.h < 6 and abs(r.w - r.h) < 1.5]      # bullets drawn as dots
    if marks:
        t["bullet"] = {"●": "•", "·": "•"}.get(marks.most_common(1)[0][0], marks.most_common(1)[0][0])
    elif len(dots) >= 3:
        t["bullet"] = "•"
    for s in starts:
        if not _grey(s.color):
            colors[s.color] += 1
    for r in dots:
        if not _grey(r.color):
            colors[r.color] += 1
    t["accent"] = colors.most_common(1)[0][0] if colors else ""
    if t["accent"]:
        found.append(f"Accent colour {t['accent']}")
    t["margin_in"] = left / 72
    t["max_pages"] = 1 if "one page" in notes else 2
    if heads:
        order = [text_of(l).upper() for l in heads]
        exp = next((k for k, h in enumerate(order) if "EXPERIENCE" in h or "EMPLOYMENT" in h or "HISTORY" in h), None)
        skills = next((k for k, h in enumerate(order) if "SKILL" in h or "COMPETENC" in h), None)
        t["skills_first"] = skills is not None and exp is not None and skills < exp
    # what a one-column, real-text page leaves out
    mid = left + cw * 0.38

    # a second column: text starting at the same left edge right of the middle, on several lines, that isn't dates
    edges = Counter(round(sp.x / 4) for l in lines if l is not name_line and not centred(l)
                    for k, sp in enumerate(l) if sp.x > mid and not DATE.search(sp.text) and not sp.text.startswith("|")
                    and (k == 0 or sp.x - (l[k - 1].x + l[k - 1].width * 0.8) > 12))
    if edges and edges.most_common(1)[0][1] >= 4:
        dropped.append("Two columns or a sidebar: ATS systems often read them out of order, so the page stays one column.")
    if "images" in notes:
        dropped.append("Photos, icons or logos: ATS systems can't read them, and many recruiters ask for no photo.")
    if any(r.w > 40 and r.h > 20 and r.color != "#FFFFFF" for r in rects):
        dropped.append("Shaded boxes or panels: kept as plain white space, so the text reads cleanly.")
    if t.get("body_font") and fam and any(m in fam.lower() for m in MONO):
        dropped.append(f"{fam} is a typewriter font; the body uses Helvetica.")
    return clean(t), {"found": found, "not_copied": dropped}


def _docx_look(data: bytes) -> tuple[dict, dict]:
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    z = zipfile.ZipFile(io.BytesIO(data))
    styles = ET.fromstring(z.read("word/styles.xml")) if "word/styles.xml" in z.namelist() else None
    doc = ET.fromstring(z.read("word/document.xml"))
    t, found, dropped = {}, [], []
    val = lambda el, tag, attr="val": (el.find(f"{W}{tag}").get(f"{W}{attr}") if el is not None and el.find(f"{W}{tag}") is not None else None)

    def run_props(rpr):
        if rpr is None:
            return {}
        fonts = rpr.find(f"{W}rFonts")
        return {"font": fonts.get(f"{W}ascii") if fonts is not None else None,
                "size": int(val(rpr, "sz")) / 2 if val(rpr, "sz") else None,
                "color": "#" + val(rpr, "color").upper() if val(rpr, "color") and val(rpr, "color") != "auto" else None,
                "caps": rpr.find(f"{W}caps") is not None, "italic": rpr.find(f"{W}i") is not None}
    by_style = {}
    default = {}
    if styles is not None:
        dd = styles.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")
        default = run_props(dd)
        for st in styles.findall(f"{W}style"):
            by_style[st.get(f"{W}styleId")] = (run_props(st.find(f"{W}rPr")), st.find(f"{W}pPr"))
    paras = []
    for p in doc.iter(f"{W}p"):
        txt = "".join(x.text or "" for x in p.iter(f"{W}t")).strip()
        if not txt:
            continue
        ppr = p.find(f"{W}pPr")
        sid = val(ppr, "pStyle")
        props = {**default, **{k: v for k, v in (by_style.get(sid, ({}, None))[0]).items() if v}}
        r = p.find(f"{W}r/{W}rPr")
        props.update({k: v for k, v in run_props(r).items() if v})
        jc = val(ppr, "jc") or val(by_style.get(sid, ({}, None))[1], "jc")
        style_ppr = by_style.get(sid, ({}, None))[1]
        border = any(x is not None and x.find(f"{W}pBdr/{W}bottom") is not None for x in (ppr, style_ppr))
        tabs = ppr is not None and any(tb.get(f"{W}val") == "right" for tb in ppr.iter(f"{W}tab"))
        paras.append({"text": txt, "jc": jc, "border": border, "right_tab": tabs, **props})
    if not paras:
        raise ValueError("No text to read a look from.")
    name = max(paras[:4], key=lambda p: p.get("size") or 0)
    body = Counter((p.get("size"), p.get("font"), p.get("color")) for p in paras if len(p["text"]) > 40)
    (bs, bfont, ink), _ = body.most_common(1)[0] if body else ((default.get("size"), default.get("font"), None), 0)
    t["body_size"] = bs or 10.5
    t["body_font"], fam = match_font(bfont or "Calibri")
    found.append(f"Body text: {fam} → {t['body_font']}")
    if ink:
        t["ink"] = ink
    t["name_font"], _ = match_font(name.get("font") or bfont or "")
    t["name_size"] = name.get("size") or 20
    t["name_align"] = "center" if name.get("jc") == "center" else "left"
    t["name_case"] = "upper" if name.get("caps") or name["text"].isupper() else "as-is"
    heads = [p for p in paras if HEADINGS.match(p["text"]) and len(p["text"]) < 40]
    colors = Counter()
    if heads:
        h = heads[0]
        t["heading_font"], _ = match_font(h.get("font") or bfont or "")
        t["heading_size"] = h.get("size") or (t["body_size"] + 1)
        t["heading_case"] = "upper" if h.get("caps") or all(p["text"].isupper() for p in heads) else "title"
        t["heading_align"] = "center" if h.get("jc") == "center" else "left"
        t["heading_style"] = "rule" if sum(p["border"] for p in heads) > len(heads) / 2 else "plain"
        for p in heads:
            if p.get("color") and not _grey(p["color"]):
                colors[p["color"]] += 2
        t["heading_color"] = "accent" if colors else "ink"
    if name.get("color") and not _grey(name["color"]):
        colors[name["color"]] += 1
        t["name_color"] = "accent"
    t["accent"] = colors.most_common(1)[0][0] if colors else ""
    if t["accent"]:
        found.append(f"Accent colour {t['accent']}")
    t["dates"] = "right" if any(p["right_tab"] and DATE.search(p["text"]) for p in paras) else "under"
    margin = doc.find(f".//{W}sectPr/{W}pgMar")
    if margin is not None and margin.get(f"{W}left"):
        t["margin_in"] = int(margin.get(f"{W}left")) / 1440
    if doc.find(f".//{W}tbl") is not None:
        dropped.append("Tables: ATS systems often read them out of order, so their text becomes plain lines.")
    if doc.find(f".//{W}drawing") is not None:
        dropped.append("Pictures or icons: ATS systems can't read them.")
    if doc.find(f".//{W}sectPr/{W}cols[@{W}num='2']") is not None:
        dropped.append("Two columns: ATS systems often read them out of order, so the page stays one column.")
    return clean(t), {"found": found, "not_copied": dropped}


def read_look(filename: str, data: bytes) -> tuple[dict, dict]:
    """(settings, {"found": [...], "not_copied": [...]}) from a PDF or Word résumé."""
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".pdf":
            return _look_from_marks(*_pdf_marks(data))
        if ext == ".docx":
            return _docx_look(data)
    except (ValueError, KeyError, IndexError, ZeroDivisionError, zipfile.BadZipFile, ET.ParseError) as e:
        raise ValueError(f"The app couldn't read a look from {filename}: {e}") from None
    raise ValueError("A look can only be read from a PDF or a Word file.")

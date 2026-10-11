"""Formats of your own: a résumé's look read into settings, drawn by the same renderer, saved in the profile."""
import io
import re
import zipfile

import pytest
from pypdf import PdfReader

from conftest import EXAMPLE_REFS
from jobpipe import config, formats, own_formats as of, render

EXAMPLE = (EXAMPLE_REFS / "resume-platform.md").read_text()
VARIANTS = [
    {"body_font": "Palatino", "heading_font": "Avenir Next", "name_font": "Avenir Next", "accent": "#7A1F5C",
     "name_align": "center", "heading_style": "lines", "heading_align": "left", "heading_case": "title",
     "header_rule": "none", "dates": "under", "bullet": "▪", "role_rule": True, "headline_color": "muted"},
    {"body_font": "Times New Roman", "heading_font": "Times New Roman", "name_font": "Times New Roman", "accent": "",
     "heading_style": "rule", "header_rule": "double", "name_case": "upper", "bullet": "–", "skills_first": True},
    {"body_font": "Arial", "heading_font": "Helvetica", "name_font": "Baskerville", "accent": "#0B6E4F",
     "heading_style": "bar", "heading_color": "ink", "name_color": "accent", "headline_italic": True, "margin_in": 0.75},
]


@pytest.fixture(scope="module")
def drawn(tmp_path_factory):
    """Each built-in format and each variant drawn as a PDF: {key: (tokens, pdf bytes, text)}."""
    d, out = tmp_path_factory.mktemp("own"), {}
    with render.Printer() as pr:
        for key, tokens in [*of.BUILTIN.items(), *((f"variant-{k}", v) for k, v in enumerate(VARIANTS))]:
            fmt = formats.get(key) if key in formats.FORMATS else of.to_format("own-x", "X", tokens)
            _, text = pr.pdf(EXAMPLE, d / f"{key}.pdf", fmt=fmt)
            out[key] = (of.clean(tokens), (d / f"{key}.pdf").read_bytes(), text)
    return out


@pytest.mark.parametrize("key", [*of.BUILTIN, *(f"variant-{k}" for k in range(len(VARIANTS)))])
def test_a_look_reads_back_from_its_own_pdf(drawn, key):
    tokens, pdf, _ = drawn[key]
    back, notes = of.read_look("x.pdf", pdf)
    skip = {"max_pages"} | ({"bullet"} if key == "modern" else set())   # Modern's dots are drawn, not typed
    assert {k: back[k] for k in tokens if k not in skip} == {k: tokens[k] for k in tokens if k not in skip}
    assert notes["not_copied"] == []


@pytest.mark.parametrize("k", range(len(VARIANTS)))
def test_own_formats_stay_ats_safe_and_fit(drawn, k):
    tokens, pdf, text = drawn[f"variant-{k}"]
    assert len(PdfReader(io.BytesIO(pdf)).pages) <= 2 and not re.search("[ﬀ-ﬆ]", text)
    for h in re.findall(r"(?m)^## (.+)$", EXAMPLE):
        assert h.strip().upper() in text.upper()
    css = of.css(tokens)
    assert all(float(m) <= 0.06 for m in re.findall(r"letter-spacing:\s*([\d.]+)em", css))
    assert "opacity" not in css and "position" not in css


def test_title_case_headings_are_drawn_as_words():
    fmt = of.to_format("own-x", "X", {"heading_case": "title"})
    doc = render.render(EXAMPLE, fmt=fmt)
    assert ">Professional Experience</h2>" in doc and ">Core Skills</h2>" in doc
    assert render.title_case("EDUCATION & CERTIFICATIONS") == "Education & Certifications"


def test_settings_are_kept_in_range():
    t = of.clean({"body_size": 30, "name_size": "x", "body_font": "Comic Sans", "ink": "#DDDDDD", "accent": "nope",
                  "bullet": "★", "margin_in": 0.1, "max_pages": "1", "evil": "<script>"})
    assert (t["body_size"], t["name_size"], t["body_font"], t["ink"]) == (11.5, 18.0, "Helvetica Neue", "#434343")
    assert (t["accent"], t["bullet"], t["margin_in"], t["max_pages"]) == ("", "-", 0.5, 1) and "evil" not in t
    assert t["name_color"] == t["heading_color"] == "ink"           # no accent: its places use the ink
    pale = of.css({"accent": "#FFE680", "heading_color": "accent"})
    assert re.search(r"color: #434343;\s*text-align: left", pale.split("h2 {")[1])   # a pale accent isn't used for text


def test_fonts_are_matched_to_standard_ones():
    assert of.match_font("/ABCDEF+EBGaramond-Bold") == ("Georgia", "EBGaramond")
    assert of.match_font("CalibriLight") == ("Arial", "CalibriLight")
    assert of.match_font("TimesNewRomanPSMT")[0] == "Times New Roman"
    assert of.match_font("SourceSansPro-Regular")[0] == "Arial" and of.match_font("CourierNewPSMT")[0] == "Helvetica"


def test_what_a_one_column_page_leaves_out_is_listed(tmp_path):
    dot = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    side = "".join(f"<p>Skill number {k} in the sidebar</p>" for k in range(8))
    body = "".join(f"<p>Led a program of work across many teams, number {k}, with measurable results.</p>" for k in range(12))
    page = (f"<html><body style='font: 10pt Georgia; margin: 0'><h1 style='font-size: 22pt'>Jordan Rivera</h1>"
            f"<p>Program Manager</p><p>jordan@example.com</p><img src='{dot}' width=60 height=60>"
            f"<div style='display: flex; gap: 20pt'><div style='width: 60%'><h2>EXPERIENCE</h2>{body}</div>"
            f"<div style='background: #E8EEF5; width: 35%'><h2>SKILLS</h2>{side}</div></div></body></html>")
    with render.Printer() as pr:
        pr.print(page, tmp_path / "two.pdf")
    tokens, notes = of.read_look("two.pdf", (tmp_path / "two.pdf").read_bytes())
    said = " ".join(notes["not_copied"])
    assert "Two columns" in said and "Photos" in said and "Shaded" in said
    assert tokens["body_font"] == "Georgia"


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def test_a_word_files_look_is_read_from_its_styles():
    styles = (f'<w:styles {W}><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Garamond"/><w:sz w:val="21"/>'
              '</w:rPr></w:rPrDefault></w:docDefaults><w:style w:styleId="Heading1"><w:pPr><w:pBdr><w:bottom w:val="single"/>'
              '</w:pBdr></w:pPr><w:rPr><w:caps/><w:color w:val="7A1F5C"/><w:sz w:val="24"/></w:rPr></w:style></w:styles>')
    p = lambda text, style="", jc="", rpr="", tab=False: (
        f'<w:p><w:pPr>{f"<w:pStyle w:val=\'{style}\'/>" if style else ""}{f"<w:jc w:val=\'{jc}\'/>" if jc else ""}'
        f'{"<w:tabs><w:tab w:val=\'right\' w:pos=\'10000\'/></w:tabs>" if tab else ""}</w:pPr>'
        f'<w:r><w:rPr>{rpr}</w:rPr><w:t>{text}</w:t></w:r></w:p>')
    body = (p("Jordan Rivera", jc="center", rpr='<w:sz w:val="48"/><w:color w:val="7A1F5C"/>') + p("Program Manager", jc="center")
            + p("EXPERIENCE", "Heading1") + p("Northwind Cloud | TPM\tMar 2023 – Present", tab=True)
            + p("Moved 31 payment services onto a new ledger platform with no customer-facing downtime at all.")
            + p("EDUCATION", "Heading1") + '<w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>'
            + '<w:sectPr><w:pgMar w:left="1080"/></w:sectPr>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/styles.xml", styles)
        z.writestr("word/document.xml", f"<w:document {W}><w:body>{body}</w:body></w:document>")
    t, notes = of.read_look("cv.docx", buf.getvalue())
    assert (t["body_font"], t["body_size"], t["name_align"], t["name_size"]) == ("Georgia", 10.5, "center", 24.0)
    assert (t["accent"], t["heading_style"], t["heading_case"], t["dates"], t["margin_in"]) == ("#7A1F5C", "rule", "upper", "right", 0.75)
    assert "Body text: Garamond → Georgia" in notes["found"] and "Tables" in " ".join(notes["not_copied"])


def test_own_formats_are_saved_found_and_deleted():
    rec = of.save("Navy serif", of.BUILTIN["executive"], source="cv.pdf", notes={"found": ["x"]})
    assert rec["id"] == "own-navy-serif" and (config.REFERENCES / "formats" / "navy-serif.json").exists()
    f = formats.get("own-navy-serif")
    assert f.name == "Navy serif" and f.font == "Georgia" and "cv.pdf" in f.blurb and formats.known("own-navy-serif")
    with pytest.raises(ValueError, match="already a format called"):
        of.save("navy serif", {})
    with pytest.raises(ValueError, match="already a format called"):
        of.save("Compact", {})
    two = of.save("Second", {"max_pages": 1})
    assert [r["id"] for r in of.records()] == ["own-navy-serif", "own-second"] and formats.get("own-second").max_pages == 1
    assert [f.id for f in formats.all_formats()][-2:] == ["own-navy-serif", "own-second"]
    of.save("Renamed", {**two["tokens"], "accent": "#0B6E4F"}, fid="own-second")
    assert formats.get("own-second").name == "Renamed" and formats.get("own-second").accent == "#0B6E4F"
    assert of.delete("own-second") and formats.get("own-second").id == formats.DEFAULT and not formats.known("own-second")
    assert formats.get("own-../../etc").id == formats.DEFAULT

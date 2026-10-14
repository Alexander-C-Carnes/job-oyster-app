"""Résumé formats: every format draws the same words, within its page limit, in a way an ATS can read."""
import io
import re

import pytest
from pypdf import PdfReader

from conftest import EXAMPLE_REFS
from jobpipe import config, formats, render

EXAMPLE = (EXAMPLE_REFS / "resume-platform.md").read_text()


def words(text: str) -> list[str]:
    return sorted(w.lower() for w in re.findall(r"\w+", text))


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    out = {}
    d = tmp_path_factory.mktemp("formats")
    with render.Printer() as pr:
        for fid in formats.FORMATS:
            out[fid] = pr.pdf(EXAMPLE, d / f"{fid}.pdf", fmt=fid)
    return out


@pytest.mark.parametrize("fid", list(formats.FORMATS))
def test_each_format_fits_its_page_limit(pdfs, fid):
    pages, _ = pdfs[fid]
    assert 1 <= pages <= formats.get(fid).max_pages


@pytest.mark.parametrize("fid", list(formats.FORMATS))
def test_every_heading_comes_out_of_the_pdf_as_text(pdfs, fid):
    # Wide letter-spacing makes a PDF's text read "S U M M A R Y", and a parser misses the section.
    text = pdfs[fid][1].upper()
    for h in re.findall(r"(?m)^## (.+)$", EXAMPLE):
        assert h.strip().upper() in text, f"{fid}: {h}"


@pytest.mark.parametrize("fid", list(formats.FORMATS))
def test_every_format_draws_the_same_words(pdfs, fid):
    # The format changes the look, never a word: an ATS reads the same résumé in each.
    assert words(pdfs[fid][1]) == words(pdfs[formats.DEFAULT][1])


@pytest.mark.parametrize("fid", list(formats.FORMATS))
def test_each_roles_bullets_come_out_under_it_in_order(pdfs, fid):
    # An ATS reading the PDF's text in order must find a role's bullets under it, not after the last section.
    text = re.sub(r"\s+", " ", pdfs[fid][1])
    text = text[text.index("PROFESSIONAL EXPERIENCE"):]
    order = ["Northwind Cloud", "Ran the ledger migration", "Fabrikam Games", "Owned the build-farm",
             "Contoso Health", "Wrote the requirements", "ADDITIONAL EXPERIENCE"]
    at = [text.find(x) for x in order]
    assert -1 not in at and at == sorted(at), (fid, at)


@pytest.mark.parametrize("fid", list(formats.FORMATS))
def test_no_ligatures_in_the_pdf_text(pdfs, fid):
    assert not re.search("[\ufb00-\ufb06]", pdfs[fid][1]), fid


def test_formats_stay_ats_safe():
    for f in formats.FORMATS.values():
        css = f.css + f.tighten
        assert "position: fixed" not in css and "column-count" not in css and "grid" not in css, f.id
        for m in re.finditer(r"letter-spacing:\s*([\d.]+)em", css):
            assert float(m.group(1)) <= 0.06, f.id
        doc = render.render(EXAMPLE, fmt=f.id)
        assert "<table" not in doc and "<img" not in doc


def test_compact_puts_skills_after_the_summary_and_keeps_editor_lines():
    page = render.render(EXAMPLE, editable=True, fmt="compact")
    heads = re.findall(r"<h2[^>]*>(.*?)</h2>", page)
    assert heads[:3] == ["SUMMARY", "CORE SKILLS", "PROFESSIONAL EXPERIENCE"]
    # The page editor maps each span to its markdown line, so reordering mustn't renumber them.
    lines = EXAMPLE.splitlines()
    for key, text in re.findall(r'data-ed="(\d+):\w+"[^>]*>([^<]{12,40})', page):
        assert text.replace("’", "'")[:12] in lines[int(key)].replace("**", ""), key


def test_job_line_parts_are_spans_and_unknown_formats_fall_back():
    doc = render.render(EXAMPLE)
    assert '<span class="co">Northwind Cloud</span><span class="sep"> | </span><span class="role">' in doc
    assert formats.get("gone").id == formats.get(None).id == formats.DEFAULT
    assert "one page" in formats.length_rules(formats.get("compact"))
    assert "two pages" in formats.length_rules(formats.get("signature"))


def test_render_pdf_tightens_once_when_over_the_limit(tmp_path, monkeypatch):
    printed = []
    real = render.Printer.print

    def spy(self, doc, path):
        printed.append(doc)
        real(self, doc, path)
    monkeypatch.setattr(render.Printer, "print", spy)
    long = EXAMPLE + "\n" + "\n".join(f"- Extra line {k} about delivery and platform work" for k in range(40))
    pages, _ = render.render_pdf(long, tmp_path / "x.pdf", fmt="compact")
    assert pages > 1 and len(printed) == 2 and formats.get("compact").tighten in printed[1]
    assert len(PdfReader(str(tmp_path / "x.pdf")).pages) == pages


def test_the_format_setting_is_saved_beside_the_pdf_prefix(tmp_path):
    from jobpipe.web import searches_file
    p = tmp_path / "searches.yaml"
    p.write_text("candidate:\n  name: Jordan Rivera\n  # file names\n  pdf_prefix: JR-Resume\n  resumes:\n"
                 "    Platform resume: resume-platform.md\n")
    searches_file.save_resume_format(p, "compact")
    text = p.read_text()
    assert "# file names" in text and text.index("pdf_prefix") < text.index("resume_format: compact") < text.index("resumes")
    assert searches_file.load_candidate(p)["resume_format"] == "compact"
    searches_file.save_resume_format(p, "modern")
    assert searches_file.load_candidate(p)["resume_format"] == "modern" and p.read_text().count("resume_format") == 1
    q = tmp_path / "empty.yaml"
    q.write_text("defaults: {}\n")
    with pytest.raises(searches_file.SearchError, match="Save your profile first"):
        searches_file.save_resume_format(q, "modern")


def test_check_resume_warns_by_the_formats_page_limit(tmp_path):
    import subprocess
    import sys
    from jobpipe import config
    md = tmp_path / "r.md"
    md.write_text(EXAMPLE + "\n" + " ".join(["word"] * 200))
    run = lambda *a: subprocess.run([sys.executable, str(config.SKILL / "scripts" / "check_resume.py"), str(md),
                                     "--title", "Senior Technical Program Manager, Platform", *a],
                                    capture_output=True, text=True).stdout
    assert "likely over one page" in run("--max-pages", "1") and "likely over" not in run()


def test_a_pdf_is_titled_after_the_role(tmp_path):
    """Browsers save a PDF under its own title, so a résumé's title names the role."""
    assert config.candidate().resume_title('Staff TPM: Platform/Infra', "Acme") == "Staff TPM Platform Infra - Acme"
    assert config.candidate().resume_title("Staff TPM") == "Staff TPM"
    assert config.candidate().resume_title("") == f"{config.candidate().name} Resume"
    assert "<title>X - Role Resume</title>" in render.render(EXAMPLE, title="X - Role Resume")
    render.render_pdf(EXAMPLE, tmp_path / "x.pdf", title="X - Role Resume")
    assert PdfReader(tmp_path / "x.pdf").metadata.title == "X - Role Resume"
    assert PdfReader(io.BytesIO(render.retitled(tmp_path / "x.pdf", "Y"))).metadata.title == "Y"
    assert render.retitled(tmp_path / "missing.pdf", "Y") is None


# ---- when the browser can't start ----------------------------------------------------------------------
def test_a_browser_that_wont_start_is_held_a_minute_then_tried_again(monkeypatch):
    """The Profile asks for five thumbnails at once: after the first ask finds the browser won't start, the
    others fail at once instead of each waiting on it, and a minute later it's tried again."""
    launches = []

    class Chromium:
        def launch(self, **kw):
            launches.append(kw)
            raise RuntimeError("BrowserType.launch: Timeout 120000ms exceeded.\nCall log:\n  - <launching>")

    class Driver:
        chromium = Chromium()
        stopped = 0

        def stop(self):
            Driver.stopped += 1

    class PW:
        def start(self):
            return Driver()

    monkeypatch.setattr(render, "sync_playwright", lambda: PW())
    monkeypatch.setattr(render, "_no_browser", {"at": 0.0, "why": ""})
    now = [1000.0]
    monkeypatch.setattr(render.time, "time", lambda: now[0])

    with pytest.raises(render.BrowserUnavailable) as first:
        with render.Printer():
            pass
    assert first.value.tried and "Timeout 120000ms exceeded" in str(first.value) and "Call log" not in str(first.value)
    assert isinstance(first.value.__cause__, RuntimeError) and Driver.stopped == 1   # the driver isn't left running
    assert launches[0]["timeout"] == render.LAUNCH_TIMEOUT_MS

    now[0] += 30
    with pytest.raises(render.BrowserUnavailable) as again:
        with render.Printer():
            pass
    assert not again.value.tried and len(launches) == 1 and again.value.why == first.value.why

    now[0] += 31
    with pytest.raises(render.BrowserUnavailable) as later:
        with render.Printer():
            pass
    assert later.value.tried and len(launches) == 2


def test_the_couldnt_draw_picture_is_a_letter_page_with_the_reason():
    svg = render.unavailable_svg("The PDF renderer (headless Chromium) couldn't start: spawn <chrome> & co EACCES").decode()
    assert svg.startswith("<svg") and 'viewBox="0 0 816 1056"' in svg
    assert "This preview couldn't be drawn." in svg and "The app's log says more." in svg
    assert "&lt;chrome&gt; &amp; co EACCES" in svg and "<chrome>" not in svg

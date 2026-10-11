"""An uploaded résumé's roles and results that the impact record lacks, offered line by line."""
from datetime import date

from conftest import EXAMPLE_REFS
from jobpipe import evidence, record_import as ri

RECORD = (EXAMPLE_REFS / "impact-record.md").read_text()
RESUME = (EXAMPLE_REFS / "resume-platform.md").read_text()


def by_text(found):
    return {l.text[:30]: l.status for r in found for l in r.lines}


def test_lines_the_record_has_are_detail_and_the_rest_are_new():
    found = ri.suggest(RECORD, RESUME)
    assert [(r.company, r.known) for r in found] == [("Northwind Cloud", True), ("Fabrikam Games", True),
                                                     ("Contoso Health", True), ("Woodgrove Bank", False)]
    s = by_text(found)
    # the record states these, the résumé adds detail
    for line in ("Ran the ledger migration progr", "Built an automated weekly port", "Led PCI DSS audit readiness ac",
                 "Owned the build-farm reliabili", "Wrote the requirements for a p"):
        assert s[line] == "detail", line
    # the record doesn't have these
    for line in ("Defined go/no-go criteria for ", "Coordinated the card-processor", "Rolled out shared build cachin",
                 "Tested online-banking releases"):
        assert s[line] == "new", line
    assert found[1].dates == "Jan 2021–Mar 2023" and found[3].lines[0].why == "Not in your record."


def test_a_figure_the_record_lacks_makes_a_line_new_and_a_stated_line_is_covered():
    md = RESUME.replace("cutting average full-build time from 95 to 40 minutes",
                        "cutting average full-build time from 95 to 38 minutes")
    line = next(l for r in ri.suggest(RECORD, md) for l in r.lines if "build caching" in l.text)
    assert line.status == "new" and line.why == "95 and 38 aren't in your record."
    same = RESUME.replace("- Rolled out shared build caching to 9 studios, cutting average full-build time from 95 to 40 minutes.",
                          "- Owned the build-farm reliability program for 14 game studios; nightly build success rose from 72% to 93%.")
    assert by_text(ri.suggest(RECORD, same))["Owned the build-farm reliabili"] == "covered"


def test_the_models_mark_a_line_covered_when_the_record_entails_it():
    class Models:
        name = "fake"

        def embed(self, texts, instruction=None):
            import numpy as np
            return np.array([[1.0, float(len(t) % 7)] for t in texts])

        def entail(self, pairs):
            return [0.9 if "regression checklist" in claim else 0.0 for _, claim in pairs]
    found = ri.suggest(RECORD + "\n- Tested online-banking releases with a regression checklist.\n", RESUME, Models())
    line = next(l for r in found for l in r.lines if "online-banking" in l.text)
    assert line.status == "covered" and line.entailed == 0.9


def test_accepted_lines_go_to_one_section_by_role_and_are_never_doubled():
    day = date(2026, 10, 11)
    md = ri.apply(RECORD, "Woodgrove Bank", "Quality Assurance Analyst", "Jun 2015–Jul 2017",
                  ["Tested online-banking releases."], "cv.pdf", day)
    assert md.startswith(RECORD.rstrip("\n"))                        # the candidate's own sections are untouched
    tail = md[len(RECORD.rstrip("\n")):]
    assert "\n# From your résumés\n" in tail and "## Woodgrove Bank, Quality Assurance Analyst (Jun 2015–Jul 2017)" in tail
    assert "<!-- added from cv.pdf, Oct 11, 2026 -->\n- Tested online-banking releases." in tail
    md2 = ri.apply(md, "Northwind Cloud", "Senior TPM", "", ["Defined go/no-go criteria."], "cv.pdf", day)
    md3 = ri.apply(md2, "Woodgrove Bank", "Quality Assurance Analyst", "", ["Tested online-banking releases.",
                                                                            "Wrote the regression checklist."], "b.pdf", day)
    sec = md3.split("# From your résumés")[1]
    assert sec.count("## Woodgrove Bank") == 1 and sec.count("Tested online-banking releases.") == 1
    assert sec.index("Wrote the regression checklist.") < sec.index("## Northwind Cloud")   # under its own role
    assert ri.apply(md3, "Woodgrove Bank", "Quality Assurance Analyst", "", ["Tested online-banking releases."], "c", day) == md3
    # the notes aren't evidence; the added lines are
    texts = [p.text for p in evidence.split_markdown("Impact record", md3)]
    assert not any("added from" in t for t in texts) and "Wrote the regression checklist." in texts


def test_roles_reads_experience_sections_only():
    rs = ri.roles(RESUME)
    assert [r.company for r in rs] == ["Northwind Cloud", "Fabrikam Games", "Contoso Health", "Woodgrove Bank"]
    assert len(rs[0].lines) == 7 and rs[3].lines[0].text.startswith("Tested online-banking")

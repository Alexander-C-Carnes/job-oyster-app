import asyncio
import io
import zipfile
from datetime import date

import pytest

from jobpipe import onboarding, record_import
from jobpipe.config import ModelCfg
from jobpipe.llm import AgentResult

# What pypdf reads from a profile's Save to PDF: the sidebar first, then the name, a wrapped headline and the place,
# then Summary, Experience (a company with two roles, two with one) and Education, with page footers.
LINKEDIN_PDF = """Contact
jordan@example.com
www.linkedin.com/in/jordan-rivera (LinkedIn)
Top Skills
Program Management
Kubernetes
Languages
Spanish (Professional Working)
Jordan Rivera
Senior Technical Program Manager | Platform reliability and
developer tooling
Portland, Oregon, United States
Summary
I lead platform programs that ship on time and stay
reliable.
Experience
Northwind Traders
3 years 4 months
Senior Technical Program Manager
January 2024 - Present (1 year 10 months)
Portland, Oregon, United States
Led the migration of 140 services to Kubernetes, cutting deploy time
from 2 hours to 15 minutes.
Ran quarterly planning for 6 teams.
Technical Program Manager
July 2022 - January 2024 (1 year 7 months)
Portland, Oregon
Launched the incident review program.
Page 1 of 2
Contoso
Program Manager
March 2019 - June 2022 (3 years 4 months)
Seattle, Washington
• Owned the vendor onboarding program for 30 vendors.
• Cut onboarding time by 40%.
Fabrikam
Analyst
2016 - 2019 (3 years)
Education
Portland State University
Bachelor of Science - BS, Computer Science · (2012 - 2016)
Page 2 of 2
"""


def export_zip(**files: str) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, text in files.items():
            z.writestr(f"Basic_LinkedInDataExport_10-24-2026/{name.replace('_', ' ')}.csv", text)
    return b.getvalue()


EXPORT = export_zip(
    Positions="Company Name,Title,Description,Location,Started On,Finished On\n"
              'Northwind Cloud,"Senior TPM, Payments","Ran the ledger migration across 31 services.\n'
              'Built a weekly digest for 18\nprograms.","Seattle, Washington",Mar 2023,\n'
              "Litware,Program Manager,,Remote,Jun 2014,Jun 2015\n",
    Profile="First Name,Last Name,Maiden Name,Address,Birth Date,Headline,Summary,Industry,Zip Code,Geo Location\n"
            'Sam,Lee,,,,Senior TPM | Payments,I run platform programs.,Software,,"Austin, Texas, United States"\n',
    Skills="Name\nProgram Management\nPayments\n",
    Education="School Name,Start Date,End Date,Notes,Degree Name,Activities\nUT Austin,2008,2012,,BS Computer Science,\n",
    Email_Addresses="Email Address,Confirmed,Primary,Updated On\nold@example.com,Yes,No,x\nsam@example.com,Yes,Yes,x\n",
    PhoneNumbers="Extension,Number,Type\n,+1 512 555 0100,Mobile\n")


def test_the_data_export_becomes_profile_fields_and_resume_markdown():
    p = onboarding.linkedin_zip(EXPORT)
    assert p.fields() == {"name": "Sam Lee", "city": "Austin, Texas", "email": "sam@example.com", "phone": "+1 512 555 0100"}
    assert [(x.company, x.title, x.dates, x.location) for x in p.positions] == [
        ("Northwind Cloud", "Senior TPM, Payments", "Mar 2023–Present", "Seattle, Washington"),
        ("Litware", "Program Manager", "Jun 2014–Jun 2015", "Remote")]
    # a description's wrapped line joins the one before; each sentence-ended line is its own item
    assert p.positions[0].lines == ["Ran the ledger migration across 31 services.", "Built a weekly digest for 18 programs."]
    md = onboarding.markdown(p)
    roles = record_import.roles(md)          # the review reads it like any résumé
    assert [(r.company, r.title, r.dates) for r in roles] == [("Northwind Cloud", "Senior TPM, Payments", "Mar 2023–Present")]
    assert "Hard Skills: Program Management | Payments" in md and "BS Computer Science, UT Austin (2008–2012)" in md
    assert "31 services" in onboarding.zip_text(p)


def test_an_export_that_isnt_linkedins_is_refused_in_words():
    with pytest.raises(onboarding.ExportError, match="isn't a ZIP"):
        onboarding.linkedin_zip(b"not a zip")
    with pytest.raises(onboarding.ExportError, match="isn't a LinkedIn data export"):
        onboarding.linkedin_zip(export_zip(Connections="First Name,Last Name\nA,B\n"))
    # some export files open with notes before their header
    p = onboarding.linkedin_zip(export_zip(Positions="Notes:\n\"About these positions\"\n\nCompany Name,Title,Description,"
                                                     "Location,Started On,Finished On\nAcme,PM,,,2020-03,\n"))
    assert (p.positions[0].company, p.positions[0].dates) == ("Acme", "Mar 2020–Present")


def test_a_save_to_pdf_profile_is_read_without_ai():
    assert onboarding.looks_like_linkedin(LINKEDIN_PDF)
    assert not onboarding.looks_like_linkedin("# Jordan Rivera\n\n## Experience\n\nAcme | PM")
    p = onboarding.linkedin_pdf(LINKEDIN_PDF)
    assert (p.name, p.city, p.email, p.linkedin) == ("Jordan Rivera", "Portland, Oregon", "jordan@example.com",
                                                      "linkedin.com/in/jordan-rivera")
    assert p.headline == "Senior Technical Program Manager | Platform reliability and developer tooling"
    assert p.skills == ["Program Management", "Kubernetes"]
    assert p.summary == "I lead platform programs that ship on time and stay reliable."
    got = [(x.company, x.title, x.dates, x.location, x.lines) for x in p.positions]
    assert got == [
        ("Northwind Traders", "Senior Technical Program Manager", "Jan 2024–Present", "Portland, Oregon, United States",
         ["Led the migration of 140 services to Kubernetes, cutting deploy time from 2 hours to 15 minutes.",
          "Ran quarterly planning for 6 teams."]),
        ("Northwind Traders", "Technical Program Manager", "Jul 2022–Jan 2024", "Portland, Oregon",
         ["Launched the incident review program."]),
        ("Contoso", "Program Manager", "Mar 2019–Jun 2022", "Seattle, Washington",
         ["Owned the vendor onboarding program for 30 vendors.", "Cut onboarding time by 40%."]),
        ("Fabrikam", "Analyst", "2016–2019", "", [])]
    assert p.education == ["Bachelor of Science - BS, Computer Science (2012 - 2016), Portland State University"]


def test_the_name_falls_back_to_the_line_over_the_headline():
    p = onboarding.linkedin_pdf(LINKEDIN_PDF.replace("jordan-rivera", "jr-pm-77"))
    assert p.name == "Jordan Rivera" and p.headline.startswith("Senior Technical")


def test_interview_lines_go_to_their_own_section():
    rec = "# Impact record\n\n## Northwind\n\nLed 22 teams.\n"
    new = record_import.apply(rec, "Acme", "PM", "2020–2022", ["Cut costs by 10%."], "the setup interview",
                              date(2026, 10, 24), section=onboarding.INTERVIEW_SECTION, intro=onboarding.INTERVIEW_INTRO)
    assert "# From your interviews" in new and "## Acme, PM (2020–2022)" in new and "- Cut costs by 10%." in new
    assert "From your résumés" not in new
    again = record_import.apply(new, "Acme", "PM", "", ["Cut costs by 10%.", "Hired 3 people."], "x", date(2026, 10, 25),
                                section=onboarding.INTERVIEW_SECTION)
    assert again.count("# From your interviews") == 1 and again.count("Cut costs by 10%.") == 1 and "- Hired 3 people." in again


class Canned:
    def __init__(self, files):
        self.files, self.calls = files, []

    async def run(self, call):
        self.calls.append(call)
        return AgentResult(files=self.files, summary="")


def test_the_interview_asks_until_done_and_flags_figures_it_wasnt_given():
    role = {"company": "Acme", "title": "PM", "notes": "Shipped the portal to 120 partners."}
    m = ModelCfg("claude-sonnet-5-5", "medium")
    r = Canned({"reply.md": '"How big was the team?"'})
    turns = []
    q = asyncio.run(onboarding.ask(r, m, role, turns, "Sam", onboarding.ASK_BRIEF))
    assert q == "How big was the team?" and "(no questions yet)" in r.calls[0].tail
    assert "120 partners" in r.calls[0].documents["role.md"] and r.calls[0].candidate   # sees the record, to skip what it has
    turns = [onboarding.Turn(q, "Eight engineers; deploys went from 2 hours to 15 minutes.")]
    assert asyncio.run(onboarding.ask(Canned({"reply.md": "DONE"}), m, role, turns, "Sam", "b")) is None
    full = [onboarding.Turn("q", "a")] * onboarding.QUESTIONS
    assert asyncio.run(onboarding.ask(r, m, role, full, "Sam", "b")) is None and len(r.calls) == 1   # no call past the limit
    d = Canned({"lines.md": "Here you go:\n- Led 8 engineers.\n- Cut deploys from 2 hours to 15 minutes.\n"
                            "- Grew partners to 120.\n- Saved $400K a year.\n"})
    lines = asyncio.run(onboarding.draft(d, m, role, turns, "Sam", "b"))
    assert [(l["text"], l["ok"]) for l in lines] == [
        ("Led 8 engineers.", False), ("Cut deploys from 2 hours to 15 minutes.", True),
        ("Grew partners to 120.", True), ("Saved $400K a year.", False)]
    assert "400" in lines[3]["why"]
    assert asyncio.run(onboarding.draft(d, m, role, [onboarding.Turn("q")], "Sam", "b")) == []   # nothing answered

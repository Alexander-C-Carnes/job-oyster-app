import json
from pathlib import Path

import httpx
import pytest

from jobpipe.jd import build_jd, detect_ats, posting_key, read_posting
from jobpipe.llm import candidate_materials, parse_output, validate
from conftest import make_job


def test_parse_output_and_validate():
    text = ('Here you go.\n<file name="ratings.json">\n{"a": 1}\n</file>\n'
            '<file name="04-match.md">\n# Match\n</file>\nSummary: done.')
    res = parse_output(text)
    assert res.files == {"ratings.json": '{"a": 1}\n', "04-match.md": "# Match\n"}
    assert "Summary: done." in res.summary
    assert validate(res, ["ratings.json", "04-match.md"]) == []
    assert validate(res, ["keywords.json"]) == ["missing file keywords.json"]
    bad = parse_output('<file name="x.json">\n{nope}\n</file>')
    assert "not valid JSON" in validate(bad, ["x.json"])[0]


def test_candidate_materials_include_uploaded_resumes():
    text = candidate_materials()
    assert "[Impact record]" in text and "Jordan Rivera (example)" in text
    assert "[Platform resume]" in text
    assert "Honesty guardrails" in text


def test_build_jd():
    jd, usable = build_jd(make_job(), fetch=False)
    lines = jd.splitlines()
    assert lines[0] == "ATS: Greenhouse"
    assert lines[1] == "Source: https://boards.greenhouse.io/acme/jobs/j1"
    assert usable and "Minimum qualifications" in jd
    jd, usable = build_jd(make_job(description="Short stub."), fetch=False)
    assert not usable


def test_detect_ats():
    assert detect_ats("https://jobs.lever.co/x/1") == "Lever"
    assert detect_ats("https://acme.wd5.myworkdayjobs.com/x") == "Workday"
    assert detect_ats("https://example.com/careers/1") == "not identified"


BODY = "<p>Own the release train across twelve teams and report on delivery risk.</p>" * 12


def _pages(routes):
    def handle(request: httpx.Request) -> httpx.Response:
        for prefix, resp in routes.items():
            if str(request.url).startswith(prefix):
                return resp
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_read_posting_from_a_careers_page():
    ld = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Staff TPM", "description": BODY,
          "hiringOrganization": {"@type": "Organization", "name": "Acme"}, "jobLocationType": "TELECOMMUTE",
          "jobLocation": {"@type": "Place", "address": {"addressLocality": "Austin", "addressRegion": "TX"}},
          "baseSalary": {"currency": "USD", "value": {"minValue": 200000, "maxValue": 250000, "unitText": "YEAR"}}}
    page = f'<html><head><title>Careers</title><script type="application/ld+json">{json.dumps(ld)}</script></head></html>'
    d = read_posting("https://acme.example/careers/1", _pages({"https://acme.example/": httpx.Response(200, text=page)}))
    assert (d["job_title"], d["company"], d["location"], d["remote"]) == ("Staff TPM", "Acme", "Austin, TX", True)
    assert d["min_annual_salary_usd"] == 200000 and "release train" in d["description"]
    # a page with no job data: the title still comes from the page's own title, the text stays empty
    shell = '<html><head><meta property="og:title" content="Job Application for Staff TPM at Acme"></head>' \
            '<body>' + "<div>app shell</div>" * 9000 + "</body></html>"
    d = read_posting("https://acme.example/careers/2", _pages({"https://acme.example/": httpx.Response(200, text=shell)}))
    assert (d["job_title"], d["company"], d["description"]) == ("Staff TPM", "Acme", "")


def test_read_posting_from_a_site_that_wants_a_browser():
    # HiringCafe puts every page behind Cloudflare's "verify you are human" check: the app doesn't ask
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"requested {request.url}")
    d = read_posting("https://hiring.cafe/job/9naqj25yblgrkhki", httpx.Client(transport=httpx.MockTransport(refuse)))
    assert (d["browser_check"], d["description"]) == ("HiringCafe", "")
    # another site answering with the challenge is named, and "Just a moment..." isn't taken for the job's title
    challenge = httpx.Response(403, headers={"cf-mitigated": "challenge"},
                               text="<html><head><title>Just a moment...</title></head></html>")
    d = read_posting("https://www.acme.example/careers/7", _pages({"https://www.acme.example/": challenge}))
    assert (d["browser_check"], d["description"]) == ("acme.example", "") and "job_title" not in d


def test_read_posting_from_a_company_page_on_an_ashby_board():
    # melodia.example/careers/<id>: no JobPosting data on the company's page (its text is in a Next.js blob
    # and its title is "Melodia | Staff TPM"), but it links to the job on its Ashby board, which has all of it
    uid = "0f1e2d3c-4b5a-4697-8877-665544332211"
    ld = {"@type": "JobPosting", "title": "Staff TPM", "description": BODY,
          "hiringOrganization": {"@type": "Organization", "name": "Melodia"},
          "jobLocation": {"@type": "Place", "address": {"addressLocality": "Denver", "addressRegion": "CO"}}}
    next_data = json.dumps({"props": {"pageProps": {"job": {"id": uid, "descriptionHtml": BODY}}}})
    page = (f'<html><head><title>Melodia | Staff TPM</title></head><a href="https://jobs.ashbyhq.com/melodia/{uid}/application">'
            f'Apply</a><script id="__NEXT_DATA__" type="application/json">{next_data}</script></html>')
    routes = {f"https://melodia.example/careers/{uid}": httpx.Response(200, text=page)}
    d = read_posting(f"https://melodia.example/careers/{uid}", _pages({
        **routes, f"https://jobs.ashbyhq.com/melodia/{uid}": httpx.Response(200, text=f'<script type="application/ld+json">{json.dumps(ld)}</script>')}))
    assert (d["job_title"], d["company"], d["location"]) == ("Staff TPM", "Melodia", "Denver, CO")
    # without the board page, the employer is the half of the page title that is the site's own name
    d = read_posting(f"https://melodia.example/careers/{uid}", _pages(routes))
    assert (d["job_title"], d["company"]) == ("Staff TPM", "Melodia") and "release train" in d["description"]


def test_read_posting_from_comeet():
    # comeet.com/jobs/...: the page is an Angular template, the posting is in its POSITION_DATA script
    page = (Path(__file__).parent / "fixtures" / "comeet" / "position.html").read_text()
    url = "https://www.comeet.com/jobs/cyera/17.008/careers/8E.056"
    d = read_posting(url, _pages({url: httpx.Response(200, text=page)}))
    assert (d["job_title"], d["company"], d["location"], d["remote"]) == (
        "Senior Customer Success Engineer - Southeast", "Cyera", "Southeast (US)", True)
    assert d["description"].startswith("WHO WE ARE") and "THE OPPORTUNITY" in d["description"]
    # without the data, the template's placeholders aren't taken for the posting
    d = read_posting(url, _pages({url: httpx.Response(200, text=page.replace("POSITION_DATA", "OTHER"))}))
    assert d["description"] == ""


def test_read_posting_from_greenhouse_and_linkedin():
    gh = {"title": "Staff TPM", "company_name": "Acme", "location": {"name": "Remote, US"}, "content": BODY}
    d = read_posting("https://job-boards.greenhouse.io/acme/jobs/123?gh_src=x", _pages(
        {"https://boards-api.greenhouse.io/v1/boards/acme/jobs/123": httpx.Response(200, json=gh)}))
    assert (d["job_title"], d["company"], d["location"]) == ("Staff TPM", "Acme", "Remote, US")
    li = ('<h2 class="top-card-layout__title font-sans">Staff TPM</h2>'
          '<a class="topcard__org-name-link topcard__flavor--black-link" href="#">\n Acme\n </a>'
          '<span class="topcard__flavor topcard__flavor--bullet">\n Austin, TX\n </span>'
          f'<div class="show-more-less-html__markup relative">{BODY}</div>')
    # a link copied from LinkedIn search results is kept as the job's own page
    d = read_posting("https://www.linkedin.com/jobs/search/?currentJobId=4000000001&keywords=tpm", _pages(
        {"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4000000001": httpx.Response(200, text=li)}))
    assert d["url"] == "https://www.linkedin.com/jobs/view/4000000001"
    assert (d["job_title"], d["company"], d["location"]) == ("Staff TPM", "Acme", "Austin, TX")
    assert d["description"].startswith("Own the release train")


def test_read_posting_from_icims():
    # the link's page is the employer's site (menus, no posting); the posting is the iframe's page
    ld = {"@type": "JobPosting", "title": "Principal TPM - Platform & Tools", "description": BODY,
          "hiringOrganization": {"@type": "Organization", "name": "Acme"}, "jobLocationType": "TELECOMMUTE",
          "jobLocation": [{"@type": "Place", "address": {"addressLocality": "Austin", "addressRegion": "TX"}}]}
    frame = f'<html><title>X in | Careers at Acme - Americas</title><script type="application/ld+json">{json.dumps(ld)}</script></html>'
    chrome = "<html><title>Careers at Acme - Americas</title>" + "<li>Products</li>" * 2000
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "careers-americas.icims.com" and request.url.params.get("in_iframe") == "1":
            return httpx.Response(200, text=frame)
        if request.url.host == "careers.acme.example":
            return httpx.Response(200, text=chrome + '<iframe src="https://careers-americas.icims.com/jobs/10001/'
                                  'principal-tpm/job?iis=Acme&amp;in_iframe=1"></iframe>')
        return httpx.Response(200, text=chrome)

    c = httpx.Client(transport=httpx.MockTransport(handler))
    d = read_posting("https://careers-americas.icims.com/jobs/10001/principal-tpm---platform-&-tools/job"
                     "?iis=LinkedIn&mobile=false&width=1200&height=800&jan1offset=0", c)
    assert d["url"] == "https://careers-americas.icims.com/jobs/10001/principal-tpm---platform-&-tools/job"
    assert (d["job_title"], d["company"], d["location"], d["remote"]) == \
        ("Principal TPM - Platform & Tools", "Acme", "Austin, TX", True)
    assert d["description"].startswith("Own the release train") and "Products" not in d["description"]
    assert "width=" not in seen[0]
    # a company's own careers page that embeds its iCIMS portal
    d = read_posting("https://careers.acme.example/job/10001", c)
    assert d["company"] == "Acme" and d["description"].startswith("Own the release train")


def test_posting_key():
    assert posting_key("https://acme.com/careers/?gh_jid=1&utm_source=x") == posting_key("https://ACME.com/careers?gh_jid=1")
    assert posting_key("https://acme.com/careers?gh_jid=1") != posting_key("https://acme.com/careers?gh_jid=2")
    assert posting_key("https://www.linkedin.com/jobs/view/staff-tpm-at-acme-4000000001?trk=a") == \
        posting_key("https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4000000001")


def test_env_loader_skips_blank_values(tmp_path, monkeypatch):
    import os
    from jobpipe import cli, config
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=\nJOBSPIPE_API_KEY=jp_x\n# NOTION_TOKEN=n\n")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    for k in ("ANTHROPIC_API_KEY", "JOBSPIPE_API_KEY", "NOTION_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    cli._load_env()
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert os.environ["JOBSPIPE_API_KEY"] == "jp_x"
    assert "NOTION_TOKEN" not in os.environ


def test_signal_score_is_computed_from_the_rated_requirements():
    import json
    from jobpipe.triage import Triage, coverage, score_from_rows

    def rows(high, med=(), low=()):
        return ([{"req": "h", "weight": "High", "rating": r} for r in high]
                + [{"req": "m", "weight": "Med", "rating": r} for r in med]
                + [{"req": "l", "weight": "Low", "rating": r} for r in low])

    # High 3, Med 2, Low 1; strong 1, partial 0.5, missing 0 (the scorecard's weights)
    assert coverage(rows(["strong", "partial"], ["missing"], ["strong"])) == round(100 * (3 + 1.5 + 0 + 1) / 9, 1)
    assert coverage([]) is None and coverage([{"weight": "?", "rating": "strong"}]) is None
    # the rubric's bands: about 60% is 6, about 70% is 7, 80-90% is 8, above that 9
    assert [score_from_rows(p, 12, 3) for p in (56, 64.9, 65, 74.9, 75, 90.9, 91, 100)] == [6, 6, 7, 7, 8, 8, 9, 10]
    # under 55% the model's own read decides, never above 5; too few rows and it decides alone
    assert score_from_rows(51, 12, 3) == 3 and score_from_rows(51, 12, 7) == 5 and score_from_rows(50, 1, 4) == 4
    assert score_from_rows(88, 12, 8, cap=5) == 5

    base = {"fit_score": 7, "recommended_base": "Platform resume", "one_line": "x"}
    t = Triage.from_json(json.dumps({**base, "requirements": rows(["strong"] * 6, ["partial"] * 2)}))
    assert (t.fit_score, t.coverage_pct, t.model_score) == (8, round(100 * 20 / 22, 1), 7)   # 90.9%: just under the 9 band
    assert Triage.from_json(json.dumps(base)).fit_score == 7          # older answers without rows still load
    capped = Triage.from_json(json.dumps({**base, "score_cap": 4, "requirements": rows(["strong"] * 8)}))
    assert capped.fit_score == 4 and capped.score_cap == 4
    with pytest.raises(ValueError, match="JSON object"):                # an array is a bad answer, not a crash
        Triage.from_json(json.dumps([base]))


def test_posting_text_is_read_from_structured_data_and_ats_apis():
    import json
    import httpx
    from jobpipe.jd import fetch_posting_text

    body = "<p>Lead cross-functional programs.</p><ul><li>Own delivery</li></ul>" * 20
    ld = json.dumps({"@type": "JobPosting", "title": "TPM", "description": body})
    shell = "<html><body><div id=root></div><script>var app = 1</script></body></html>"
    next_data = json.dumps({"props": {"pageProps": {"jobs": [
        {"id": "11111111-1111-1111-1111-111111111111", "descriptionHtml": "<p>Other job.</p>" * 80},
        {"id": "22222222-2222-2222-2222-222222222222", "descriptionHtml": "<p>The right job.</p>" * 60}]}}})
    pages = {
        "https://jobs.ashbyhq.com/acme/abc": f'<html><script type="application/ld+json">{ld}</script>{shell}',
        "https://acme.com/careers/22222222-2222-2222-2222-222222222222":
            f'<html><script id="__NEXT_DATA__" type="application/json">{next_data}</script>{shell}',
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123": json.dumps({"content": "&lt;p&gt;From the Greenhouse API.&lt;/p&gt;" * 40}),
        "https://www.acme.com/jobs": 'nav <a href="https://jobs.ashbyhq.com/acmelabs">board</a>' + "menu " * 300,
        "https://jobs.ashbyhq.com/acmelabs/33333333-3333-3333-3333-333333333333":
            f'<script type="application/ld+json">{ld}</script>',
        "https://spa.example.com/job/9": "<html><body>" + "x " * 30000 + "</body></html>",
        "https://plain.example.com/job/9": "<html><body><p>" + "Plain server-rendered posting. " * 40 + "</p></body></html>",
    }

    def handler(request):
        url = str(request.url).split("?")[0]
        if url not in pages:
            return httpx.Response(404, text="gone")
        kind = "application/json" if url.startswith("https://boards-api") else "text/html"
        return httpx.Response(200, text=pages[url], headers={"content-type": kind})

    c = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    assert "Lead cross-functional programs." in fetch_posting_text("https://jobs.ashbyhq.com/acme/abc", c)      # JSON-LD
    assert fetch_posting_text("https://acme.com/careers/22222222-2222-2222-2222-222222222222", c).startswith("The right job.")
    assert "From the Greenhouse API." in fetch_posting_text("https://job-boards.greenhouse.io/acme/jobs/123", c)
    assert "From the Greenhouse API." in fetch_posting_text("https://www.acme.com/careers/job?gh_jid=123", c)   # embedded board
    assert "Own delivery" in fetch_posting_text("https://www.acme.com/jobs?ashby_jid=33333333-3333-3333-3333-333333333333", c)
    assert fetch_posting_text("https://spa.example.com/job/9", c) == ""        # an app shell's blob is not a posting
    assert "Plain server-rendered posting." in fetch_posting_text("https://plain.example.com/job/9", c)
    assert fetch_posting_text("https://acme.com/closed", c) == "" and fetch_posting_text("", c) == ""


def test_keywords_normalized_from_the_shapes_the_matcher_writes():
    from jobpipe.tailor import check_keywords, normalize_keywords
    flat = {"Technical program management": "technical program manag", "CI/CD": r"ci\s*/\s*cd", "SRE": r"\bsre\b",
            "GenAI": "gen ?ai", "Roadmaps": "roadmap"}
    assert normalize_keywords(flat) == (flat, [])
    assert normalize_keywords({"keywords": flat}) == (flat, [])                       # wrapped in one key
    listed = normalize_keywords({"keywords": [{"keyword": k, "regex": v} for k, v in flat.items()]})
    assert listed == (flat, [])
    kw, literal = normalize_keywords({**flat, "Go (lang)": "go (lang", "Kubernetes": ["kubernetes", "k8s"]})
    assert kw["Go (lang)"] == r"Go\ \(lang\)" and literal == ["Go (lang)"]         # invalid regex: matched literally
    assert kw["Kubernetes"] == "(?:kubernetes)|(?:k8s)"
    assert normalize_keywords(["Python", "Go"])[0] == {"Python": "Python", "Go": "Go"}
    assert check_keywords(json.dumps({"keywords": flat})) == []
    assert "flat JSON object" in check_keywords(json.dumps({}))[0]
    assert "not valid JSON" in check_keywords("{")[0]

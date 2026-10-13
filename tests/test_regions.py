import pytest

from jobpipe import regions
from jobpipe.config import Search


def test_regions_expand_to_country_codes():
    assert len(regions.codes("EU")) == 27 and len(regions.codes("EEA")) == 30 and len(regions.codes("Schengen")) == 29
    eu_schengen = regions.codes(["EU", "Schengen"])
    assert len(eu_schengen) == 31 and {"IE", "CY", "CH", "NO", "IS", "LI"} <= set(eu_schengen) and "GB" not in eu_schengen
    assert "GB" in regions.codes("Europe") and {"AE", "IL", "ZA", "NG"} <= set(regions.codes("EMEA"))
    assert regions.codes("us") == ["US"] and regions.codes("EU, UK") == sorted(set(regions.EU) | {"GB"})
    assert regions.codes(None) is None and regions.codes("Any") is None and regions.codes("") is None


def test_country_is_kept_in_its_own_spelling_and_checked():
    assert regions.canonical("eu+schengen") == ["EU", "Schengen"]
    assert regions.canonical(["de", "nl", "de"]) == ["DE", "NL"]
    assert regions.canonical("any") == "Any" and regions.canonical("") is None
    assert regions.describe(["EU", "Schengen"]) == "EU + Schengen (31 countries)" and regions.describe("US") == "US"
    with pytest.raises(ValueError):
        regions.canonical("Narnia")
    with pytest.raises(ValueError):
        regions.canonical(["Any", "DE"])


def test_a_region_search_asks_jobspipe_for_its_countries():
    s = Search(id="em-eu", name="EM EU", titles=["Engineering Manager"], remote=True, country=["EU", "Schengen"])
    assert s.request_body()["job_country_code_or"] == regions.codes(["EU", "Schengen"])
    assert "job_country_code_or" not in Search(id="any", name="any", titles=["x"], country="Any").request_body()


def test_a_zero_pay_floor_sends_none():
    # Few European postings state US-level pay: a search can drop the default floor with 0
    from jobpipe import config
    s = config._search({"id": "em-eu", "titles": ["x"], "min_salary_usd": 0}, {"min_salary_usd": 300000, "country": "US"})
    assert "min_salary_usd" not in s.request_body() and s.request_body()["job_country_code_or"] == ["US"]


@pytest.mark.parametrize("text, us, eu", [
    ("Remote (EU)", True, False),
    ("Remote, in the EU", True, False),          # "in" after a comma isn't Indiana
    ("Remote (CET ± 2h)", True, False),
    ("Berlin, Germany", True, False),
    ("Engineering Manager - EMEA", True, False),
    ("Zurich or London", True, False),           # either will do: Switzerland is Schengen
    ("London", True, True),                      # the UK is neither EU nor Schengen
    ("Zurich", True, False),
    ("Austin, TX (Remote)", False, True),
    ("U.S. Remote", False, True),
    ("London or New York, NY", False, True),
    ("Latin America", True, True),
    ("Dubai", True, True),
    ("Remote", False, False),                    # no place named: the benefit of the doubt
    ("", False, False),
])
def test_roles_pinned_elsewhere(text, us, eu):
    assert regions.elsewhere(text, "US") is us
    assert regions.elsewhere(text, ["EU", "Schengen"]) is eu
    assert regions.elsewhere(text, "Any") is False


@pytest.mark.parametrize("codes, location, title, want", [
    ([], "Austin, TX", "", ["US"]),
    (["US"], "United States, Remote", "", ["US"]),
    (["GB"], "London", "", ["UK"]),                              # the UK isn't counted as EMEA too
    (["GB"], "London, UK", "", ["UK"]),
    ([], "Remote (EU)", "", ["EMEA"]),
    ([], "Dubai, UAE", "", ["EMEA"]),
    ([], "London or New York", "", ["US", "UK"]),
    ([], "London or Berlin", "", ["UK", "EMEA"]),
    ([], "Cambridge, MA, USA", "", ["US"]),                     # the American Cambridge, not the British one
    ([], "Berlin, MD", "", ["US"]),
    ([], "Portland, OR", "", ["US"]),                           # OR is the state, not "or"
    ([], "Toronto, ON, Canada", "", ["Other"]),
    ([], "Remote, anywhere", "", ["Anywhere"]),
    ([], "Remote", "Engineering Manager, EMEA", ["UK", "EMEA"]),  # the title can say where
    ([], "Remote", "Global Head of Platform", []),               # "global" in a title isn't a location
    ([], "", "", []),
])
def test_where_a_role_is_for_the_region_filter(codes, location, title, want):
    assert regions.areas(codes, location, title) == want


@pytest.mark.parametrize("codes, location, want", [
    ([], "London, UK", "UK"),
    (["GB"], "Birmingham, England, United Kingdom", "UK"),
    (["DE"], "Berlin, Berlin, Germany", "Germany"),
    ([], "Austin, TX (Remote)", "TX"),
    (["US"], "New York, NY", "NY"),
    ([], "Irving, TX 75039", "TX"),
    ([], "US-CA-Menlo Park", "CA"),
    ([], "Utah, USA", "UT"),
    ([], "Berlin, MD, USA", "MD"),                                # the American Berlin
    ([], "Toronto, ON, Canada", "Canada"),                        # ON isn't a state
    ([], "San Francisco Bay Area", "CA"),
    ([], "New York City; San Francisco", "NY, CA"),
    ([], "San Francisco, CA, USA; New York, NY, USA; Seattle, WA, USA; Austin, TX", "CA, NY +2"),
    ([], "United States; New York, NY, USA", "NY"),               # a state says more than the country
    (["US"], "United States", "US"),
    ([], "Remote - Europe", "Europe"),
    ([], "Novi Sad, South Bačka, Serbia, EMEA", "Serbia"),
    (["US"], "Remote", "US"),                                     # the posting's country when the location names none
    ([], "Remote, anywhere", "Anywhere"),
    ([], "Remote", ""),
    ([], "", ""),
])
def test_where_a_role_is_in_a_word(codes, location, want):
    assert regions.place(codes, location) == want

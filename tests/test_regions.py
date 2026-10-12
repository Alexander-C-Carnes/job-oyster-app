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

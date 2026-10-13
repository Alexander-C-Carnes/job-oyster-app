"""Where a search looks: country codes and named regions (EU, Schengen, EEA, Europe, EMEA).

A search's `country` is one country code ("US", "DE"), a region name, or a list of either ("[EU, Schengen]");
`codes()` turns it into the ISO 3166 country codes JobsPipe's job_country_code_or takes. "Any" searches everywhere.

`elsewhere()` reads a startup role's title and location for the places it names ("Remote (EU)", "London",
"Austin, TX"), so a search keeps only roles somewhere it looks.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional, Union

EU = ("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE").split()
EEA = EU + ["IS", "LI", "NO"]
# Every EU country but Ireland and Cyprus (Bulgaria and Romania joined in full in January 2025), plus the four EFTA states.
SCHENGEN = [c for c in EU if c not in ("IE", "CY")] + ["IS", "LI", "NO", "CH"]
EUROPE = sorted(set(EEA) | set(SCHENGEN) | {"GB", "AD", "MC", "SM", "VA", "AL", "BA", "ME", "MK", "RS", "MD", "UA"})
MIDDLE_EAST = ["AE", "SA", "QA", "BH", "KW", "OM", "IL", "JO", "LB", "TR", "IQ"]
AFRICA = ("DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW KE LS LR LY MG MW ML MR MU "
          "MA MZ NA NE NG RW ST SN SC SL SO ZA SS SD TZ TG TN UG ZM ZW").split()
EMEA = sorted(set(EUROPE) | set(MIDDLE_EAST) | set(AFRICA))

# Named regions a search can use, in the order the app offers them: id -> (label, codes).
REGIONS: dict[str, tuple[str, list[str]]] = {
    "EU": ("European Union", EU),
    "Schengen": ("Schengen area", SCHENGEN),
    "EEA": ("European Economic Area", EEA),
    "Europe": ("Europe, with the UK", EUROPE),
    "EMEA": ("Europe, the Middle East and Africa", EMEA),
}
ANY = "Any"                     # search every country (overrides a default country)
ALIASES = {"UK": "GB"}          # ISO says GB; people write UK

_TOKEN = re.compile(r"[^,+;\s]+")


def _tokens(value: Union[str, Iterable[str], None]) -> list[str]:
    if value is None:
        return []
    items = [value] if isinstance(value, str) else list(value)
    return [t for item in items for t in _TOKEN.findall(str(item))]


def canonical(value: Union[str, Iterable[str], None]) -> Union[str, list[str], None]:
    """A search's `country` as searches.yaml keeps it: region names in their own spelling, codes in capitals,
    one item as a plain string. Raises ValueError for anything that isn't a region or a two-letter code."""
    names = {k.lower(): k for k in (*REGIONS, ANY)}
    out = []
    for t in _tokens(value):
        if t.lower() in names:
            t = names[t.lower()]
        elif re.fullmatch(r"[A-Za-z]{2}", t):
            t = ALIASES.get(t.upper(), t.upper())
        else:
            raise ValueError(f"{t!r} isn't a country code or a region ({', '.join([*REGIONS, ANY])})")
        if t not in out:
            out.append(t)
    if ANY in out and len(out) > 1:
        raise ValueError(f"{ANY} already means every country; drop the others")
    return None if not out else out[0] if len(out) == 1 else out


def codes(value: Union[str, Iterable[str], None]) -> Optional[list[str]]:
    """The country codes a `country` value covers, sorted; None for no limit (blank or Any)."""
    c = canonical(value)
    found: set[str] = set()
    for t in [c] if isinstance(c, str) else c or []:
        if t == ANY:
            return None
        found.update(REGIONS[t][1] if t in REGIONS else [t])
    return sorted(found) or None


def describe(value: Union[str, Iterable[str], None]) -> str:
    """'EU + Schengen (31 countries)', 'US', 'any country'."""
    c = canonical(value)
    if c is None or c == ANY:
        return "any country"
    parts = [c] if isinstance(c, str) else c
    n = len(codes(c) or [])
    return " + ".join(parts) + (f" ({n} countries)" if n > 1 and any(p in REGIONS for p in parts) else "")


def choices() -> list[dict]:
    """The regions, for the app's Where picker."""
    return [{"id": k, "label": label, "codes": list(cs)} for k, (label, cs) in REGIONS.items()]


# ---- places a role names --------------------------------------------------------------------------------------------
_EUR = set(EUROPE)
# Words that pin a role to a set of countries. A role that names none of them could be anywhere.
_PLACES: dict[str, set[str]] = {
    # regions
    "eu": set(EU), "european union": set(EU), "eea": set(EEA), "schengen": set(SCHENGEN),
    "europe": _EUR, "european": _EUR, "cet": _EUR, "cest": _EUR, "eet": _EUR, "eest": _EUR,
    "emea": set(EMEA), "dach": {"DE", "AT", "CH"}, "benelux": {"BE", "NL", "LU"}, "baltics": {"EE", "LV", "LT"},
    "nordics": {"DK", "FI", "IS", "NO", "SE"}, "nordic": {"DK", "FI", "IS", "NO", "SE"},
    "scandinavia": {"DK", "NO", "SE"}, "balkans": {"AL", "BA", "BG", "HR", "ME", "MK", "RS", "SI", "GR", "RO"},
    "middle east": set(MIDDLE_EAST), "mena": set(MIDDLE_EAST) | {"EG", "MA", "DZ", "TN", "LY"},
    "gcc": {"AE", "SA", "QA", "BH", "KW", "OM"}, "africa": set(AFRICA),
    "apac": {"AU", "NZ", "JP", "KR", "CN", "HK", "TW", "SG", "IN", "ID", "MY", "PH", "TH", "VN"},
    "asia": {"JP", "KR", "CN", "HK", "TW", "SG", "IN", "ID", "MY", "PH", "TH", "VN", "PK"},
    "latam": {"MX", "BR", "AR", "CO", "CL", "PE", "UY", "CR"}, "latin america": {"MX", "BR", "AR", "CO", "CL", "PE", "UY", "CR"},
    "south america": {"BR", "AR", "CO", "CL", "PE", "UY"}, "central america": {"CR", "PA", "GT"},
    "americas": {"US", "CA", "MX", "BR", "AR", "CO", "CL", "PE", "UY", "CR"}, "north america": {"US", "CA", "MX"},
    # the United States
    "us": {"US"}, "u.s.": {"US"}, "usa": {"US"}, "united states": {"US"}, "america": {"US"},
    "remote-us": {"US"}, "us-remote": {"US"}, "pst": {"US"}, "pdt": {"US"}, "est": {"US"}, "edt": {"US"},
    "new york": {"US"}, "nyc": {"US"}, "san francisco": {"US"}, "bay area": {"US"}, "seattle": {"US"},
    "los angeles": {"US"}, "boston": {"US"}, "chicago": {"US"}, "austin": {"US"}, "denver": {"US"},
    "atlanta": {"US"}, "washington, dc": {"US"}, "georgia": {"US", "GE"},
}
_COUNTRY_WORDS = {
    "GB": "uk|u.k.|united kingdom|great britain|britain|england|scotland|wales|london|edinburgh|manchester|cambridge|oxford|bristol|belfast",
    "IE": "ireland|dublin|cork", "DE": "germany|deutschland|berlin|munich|münchen|hamburg|frankfurt|cologne|köln|stuttgart",
    "FR": "france|paris|lyon", "NL": "netherlands|the netherlands|holland|amsterdam|rotterdam|utrecht|eindhoven",
    "BE": "belgium|brussels|antwerp", "LU": "luxembourg", "ES": "spain|madrid|barcelona|valencia|málaga|malaga",
    "PT": "portugal|lisbon|porto", "IT": "italy|milan|rome|turin", "AT": "austria|vienna", "CH": "switzerland|zurich|zürich|geneva|lausanne|basel",
    "DK": "denmark|copenhagen", "SE": "sweden|stockholm|gothenburg", "NO": "norway|oslo", "FI": "finland|helsinki",
    "IS": "iceland|reykjavik", "EE": "estonia|tallinn", "LV": "latvia|riga", "LT": "lithuania|vilnius",
    "PL": "poland|warsaw|kraków|krakow|wrocław|wroclaw|gdańsk|gdansk", "CZ": "czechia|czech republic|czech|prague|brno",
    "SK": "slovakia|bratislava", "HU": "hungary|budapest", "RO": "romania|bucharest|cluj", "BG": "bulgaria|sofia",
    "GR": "greece|athens", "HR": "croatia|zagreb", "SI": "slovenia|ljubljana", "CY": "cyprus|limassol|nicosia", "MT": "malta",
    "RS": "serbia|belgrade", "UA": "ukraine|kyiv|kiev|lviv", "MD": "moldova", "TR": "turkey|türkiye|istanbul|ankara",
    "IL": "israel|tel aviv|jerusalem", "AE": "uae|united arab emirates|dubai|abu dhabi", "SA": "saudi|saudi arabia|riyadh",
    "QA": "qatar|doha", "EG": "egypt|cairo", "MA": "morocco|casablanca", "NG": "nigeria|lagos", "KE": "kenya|nairobi",
    "ZA": "south africa|cape town|johannesburg", "GH": "ghana|accra", "RU": "russia|moscow",
    "CA": "canada|toronto|vancouver|montreal|ottawa", "MX": "mexico|mexico city", "BR": "brazil|são paulo|sao paulo",
    "AR": "argentina|buenos aires", "CO": "colombia|bogotá|bogota", "CL": "chile|santiago", "PE": "peru|lima", "CR": "costa rica",
    "IN": "india|bangalore|bengaluru|mumbai|hyderabad|delhi|pune|chennai|gurgaon|gurugram", "PK": "pakistan",
    "SG": "singapore", "JP": "japan|tokyo", "KR": "korea|south korea|seoul", "CN": "china|beijing|shanghai|shenzhen",
    "HK": "hong kong", "TW": "taiwan|taipei", "VN": "vietnam|ho chi minh|hanoi", "TH": "thailand|bangkok",
    "ID": "indonesia|jakarta", "PH": "philippines|manila", "MY": "malaysia|kuala lumpur",
    "AU": "australia|sydney|melbourne|brisbane", "NZ": "new zealand|auckland",
}
for _code, _words in _COUNTRY_WORDS.items():
    for _w in _words.split("|"):
        _PLACES.setdefault(_w, set()).add(_code)
# US state abbreviations after a city ("Austin, TX"), not a word that happens to follow a comma ("Remote, in the EU").
_STATES = ("al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd "
           "oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc").split()
# Longest first, so "latin america" wins over "america" and "new york" over "york".
_PLACE_RE = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, sorted(_PLACES, key=len, reverse=True))) + r")(?!\w)"
                       r"|, (" + "|".join(_STATES) + r")(?=\s*(?:$|[;,)/|(\d-]))")


def places(text: str) -> set[str]:
    """The country codes a title or location names ('Remote (EU)' -> the EU's 27; 'Austin, TX' -> US)."""
    found: set[str] = set()
    for m in _PLACE_RE.finditer((text or "").lower()):
        found |= {"US"} if m.group(2) else _PLACES.get(m.group(1), set())
    return found


def elsewhere(text: str, country: Union[str, Iterable[str], None]) -> bool:
    """Does this title or location pin the role somewhere outside where the search looks? A role that names
    no place, or names one the search covers ("London or New York" for a US search), is kept."""
    allowed = codes(country)
    if allowed is None:
        return False
    named = places(text)
    return bool(named) and not named & set(allowed)


# ---- where a role is, for the list's Region filter -------------------------------------------------------------------
_PARTS = re.compile(r";|\s[/|]\s|\sor\s")    # one place from the next ("or" in lower case: "Portland, OR" is a state)
_IN_US = re.compile(r", (" + "|".join(_STATES) + r")(?=\s*(?:$|[,)(\d-]))|\b(usa|united states)\b")
_ANYWHERE = re.compile(r"\b(anywhere|worldwide|global(ly)?|any country|any location)\b", re.I)
_EMEA_BUT_UK = set(EMEA) - {"GB"}


def areas(codes: Iterable[str], location: str = "", title: str = "") -> list[str]:
    """The list's regions a role is in, from the country codes its posting gives and the places its location and title
    name: US, UK, EMEA (somewhere in Europe, the Middle East or Africa besides the UK, so a London role is UK alone),
    Other for a place outside all three, Anywhere for a role its location says is open worldwide. Empty when the role
    names no place at all."""
    found = {c.upper() for c in codes if c} | places(title)
    for part in _PARTS.split(location or ""):
        # "Cambridge, MA" and "Berlin, MD, USA" are American towns, not the British or German ones
        found |= {"US"} if _IN_US.search(part.lower()) else places(part)
    out = [a for a, hit in (("US", "US" in found), ("UK", "GB" in found), ("EMEA", bool(found & _EMEA_BUT_UK))) if hit]
    if found and not out:
        out.append("Other")
    if _ANYWHERE.search(location or ""):
        out.append("Anywhere")
    return out


# ---- where a role is, in a word, for the list's rows ------------------------------------------------------------------
_STATE_NAMES = dict(zip(
    (n.replace("_", " ") for n in "alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois "
     "indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri "
     "montana nebraska nevada new_hampshire new_jersey new_mexico new_york north_carolina north_dakota ohio oklahoma "
     "oregon pennsylvania rhode_island south_carolina south_dakota tennessee texas utah vermont virginia washington "
     "west_virginia wisconsin wyoming".split()),
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA "
    "RI SC SD TN TX UT VT VA WA WV WI WY".split()))
_US_CITIES = {"new york": "NY", "nyc": "NY", "san francisco": "CA", "bay area": "CA", "silicon valley": "CA",
              "los angeles": "CA", "seattle": "WA", "boston": "MA", "chicago": "IL", "austin": "TX", "denver": "CO",
              "atlanta": "GA", "washington, dc": "DC", "washington dc": "DC"}
_US_CITY_RE = re.compile(r"(?<!\w)(" + "|".join(map(re.escape, sorted(_US_CITIES, key=len, reverse=True))) + r")(?!\w)")
_STATE_CODE = re.compile(r"(?:,|\(|^us-)\s*([a-z]{2})(?:\s+\d{5})?\s*(?=$|[,)-])")   # "Austin, TX", "Denver (CO)", "US-CA-Menlo Park"
_IN_USA = re.compile(r"\b(usa|united states|u\.s\.|us)\b")
_WORK_MODE = re.compile(r"\b(remote|hybrid|on-?site|in-office)\b", re.I)
_US_WORDS = {"us", "u.s.", "usa", "united states", "america", "remote-us", "us-remote"}
_REGION_NAMES = {"eu": "EU", "european union": "EU", "eea": "EEA", "schengen": "Schengen", "europe": "Europe",
                 "european": "Europe", "emea": "EMEA", "dach": "DACH", "benelux": "Benelux", "baltics": "Baltics",
                 "nordics": "Nordics", "nordic": "Nordics", "scandinavia": "Scandinavia", "balkans": "Balkans",
                 "middle east": "Middle East", "mena": "MENA", "gcc": "GCC", "africa": "Africa", "apac": "APAC",
                 "asia": "Asia", "latam": "LATAM", "latin america": "LATAM", "south america": "South America",
                 "central america": "Central America", "americas": "Americas", "north america": "North America"}
_COUNTRY_NAMES = {"US": "US", "GB": "UK", "AE": "UAE", "SA": "Saudi Arabia", "KR": "South Korea",
                  **{c: w.split("|")[0].title() for c, w in _COUNTRY_WORDS.items() if c not in ("GB", "AE", "SA", "KR")}}


def _part_places(part: str) -> list[str]:
    """One place in a location ("Austin, TX", "Berlin, Germany", "Remote - Europe"): its US states, else the countries
    it names, else the regions."""
    low = _WORK_MODE.sub("", part).strip(" ,;:()-–").lower()
    if not low:
        return []
    elems = [e.strip(" ()") for e in low.split(",")]
    us = bool(_IN_USA.search(low))
    states = [m.group(1).upper() for m in _STATE_CODE.finditer(low) if m.group(1) in _STATES]
    # a state by name: "Utah, USA", "New York City, New York, USA" (alone, "Georgia" could be the country)
    states += [_STATE_NAMES[e] for i, e in enumerate(elems) if e in _STATE_NAMES and (us or i > 0)]
    if not states:
        states = [_US_CITIES[m.group(1)] for m in _US_CITY_RE.finditer(low)]
    if states:
        return states
    countries, areas_ = [], []
    for m in _PLACE_RE.finditer(low):
        w = m.group(1)
        if not w:
            continue
        if w in _US_WORDS:
            countries.append("US")
        elif w in _REGION_NAMES:
            areas_.append(_REGION_NAMES[w])
        elif len(_PLACES[w]) == 1:
            countries.append(_COUNTRY_NAMES.get(next(iter(_PLACES[w])), next(iter(_PLACES[w]))))
    return countries or areas_


def place(codes: Iterable[str], location: str = "", most: int = 2) -> str:
    """Where a role is, in a few words for the list: the US state ("CA"), else the country ("UK", "Germany") or the
    region ("EU") its location names, a few at most ("CA, NY +3"); else the country its posting's codes give; else
    Anywhere for a role open worldwide; else ''."""
    found: list[str] = []
    for part in _PARTS.split(location or ""):
        found += [p for p in dict.fromkeys(_part_places(part)) if p not in found]
    if any(len(p) == 2 and p in _STATE_NAMES.values() for p in found):
        found = [p for p in found if p != "US"]          # "United States; New York, NY" says no more than NY
    if not found:
        cs = sorted({c.upper() for c in codes if c})
        found = [_COUNTRY_NAMES.get(c, c) for c in cs] if 0 < len(cs) <= most else []
    if not found and _ANYWHERE.search(location or ""):
        return "Anywhere"
    return ", ".join(found[:most]) + (f" +{len(found) - most}" if len(found) > most else "")

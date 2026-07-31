"""Pure functions. No IO, no network -- so you can unit test all of it in a second.

The dedupe key is the load-bearing piece here: the same role shows up on the
company board, an aggregator, and LinkedIn. Collapse them or your counts lie.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Noise that appears in titles but does not change the role identity.
_REQ_ID = re.compile(r"\b(req|job|jr|id)[-_# ]?\d{3,}\b", re.I)
_BRACKETED = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
# Pipes and slashes split tightly; dashes need surrounding space so that
# 'e-commerce' and 'full-stack' survive intact.
_SEPARATORS = re.compile(r"\s*[|/]\s*|\s+[-\u2013\u2014]\s+")
_MULTISPACE = re.compile(r"\s+")

_SENIORITY_WORDS = {
    "senior", "sr", "staff", "principal", "lead", "junior", "jr",
    "entry", "level", "i", "ii", "iii", "iv", "1", "2", "3", "4",
    "associate", "mid",
}

_COMPANY_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "corp", "corporation",
    "co", "gmbh", "pvt", "private", "plc", "sa", "bv", "srl", "ag",
}

_REMOTE_HINTS = ("remote", "work from home", "wfh", "anywhere", "distributed")
_HYBRID_HINTS = ("hybrid", "flexible", "partially remote")


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def normalize_company(name: str) -> str:
    """'Acme Technologies Pvt. Ltd.' -> 'acme technologies'"""
    name = strip_accents(name).lower()
    name = re.sub(r"[^\w\s]", " ", name)  # drops the dot in 'Pvt.' / 'Ltd.'
    tokens = [t for t in _MULTISPACE.split(name) if t and t not in _COMPANY_SUFFIXES]
    return " ".join(tokens).strip()


def normalize_title(title: str) -> str:
    """Strip req ids, bracketed asides and seniority so 'Senior Backend
    Engineer (Remote) - Req 12345' and 'Backend Engineer II' collapse together.

    Seniority is stripped here so that cosmetic variants collapse, but it is
    NOT discarded: `dedupe_key()` re-adds it as a coarse band via
    `seniority_of()`, so 'Senior X' and 'Staff X' stay distinct roles.
    """
    title = strip_accents(title).lower()
    title = _BRACKETED.sub(" ", title)
    title = _REQ_ID.sub(" ", title)

    # ATS titles put the role first: 'Backend Engineer | Bangalore',
    # 'Backend Engineer - Payments'. Keep the leading segment, drop the rest.
    segments = [s.strip() for s in _SEPARATORS.split(title) if s and s.strip()]
    if segments and len(segments[0].split()) >= 2:
        title = segments[0]

    title = re.sub(r"[^\w\s]", " ", title)
    tokens = [t for t in _MULTISPACE.split(title) if t and t not in _SENIORITY_WORDS]
    return " ".join(tokens).strip()


def seniority_of(title: str) -> str:
    """Coarse band, used as a matching feature (not part of the dedupe key)."""
    t = title.lower()
    if any(w in t for w in ("principal", "staff", "distinguished")):
        return "staff+"
    if any(w in t for w in ("senior", "sr.", "sr ", "lead")):
        return "senior"
    if any(w in t for w in ("intern", "trainee", "graduate", "fresher")):
        return "intern"
    if any(w in t for w in ("junior", "jr.", "jr ", "entry", " i ", " 1 ")):
        return "junior"
    return "mid"


def location_bucket(location: str | None) -> str:
    """Coarse enough that 'Bengaluru, KA' and 'Bangalore, India' collapse."""
    if not location:
        return "unknown"
    loc = strip_accents(location).lower()
    loc = re.sub(r"[^\w\s]", " ", loc)
    loc = _MULTISPACE.sub(" ", loc).strip()

    # Every alias maps straight to its canonical bucket -- no chaining, because
    # the first substring hit wins. Grow this list as your registry grows.
    aliases = {
        "bengaluru": "bangalore",
        "bangalore": "bangalore",
        "gurugram": "delhi ncr",
        "gurgaon": "delhi ncr",
        "noida": "delhi ncr",
        "new delhi": "delhi ncr",
        "delhi": "delhi ncr",
        "bombay": "mumbai",
        "madras": "chennai",
        "hyderabad": "hyderabad",
        "pune": "pune",
    }
    for src, dst in aliases.items():
        if src in loc:
            return dst
    if any(h in loc for h in _REMOTE_HINTS):
        return "remote"
    return loc.split(" ")[0] if loc else "unknown"


# Segment separators in ATS location strings. Multi-site postings look like
# 'New York City; Palo Alto; San Francisco' or 'San Diego, CA or San Francisco'.
# Hyphens are included because Workday encodes locations as 'US-CA-Menlo Park'
# and 'GB-London'; the two-letter leaders are dropped by the length guard below.
_LOC_SPLIT = re.compile(r"\s*[;/-]\s*|\s+or\s+|\s*,\s*|\s*&\s*")
# Trailing administrative noise that adds nothing to a filter label.
_LOC_TAIL = {
    "united states", "united states of america", "usa", "us", "u s",
    "united kingdom", "uk", "canada", "india", "hq", "office", "remote",
}
# Work-arrangement words are not places. They already have their own facet in
# remote_type; letting them through creates a 'hybrid' bucket that competes with
# 'bangalore' in the location filter and means something entirely different.
_LOC_JUNK = {
    "n a", "na", "n", "tbd", "various", "multiple", "unknown", "none",
    "in office", "in-office", "onsite", "on site", "on-site", "hybrid",
    "in person", "in-person", "flexible", "field", "home", "locations",
}

# Global cities worth their own bucket. The India table above stays the
# authority for India; this is everything the fallback used to mangle.
_LOC_GLOBAL = {
    "san francisco": "san francisco", "sf": "san francisco",
    "san jose": "san jose", "san mateo": "san mateo", "san diego": "san diego",
    "palo alto": "palo alto", "mountain view": "mountain view",
    "redwood city": "redwood city", "sunnyvale": "sunnyvale",
    "new york": "new york", "nyc": "new york", "brooklyn": "new york",
    "seattle": "seattle", "bellevue": "seattle", "austin": "austin",
    "boston": "boston", "chicago": "chicago", "denver": "denver",
    "atlanta": "atlanta", "los angeles": "los angeles", "washington": "washington dc",
    "london": "london", "dublin": "dublin", "cork": "cork", "berlin": "berlin",
    "munich": "munich", "hamburg": "hamburg", "paris": "paris", "amsterdam": "amsterdam",
    "barcelona": "barcelona", "madrid": "madrid", "milan": "milan", "zurich": "zurich",
    "basel": "basel", "stockholm": "stockholm", "vienna": "vienna", "warsaw": "warsaw",
    "belgrade": "belgrade", "bucharest": "bucharest", "lisbon": "lisbon",
    "toronto": "toronto", "vancouver": "vancouver", "montreal": "montreal",
    "sydney": "sydney", "melbourne": "melbourne", "auckland": "auckland",
    "singapore": "singapore", "tokyo": "tokyo", "seoul": "seoul", "taipei": "taipei",
    "beijing": "beijing", "shanghai": "shanghai", "guangzhou": "guangzhou",
    "tel aviv": "tel aviv", "dubai": "dubai", "sao paulo": "sao paulo",
    "mexico city": "mexico city", "buenos aires": "buenos aires",
    "kuala lumpur": "kuala lumpur", "manila": "manila", "ho chi minh city": "ho chi minh city",
}


def location_facet(location: str | None) -> str:
    """Human-readable bucket for the location *filter*.

    Deliberately separate from `location_bucket()`. That one only has to be
    stable and consistent, because its output is immediately hashed into a
    dedupe key -- nobody ever reads it. This one is a label in a filter rail,
    so it has to be right.

    The difference matters: `location_bucket()` falls back to the first
    whitespace token, which turns 'San Francisco, CA' into 'san', 'New York'
    into 'new', 'n/a' into 'n' and '2 Locations' into '2'. Fine for a hash
    input, useless as something to click on.
    """
    if not location:
        return "unknown"

    loc = strip_accents(location).lower()
    loc = re.sub(r"[^\w\s;/&,-]", " ", loc)
    loc = _MULTISPACE.sub(" ", loc).strip()
    if not loc:
        return "unknown"

    # India first -- it is the registry's centre of gravity and its aliases are
    # substring matches ('Bengaluru, KA', 'IN-Mumbai-MSO').
    for src, dst in {
        "bengaluru": "bangalore", "bangalore": "bangalore",
        "gurugram": "delhi ncr", "gurgaon": "delhi ncr", "noida": "delhi ncr",
        "new delhi": "delhi ncr", "delhi": "delhi ncr",
        "bombay": "mumbai", "mumbai": "mumbai", "madras": "chennai",
        "chennai": "chennai", "hyderabad": "hyderabad", "pune": "pune",
        "kolkata": "kolkata", "ahmedabad": "ahmedabad", "jaipur": "jaipur",
    }.items():
        if src in loc:
            return dst

    if any(h in loc for h in _REMOTE_HINTS) or loc.startswith("remot"):
        return "remote"

    for city, dst in _LOC_GLOBAL.items():
        if city in loc:
            return dst

    # Fall back to the first segment, not the first word, so multi-word cities
    # survive. A segment that is numeric, a placeholder, or too short to be a
    # place name is dropped rather than shown as its own bucket.
    for seg in _LOC_SPLIT.split(loc):
        seg = re.sub(r"[^\w\s]", " ", seg)
        seg = _MULTISPACE.sub(" ", seg).strip()
        if not seg or seg in _LOC_JUNK or seg in _LOC_TAIL:
            continue
        if seg[0].isdigit() or len(seg) <= 2:
            continue
        return seg
    return "unknown"


def detect_remote_type(*fields: str | None) -> str:
    blob = " ".join(f.lower() for f in fields if f)
    if any(h in blob for h in _HYBRID_HINTS):
        return "hybrid"
    if any(h in blob for h in _REMOTE_HINTS):
        return "remote"
    if blob.strip():
        return "onsite"
    return "unknown"


def dedupe_key(company_name: str, title: str, location: str | None) -> str:
    """Stable across boards. Same role posted three places -> one key.

    Seniority is part of the identity: 'Senior SWE, Observability' and 'Staff
    SWE, Observability' at one company are two separate applications with
    different comp bands and different interview loops. Collapsing them hides
    one of the two from you permanently.

    It is added via the coarse `seniority_of()` band rather than the raw title,
    so cosmetic variants ('Sr.' vs 'Senior', 'Engineer II' vs plain 'Engineer')
    still collapse -- only a genuine band change splits the key.
    """
    basis = "|".join(
        (
            normalize_company(company_name),
            normalize_title(title),
            location_bucket(location),
            seniority_of(title),
        )
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def question_hash(question: str) -> str:
    """For the answer bank -- so 'Why do you want to work here?' matches
    'Why do you want to work here? *' and similar cosmetic variants."""
    q = strip_accents(question).lower()
    q = re.sub(r"[^\w\s]", " ", q)
    q = _MULTISPACE.sub(" ", q).strip()
    return hashlib.sha1(q.encode("utf-8")).hexdigest()

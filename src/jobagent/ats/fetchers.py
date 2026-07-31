"""One module, six ATS providers, one interface.

    fetch(provider, token, client) -> list[RawPosting]

Provider-specific weirdness is contained inside each function. Adding another
provider means a new function plus a FETCHERS entry, nothing else.

greenhouse | lever | ashby      return a whole board in one response
smartrecruiters | workday | amazon  paginate, and the first two carry no
                                job description in the list response

IMPORTANT: verify each endpoint with curl before trusting the parser. These
URLs drift. One minute of curl saves an hour of debugging a 404.

    curl -s 'https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true' | head -c 400
"""

from __future__ import annotations

import html
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx

from ..models import RawPosting
from ..normalize import detect_remote_type

TIMEOUT = httpx.Timeout(20.0)
UA = "jobagent/0.1 (personal job search; contact: you@example.com)"

# Greenhouse/Lever/Ashby hand back a whole board in one response. The three
# paginated providers below do not, so they need their own limits: a page cap
# so a bad `total` cannot spin forever, and a pause between pages, since
# sync.py's RateLimiter only spaces out whole companies, not pages within one.
PAGE_SIZE = 100
MAX_PAGES = 40
PAGE_PAUSE = 0.7


def _truncated(provider: str, token: str, got: int) -> None:
    """Hitting MAX_PAGES means the board is bigger than we fetched. Say so --
    a silent cap reads exactly like 'that is the whole board'."""
    print(f"  ! {provider}:{token} hit the {MAX_PAGES}-page cap at {got} jobs (board is larger)")


class FetchError(RuntimeError):
    pass


def _strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ts(value) -> datetime | None:
    """ATSes disagree on time format: ISO strings and epoch millis both appear."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except (ValueError, OSError, OverflowError):
        return None


# --------------------------------------------------------------------------
# Greenhouse
# --------------------------------------------------------------------------
def fetch_greenhouse(token: str, client: httpx.Client) -> list[RawPosting]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    resp = client.get(url, params={"content": "true"})
    if resp.status_code == 404:
        raise FetchError(f"greenhouse board not found: {token}")
    resp.raise_for_status()

    out: list[RawPosting] = []
    for j in resp.json().get("jobs", []):
        location = (j.get("location") or {}).get("name")
        body = _strip_html(j.get("content"))
        out.append(
            RawPosting(
                external_id=str(j["id"]),
                title=j["title"],
                apply_url=j["absolute_url"],
                location=location,
                # Greenhouse exposes no workplace-type field. Infer, don't guess.
                remote_type=detect_remote_type(location, j["title"]),
                description_md=body,
                posted_at=_ts(j.get("updated_at")),
            )
        )
    return out


# --------------------------------------------------------------------------
# Lever
# --------------------------------------------------------------------------
def fetch_lever(token: str, client: httpx.Client) -> list[RawPosting]:
    url = f"https://api.lever.co/v0/postings/{token}"
    resp = client.get(url, params={"mode": "json"})
    if resp.status_code in (404, 400):
        raise FetchError(f"lever board not found: {token}")
    resp.raise_for_status()

    out: list[RawPosting] = []
    for j in resp.json():
        cat = j.get("categories") or {}
        location = cat.get("location")
        workplace = j.get("workplaceType")  # remote | hybrid | on-site
        remote = (
            {"on-site": "onsite", "onsite": "onsite", "remote": "remote", "hybrid": "hybrid"}
            .get(str(workplace).lower())
            or detect_remote_type(location, cat.get("commitment"))
        )
        out.append(
            RawPosting(
                external_id=str(j["id"]),
                title=j["text"],
                apply_url=j.get("hostedUrl") or j.get("applyUrl", ""),
                location=location,
                remote_type=remote,
                description_md=_strip_html(j.get("descriptionPlain") or j.get("description")),
                posted_at=_ts(j.get("createdAt")),
            )
        )
    return out


# --------------------------------------------------------------------------
# Ashby
# --------------------------------------------------------------------------
def fetch_ashby(token: str, client: httpx.Client) -> list[RawPosting]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    resp = client.get(url, params={"includeCompensation": "true"})
    if resp.status_code == 404:
        raise FetchError(f"ashby board not found: {token}")
    resp.raise_for_status()

    out: list[RawPosting] = []
    for j in resp.json().get("jobs", []):
        location = j.get("location")
        remote = "remote" if j.get("isRemote") else detect_remote_type(location)
        out.append(
            RawPosting(
                external_id=str(j["id"]),
                title=j["title"],
                apply_url=j.get("jobUrl") or j.get("applyUrl", ""),
                location=location,
                remote_type=remote,
                description_md=_strip_html(j.get("descriptionHtml")),
                posted_at=_ts(j.get("publishedAt")),
            )
        )
    return out


# --------------------------------------------------------------------------
# SmartRecruiters
#
# Public, documented, no auth: https://developers.smartrecruiters.com/
# Token is the company identifier, e.g. 'Freshworks'.
#
# The list endpoint carries no job description -- SmartRecruiters puts it
# behind a per-posting detail call. Fetching it would mean one extra request
# per job on every sync, which is exactly the hammering the README warns
# against. description_md is left None; pull it lazily for the shortlist.
# --------------------------------------------------------------------------
def fetch_smartrecruiters(token: str, client: httpx.Client) -> list[RawPosting]:
    out: list[RawPosting] = []
    offset = 0
    for _ in range(MAX_PAGES):
        resp = client.get(
            f"https://api.smartrecruiters.com/v1/companies/{token}/postings",
            params={"limit": PAGE_SIZE, "offset": offset},
        )
        if resp.status_code in (403, 404):
            raise FetchError(f"smartrecruiters board not found: {token}")
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("content") or []
        if not batch:
            break

        for j in batch:
            # Same defensiveness as the Workday loop: one malformed row must
            # not cost us the rest of the board.
            if not j.get("name") or not j.get("id"):
                continue
            loc = j.get("location") or {}
            location = loc.get("fullLocation") or ", ".join(
                p for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p
            )
            # SmartRecruiters states workplace type outright -- trust it over inference.
            if loc.get("remote"):
                remote = "remote"
            elif loc.get("hybrid"):
                remote = "hybrid"
            else:
                remote = detect_remote_type(location)
            out.append(
                RawPosting(
                    external_id=str(j["id"]),
                    title=j["name"],
                    apply_url=f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
                    location=location or None,
                    remote_type=remote,
                    description_md=None,
                    posted_at=_ts(j.get("releasedDate")),
                )
            )

        offset += len(batch)
        if offset >= data.get("totalFound", 0):
            break
        time.sleep(PAGE_PAUSE)
    else:
        _truncated("smartrecruiters", token, len(out))
    return out


# --------------------------------------------------------------------------
# Workday
#
# The CXS endpoint every Workday careers site calls to render itself. POST,
# public, no auth.
#
# Token packs the three things a Workday tenant needs, colon-separated, plus
# an optional country to filter on:
#     '<host>:<site>:<wd-number>[:<country>]'
#     'nvidia:NVIDIAExternalCareerSite:5:India'
# read straight off the careers URL:
#     https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite?country=India
#
# The country matters: these boards run to thousands of postings, so without
# it MAX_PAGES truncates and you silently lose most of the board. NVIDIA is
# 2000 jobs unfiltered, 211 in India.
#
# Like SmartRecruiters, the list response has no description. It also gives
# only a relative date ('Posted 19 Days Ago'), which _relative_date parses
# back to an approximate timestamp -- good enough to rank freshness on.
# --------------------------------------------------------------------------
_RELATIVE = re.compile(r"(\d+)\+?\s*(day|week|month|hour)", re.I)
_N_LOCATIONS = re.compile(r"^\d+\s+locations?$", re.I)


def _relative_date(text: str | None) -> datetime | None:
    """'Posted 19 Days Ago' -> a timestamp. 'Posted Today' -> now."""
    if not text:
        return None
    now = datetime.now(timezone.utc)
    low = text.lower()
    if "today" in low or "just posted" in low:
        return now
    if "yesterday" in low:
        return now - timedelta(days=1)
    m = _RELATIVE.search(low)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    days = {"hour": n / 24, "day": n, "week": n * 7, "month": n * 30}[unit]
    return now - timedelta(days=days)


def _facet_values(facets, out: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Flatten Workday's facet tree to (facetParameter, descriptor, id).

    Location facets sit one level down, nested under a 'locationMainGroup'
    entry that carries no facetParameter of its own.
    """
    for f in facets or []:
        param = f.get("facetParameter")
        for v in f.get("values") or []:
            if not isinstance(v, dict):
                continue
            if v.get("values"):
                _facet_values([v], out)
            elif param and v.get("id"):
                out.append((param, str(v.get("descriptor") or ""), v["id"]))
    return out


def _country_facet(client: httpx.Client, api: str, country: str):
    """Find the facet that narrows a Workday board to one country.

    Tenants disagree on the parameter name -- locationHierarchy1 (NVIDIA),
    locationCountry (Adobe), Location_Country (Target) have all been seen --
    so match on the value's descriptor and use whichever parameter it came
    from. Returns (facetParameter, [ids]) or (None, None).

    The match is exact, not a prefix: Target publishes both 'India' and
    'Indiana', and a prefix match would quietly pull in 204 US jobs.
    """
    resp = client.post(api, json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    resp.raise_for_status()
    values = _facet_values(resp.json().get("facets"), [])

    wanted = country.strip().lower()
    for param, desc, fid in values:
        if desc.strip().lower() == wanted:
            return param, [fid]

    # No country facet: fall back to site-level entries, which Workday names
    # '<Country>, <City>'. PayPal, for one, exposes nothing else.
    sites = [fid for param, desc, fid in values
             if param == "locations" and desc.strip().lower().startswith(wanted + ",")]
    if sites:
        return "locations", sites
    return None, None


def fetch_workday(token: str, client: httpx.Client) -> list[RawPosting]:
    parts = token.split(":")
    if len(parts) not in (3, 4):
        raise FetchError(
            "workday token must be '<host>:<site>:<wd-number>' with an optional "
            f"':<country>', got {token!r}"
        )
    host, site, wd = parts[0], parts[1], parts[2]
    country = parts[3] if len(parts) == 4 else None
    base = f"https://{host}.wd{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{host}/{site}/jobs"

    # One extra request, once per sync, to turn a 2000-job board into the few
    # hundred that are actually in-country -- and to stop the page cap from
    # silently deciding which two thirds you never see.
    applied: dict = {}
    if country:
        param, ids = _country_facet(client, api, country)
        if param:
            applied = {param: ids}
        else:
            print(f"  ! workday:{host} exposes no '{country}' facet -- fetching unfiltered")

    out: list[RawPosting] = []
    offset = 0
    total = None
    for _ in range(MAX_PAGES):
        resp = client.post(
            api, json={"appliedFacets": applied, "limit": 20, "offset": offset, "searchText": ""}
        )
        if resp.status_code in (400, 404):
            raise FetchError(f"workday board not found: {token}")
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("jobPostings") or []
        if not batch:
            break
        # Workday reports the real count on page 1 and then sends total=0 for
        # every page after it. Trusting the per-page value stops the loop dead
        # at offset 40. Keep the first one.
        if total is None:
            total = data.get("total") or 0

        for j in batch:
            # Undocumented endpoint, live search behind it: the result set
            # reorders between pages and an occasional entry arrives without a
            # title. Skip those rather than let one bad row lose the other 800.
            title = j.get("title")
            path = j.get("externalPath") or ""
            if not title or not path:
                continue
            # bulletFields is where Workday hides the requisition id.
            bullets = j.get("bulletFields") or []
            external_id = bullets[0] if bullets else path.rsplit("_", 1)[-1] or path
            location = j.get("locationsText")
            # Multi-site postings come back as the literal '2 Locations', which
            # names nowhere: location_bucket() reads that as the city '2' and
            # the row gets filtered out of every location query. When we asked
            # for one country the facet guarantees it applies, so say so.
            if country and location and _N_LOCATIONS.match(location.strip()):
                location = f"{country} ({location.strip().lower()})"
            out.append(
                RawPosting(
                    external_id=str(external_id),
                    title=title,
                    apply_url=f"{base}/{site}{path}",
                    location=location,
                    remote_type=detect_remote_type(location, title),
                    description_md=None,
                    posted_at=_relative_date(j.get("postedOn")),
                )
            )

        offset += len(batch)
        if offset >= total:
            break
        time.sleep(PAGE_PAUSE)
    else:
        _truncated("workday", token, len(out))
    return out


# --------------------------------------------------------------------------
# Amazon
#
# amazon.jobs/en/search.json is the endpoint their own search page calls.
# robots.txt disallows only /internal paths, so this one is fair game.
#
# Amazon is a single employer, so the token selects geography instead of a
# board: an ISO-3 country code such as 'IND'. Unlike the two above, the list
# response DOES carry the description, so nothing is left None here.
# --------------------------------------------------------------------------
def fetch_amazon(token: str, client: httpx.Client) -> list[RawPosting]:
    country = (token or "IND").upper()
    out: list[RawPosting] = []
    offset = 0
    for _ in range(MAX_PAGES):
        resp = client.get(
            "https://www.amazon.jobs/en/search.json",
            params={
                "base_query": "",
                "country": country,
                "result_limit": PAGE_SIZE,
                "offset": offset,
                "sort": "recent",
            },
        )
        if resp.status_code == 404:
            raise FetchError(f"amazon search rejected country: {country}")
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("jobs") or []
        if not batch:
            break

        for j in batch:
            if not j.get("title") or not (j.get("id_icims") or j.get("id")):
                continue
            location = j.get("normalized_location") or j.get("location")
            body = "\n\n".join(
                _strip_html(j.get(k))
                for k in ("description", "basic_qualifications", "preferred_qualifications")
                if j.get(k)
            )
            out.append(
                RawPosting(
                    external_id=str(j.get("id_icims") or j["id"]),
                    title=j["title"],
                    apply_url="https://www.amazon.jobs" + (j.get("job_path") or ""),
                    location=location,
                    remote_type=detect_remote_type(location, j.get("title")),
                    description_md=body or None,
                    # 'July 24, 2026' -- not ISO, so _ts cannot take it.
                    posted_at=_amazon_date(j.get("posted_date")),
                )
            )

        offset += len(batch)
        if offset >= data.get("hits", 0):
            break
        time.sleep(PAGE_PAUSE)
    else:
        _truncated("amazon", country, len(out))
    return out


def _amazon_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return _ts(value)


FETCHERS: dict[str, Callable[[str, httpx.Client], list[RawPosting]]] = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "amazon": fetch_amazon,
}


def make_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True)


def fetch(provider: str, token: str, client: httpx.Client) -> list[RawPosting]:
    try:
        fn = FETCHERS[provider]
    except KeyError:
        raise FetchError(f"no fetcher for provider {provider!r}") from None
    return fn(token, client)

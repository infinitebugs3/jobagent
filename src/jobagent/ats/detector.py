"""Careers URL or company name -> (provider, token).

This is the least glamorous and highest-leverage module in week 1. Everything
downstream is worthless if this cannot resolve companies to their ATS.

Strategy, cheapest first:
  1. The URL already IS an ATS board URL -> parse the token straight out.
  2. Fetch the careers page and look for an embedded ATS host or iframe.
  3. Guess a slug from the company name and probe each provider's endpoint.

Expect ~60-70% resolution on a normal registry. Do not chase the tail; log the
misses to data/unresolved.csv and move on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import httpx

from .fetchers import FetchError, fetch

# Board URLs, parsed directly.
_URL_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([\w.-]+)", re.I)),
    ("lever",      re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)", re.I)),
    ("ashby",      re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w.-]+)", re.I)),
]

# Workday needs three fields, not one, so it cannot use the shared
# (provider, single-capture) shape above. Handled separately in detect().
_WORKDAY_URL = re.compile(
    r"([\w-]+)\.wd(\d+)\.myworkdayjobs\.com/(?:[\w-]+/)?([\w-]+)", re.I
)

# Amazon is one employer rather than a board, so its 'token' is a country
# filter. Read it off the URL when present, otherwise default to India.
_AMAZON_URL = re.compile(r"amazon\.jobs", re.I)
_AMAZON_COUNTRY = re.compile(r"country=([A-Za-z]{3})", re.I)

# Workday wants the country's display name ('India'), not an ISO code, and it
# may arrive url-encoded on a multi-word country.
_COUNTRY = re.compile(r"country=([A-Za-z]+(?:(?:%20|\+)[A-Za-z]+)*)", re.I)


def _country_name(raw: str) -> str:
    return raw.replace("%20", " ").replace("+", " ").strip()

# Same patterns, but searched inside page HTML (script srcs, iframes, links).
_HTML_PATTERNS = _URL_PATTERNS


@dataclass
class Detection:
    provider: str
    token: str
    how: str  # url | html | probe


def _slug_candidates(company_name: str) -> list[str]:
    base = re.sub(r"[^\w\s-]", "", company_name.lower()).strip()
    compact = re.sub(r"[\s_-]+", "", base)
    hyphen = re.sub(r"[\s_]+", "-", base)
    seen, out = set(), []
    for c in (compact, hyphen, base.split(" ")[0] if base else ""):
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _match_patterns(text: str, patterns) -> Optional[tuple[str, str]]:
    # Workday first: its URL contains no other provider's host, but packing
    # three capture groups into one token needs its own branch.
    m = _WORKDAY_URL.search(text)
    if m:
        host, wd, site = m.group(1), m.group(2), m.group(3)
        # An optional ?country=India on the careers URL becomes a fourth token
        # field, so the registry states the filter instead of hiding it in code.
        country = _COUNTRY.search(text)
        token = f"{host}:{site}:{wd}"
        return "workday", f"{token}:{_country_name(country.group(1))}" if country else token


    for provider, pattern in patterns:
        m = pattern.search(text)
        if m:
            token = m.group(1).strip("/.")
            if token and token.lower() not in {"embed", "job_board"}:
                return provider, token
    return None


def detect(
    company_name: str,
    careers_url: str | None,
    client: httpx.Client,
    probe: bool = True,
) -> Optional[Detection]:
    # 1. The URL is already a board URL.
    if careers_url:
        # Checked only against the URL, never against page HTML: 'amazon.jobs'
        # is loose enough that any careers page merely linking to Amazon would
        # otherwise resolve to it.
        if _AMAZON_URL.search(careers_url):
            country = _AMAZON_COUNTRY.search(careers_url)
            return Detection("amazon", country.group(1).upper() if country else "IND", "url")
        hit = _match_patterns(careers_url, _URL_PATTERNS)
        if hit:
            return Detection(hit[0], hit[1], "url")

    # 2. Scan the careers page HTML for an embedded board.
    if careers_url:
        try:
            resp = client.get(careers_url)
            if resp.status_code < 400:
                hit = _match_patterns(resp.text, _HTML_PATTERNS)
                if hit:
                    return Detection(hit[0], hit[1], "html")
        except httpx.HTTPError:
            pass

    # 3. Guess a slug and probe. Cheap: each probe is one small JSON request.
    if probe:
        # Workday and Amazon are absent on purpose: a Workday token is three
        # fields that cannot be guessed from a name, and Amazon is a single
        # employer keyed by country, not a board slug. Both resolve by URL only.
        for token in _slug_candidates(company_name):
            for provider in ("greenhouse", "lever", "ashby", "smartrecruiters"):
                try:
                    postings = fetch(provider, token, client)
                except (FetchError, httpx.HTTPError):
                    continue
                if postings:
                    return Detection(provider, token, "probe")

    return None

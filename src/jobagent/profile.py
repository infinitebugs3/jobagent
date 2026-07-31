"""Load profile.yaml, and compile it into a saved search.

`profile.yaml` has been filled in since day 5 and read by nothing -- pyyaml was
a declared dependency with no `import yaml` anywhere in the tree. This module
closes that gap: it is the bridge from "who I am" to "what the search returns".
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import Profile

DEFAULT_PATH = "profile.yaml"


def load_profile(path: str = DEFAULT_PATH) -> Profile:
    fh = Path(path)
    if not fh.is_file():
        raise FileNotFoundError(
            f"{path} not found -- copy profile.example.yaml and fill it in"
        )
    data = yaml.safe_load(fh.read_text(encoding="utf-8")) or {}
    return Profile(**data)


def seniority_bands(years: float) -> list[str]:
    """Map years of experience onto the bands seniority_of() emits.

    Bands are deliberately wide. A tight band is a great way to filter out the
    job you would actually have taken: titles are noisy, and 'Engineer II' at
    one company is 'Senior' at another.
    """
    if years < 1:
        return ["intern", "junior", "mid"]
    if years < 3:
        return ["junior", "mid"]
    if years < 6:
        return ["mid", "senior"]
    return ["senior", "staff+"]


def to_search_profile(profile: Profile) -> dict:
    """Compile a Profile into the search_profile row shape.

    Note what goes where. Titles and skills become *scored* query terms, since
    they express preference. Locations, seniority and remote become *structured
    filters*, since they express constraint. Mixing the two is what makes a
    search feel arbitrary -- a hard filter that quietly re-ranks, or a
    preference that silently excludes.
    """
    terms = list(dict.fromkeys(profile.titles_wanted + profile.skills))

    locations = list(profile.locations)
    remote_types: list[str] = []
    if profile.remote_ok:
        # 'remote' is both a location bucket and a remote_type; keeping both
        # means a remote job tagged with a city location still matches.
        remote_types = ["remote", "hybrid", "onsite"]
        if "remote" not in locations:
            locations.append("remote")

    filters = {
        "location_bucket": locations,
        "seniority": seniority_bands(profile.years_experience),
    }
    if remote_types:
        filters["remote_type"] = remote_types

    return {
        "name": f"{profile.name} -- default",
        "query_text": ", ".join(terms),
        "exclude_text": ", ".join(profile.titles_excluded),
        "filters": filters,
        "is_default": True,
    }

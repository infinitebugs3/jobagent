"""Request and response shapes.

Verdicts stay `apply | maybe | no` on the wire and in the database, matching the
day-1 check constraint and every existing query. The UI presents them as
"Interested / Maybe / Not for me" -- that is a labelling choice, not a schema
one, and changing the stored vocabulary would break `cmd_stats`, the CLI, and
the README's precision@20 framing for no gain.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Verdict = Literal["apply", "maybe", "no"]
LabelSource = Literal["review", "exploration"]

# Offered as one-click chips in the review UI. Structured reasons cost one extra
# keystroke and are what rung 1 of the personalization ladder counts later --
# free-text `reason` cannot be aggregated.
REASON_TAGS = [
    "great-fit",
    "interesting-domain",
    "good-company",
    "too-senior",
    "too-junior",
    "wrong-stack",
    "wrong-role",
    "bad-location",
    "low-comp",
    "big-corp",
    "vague-jd",
]


class JobRow(BaseModel):
    id: int
    title: str
    company: str
    company_id: int
    location: Optional[str] = None
    location_bucket: Optional[str] = None
    seniority: Optional[str] = None
    remote_type: Optional[str] = None
    provider: Optional[str] = None
    apply_url: str
    dedupe_key: str
    posted_at: Optional[datetime] = None
    first_seen_at: Optional[datetime] = None
    age_days: Optional[int] = None
    has_description: bool = False

    # Years of experience the description asks for, and the sentence it was
    # read from. None means the posting says nothing, which is not zero.
    min_years_experience: Optional[int] = None
    min_years_evidence: Optional[str] = None

    # present only when a search is active
    score: float = 0.0
    rank_score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)

    # label state, if any
    verdict: Optional[Verdict] = None
    reason: Optional[str] = None
    reason_tags: list[str] = Field(default_factory=list)
    label_source: Optional[str] = None


class JobDetail(JobRow):
    description_md: Optional[str] = None
    careers_url: Optional[str] = None


class JobPage(BaseModel):
    items: list[JobRow]
    total: int
    page: int
    page_size: int
    # Roles the experience ceiling removed. Nothing else in this app tells the
    # user what a filter took away, and a filter you cannot see the cost of is
    # one you cannot judge.
    hidden_by_experience: int = 0
    # echoed back so the client can tell what the server actually searched for
    # after synonym expansion -- "why did I get this" starts here
    terms: list[str] = Field(default_factory=list)
    took_ms: int = 0


class FacetValue(BaseModel):
    value: str
    n: int


class Facets(BaseModel):
    company: list[FacetValue] = Field(default_factory=list)
    location_bucket: list[FacetValue] = Field(default_factory=list)
    remote_type: list[FacetValue] = Field(default_factory=list)
    seniority: list[FacetValue] = Field(default_factory=list)
    provider: list[FacetValue] = Field(default_factory=list)
    # One row per stated bar, plus a 'none' row for the postings that state no
    # bar at all. Cumulative rather than selectable one at a time: the client
    # sums buckets up to the slider position, and the 'none' row is added at
    # every position because those roles are never hidden.
    min_years_experience: list[FacetValue] = Field(default_factory=list)


class RoleGroup(BaseModel):
    slug: str
    label: str
    n: int = 0


class LabelIn(BaseModel):
    job_id: int
    verdict: Verdict
    reason: str = ""
    reason_tags: list[str] = Field(default_factory=list)
    source: LabelSource = "review"


class LabelOut(BaseModel):
    job_id: int
    verdict: Verdict
    postings_labeled: int  # a role spans several boards; all of them get it


class SearchProfileIn(BaseModel):
    name: str
    query_text: str = ""
    exclude_text: str = ""
    filters: dict = Field(default_factory=dict)
    is_default: bool = False


class SearchProfileOut(SearchProfileIn):
    id: int


class ProfilePreview(BaseModel):
    """Live feedback while editing a saved search, so you can see a term
    widen or narrow the result set before committing to it."""

    total: int
    terms: list[str]
    sample: list[JobRow]


class ReviewProgress(BaseModel):
    target: int
    labeled: int
    apply: int
    maybe: int
    no: int
    review: int       # profile-ranked picks
    exploration: int  # random picks -- the anti-bubble slice

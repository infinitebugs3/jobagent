"""Normalized shapes. Every ATS fetcher returns RawPosting; nothing else leaks."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

RemoteType = Literal["remote", "hybrid", "onsite", "unknown"]
Provider = Literal[
    "greenhouse", "lever", "ashby", "smartrecruiters", "workday", "amazon", "unknown"
]


class RawPosting(BaseModel):
    """One job as returned by an ATS, already mapped to our vocabulary."""

    external_id: str
    title: str
    apply_url: str
    location: Optional[str] = None
    remote_type: RemoteType = "unknown"
    description_md: Optional[str] = None
    posted_at: Optional[datetime] = None


class CompanySeed(BaseModel):
    """A row of your registry before the ATS has been detected."""

    name: str
    careers_url: Optional[str] = None
    ats_provider: Provider = "unknown"
    ats_token: Optional[str] = None
    size_bucket: Optional[str] = None
    notes: Optional[str] = None


class Profile(BaseModel):
    """Static for week 1. Becomes the dynamic profile store in phase 2."""

    name: str
    headline: str = ""
    years_experience: float = 0
    skills: list[str] = Field(default_factory=list)
    titles_wanted: list[str] = Field(default_factory=list)
    titles_excluded: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool = True
    min_salary: Optional[int] = None
    currency: str = "INR"
    work_authorization: list[str] = Field(default_factory=list)
    resume_text: str = ""

    def as_match_text(self) -> str:
        """Flattened text used for embedding the profile."""
        parts = [
            self.headline,
            " ".join(self.titles_wanted),
            " ".join(self.skills),
            self.resume_text,
        ]
        return "\n".join(p for p in parts if p).strip()

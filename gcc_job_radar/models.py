"""Data models for job postings and ATS metadata."""

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, HttpUrl


class ATSProvider(str, Enum):
    """Supported canonical ATS providers."""

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    SMARTRECRUITERS = "smartrecruiters"
    WORKDAY = "workday"
    PHENOM_SUCCESSFACTORS = "phenom_successfactors"


class JobStatus(str, Enum):
    """Job application tracking status values."""

    NEW = "NEW"
    APPLIED = "APPLIED"
    INTERVIEWING = "INTERVIEWING"
    REJECTED = "REJECTED"
    DISMISSED = "DISMISSED"


class CompanyConfig(BaseModel):
    """Configuration for an ATS job board to scan."""

    name: str
    provider: ATSProvider
    board_token: str
    cluster: Optional[str] = "3"
    extra: Optional[dict[str, Any]] = None


class JobPosting(BaseModel):
    """Normalized job posting structure across all ATS platforms."""

    id: str
    numeric_id: Optional[int] = None
    company: str
    title: str
    location: str
    apply_url: HttpUrl
    published_date: Optional[str] = "Recent"
    provider: ATSProvider
    is_remote: bool = False
    status: str = "NEW"
    applied_at: Optional[str] = None
    notes: Optional[str] = None


# Backward and forward compatible alias
JobOpening = JobPosting


"""Registry of dormant / paused companies.

These companies have valid, verified ATS configurations (Greenhouse, Lever, Ashby, etc.)
but are currently paused or excluded from active monitoring because their current
postings are unsuitable for entry-level / fresher candidates (e.g. consistently
requiring 2-4+ years of experience, experiencing hiring freezes, or not currently
offering junior roles in India).

Preserving them in this registry ensures:
1. They are not actively scanned or sent in live Telegram / webhook alerts.
2. Their validated ATS provider tokens and endpoints are retained and preserved.
3. They can quickly be reactivated or re-evaluated whenever junior pipelines reopen.
"""

from typing import Optional
from pydantic import BaseModel
from gcc_job_radar.models import ATSProvider, CompanyConfig


class DormantCompanyEntry(BaseModel):
    """Metadata container for an inactive or paused company."""

    config: CompanyConfig
    reason: str
    paused_at: str
    notes: Optional[str] = None


# Detailed dormant registry with contextual reasons
DORMANT_REGISTRY: list[DormantCompanyEntry] = [
    DormantCompanyEntry(
        config=CompanyConfig(
            name="Backblaze",
            provider=ATSProvider.GREENHOUSE,
            board_token="backblaze",
        ),
        reason="Experience mismatch: Roles frequently demand 2-4+ years of relevant experience rather than 0-2 entry-level.",
        paused_at="2026-09-04",
        notes="Preserved for future re-evaluation if junior/fresher tracks reopen.",
    ),
]

# Convenient flat list of CompanyConfig for dormant companies
DORMANT_COMPANIES: list[CompanyConfig] = [entry.config for entry in DORMANT_REGISTRY]


def get_dormant_companies() -> list[CompanyConfig]:
    """Return all currently dormant company configurations."""
    return DORMANT_COMPANIES


def is_dormant_company(name: str) -> bool:
    """Check if a company name is registered in the dormant registry."""
    normalized = name.strip().lower()
    return any(c.name.strip().lower() == normalized for c in DORMANT_COMPANIES)

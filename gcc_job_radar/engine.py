"""Async aggregation and scanning engine for GCC Job Radar."""

import asyncio
import logging
from typing import Callable, Optional
import httpx

from gcc_job_radar.clients.ashby import AshbyClient
from gcc_job_radar.clients.greenhouse import GreenhouseClient
from gcc_job_radar.clients.lever import LeverClient
from gcc_job_radar.clients.phenom_successfactors import PhenomSuccessFactorsClient
from gcc_job_radar.clients.smartrecruiters import SmartRecruitersClient
from gcc_job_radar.clients.workday import WorkdayClient
from gcc_job_radar.config import COMPANIES
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 gcc-job-radar/0.1.0"


async def fetch_single_company(company: CompanyConfig, client: httpx.AsyncClient) -> list[JobPosting]:
    """Route company to appropriate ATS client and fetch filtered postings."""
    if company.provider == ATSProvider.GREENHOUSE:
        ats_client = GreenhouseClient(client)
    elif company.provider == ATSProvider.LEVER:
        ats_client = LeverClient(client)
    elif company.provider == ATSProvider.ASHBY:
        ats_client = AshbyClient(client)
    elif company.provider == ATSProvider.SMARTRECRUITERS:
        ats_client = SmartRecruitersClient(client)
    elif company.provider == ATSProvider.WORKDAY:
        ats_client = WorkdayClient(client)
    elif company.provider == ATSProvider.PHENOM_SUCCESSFACTORS:
        ats_client = PhenomSuccessFactorsClient(client)
    else:
        logger.warning("Unsupported ATS provider: %s", company.provider)
        return []

    return await ats_client.fetch_jobs(company)


async def scan_all_companies(
    companies: list[CompanyConfig] = COMPANIES,
    concurrency: int = 15,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> list[JobPosting]:
    """Scan all configured companies concurrently with rate limiting and deduplication."""
    semaphore = asyncio.Semaphore(concurrency)
    total_companies = len(companies)
    completed_count = 0

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }

    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=True,
    ) as client:

        async def worker(company: CompanyConfig) -> list[JobPosting]:
            nonlocal completed_count
            async with semaphore:
                try:
                    results = await fetch_single_company(company, client)
                except Exception as exc:
                    logger.debug("Scan error for %s: %s", company.name, exc)
                    results = []
                finally:
                    completed_count += 1
                    if on_progress:
                        on_progress(company.name, completed_count, total_companies)
                return results

        tasks = [worker(company) for company in companies]
        gathered_results = await asyncio.gather(*tasks, return_exceptions=True)

    all_postings: list[JobPosting] = []
    seen_keys: set[tuple[str, str]] = set()

    for item in gathered_results:
        if isinstance(item, list):
            for post in item:
                key = (post.company.lower(), str(post.id).lower())
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_postings.append(post)

    # Sort postings by published_date descending (newest first, 'Active' or unknown at the end)
    def sort_key(p: JobPosting) -> tuple[int, str]:
        date = p.published_date or ""
        if date and date != "Active" and date != "Recent":
            return (0, date)
        return (1, "")

    all_postings.sort(key=sort_key, reverse=True)
    return all_postings

"""Phenom People & SAP SuccessFactors public career API client."""

import logging
from typing import Any
import httpx
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient
from gcc_job_radar.filters import matches_india_location, matches_target_title
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)


class PhenomSuccessFactorsClient(BaseATSClient):
    """Phenom / SAP SuccessFactors public job search integration."""

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        postings: list[JobPosting] = []

        domain = company.board_token.strip().rstrip("/")
        if not domain.startswith("http"):
            base_url = f"https://{domain}"
        else:
            base_url = domain

        # Primary Phenom / public job search API endpoints
        endpoints = [
            f"{base_url}/api/v1/jobs",
            f"{base_url}/services/jobs",
            f"{base_url}/api/jobs",
        ]

        for endpoint in endpoints:
            try:
                response = await self.client.get(
                    endpoint,
                    params={"country": "India", "limit": 100},
                    headers={"Accept": "application/json"},
                )
                if response.status_code != 200:
                    continue

                data: Any = response.json()
                jobs = []
                if isinstance(data, list):
                    jobs = data
                elif isinstance(data, dict):
                    jobs = data.get("jobs") or data.get("content") or data.get("data") or []

                if not jobs:
                    continue

                for job in jobs:
                    title = job.get("title") or job.get("name") or job.get("jobTitle") or ""
                    
                    # Extract location
                    city = job.get("city") or ""
                    country = job.get("country") or ""
                    loc_raw = job.get("location") or ""
                    location_str = f"{city} {country} {loc_raw}".strip()

                    if not matches_target_title(title):
                        continue

                    if not (matches_india_location(loc_raw) or matches_india_location(location_str)):
                        continue

                    job_id = str(job.get("id") or job.get("jobId") or job.get("reqId") or "")
                    if not job_id:
                        continue

                    apply_url = (
                        job.get("applyUrl")
                        or job.get("url")
                        or job.get("canonicalUrl")
                        or f"{base_url}/job/{job_id}"
                    )

                    date_posted = (
                        job.get("datePosted")
                        or job.get("postedDate")
                        or job.get("createdDate")
                        or "Active"
                    )
                    published_date = str(date_posted)[:10] if date_posted else "Active"

                    display_loc = loc_raw or (f"{city}, {country}".strip(", ")) or "India"

                    try:
                        postings.append(
                            JobPosting(
                                id=job_id,
                                company=company.name,
                                title=title.strip(),
                                location=display_loc.strip(),
                                apply_url=apply_url,
                                published_date=published_date,
                                provider=ATSProvider.PHENOM_SUCCESSFACTORS,
                            )
                        )
                    except ValidationError as e:
                        logger.debug("Validation error parsing Phenom job %s: %s", job_id, e)

                # If successful on an endpoint, stop checking fallbacks
                if postings:
                    break

            except (httpx.HTTPError, Exception) as e:
                logger.debug("Error querying endpoint %s for %s: %s", endpoint, company.name, e)

        return postings

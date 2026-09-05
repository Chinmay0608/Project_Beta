"""SmartRecruiters ATS API client."""

import logging
from typing import Any
import httpx
import orjson
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient
from gcc_job_radar.filters import matches_india_location, matches_target_title
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)


class SmartRecruitersClient(BaseATSClient):
    """SmartRecruiters public postings API integration."""

    BASE_URL = "https://api.smartrecruiters.com/v1/companies/{board_token}/postings"

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        url = self.BASE_URL.format(board_token=company.board_token)
        postings: list[JobPosting] = []

        try:
            # Request up to 100 recent postings
            response = await self.client.get(url, params={"limit": 100}, timeout=self.timeout)
            if response.status_code != 200:
                logger.debug("SmartRecruiters board %s returned status %s", company.board_token, response.status_code)
                return postings

            data: dict[str, Any] = orjson.loads(response.content)
            jobs = data.get("content", [])

            for job in jobs:
                title = job.get("name") or ""
                loc_data = job.get("location") or {}
                city = loc_data.get("city") or ""
                region = loc_data.get("region") or ""
                country = loc_data.get("country") or ""

                location_parts = [p for p in [city, region, country] if p]
                full_location = ", ".join(location_parts) if location_parts else "India"

                if not matches_target_title(title):
                    continue

                if not (matches_india_location(city) or matches_india_location(full_location)):
                    continue

                apply_url = job.get("ref")
                if not apply_url:
                    job_id = job.get("id")
                    if job_id:
                        apply_url = f"https://jobs.smartrecruiters.com/{company.board_token}/{job_id}"
                    else:
                        continue

                released_date = job.get("releasedDate")
                published_date = released_date[:10] if released_date else "Active"

                try:
                    postings.append(
                        JobPosting(
                            id=str(job.get("id")),
                            company=company.name,
                            title=title.strip(),
                            location=full_location.strip(),
                            apply_url=apply_url,
                            published_date=published_date,
                            provider=ATSProvider.SMARTRECRUITERS,
                        )
                    )
                except ValidationError as e:
                    logger.debug("Validation error parsing SmartRecruiters job %s: %s", job.get("id"), e)

        except (httpx.TimeoutException, httpx.HTTPError, Exception) as e:
            logger.debug("Error fetching SmartRecruiters jobs for %s: %s", company.name, e)

        return postings

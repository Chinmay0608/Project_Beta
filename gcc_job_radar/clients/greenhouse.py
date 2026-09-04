"""Greenhouse ATS API client."""

import logging
from typing import Any
import httpx
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient
from gcc_job_radar.filters import (
    matches_india_location,
    matches_target_title,
    requires_experienced_candidate,
)
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)


class GreenhouseClient(BaseATSClient):
    """Greenhouse job boards API integration."""

    BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        url = self.BASE_URL.format(board_token=company.board_token)
        postings: list[JobPosting] = []

        try:
            response = await self.client.get(url)
            if response.status_code != 200:
                logger.debug("Greenhouse board %s returned status %s", company.board_token, response.status_code)
                return postings

            data: dict[str, Any] = response.json()
            jobs = data.get("jobs", [])

            for job in jobs:
                title = job.get("title") or ""
                location = (job.get("location") or {}).get("name") or ""

                if not matches_target_title(title):
                    continue

                if not matches_india_location(location):
                    continue

                # Disqualify roles requiring 3+ years experience
                content = job.get("content") or ""
                if requires_experienced_candidate(content):
                    continue

                apply_url = job.get("absolute_url")
                if not apply_url:
                    continue

                updated_at = job.get("updated_at")
                published_date = updated_at[:10] if updated_at else "Active"

                try:
                    postings.append(
                        JobPosting(
                            id=str(job.get("id")),
                            company=company.name,
                            title=title.strip(),
                            location=location.strip(),
                            apply_url=apply_url,
                            published_date=published_date,
                            provider=ATSProvider.GREENHOUSE,
                        )
                    )
                except ValidationError as e:
                    logger.debug("Validation error parsing Greenhouse job %s: %s", job.get("id"), e)

        except (httpx.HTTPError, Exception) as e:
            logger.debug("Error fetching Greenhouse jobs for %s: %s", company.name, e)

        return postings

"""Ashby ATS API client."""

import logging
from typing import Any
import httpx
import orjson
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient
from gcc_job_radar.filters import (
    matches_india_location,
    matches_target_title,
    requires_experienced_candidate,
)
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)


class AshbyClient(BaseATSClient):
    """Ashby job board API integration."""

    BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{board_token}"

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        url = self.BASE_URL.format(board_token=company.board_token)
        postings: list[JobPosting] = []

        try:
            response = await self.client.get(url, timeout=self.timeout)
            if response.status_code != 200:
                logger.debug("Ashby board %s returned status %s", company.board_token, response.status_code)
                return postings

            data: dict[str, Any] = orjson.loads(response.content)
            jobs = data.get("jobs", [])

            for job in jobs:
                title = job.get("title") or ""
                location = job.get("location") or ""
                secondary_locations = [
                    sec.get("location") for sec in job.get("secondaryLocations", []) if isinstance(sec, dict)
                ]
                combined_location = ", ".join([loc for loc in [location] + secondary_locations if loc])

                # Check postal address if available
                address = job.get("address", {}) or {}
                postal_address = address.get("postalAddress", {}) or {}
                address_locality = postal_address.get("addressLocality") or ""
                address_country = postal_address.get("addressCountry") or ""
                if address_locality or address_country:
                    combined_location = f"{combined_location}, {address_locality}, {address_country}".strip(", ")

                if not matches_target_title(title):
                    continue

                if not (matches_india_location(location) or matches_india_location(combined_location)):
                    continue

                # Disqualify roles requiring 3+ years experience
                content = job.get("descriptionPlain") or job.get("descriptionHtml") or ""
                if requires_experienced_candidate(content):
                    continue

                apply_url = job.get("jobUrl")
                if not apply_url:
                    continue

                published_at = job.get("publishedAt")
                published_date = published_at[:10] if published_at else "Active"

                display_location = location or address_locality or "India"

                try:
                    postings.append(
                        JobPosting(
                            id=str(job.get("id")),
                            company=company.name,
                            title=title.strip(),
                            location=display_location.strip(),
                            apply_url=apply_url,
                            published_date=published_date,
                            provider=ATSProvider.ASHBY,
                        )
                    )
                except ValidationError as e:
                    logger.debug("Validation error parsing Ashby job %s: %s", job.get("id"), e)

        except (httpx.TimeoutException, httpx.HTTPError, Exception) as e:
            logger.debug("Error fetching Ashby jobs for %s: %s", company.name, e)

        return postings

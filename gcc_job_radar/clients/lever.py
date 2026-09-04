"""Lever ATS API client."""

from datetime import datetime, timezone
import logging
from typing import Any
import httpx
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient
from gcc_job_radar.filters import matches_india_location, matches_target_title
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)


class LeverClient(BaseATSClient):
    """Lever job postings API integration."""

    BASE_URL = "https://api.lever.co/v0/postings/{board_token}?mode=json"

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        url = self.BASE_URL.format(board_token=company.board_token)
        postings: list[JobPosting] = []

        try:
            response = await self.client.get(url)
            if response.status_code != 200:
                logger.debug("Lever board %s returned status %s", company.board_token, response.status_code)
                return postings

            jobs: list[dict[str, Any]] = response.json()
            if not isinstance(jobs, list):
                return postings

            for job in jobs:
                title = job.get("text") or ""
                categories = job.get("categories") or {}
                location = categories.get("location") or ""
                all_locations = categories.get("allLocations") or []

                # Combine locations for flexible multi-city/remote matching
                full_location_str = ", ".join([loc for loc in [location] + all_locations if loc])
                workplace_type = job.get("workplaceType") or ""
                if workplace_type:
                    full_location_str = f"{full_location_str} ({workplace_type})".strip()

                if not matches_target_title(title):
                    continue

                if not (matches_india_location(location) or matches_india_location(full_location_str)):
                    continue

                apply_url = job.get("hostedUrl") or job.get("applyUrl")
                if not apply_url:
                    continue

                created_at = job.get("createdAt")
                if created_at and isinstance(created_at, (int, float)):
                    published_date = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                else:
                    published_date = "Active"

                display_location = location or full_location_str or "India"

                try:
                    postings.append(
                        JobPosting(
                            id=str(job.get("id")),
                            company=company.name,
                            title=title.strip(),
                            location=display_location.strip(),
                            apply_url=apply_url,
                            published_date=published_date,
                            provider=ATSProvider.LEVER,
                        )
                    )
                except ValidationError as e:
                    logger.debug("Validation error parsing Lever job %s: %s", job.get("id"), e)

        except (httpx.HTTPError, Exception) as e:
            logger.debug("Error fetching Lever jobs for %s: %s", company.name, e)

        return postings

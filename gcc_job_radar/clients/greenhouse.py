"""Greenhouse ATS API client."""

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


class GreenhouseClient(BaseATSClient):
    """Greenhouse job boards API integration."""

    BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    JOB_DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}"

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        url = self.BASE_URL.format(board_token=company.board_token)
        postings: list[JobPosting] = []

        try:
            # Pass 1: Fetch board job listing (lightweight metadata)
            response = await self.client.get(url, timeout=self.timeout)
            if response.status_code != 200:
                logger.debug("Greenhouse board %s returned status %s", company.board_token, response.status_code)
                return postings

            data = orjson.loads(response.content)
            jobs = data.get("jobs", [])

            for job in jobs:
                title = job.get("title") or ""
                location = (job.get("location") or {}).get("name") or ""

                if not matches_target_title(title):
                    continue

                if not matches_india_location(location):
                    continue

                job_id = str(job.get("id"))

                # Pass 2: Inspect content if candidate passed title and location filters
                content = job.get("content")
                if content is None:
                    detail_url = self.JOB_DETAIL_URL.format(board_token=company.board_token, job_id=job_id)
                    try:
                        detail_resp = await self.client.get(detail_url, timeout=self.timeout)
                        if detail_resp.status_code == 200:
                            detail_data = orjson.loads(detail_resp.content)
                            content = detail_data.get("content") or ""
                        else:
                            content = ""
                    except Exception as exc:
                        logger.debug("Error querying Greenhouse job detail for %s: %s", job_id, exc)
                        content = ""

                # Disqualify roles requiring 3+ years experience
                if requires_experienced_candidate(content or ""):
                    continue

                apply_url = job.get("absolute_url")
                if not apply_url:
                    continue

                updated_at = job.get("updated_at")
                published_date = updated_at[:10] if updated_at else "Active"

                try:
                    postings.append(
                        JobPosting(
                            id=job_id,
                            company=company.name,
                            title=title.strip(),
                            location=location.strip(),
                            apply_url=apply_url,
                            published_date=published_date,
                            provider=ATSProvider.GREENHOUSE,
                        )
                    )
                except ValidationError as e:
                    logger.debug("Validation error parsing Greenhouse job %s: %s", job_id, e)

        except (httpx.TimeoutException, httpx.HTTPError, Exception) as e:
            logger.debug("Error fetching Greenhouse jobs for %s: %s", company.name, e)

        return postings

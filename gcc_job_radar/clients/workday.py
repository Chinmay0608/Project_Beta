"""Workday ATS API client (CXS API)."""

import logging
import re
from typing import Any
import httpx
import orjson
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient
from gcc_job_radar.filters import matches_india_location, matches_target_title
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)


class WorkdayClient(BaseATSClient):
    """Workday public CXS jobs API integration."""

    # Base format: https://{tenant}.wd{cluster}.myworkdayjobs.com/wday/cxs/{tenant}/{site_id}/jobs
    BASE_URL = "https://{tenant}.wd{cluster}.myworkdayjobs.com/wday/cxs/{tenant}/{site_id}/jobs"
    APPLY_BASE_URL = "https://{tenant}.wd{cluster}.myworkdayjobs.com/en-US/{site_id}{external_path}"

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        postings: list[JobPosting] = []

        parts = company.board_token.split("/")
        if len(parts) != 2:
            logger.warning(
                "Invalid Workday board_token '%s' for %s. Expected format 'tenant/site_id'",
                company.board_token,
                company.name,
            )
            return postings

        tenant, site_id = parts[0].strip(), parts[1].strip()
        cluster = company.cluster or "3"

        url = self.BASE_URL.format(tenant=tenant, cluster=cluster, site_id=site_id)
        payload = {
            "appliedFacets": {},
            "limit": 20,
            "offset": 0,
            "searchText": "",
        }

        try:
            response = await self.client.post(
                url,
                json=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
            if response.status_code != 200:
                logger.debug("Workday board %s returned status %s", company.board_token, response.status_code)
                return postings

            data: dict[str, Any] = orjson.loads(response.content)
            jobs = data.get("jobPostings", [])

            for job in jobs:
                title = job.get("title") or ""
                location = job.get("locationsText") or ""

                # Additional locations in bulletFields if present
                bullet_fields = job.get("bulletFields") or []
                full_location = f"{location} {' '.join(bullet_fields)}".strip()

                if not matches_target_title(title):
                    continue

                if not (matches_india_location(location) or matches_india_location(full_location)):
                    continue

                external_path = job.get("externalPath") or ""
                if not external_path:
                    continue

                apply_url = self.APPLY_BASE_URL.format(
                    tenant=tenant,
                    cluster=cluster,
                    site_id=site_id,
                    external_path=external_path,
                )

                # Extract ID from externalPath (e.g. /job/Bengaluru-India/Software-Engineer-1_JR12345 -> JR12345)
                job_id = external_path.split("_")[-1] if "_" in external_path else external_path.strip("/")

                posted_on = job.get("postedOn") or "Active"
                # Strip 'Posted' or 'Posted Today' prefix if needed
                published_date = posted_on

                try:
                    postings.append(
                        JobPosting(
                            id=job_id,
                            company=company.name,
                            title=title.strip(),
                            location=location.strip() or "India",
                            apply_url=apply_url,
                            published_date=published_date,
                            provider=ATSProvider.WORKDAY,
                        )
                    )
                except ValidationError as e:
                    logger.debug("Validation error parsing Workday job %s: %s", job_id, e)

        except (httpx.TimeoutException, httpx.HTTPError, Exception) as e:
            logger.debug("Error fetching Workday jobs for %s: %s", company.name, e)

        return postings

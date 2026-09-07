"""Workday ATS API client (CXS API)."""

import asyncio
import logging
from typing import Any, Optional
import httpx
import orjson
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient, DEFAULT_TIMEOUT
from gcc_job_radar.filters import matches_india_location, matches_target_title
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)


def resolve_workday_domain(tenant: str, host: str) -> str:
    """Resolve full domain for a Workday tenant and host/cluster identifier.

    Handles cluster numerals (e.g. '5', 'wd5'), full domains ('wd5.myworkdayjobs.com'),
    and existing full hostnames ('tenant.wd5.myworkdayjobs.com').
    """
    h = host.strip()
    if h.startswith("http://") or h.startswith("https://"):
        parsed = httpx.URL(h)
        return parsed.host or f"{tenant}.wd3.myworkdayjobs.com"
    if "myworkdayjobs.com" in h:
        if h.startswith(f"{tenant}."):
            return h
        return f"{tenant}.{h}"
    cluster = h if h.startswith("wd") else f"wd{h}"
    return f"{tenant}.{cluster}.myworkdayjobs.com"


async def fetch_workday_jobs(
    tenant: str,
    host: str,
    site: str,
    client: httpx.AsyncClient,
    company_name: Optional[str] = None,
    search_text: str = "",
    applied_facets: Optional[dict[str, Any]] = None,
    limit: int = 20,
    offset: int = 0,
    timeout: Optional[float] = None,
) -> list[JobPosting]:
    """Fetch candidate postings from a Workday CXS search endpoint.

    POST https://{domain}/wday/cxs/{tenant}/{site}/jobs
    Candidate apply URL: https://{domain}/en-US/{site}{externalPath}
    """
    postings: list[JobPosting] = []
    clean_tenant = tenant.strip()
    clean_site = site.strip()
    domain = resolve_workday_domain(clean_tenant, host)
    display_name = company_name or clean_tenant.capitalize()

    url = f"https://{domain}/wday/cxs/{clean_tenant}/{clean_site}/jobs"
    payload = {
        "appliedFacets": applied_facets or {},
        "limit": max(1, limit),
        "offset": max(0, offset),
        "searchText": search_text or "",
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    }

    req_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

    for attempt in range(2):
        try:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
                timeout=req_timeout,
            )

            # Handle 429 rate limits gracefully with backoff
            if response.status_code == 429:
                if attempt == 0:
                    retry_after = response.headers.get("Retry-After")
                    sleep_sec = float(retry_after) if retry_after and retry_after.isdigit() else 2.0
                    logger.warning("Workday CXS rate limited for %s (%s). Retrying in %.1fs...", display_name, domain, sleep_sec)
                    await asyncio.sleep(sleep_sec)
                    continue
                logger.warning("Workday CXS rate limit exceeded for %s (%s)", display_name, domain)
                return postings

            if response.status_code != 200:
                logger.debug("Workday CXS board %s returned HTTP %s", url, response.status_code)
                return postings

            data: dict[str, Any] = orjson.loads(response.content)
            jobs = data.get("jobPostings", [])

            for job in jobs:
                title = job.get("title") or ""
                location = job.get("locationsText") or ""
                bullet_fields = job.get("bulletFields") or []
                full_location = f"{location} {' '.join(bullet_fields)}".strip()

                if not matches_target_title(title):
                    continue

                if not (matches_india_location(location) or matches_india_location(full_location)):
                    continue

                external_path = job.get("externalPath") or ""
                if not external_path:
                    continue

                apply_url = f"https://{domain}/en-US/{clean_site}{external_path}"
                job_id = external_path.split("_")[-1] if "_" in external_path else external_path.strip("/")
                posted_on = job.get("postedOn") or "Active"

                try:
                    postings.append(
                        JobPosting(
                            id=job_id,
                            company=display_name,
                            title=title.strip(),
                            location=location.strip() or "India",
                            apply_url=apply_url,
                            published_date=posted_on,
                            provider=ATSProvider.WORKDAY,
                        )
                    )
                except ValidationError as e:
                    logger.debug("Validation error parsing Workday job %s: %s", job_id, e)

            break  # Success, exit retry loop

        except (httpx.TimeoutException, httpx.HTTPError, Exception) as e:
            logger.debug("Error fetching Workday jobs for %s (%s): %s", display_name, domain, e)
            break

    return postings


class WorkdayClient(BaseATSClient):
    """Workday public CXS jobs API integration."""

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        parts = company.board_token.split("/")
        if len(parts) != 2:
            logger.warning(
                "Invalid Workday board_token '%s' for %s. Expected format 'tenant/site_id'",
                company.board_token,
                company.name,
            )
            return []

        tenant, site_id = parts[0].strip(), parts[1].strip()
        host = company.cluster or "3"
        extra = company.extra or {}
        search_text = extra.get("searchText", "")
        applied_facets = extra.get("appliedFacets")

        return await fetch_workday_jobs(
            tenant=tenant,
            host=host,
            site=site_id,
            client=self.client,
            company_name=company.name,
            search_text=search_text,
            applied_facets=applied_facets,
            timeout=self.timeout,
        )

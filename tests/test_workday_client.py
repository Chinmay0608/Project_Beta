"""Unit tests for Workday ATS CXS client and ingestion engine."""

import httpx
import pytest

from gcc_job_radar.clients.workday import (
    WorkdayClient,
    fetch_workday_jobs,
    resolve_workday_domain,
)
from gcc_job_radar.models import ATSProvider, CompanyConfig


def test_resolve_workday_domain() -> None:
    """Verify host and domain resolution across cluster numerals and domain variants."""
    assert resolve_workday_domain("walmart", "5") == "walmart.wd5.myworkdayjobs.com"
    assert resolve_workday_domain("walmart", "wd5") == "walmart.wd5.myworkdayjobs.com"
    assert resolve_workday_domain("adobe", "wd5.myworkdayjobs.com") == "adobe.wd5.myworkdayjobs.com"
    assert resolve_workday_domain("nvidia", "nvidia.wd5.myworkdayjobs.com") == "nvidia.wd5.myworkdayjobs.com"
    assert resolve_workday_domain("target", "https://target.wd5.myworkdayjobs.com") == "target.wd5.myworkdayjobs.com"


@pytest.mark.asyncio
async def test_fetch_workday_jobs_success() -> None:
    """Verify parsing of jobPostings, URL formatting, and India location filtering."""
    cxs_payload = {
        "total": 3,
        "jobPostings": [
            {
                "title": "Associate Software Engineer",
                "locationsText": "Bengaluru, Karnataka, India",
                "externalPath": "/job/Bengaluru-India/Associate-Software-Engineer_JR1001",
                "postedOn": "Posted Today",
            },
            {
                "title": "Software Development Engineer 1",
                "locationsText": "Hyderabad, Telangana",
                "bulletFields": ["India"],
                "externalPath": "/job/Hyderabad/SDE-1_JR1002",
                "postedOn": "Posted 2 Days Ago",
            },
            # Non-India posting should be filtered out
            {
                "title": "Associate Software Engineer",
                "locationsText": "San Jose, CA, USA",
                "externalPath": "/job/San-Jose/Associate-Software-Engineer_JR1003",
                "postedOn": "Posted Today",
            },
            # Senior role should be filtered out
            {
                "title": "Senior Software Engineer",
                "locationsText": "Bengaluru, India",
                "externalPath": "/job/Bengaluru/Senior-SWE_JR1004",
                "postedOn": "Posted Today",
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "wday/cxs/walmart/WalmartExternal/jobs" in str(request.url)
        return httpx.Response(200, json=cxs_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await fetch_workday_jobs(
            tenant="walmart",
            host="5",
            site="WalmartExternal",
            client=client,
            company_name="Walmart Global Tech",
        )

    assert len(jobs) == 2
    assert jobs[0].id == "JR1001"
    assert jobs[0].company == "Walmart Global Tech"
    assert jobs[0].title == "Associate Software Engineer"
    assert jobs[0].location == "Bengaluru, Karnataka, India"
    assert str(jobs[0].apply_url) == "https://walmart.wd5.myworkdayjobs.com/en-US/WalmartExternal/job/Bengaluru-India/Associate-Software-Engineer_JR1001"
    assert jobs[0].provider == ATSProvider.WORKDAY

    assert jobs[1].id == "JR1002"
    assert jobs[1].title == "Software Development Engineer 1"
    assert str(jobs[1].apply_url) == "https://walmart.wd5.myworkdayjobs.com/en-US/WalmartExternal/job/Hyderabad/SDE-1_JR1002"


@pytest.mark.asyncio
async def test_workday_client_fetch_jobs_integration() -> None:
    """Verify WorkdayClient.fetch_jobs integration with CompanyConfig."""
    company = CompanyConfig(
        name="Target",
        provider=ATSProvider.WORKDAY,
        board_token="target/TargetCareers",
        cluster="5",
    )

    cxs_payload = {
        "total": 1,
        "jobPostings": [
            {
                "title": "Engineer 1 - Software",
                "locationsText": "Bengaluru, India",
                "externalPath": "/job/Bengaluru/Engineer-1_R0001",
                "postedOn": "Active",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=cxs_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        workday_client = WorkdayClient(client)
        jobs = await workday_client.fetch_jobs(company)

    assert len(jobs) == 1
    assert jobs[0].id == "R0001"
    assert jobs[0].company == "Target"
    assert jobs[0].title == "Engineer 1 - Software"


@pytest.mark.asyncio
async def test_workday_client_invalid_board_token() -> None:
    """Verify invalid board_token without slash returns empty list gracefully."""
    company = CompanyConfig(
        name="Invalid Co",
        provider=ATSProvider.WORKDAY,
        board_token="invalid_token_without_slash",
    )

    async with httpx.AsyncClient() as client:
        workday_client = WorkdayClient(client)
        jobs = await workday_client.fetch_jobs(company)

    assert jobs == []


@pytest.mark.asyncio
async def test_fetch_workday_jobs_rate_limit_retry() -> None:
    """Verify HTTP 429 rate limit is retried gracefully with backoff."""
    attempt = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(
            200,
            json={
                "jobPostings": [
                    {
                        "title": "Graduate Technical Intern",
                        "locationsText": "Pune, India",
                        "externalPath": "/job/Pune/Intern_JR99",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await fetch_workday_jobs(
            tenant="micron",
            host="5",
            site="micron_careers",
            client=client,
        )

    assert attempt == 2
    assert len(jobs) == 1
    assert jobs[0].id == "JR99"


@pytest.mark.asyncio
async def test_fetch_workday_jobs_server_error_and_timeout() -> None:
    """Verify HTTP 500 and network timeouts return empty list without crashing."""
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(error_handler)) as client:
        jobs = await fetch_workday_jobs(
            tenant="adobe",
            host="5",
            site="external_experienced",
            client=client,
        )
    assert jobs == []

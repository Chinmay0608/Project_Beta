""""Unit tests for Microsoft Careers ATS client and scanner integration."""

import httpx
import pytest

from gcc_job_radar.clients.microsoft import MicrosoftClient
from gcc_job_radar.models import ATSProvider, CompanyConfig
from gcc_job_radar.scanner import DEFAULT_DOMAIN_LIMITS, get_company_domain


@pytest.mark.asyncio
async def test_microsoft_client_success() -> None:
    """Test Microsoft client parses Eightfold payload, filters tech & India jobs, and normalizes output."""
    payload = {
        "status": 200,
        "data": {
            "count": 4,
            "positions": [
                {
                    "id": "1970393556911730",
                    "atsJobId": "200044444",
                    "name": "Software Engineering INTERN",
                    "locations": ["India, Karnataka, Bangalore"],
                    "standardizedLocations": ["Bengaluru, KA, IN"],
                    "positionUrl": "/careers/job/1970393556911730",
                    "postedTs": 1788922261,  # 2026-09-09
                    "department": "Software Engineering",
                },
                {
                    "id": "1970393556911731",
                    "atsJobId": "200044445",
                    "name": "Principal Software Engineering Manager",  # Disqualified by title
                    "locations": ["India, Telangana, Hyderabad"],
                    "standardizedLocations": ["Hyderabad, TS, IN"],
                    "positionUrl": "/careers/job/1970393556911731",
                    "postedTs": 1788922261,
                    "department": "Software Engineering",
                },
                {
                    "id": "1970393556911732",
                    "atsJobId": "200044446",
                    "name": "Software Engineer",
                    "locations": ["Redmond, WA, United States"],  # Disqualified by location
                    "standardizedLocations": ["Redmond, WA, US"],
                    "positionUrl": "/careers/job/1970393556911732",
                    "postedTs": 1788922261,
                    "department": "Software Engineering",
                },
                {
                    "id": "1970393556911733",
                    "atsJobId": "200044447",
                    "name": "Senior Software Engineer",  # Disqualified by title/seniority
                    "locations": ["India, Karnataka, Bangalore"],
                    "standardizedLocations": ["Bengaluru, KA, IN"],
                    "positionUrl": "/careers/job/1970393556911733",
                    "postedTs": 1788922261,
                    "department": "Software Engineering",
                },
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "microsoft.eightfold.ai/api/pcsx/search" in str(request.url)
        assert "domain=microsoft.com" in str(request.url)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(
            name="Microsoft",
            provider=ATSProvider.MICROSOFT,
            board_token="microsoft",
            extra={"max_pages": 1},
        )
        jobs = await MicrosoftClient(client).fetch_jobs(company)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.id == "1970393556911730"
        assert job.company == "Microsoft"
        assert job.title == "Software Engineering INTERN"
        assert "Bengaluru" in job.location
        assert str(job.apply_url) == "https://apply.careers.microsoft.com/careers/job/1970393556911730"
        assert job.published_date == "2026-09-09"
        assert job.provider == ATSProvider.MICROSOFT


@pytest.mark.asyncio
async def test_microsoft_client_pagination() -> None:
    """Test Microsoft client pagination traverses offset properly."""
    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "start=0" in url_str:
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "data": {
                        "count": 40,
                        "positions": [
                            {
                                "id": "101",
                                "name": "Software Engineer 1",
                                "standardizedLocations": ["Bengaluru, KA, IN"],
                                "positionUrl": "/careers/job/101",
                                "postedTs": 1788922261,
                            }
                        ],
                    },
                },
            )
        elif "start=20" in url_str:
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "data": {
                        "count": 40,
                        "positions": [
                            {
                                "id": "102",
                                "name": "Software Engineering INTERN",
                                "standardizedLocations": ["Hyderabad, TS, IN"],
                                "positionUrl": "/careers/job/102",
                                "postedTs": 1788922261,
                            }
                        ],
                    },
                },
            )
        return httpx.Response(200, json={"status": 200, "data": {"count": 40, "positions": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(
            name="Microsoft",
            provider=ATSProvider.MICROSOFT,
            board_token="microsoft",
            extra={"max_pages": 2, "page_size": 20},
        )
        jobs = await MicrosoftClient(client).fetch_jobs(company)
        assert len(jobs) == 2
        assert {j.id for j in jobs} == {"101", "102"}


@pytest.mark.asyncio
async def test_microsoft_client_http_error() -> None:
    """Test Microsoft client handles HTTP failures gracefully."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(
            name="Microsoft",
            provider=ATSProvider.MICROSOFT,
            board_token="microsoft",
        )
        jobs = await MicrosoftClient(client).fetch_jobs(company)
        assert jobs == []


def test_microsoft_scanner_domain_and_rate_limits() -> None:
    """Test scanner correctly resolves Microsoft host and registers rate limits."""
    company = CompanyConfig(
        name="Microsoft",
        provider=ATSProvider.MICROSOFT,
        board_token="microsoft",
    )
    domain = get_company_domain(company)
    assert domain == "microsoft.eightfold.ai"
    assert "microsoft.eightfold.ai" in DEFAULT_DOMAIN_LIMITS

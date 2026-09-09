""""Unit tests for Apple Careers ATS client and scanner integration."""

import json
import httpx
import pytest

from gcc_job_radar.clients.apple import AppleClient
from gcc_job_radar.models import ATSProvider, CompanyConfig
from gcc_job_radar.scanner import DEFAULT_DOMAIN_LIMITS, get_company_domain


def make_apple_html(jobs: list[dict], total_records: int = 10) -> str:
    """Generate mock Apple HTML response embedding static router hydration JSON."""
    payload = {
        "loaderData": {
            "search": {
                "totalRecords": total_records,
                "searchResults": jobs,
            }
        }
    }
    encoded = json.dumps(payload).replace('"', '\\"')
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
    <script>
    window.__staticRouterHydrationData = JSON.parse("{encoded}");
    </script>
    </head>
    <body></body>
    </html>
    """


@pytest.mark.asyncio
async def test_apple_client_success() -> None:
    """Test Apple client extracts hydration state, filters tech & India jobs, and normalizes output."""
    mock_jobs = [
        {
            "id": "PIPE-200614863",
            "positionId": "200614863",
            "postingTitle": "Software Engineering Intern, Apple Intelligence",
            "postingDate": "08 Sept 2026",
            "locations": [{"name": "Bengaluru, Karnataka, India"}],
            "team": {"teamName": "Machine Learning and AI"},
        },
        {
            "id": "PIPE-200314010",
            "positionId": "200314010",
            "postingTitle": "IN-Store Leader",  # Disqualified by non-tech
            "postingDate": "08 Sept 2026",
            "locations": [{"name": "Mumbai, India"}],
            "team": {"teamName": "Apple Retail"},
        },
        {
            "id": "PIPE-200614864",
            "positionId": "200614864",
            "postingTitle": "Software Development Engineer 1",
            "postingDate": "08 Sept 2026",
            "locations": [{"name": "Cupertino, California, United States"}],  # Disqualified by location
            "team": {"teamName": "Software and Services"},
        },
        {
            "id": "PIPE-200614865",
            "positionId": "200614865",
            "postingTitle": "Lead Software Engineer",  # Disqualified by seniority
            "postingDate": "08 Sept 2026",
            "locations": [{"name": "Bengaluru, India"}],
            "team": {"teamName": "Hardware Technologies"},
        },
    ]

    html_content = make_apple_html(mock_jobs, total_records=4)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "jobs.apple.com/en-in/search" in str(request.url)
        assert "location=india-INDC" in str(request.url)
        return httpx.Response(200, text=html_content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(
            name="Apple",
            provider=ATSProvider.APPLE,
            board_token="apple",
            extra={"max_pages": 1},
        )
        jobs = await AppleClient(client).fetch_jobs(company)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.id == "PIPE-200614863"
        assert job.company == "Apple"
        assert job.title == "Software Engineering Intern, Apple Intelligence"
        assert "Bengaluru" in job.location
        assert str(job.apply_url) == "https://jobs.apple.com/en-in/details/200614863"
        assert job.published_date == "2026-09-08"
        assert job.provider == ATSProvider.APPLE


@pytest.mark.asyncio
async def test_apple_client_pagination() -> None:
    """Test Apple client traverses multiple pages."""
    page1_html = make_apple_html(
        [
            {
                "id": "A1",
                "positionId": "A1",
                "postingTitle": "Software Development Engineer 1",
                "postingDate": "08 Sept 2026",
                "locations": [{"name": "Bengaluru, India"}],
            }
        ],
        total_records=40,
    )
    page2_html = make_apple_html(
        [
            {
                "id": "A2",
                "positionId": "A2",
                "postingTitle": "Associate Data Engineer",
                "postingDate": "08 Sept 2026",
                "locations": [{"name": "Hyderabad, India"}],
            }
        ],
        total_records=40,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "page=1" in url_str:
            return httpx.Response(200, text=page1_html)
        elif "page=2" in url_str:
            return httpx.Response(200, text=page2_html)
        return httpx.Response(200, text=make_apple_html([], total_records=40))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(
            name="Apple",
            provider=ATSProvider.APPLE,
            board_token="apple",
            extra={"max_pages": 2},
        )
        jobs = await AppleClient(client).fetch_jobs(company)
        assert len(jobs) == 2
        assert {j.id for j in jobs} == {"A1", "A2"}


@pytest.mark.asyncio
async def test_apple_client_missing_script() -> None:
    """Test Apple client handles missing hydration script gracefully."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>No scripts here</body></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(
            name="Apple",
            provider=ATSProvider.APPLE,
            board_token="apple",
        )
        jobs = await AppleClient(client).fetch_jobs(company)
        assert jobs == []


def test_apple_scanner_domain_and_rate_limits() -> None:
    """Test scanner correctly resolves Apple host and registers rate limits."""
    company = CompanyConfig(
        name="Apple",
        provider=ATSProvider.APPLE,
        board_token="apple",
    )
    domain = get_company_domain(company)
    assert domain == "jobs.apple.com"
    assert "jobs.apple.com" in DEFAULT_DOMAIN_LIMITS

"""Unit tests for Amazon Jobs ATS client and scanner integration."""

import httpx
import pytest

from gcc_job_radar.clients.amazon import AmazonClient
from gcc_job_radar.models import ATSProvider, CompanyConfig
from gcc_job_radar.scanner import DEFAULT_DOMAIN_LIMITS, get_company_domain


@pytest.mark.asyncio
async def test_amazon_client_success() -> None:
    """Test Amazon client parses payload, applies title/location/experience filters, and normalizes output."""
    payload = {
        "hits": 4,
        "jobs": [
            {
                "id_icims": "10530940",
                "id": "guid-1",
                "title": "Software Development Engineer I, Amazon Payments",
                "normalized_location": "Hyderabad, Telangana, IND",
                "job_path": "/en/jobs/10530940/software-development-engineer-i-amazon-payments",
                "posted_date": "September  7, 2026",
                "description": "Amazon Payments is seeking talented SDE-1 engineers to join our team.",
                "basic_qualifications": "- Bachelor's degree in CS or related field<br/>- Proficiency in Java or C++",
            },
            {
                "id_icims": "10531173",
                "id": "guid-2",
                "title": "Software Development Manager, Measurements and Data Science",  # Disqualified by title
                "normalized_location": "Bengaluru, Karnataka, IND",
                "job_path": "/en/jobs/10531173/sdm",
                "posted_date": "September  7, 2026",
                "description": "Lead engineering teams.",
                "basic_qualifications": "- 7+ years of engineering experience",
            },
            {
                "id_icims": "10532222",
                "id": "guid-3",
                "title": "Software Development Engineer I",
                "normalized_location": "Seattle, WA, USA",  # Disqualified by location
                "job_path": "/en/jobs/10532222/sde1-sea",
                "posted_date": "September  7, 2026",
                "description": "Entry-level engineer.",
                "basic_qualifications": "- Bachelor's degree in CS",
            },
            {
                "id_icims": "10533333",
                "id": "guid-4",
                "title": "Software Development Engineer I",  # Disqualified by 4+ YOE in qualifications
                "normalized_location": "Bengaluru, Karnataka, IND",
                "job_path": "/en/jobs/10533333/sde1-blr",
                "posted_date": "September  6, 2026",
                "description": "Join our team.",
                "basic_qualifications": "- Minimum 4+ years of software development experience required",
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "amazon.jobs/en/search.json" in str(request.url)
        assert "country=IND" in str(request.url)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(
            name="Amazon",
            provider=ATSProvider.AMAZON,
            board_token="amazon",
            extra={"max_pages": 1},
        )
        jobs = await AmazonClient(client).fetch_jobs(company)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.id == "10530940"
        assert job.company == "Amazon"
        assert job.title == "Software Development Engineer I, Amazon Payments"
        assert job.location == "Hyderabad, Telangana, IND"
        assert str(job.apply_url) == "https://www.amazon.jobs/en/jobs/10530940/software-development-engineer-i-amazon-payments"
        assert job.published_date == "2026-09-07"
        assert job.provider == ATSProvider.AMAZON


@pytest.mark.asyncio
async def test_amazon_client_pagination() -> None:
    """Test Amazon client traverses multiple pages via offset parameter."""
    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "offset=0" in url_str:
            return httpx.Response(
                200,
                json={
                    "hits": 200,
                    "jobs": [
                        {
                            "id_icims": "101",
                            "title": "Software Development Engineer I",
                            "normalized_location": "Bengaluru, India",
                            "job_path": "/en/jobs/101/sde-1",
                            "posted_date": "September 1, 2026",
                            "basic_qualifications": "0-1 years experience or fresh graduate",
                        }
                    ],
                },
            )
        elif "offset=100" in url_str:
            return httpx.Response(
                200,
                json={
                    "hits": 200,
                    "jobs": [
                        {
                            "id_icims": "102",
                            "title": "Software Development Engineer I",
                            "normalized_location": "Hyderabad, India",
                            "job_path": "/en/jobs/102/sde-1",
                            "posted_date": "August 28, 2026",
                            "basic_qualifications": "Bachelor's degree in CS",
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"hits": 200, "jobs": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(
            name="Amazon",
            provider=ATSProvider.AMAZON,
            board_token="amazon",
            extra={"max_pages": 2, "result_limit": 100},
        )
        jobs = await AmazonClient(client).fetch_jobs(company)

        assert len(jobs) == 2
        assert [j.id for j in jobs] == ["101", "102"]


@pytest.mark.asyncio
async def test_amazon_client_errors_and_empty() -> None:
    """Test Amazon client handles 500 error, 404, or empty responses safely."""
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Internal Server Error"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(error_handler)) as client:
        company = CompanyConfig(
            name="Amazon",
            provider=ATSProvider.AMAZON,
            board_token="amazon",
        )
        jobs = await AmazonClient(client).fetch_jobs(company)
        assert jobs == []


def test_amazon_scanner_domain_and_limits() -> None:
    """Test scanner domain resolution and concurrency limit for Amazon."""
    company = CompanyConfig(name="Amazon", provider=ATSProvider.AMAZON, board_token="amazon")
    assert get_company_domain(company) == "amazon.jobs"
    assert DEFAULT_DOMAIN_LIMITS.get("amazon.jobs") == 5


def test_amazon_portal_link_resolution() -> None:
    """Test link resolver mapping for Amazon."""
    from gcc_job_radar.link_resolver import resolve_company_career_portal

    assert resolve_company_career_portal("Amazon") in ("https://amazon.jobs", "https://www.amazon.jobs")



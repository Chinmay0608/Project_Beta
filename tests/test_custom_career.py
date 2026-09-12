"""Unit tests for CustomCareerClient direct HTML career page scraper."""

import httpx
import pytest
from gcc_job_radar.clients.custom_career import CustomCareerClient, clean_company_slug
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting
from gcc_job_radar.scanner import fetch_single_company


SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Acme Careers</title></head>
<body>
    <header>
        <a href="/about">About Us</a>
        <a href="/contact">Contact</a>
    </header>
    <main>
        <h1>Open Positions</h1>
        <div class="job-list">
            <!-- Tech positions (should be extracted) -->
            <a href="/careers/sde-1" class="job-link">Software Development Engineer - 1</a>
            <a href="https://acme.com/jobs/frontend" class="job-link">Junior Frontend Developer</a>
            <a href="/jobs/devops-intern">Cloud & DevOps Engineer Intern</a>
            <a href="/jobs/data-analyst">Associate Data Analyst</a>

            <!-- Non-tech positions (must be filtered out) -->
            <a href="/careers/sales-lead">Regional Sales Executive</a>
            <a href="/jobs/hr-manager">Human Resources Specialist</a>
            <a href="/jobs/telecaller">Customer Support Telecaller</a>
            <a href="/jobs/nurse">Corporate Staff Nurse</a>

            <!-- Irrelevant / anchor links -->
            <a href="#apply-now">Apply Now</a>
            <a href="javascript:void(0)">Click Here</a>
            <a href="mailto:careers@acme.com">Email Us</a>
        </div>
    </main>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_custom_career_fetch_jobs_success():
    """Verify that engineering links are extracted and non-tech links are excluded."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_HTML, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = CustomCareerClient(http_client)
        postings = await client.fetch_jobs("Acme Corp", "https://acme.com/careers")

        assert len(postings) == 4
        titles = [p.title for p in postings]
        assert "Software Development Engineer - 1" in titles
        assert "Junior Frontend Developer" in titles
        assert "Cloud & DevOps Engineer Intern" in titles
        assert "Associate Data Analyst" in titles

        # Verify non-tech titles excluded
        assert not any("Sales" in t for t in titles)
        assert not any("Human Resources" in t for t in titles)
        assert not any("Customer Support" in t for t in titles)
        assert not any("Nurse" in t for t in titles)

        # Verify URL resolution
        urls = [str(p.apply_url) for p in postings]
        assert "https://acme.com/careers/sde-1" in urls
        assert "https://acme.com/jobs/frontend" in urls

        # Verify deterministic ID format
        for p in postings:
            assert p.id.startswith("custom_acme_corp_")
            assert p.provider == ATSProvider.CUSTOM
            assert p.company == "Acme Corp"


@pytest.mark.asyncio
async def test_custom_career_fetch_with_company_config():
    """Verify fetch_jobs works with CompanyConfig instance."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_HTML, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = CustomCareerClient(http_client)
        company = CompanyConfig(
            name="TCS Enterprise",
            provider=ATSProvider.CUSTOM,
            career_url="https://tcs.com/careers",
        )
        postings = await client.fetch_jobs(company)
        assert len(postings) == 4
        assert postings[0].company == "TCS Enterprise"
        assert postings[0].id.startswith("custom_tcs_enterprise_")


@pytest.mark.asyncio
async def test_scanner_fetch_single_company_routes_custom():
    """Verify scanner.fetch_single_company correctly dispatches custom providers."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_HTML, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        company = CompanyConfig(
            name="Reliance Jio",
            provider=ATSProvider.CUSTOM,
            career_url="https://jio.com/careers",
        )
        postings = await fetch_single_company(company, http_client)
        assert len(postings) == 4
        assert postings[0].company == "Reliance Jio"
        assert postings[0].id.startswith("custom_reliance_jio_")


@pytest.mark.asyncio
async def test_custom_career_error_handling():
    """Verify graceful handling of HTTP 404/500 and network errors."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = CustomCareerClient(http_client)
        postings = await client.fetch_jobs("Broken Site", "https://broken.com")
        assert postings == []


def test_clean_company_slug():
    """Verify company slug normalization."""
    assert clean_company_slug("Reliance Jio 5G") == "reliance_jio_5g"
    assert clean_company_slug("TCS (Tata Consultancy Services)") == "tcs_tata_consultancy_services"
    assert clean_company_slug("!!!") == "company"

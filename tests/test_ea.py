"""Unit tests for Electronic Arts (EA) Careers ATS client and scanner integration."""

import httpx
import pytest

from gcc_job_radar.clients.ea import EAClient
from gcc_job_radar.models import ATSProvider, CompanyConfig
from gcc_job_radar.scanner import DEFAULT_DOMAIN_LIMITS, get_company_domain


SAMPLE_EA_HTML = """
<!DOCTYPE html>
<html>
<body>
<article class="article article--result article--non-toggle" id="article--1">
    <div class="article__header">
        <div class="article__header__text">
            <h3 class="article__header__text__title title title--04">
                <a class="link link_result" href="https://jobs.ea.com/en_US/careers/JobDetail/Full-Stack-Software-Engineer-Intern/215942">
                    Full Stack Software Engineer Intern
                </a>
            </h3>
            <div class="article__header__text__subtitle">
                <span class="list-item-location">Hyderabad, India</span>
                <span class="list-item-id">Role ID 215942</span>
            </div>
        </div>
    </div>
</article>

<article class="article article--result article--non-toggle" id="article--2">
    <div class="article__header">
        <div class="article__header__text">
            <h3 class="article__header__text__title title title--04">
                <a class="link link_result" href="https://jobs.ea.com/en_US/careers/JobDetail/Senior-Accountant-I/215693">
                    Senior Accountant I
                </a>
            </h3>
            <div class="article__header__text__subtitle">
                <span class="list-item-location">Hyderabad, India</span>
                <span class="list-item-id">Role ID 215693</span>
            </div>
        </div>
    </div>
</article>

<article class="article article--result article--non-toggle" id="article--3">
    <div class="article__header">
        <div class="article__header__text">
            <h3 class="article__header__text__title title title--04">
                <a class="link link_result" href="https://jobs.ea.com/en_US/careers/JobDetail/Senior-Software-Engineer/216059">
                    Senior Software Engineer
                </a>
            </h3>
            <div class="article__header__text__subtitle">
                <span class="list-item-location">Hyderabad, India</span>
                <span class="list-item-id">Role ID 216059</span>
            </div>
        </div>
    </div>
</article>

<article class="article article--result article--non-toggle" id="article--4">
    <div class="article__header">
        <div class="article__header__text">
            <h3 class="article__header__text__title title title--04">
                <a class="link link_result" href="https://jobs.ea.com/en_US/careers/JobDetail/Software-Engineer-Intern/216034">
                    Software Engineer Intern
                </a>
            </h3>
            <div class="article__header__text__subtitle">
                <span class="list-item-location">San Francisco, CA, USA</span>
                <span class="list-item-id">Role ID 216034</span>
            </div>
        </div>
    </div>
</article>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_ea_client_success() -> None:
    """Verify EAClient extracts tech jobs in India, filtering out non-tech, senior, and foreign roles."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_EA_HTML)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        client = EAClient(mock_client)
        cfg = CompanyConfig(name="Electronic Arts", provider=ATSProvider.EA, board_token="ea")
        jobs = await client.fetch_jobs(cfg)

    # Only article 1 (Full Stack Software Engineer Intern in Hyderabad) matches entry-level tech India
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "ea_215942"
    assert job.company == "Electronic Arts"
    assert job.title == "Full Stack Software Engineer Intern"
    assert job.location == "Hyderabad, India"
    assert str(job.apply_url) == "https://jobs.ea.com/en_US/careers/JobDetail/Full-Stack-Software-Engineer-Intern/215942"
    assert job.provider == ATSProvider.EA


@pytest.mark.asyncio
async def test_ea_client_http_error() -> None:
    """Verify EAClient handles HTTP errors gracefully without unhandled exceptions."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        client = EAClient(mock_client)
        cfg = CompanyConfig(name="Electronic Arts", provider=ATSProvider.EA, board_token="ea")
        jobs = await client.fetch_jobs(cfg)

    assert jobs == []


def test_ea_scanner_integration() -> None:
    """Verify Electronic Arts domain mapping and concurrency rate limits."""
    cfg = CompanyConfig(name="Electronic Arts", provider=ATSProvider.EA, board_token="ea")
    assert get_company_domain(cfg) == "jobs.ea.com"
    assert DEFAULT_DOMAIN_LIMITS["jobs.ea.com"] == 5

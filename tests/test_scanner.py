"""Unit and integration tests for high-throughput scanner engine."""

import asyncio
from collections import defaultdict
from typing import Any
import httpx
import pytest

from gcc_job_radar.clients.greenhouse import GreenhouseClient
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting
from gcc_job_radar.scanner import (
    DEFAULT_GLOBAL_CONCURRENCY,
    DEFAULT_HOST_LIMIT,
    HAS_HTTP2,
    HostRateLimiter,
    RetryTransport,
    fast_json_loads,
    get_company_domain,
    scan_all_companies,
)


@pytest.mark.asyncio
async def test_retry_transport_429_success() -> None:
    """Verify RetryTransport retries HTTP 429 with exponential backoff and succeeds."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, headers={"Retry-After": "0.01"}, text="Too Many Requests")
        return httpx.Response(200, json={"status": "ok", "attempts": attempts})

    mock_transport = httpx.MockTransport(handler)
    retry_transport = RetryTransport(mock_transport, max_retries=3, base_delay=0.01)

    async with httpx.AsyncClient(transport=retry_transport) as client:
        response = await client.get("https://example.com/test")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["attempts"] == 3
        assert retry_transport.retry_count == 2


@pytest.mark.asyncio
async def test_retry_transport_429_exhaustion() -> None:
    """Verify RetryTransport returns final 429 response when retries are exhausted."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, headers={"Retry-After": "0.01"}, text="Rate limited")

    mock_transport = httpx.MockTransport(handler)
    retry_transport = RetryTransport(mock_transport, max_retries=3, base_delay=0.01)

    async with httpx.AsyncClient(transport=retry_transport) as client:
        response = await client.get("https://example.com/test")
        assert response.status_code == 429
        assert attempts == 4  # Initial attempt + 3 retries
        assert retry_transport.retry_count == 3


@pytest.mark.asyncio
async def test_retry_transport_transient_connection_error() -> None:
    """Verify RetryTransport catches and retries transient connection errors."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectTimeout("Connect timeout error")
        return httpx.Response(200, json={"connected": True})

    mock_transport = httpx.MockTransport(handler)
    retry_transport = RetryTransport(mock_transport, max_retries=2, base_delay=0.01)

    async with httpx.AsyncClient(transport=retry_transport) as client:
        response = await client.get("https://example.com/connect")
        assert response.status_code == 200
        assert response.json()["connected"] is True
        assert retry_transport.retry_count == 1


@pytest.mark.asyncio
async def test_retry_transport_connection_error_exhaustion() -> None:
    """Verify RetryTransport re-raises connection error after retries are exhausted."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("Network unreachable")

    mock_transport = httpx.MockTransport(handler)
    retry_transport = RetryTransport(mock_transport, max_retries=2, base_delay=0.01)

    async with httpx.AsyncClient(transport=retry_transport) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://example.com/unreachable")
        assert attempts == 3  # Initial attempt + 2 retries
        assert retry_transport.retry_count == 2


@pytest.mark.asyncio
async def test_host_rate_limiter_concurrency_bounds() -> None:
    """Verify HostRateLimiter bounds global concurrency and domain-level concurrency simultaneously."""
    active_global = 0
    max_active_global = 0
    active_by_domain: dict[str, int] = defaultdict(int)
    max_active_by_domain: dict[str, int] = defaultdict(int)
    lock = asyncio.Lock()

    async def instrumented_handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_global, max_active_global
        domain = "greenhouse.io" if "greenhouse" in str(request.url) else "lever.co"

        async with lock:
            active_global += 1
            active_by_domain[domain] += 1
            if active_global > max_active_global:
                max_active_global = active_global
            if active_by_domain[domain] > max_active_by_domain[domain]:
                max_active_by_domain[domain] = active_by_domain[domain]

        # Simulate network latency
        await asyncio.sleep(0.04)

        async with lock:
            active_global -= 1
            active_by_domain[domain] -= 1

        if "greenhouse" in str(request.url):
            return httpx.Response(200, json={"jobs": []})
        return httpx.Response(200, json=[])

    companies = [
        CompanyConfig(name=f"GH_{i}", provider=ATSProvider.GREENHOUSE, board_token=f"gh_{i}")
        for i in range(8)
    ] + [
        CompanyConfig(name=f"LV_{i}", provider=ATSProvider.LEVER, board_token=f"lv_{i}")
        for i in range(8)
    ]

    client = httpx.AsyncClient(transport=httpx.MockTransport(instrumented_handler))

    # Global limit = 4, Domain limit = 2 for both
    await scan_all_companies(
        companies=companies,
        concurrency=4,
        domain_limits={"greenhouse.io": 2, "lever.co": 2},
        client=client,
        base_retry_delay=0.01,
    )

    assert max_active_global <= 4
    assert max_active_by_domain["greenhouse.io"] <= 2
    assert max_active_by_domain["lever.co"] <= 2


@pytest.mark.asyncio
async def test_scan_all_companies_graceful_error_handling() -> None:
    """Verify scan_all_companies handles 404, timeouts, and 500 without crashing parallel scans."""
    progress_updates: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "timeout-co" in url:
            raise httpx.ReadTimeout("Read timed out")
        elif "broken-404" in url:
            return httpx.Response(404, text="Not Found")
        elif "broken-500" in url:
            return httpx.Response(500, text="Internal Server Error")
        elif "valid-gh" in url:
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 101,
                            "title": "Software Engineer 1",
                            "location": {"name": "Bengaluru, India"},
                            "content": "<p>Entry level role</p>",
                            "absolute_url": "https://boards.greenhouse.io/valid-gh/jobs/101",
                            "updated_at": "2026-08-30T10:00:00Z",
                        }
                    ]
                },
            )
        elif "valid-lv" in url:
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "lv-202",
                        "text": "Associate Software Engineer",
                        "categories": {"location": "Pune, India"},
                        "hostedUrl": "https://jobs.lever.co/valid-lv/lv-202",
                        "createdAt": 1756000000000,
                    }
                ],
            )
        return httpx.Response(200, json=[])

    companies = [
        CompanyConfig(name="Valid GH", provider=ATSProvider.GREENHOUSE, board_token="valid-gh"),
        CompanyConfig(name="Timeout Co", provider=ATSProvider.GREENHOUSE, board_token="timeout-co"),
        CompanyConfig(name="Broken 404", provider=ATSProvider.LEVER, board_token="broken-404"),
        CompanyConfig(name="Broken 500", provider=ATSProvider.SMARTRECRUITERS, board_token="broken-500"),
        CompanyConfig(name="Valid LV", provider=ATSProvider.LEVER, board_token="valid-lv"),
    ]

    def on_progress(name: str, current: int, total: int) -> None:
        progress_updates.append(name)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    postings = await scan_all_companies(
        companies=companies,
        concurrency=3,
        on_progress=on_progress,
        client=client,
        max_retries=1,
        base_retry_delay=0.01,
    )

    # Scanning completed all 5 companies despite failures
    assert len(progress_updates) == 5
    # Only postings from Valid GH and Valid LV were returned
    assert len(postings) == 2
    returned_companies = {p.company for p in postings}
    assert returned_companies == {"Valid GH", "Valid LV"}


@pytest.mark.asyncio
async def test_greenhouse_two_pass_inspection_targeted_detail_fetch() -> None:
    """Verify Greenhouse two-pass only queries job detail for roles matching title & location."""
    board_payload = {
        "jobs": [
            {
                "id": 901,
                "title": "Senior Software Engineer",  # Filtered out by title in pass 1
                "location": {"name": "Bengaluru, India"},
                "absolute_url": "https://boards.greenhouse.io/twopass/jobs/901",
            },
            {
                "id": 902,
                "title": "Software Engineer 1",  # Filtered out by location in pass 1
                "location": {"name": "Austin, TX"},
                "absolute_url": "https://boards.greenhouse.io/twopass/jobs/902",
            },
            {
                "id": 903,
                "title": "Software Engineer 1",  # Matches title + location -> triggers pass 2
                "location": {"name": "Bengaluru, India"},
                "absolute_url": "https://boards.greenhouse.io/twopass/jobs/903",
                "updated_at": "2026-08-30T10:00:00Z",
            },
        ]
    }

    queried_detail_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/jobs"):
            return httpx.Response(200, json=board_payload)
        elif "/jobs/903" in url:
            queried_detail_ids.append("903")
            return httpx.Response(
                200,
                json={
                    "id": 903,
                    "title": "Software Engineer 1",
                    "content": "<p>Entry level 0-1 years experience required</p>",
                },
            )
        elif "/jobs/901" in url:
            queried_detail_ids.append("901")
            return httpx.Response(200, json={"id": 901, "content": "senior role"})
        elif "/jobs/902" in url:
            queried_detail_ids.append("902")
            return httpx.Response(200, json={"id": 902, "content": "us role"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(name="TwoPass Inc", provider=ATSProvider.GREENHOUSE, board_token="twopass")
        jobs = await GreenhouseClient(client).fetch_jobs(company)

        # Pass 2 was ONLY invoked for job 903!
        assert queried_detail_ids == ["903"]
        assert len(jobs) == 1
        assert jobs[0].id == "903"
        assert jobs[0].title == "Software Engineer 1"


@pytest.mark.asyncio
async def test_greenhouse_two_pass_inspection_filters_experienced_candidate() -> None:
    """Verify Greenhouse two-pass inspects detail content and filters out experienced roles."""
    board_payload = {
        "jobs": [
            {
                "id": 905,
                "title": "Software Engineer 1",
                "location": {"name": "Bengaluru, India"},
                "absolute_url": "https://boards.greenhouse.io/twopass/jobs/905",
                "updated_at": "2026-08-30T10:00:00Z",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/jobs"):
            return httpx.Response(200, json=board_payload)
        elif "/jobs/905" in url:
            return httpx.Response(
                200,
                json={
                    "id": 905,
                    "title": "Software Engineer 1",
                    "content": "<p>Candidate must have 5+ years of software engineering experience.</p>",
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(name="TwoPass Inc", provider=ATSProvider.GREENHOUSE, board_token="twopass")
        jobs = await GreenhouseClient(client).fetch_jobs(company)

        # Job 905 was disqualified by experience check on pass 2 detail content
        assert len(jobs) == 0


def test_get_company_domain_mapping() -> None:
    """Verify domain extraction maps companies to correct rate limiting domain."""
    assert get_company_domain(CompanyConfig(name="A", provider=ATSProvider.GREENHOUSE, board_token="a")) == "greenhouse.io"
    assert get_company_domain(CompanyConfig(name="B", provider=ATSProvider.LEVER, board_token="b")) == "lever.co"
    assert get_company_domain(CompanyConfig(name="C", provider=ATSProvider.ASHBY, board_token="c")) == "ashbyhq.com"
    assert get_company_domain(CompanyConfig(name="D", provider=ATSProvider.SMARTRECRUITERS, board_token="d")) == "smartrecruiters.com"
    assert get_company_domain(CompanyConfig(name="E", provider=ATSProvider.WORKDAY, board_token="tenant/site")) == "myworkdayjobs.com"
    assert get_company_domain(CompanyConfig(name="F", provider=ATSProvider.PHENOM_SUCCESSFACTORS, board_token="https://careers.citi.com")) == "careers.citi.com"


def test_fast_json_loads_orjson() -> None:
    """Verify ultra-fast orjson deserialization handles strings, bytes, and Responses."""
    # String
    data_str = '{"company": "Google", "count": 42}'
    assert fast_json_loads(data_str) == {"company": "Google", "count": 42}

    # Bytes
    data_bytes = b'{"company": "Meta", "active": true}'
    assert fast_json_loads(data_bytes) == {"company": "Meta", "active": True}

    # Response
    resp = httpx.Response(200, content=b'{"items": [1, 2, 3]}')
    assert fast_json_loads(resp) == {"items": [1, 2, 3]}


def test_scanner_http2_enabled() -> None:
    """Verify HTTP/2 flag is enabled when h2 is available."""
    assert HAS_HTTP2 is True
    assert DEFAULT_GLOBAL_CONCURRENCY == 30

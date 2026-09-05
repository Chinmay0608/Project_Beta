"""Unit tests for tools/probe_ats.py automated candidate token discovery tool."""

import asyncio
from pathlib import Path
import httpx
import pytest

from gcc_job_radar.models import ATSProvider
from tools.probe_ats import (
    DEFAULT_CONCURRENCY,
    DEFAULT_LIMITS,
    DEFAULT_TIMEOUT,
    ProbeResult,
    append_to_config,
    check_platform,
    clean_company_name,
    deduplicate_results,
    format_python_snippets,
    format_results_table,
    generate_slug_candidates,
    probe_companies,
    probe_company,
)


def test_clean_company_name() -> None:
    """Verify stripping corporate suffixes while preserving actual company brand."""
    assert clean_company_name("Western Digital Technologies, Inc.") == "Western Digital"
    assert clean_company_name("Texas Instruments Inc") == "Texas Instruments"
    assert clean_company_name("Acme Software Corp.") == "Acme"
    assert clean_company_name("Global Tech Solutions Ltd.") == "Global Tech"
    assert clean_company_name("Databricks") == "Databricks"


def test_generate_slug_candidates() -> None:
    """Verify pruned slug generation covers top 2-3 variations (exact lowercase, hyphenated, known acronym)."""
    slugs = generate_slug_candidates("Texas Instruments")
    assert slugs == ["texasinstruments", "texas-instruments", "ti"]
    assert len(slugs) <= 3

    wd_slugs = generate_slug_candidates("Western Digital")
    assert wd_slugs == ["westerndigital", "western-digital", "wd"]
    assert len(wd_slugs) <= 3

    single_slugs = generate_slug_candidates("Databricks")
    assert single_slugs == ["databricks"]
    assert len(single_slugs) <= 3


@pytest.mark.asyncio
async def test_check_platform_greenhouse_success() -> None:
    """Verify Greenhouse live board detection and posting count extraction."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "boards-api.greenhouse.io/v1/boards/mycorp/jobs" in str(request.url)
        return httpx.Response(200, json={"jobs": [{"id": 1}, {"id": 2}, {"id": 3}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        count = await check_platform(ATSProvider.GREENHOUSE, "mycorp", client)
        assert count == 3


@pytest.mark.asyncio
async def test_check_platform_lever_success() -> None:
    """Verify Lever live board detection and posting count extraction."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.lever.co/v0/postings/mycorp?mode=json" in str(request.url)
        return httpx.Response(200, json=[{"id": "l1"}, {"id": "l2"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        count = await check_platform(ATSProvider.LEVER, "mycorp", client)
        assert count == 2


@pytest.mark.asyncio
async def test_check_platform_ashby_success() -> None:
    """Verify Ashby live board detection and posting count extraction."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.ashbyhq.com/posting-api/job-board/mycorp" in str(request.url)
        return httpx.Response(200, json={"jobs": [{"id": 10}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        count = await check_platform(ATSProvider.ASHBY, "mycorp", client)
        assert count == 1


@pytest.mark.asyncio
async def test_check_platform_smartrecruiters_success() -> None:
    """Verify SmartRecruiters live board detection and totalFound extraction."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.smartrecruiters.com/v1/companies/mycorp/postings" in str(request.url)
        return httpx.Response(200, json={"content": [{"id": 1}], "totalFound": 45})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        count = await check_platform(ATSProvider.SMARTRECRUITERS, "mycorp", client)
        assert count == 45


@pytest.mark.asyncio
async def test_check_platform_failure_cases() -> None:
    """Verify 404, 500, malformed, or empty ghost board responses return None gracefully."""
    def handler_404(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Not Found"})

    def handler_malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<!DOCTYPE html><html><body>Error</body></html>")

    def handler_sr_empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"totalFound": 0, "content": []})

    def handler_gh_empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": []})

    def handler_lever_empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    def handler_ashby_empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_404)) as client:
        assert await check_platform(ATSProvider.GREENHOUSE, "unknown", client) is None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_malformed)) as client:
        assert await check_platform(ATSProvider.LEVER, "unknown", client) is None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_sr_empty)) as client:
        assert await check_platform(ATSProvider.SMARTRECRUITERS, "ghost_co", client) is None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_gh_empty)) as client:
        assert await check_platform(ATSProvider.GREENHOUSE, "ghost_co", client) is None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_lever_empty)) as client:
        assert await check_platform(ATSProvider.LEVER, "ghost_co", client) is None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_ashby_empty)) as client:
        assert await check_platform(ATSProvider.ASHBY, "ghost_co", client) is None


@pytest.mark.asyncio
async def test_probe_company_skips_existing_companies() -> None:
    """Verify that existing companies in config.py registry are skipped without HTTP requests."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    existing = {"google", "microsoft", "amazon", "postman"}
    sem = asyncio.Semaphore(5)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_company("Postman", client, existing, sem)
        assert result is None
        assert not called


@pytest.mark.asyncio
async def test_probe_company_finds_live_board_and_stops() -> None:
    """Verify probe_company identifies the active ATS platform following prioritized sequence and stops."""
    queried_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        queried_urls.append(url)
        # Simulate Ashby 404, Greenhouse 404, but Lever 200
        if "api.ashbyhq.com" in url or "boards-api.greenhouse.io" in url:
            return httpx.Response(404, text="Not Found")
        elif "api.lever.co/v0/postings/testcorp" in url:
            return httpx.Response(200, json=[{"id": "job1"}, {"id": "job2"}])
        return httpx.Response(404, text="Not Found")

    existing: set[str] = set()
    sem = asyncio.Semaphore(5)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_company("TestCorp", client, existing, sem)
        assert result is not None
        assert result.company_name == "TestCorp"
        assert result.provider == ATSProvider.LEVER
        assert result.board_token == "testcorp"
        assert result.active_postings == 2

        # Verify Ashby (#1) and Greenhouse (#2) were probed before Lever (#3)
        assert any("api.ashbyhq.com" in u for u in queried_urls)
        assert any("boards-api.greenhouse.io" in u for u in queried_urls)
        # Verify SmartRecruiters (#4) was never queried for testcorp since Lever succeeded
        assert not any("api.smartrecruiters.com" in u for u in queried_urls)


@pytest.mark.asyncio
async def test_probe_company_prioritizes_ashby_first() -> None:
    """Verify Ashby (#1) is probed first, and if live, remaining platforms are never queried."""
    queried_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        queried_urls.append(url)
        if "api.ashbyhq.com" in url:
            return httpx.Response(200, json={"jobs": [{"id": 1}]})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_company("FastStartup", client, set())
        assert result is not None
        assert result.provider == ATSProvider.ASHBY
        assert result.board_token == "faststartup"
        # Greenhouse, Lever, SmartRecruiters should never have been queried
        assert not any("boards-api.greenhouse.io" in u for u in queried_urls)
        assert not any("api.lever.co" in u for u in queried_urls)
        assert not any("api.smartrecruiters.com" in u for u in queried_urls)


@pytest.mark.asyncio
async def test_check_platform_head_preflight_discards_on_non_200() -> None:
    """Verify HEAD pre-flight discards immediately on non-200 without issuing GET."""
    methods_called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods_called.append(request.method)
        return httpx.Response(404, text="Not Found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        res = await check_platform(ATSProvider.ASHBY, "unknown_board", client)
        assert res is None
        # Only HEAD was issued; GET was never called
        assert methods_called == ["HEAD"]


def test_format_results_table_and_snippets() -> None:
    """Verify ASCII table and Python CompanyConfig code formatting."""
    results = [
        ProbeResult("Test One", ATSProvider.GREENHOUSE, "testone", 12),
        ProbeResult("Test Two", ATSProvider.ASHBY, "testtwo", 5),
    ]

    table = format_results_table(results)
    assert "Test One" in table
    assert "Greenhouse" in table
    assert "testone" in table
    assert "Test Two" in table
    assert "Ashby" in table

    snippets = format_python_snippets(results)
    assert 'CompanyConfig(name="Test One", provider=ATSProvider.GREENHOUSE, board_token="testone")' in snippets
    assert 'CompanyConfig(name="Test Two", provider=ATSProvider.ASHBY, board_token="testtwo")' in snippets


def test_append_to_config_temp_file(tmp_path: Path) -> None:
    """Verify appending verified entries to config.py file before closing bracket."""
    sample_config = """\"\"\"Sample config.\"\"\"
from gcc_job_radar.models import ATSProvider, CompanyConfig

COMPANIES: list[CompanyConfig] = [
    CompanyConfig(name="Existing", provider=ATSProvider.GREENHOUSE, board_token="existing"),
]

# Strict entry-level tech title positive pattern
INCLUDE_TITLE_PATTERN = None
"""
    temp_file = tmp_path / "config.py"
    temp_file.write_text(sample_config, encoding="utf-8")

    results = [
        ProbeResult("New Co", ATSProvider.LEVER, "newco", 7),
    ]

    count = append_to_config(results, config_path=temp_file)
    assert count == 1

    updated_content = temp_file.read_text(encoding="utf-8")
    assert 'CompanyConfig(name="New Co", provider=ATSProvider.LEVER, board_token="newco"),' in updated_content
    # Ensure closing bracket is still intact
    assert "]\n\n# Strict entry-level" in updated_content


@pytest.mark.asyncio
async def test_probe_companies_batch_concurrent() -> None:
    """Verify batch discovery probes multiple companies concurrently, collects results, and skips existing."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "boards-api.greenhouse.io/v1/boards/alphacorp/jobs" in url:
            return httpx.Response(200, json={"jobs": [{"id": 1}, {"id": 2}]})
        elif "api.lever.co/v0/postings/betacorp?mode=json" in url:
            return httpx.Response(200, json=[{"id": "b1"}])
        elif "api.ashbyhq.com/posting-api/job-board/gamma" in url:
            return httpx.Response(200, json={"jobs": [{"id": "g1"}]})
        return httpx.Response(404, text="Not Found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        targets = ["Alpha Corp", "Beta Corp", "Gamma", "Existing Corp", "NonExistent Co"]
        existing = {"existing corp"}
        results = await probe_companies(
            target_names=targets,
            client=client,
            existing_names=existing,
            concurrency=5,
            show_progress=False,
        )

        assert len(results) == 3
        names = {r.company_name for r in results}
        assert "Alpha Corp" in names
        assert "Beta Corp" in names
        assert "Gamma" in names

        alpha = next(r for r in results if r.company_name == "Alpha Corp")
        assert alpha.provider == ATSProvider.GREENHOUSE
        assert alpha.board_token == "alphacorp"
        assert alpha.active_postings == 2

        beta = next(r for r in results if r.company_name == "Beta Corp")
        assert beta.provider == ATSProvider.LEVER
        assert beta.board_token == "betacorp"
        assert beta.active_postings == 1


@pytest.mark.asyncio
async def test_probe_companies_batch_concurrency_bounds() -> None:
    """Verify that worker pool strictly bounds concurrent company probes via Semaphore."""
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        async with lock:
            in_flight -= 1
        return httpx.Response(404, text="Not Found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_handler)) as client:
        targets = [f"Company {i}" for i in range(12)]
        await probe_companies(
            target_names=targets,
            client=client,
            existing_names=set(),
            concurrency=3,
            show_progress=False,
        )

        # In-flight concurrency must never exceed the worker pool semaphore bound (3)
        assert max_in_flight <= 3


def test_deduplicate_results() -> None:
    """Verify duplicate company names and duplicate (provider, token) pairs are stripped."""
    existing_names = {"alpha corp", "existing"}
    existing_tokens = {(ATSProvider.GREENHOUSE, "alpha")}

    results = [
        ProbeResult("Alpha Corp", ATSProvider.GREENHOUSE, "alpha", 5),  # already in existing
        ProbeResult("Beta", ATSProvider.LEVER, "beta", 2),  # valid new
        ProbeResult("Beta", ATSProvider.ASHBY, "beta", 1),  # duplicate name in batch
        ProbeResult("Gamma", ATSProvider.LEVER, "beta", 3),  # duplicate token (LEVER, beta) in batch
        ProbeResult("Delta", ATSProvider.ASHBY, "delta", 4),  # valid new
    ]

    deduped = deduplicate_results(results, existing_names, existing_tokens)
    assert len(deduped) == 2
    assert deduped[0].company_name == "Beta"
    assert deduped[0].provider == ATSProvider.LEVER
    assert deduped[1].company_name == "Delta"
    assert deduped[1].provider == ATSProvider.ASHBY


def test_shared_client_limits_and_timeout() -> None:
    """Verify high-throughput connection limits and fast-fail timeouts are configured."""
    assert DEFAULT_LIMITS.max_connections == 250
    assert DEFAULT_LIMITS.max_keepalive_connections == 100
    assert DEFAULT_TIMEOUT.read == 2.0
    assert DEFAULT_TIMEOUT.connect == 1.0
    assert DEFAULT_CONCURRENCY == 50

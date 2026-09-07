"""Unit tests for tools/harvest_mass_ats.py automated ATS mass harvester and prober."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from gcc_job_radar.models import ATSProvider
from tools.harvest_mass_ats import (
    RESERVED_SLUGS,
    URLSCAN_SEARCH_URL,
    VerifiedATSBoard,
    append_verified_boards_to_config,
    deduplicate_candidates,
    extract_slug_from_url,
    fetch_urlscan_candidates,
    generate_slug_variations,
    run_harvest_mass_ats,
    slug_to_company_name,
    strip_legal_suffixes,
    verify_ats_board,
)


# --- 1. URLScan Slug Extraction & Filtering ---


def test_extract_slug_from_url_greenhouse() -> None:
    """Verify Greenhouse slug extraction from various URL shapes."""
    # Standard board URL
    res = extract_slug_from_url("https://boards.greenhouse.io/stripe")
    assert res == (ATSProvider.GREENHOUSE, "stripe")

    # Deep posting URL
    res = extract_slug_from_url("https://boards.greenhouse.io/stripe/jobs/12345")
    assert res == (ATSProvider.GREENHOUSE, "stripe")

    # Job-boards subdomain
    res = extract_slug_from_url("https://job-boards.greenhouse.io/celonis/jobs/999")
    assert res == (ATSProvider.GREENHOUSE, "celonis")

    # Embed parameter URL
    res = extract_slug_from_url("https://boards.greenhouse.io/embed/job_board?for=figma")
    assert res == (ATSProvider.GREENHOUSE, "figma")


def test_extract_slug_from_url_lever() -> None:
    """Verify Lever slug extraction."""
    res = extract_slug_from_url("https://jobs.lever.co/netflix")
    assert res == (ATSProvider.LEVER, "netflix")

    res = extract_slug_from_url("https://jobs.lever.co/atlassian/abc-123-def")
    assert res == (ATSProvider.LEVER, "atlassian")


def test_extract_slug_from_url_ashby() -> None:
    """Verify Ashby slug extraction."""
    res = extract_slug_from_url("https://jobs.ashbyhq.com/linear")
    assert res == (ATSProvider.ASHBY, "linear")

    res = extract_slug_from_url("https://jobs.ashbyhq.com/retool/roles/789")
    assert res == (ATSProvider.ASHBY, "retool")


def test_extract_slug_from_url_smartrecruiters() -> None:
    """Verify SmartRecruiters slug extraction."""
    res = extract_slug_from_url("https://jobs.smartrecruiters.com/BoschGroup")
    assert res == (ATSProvider.SMARTRECRUITERS, "boschgroup")

    res = extract_slug_from_url("https://jobs.smartrecruiters.com/Visa/123456")
    assert res == (ATSProvider.SMARTRECRUITERS, "visa")


def test_extract_slug_from_url_reserved_and_invalid() -> None:
    """Verify reserved paths, invalid formats, and external domains return None."""
    # Reserved words
    for slug in ("embed", "search", "jobs", "careers", "api", "static", "assets"):
        assert extract_slug_from_url(f"https://boards.greenhouse.io/{slug}") is None
        assert extract_slug_from_url(f"https://jobs.lever.co/{slug}") is None
        assert extract_slug_from_url(f"https://jobs.ashbyhq.com/{slug}") is None

    # Empty, invalid, and non-ATS URLs
    assert extract_slug_from_url("") is None
    assert extract_slug_from_url("https://google.com/jobs") is None
    assert extract_slug_from_url("not a url") is None
    assert extract_slug_from_url("https://boards.greenhouse.io/") is None


# --- 2. Suffix Stripping & Slug Variation Generation ---


def test_strip_legal_suffixes() -> None:
    """Verify corporate and legal suffixes are cleanly stripped."""
    assert strip_legal_suffixes("CrowdStrike, Inc.") == "CrowdStrike"
    assert strip_legal_suffixes("Palo Alto Networks Corp.") == "Palo Alto Networks"
    assert strip_legal_suffixes("Snowflake Technologies LLC") == "Snowflake"
    assert strip_legal_suffixes("Datadog Software Solutions Ltd.") == "Datadog"
    assert strip_legal_suffixes("Infosys Limited") == "Infosys"
    assert strip_legal_suffixes("Tech Mahindra Pvt Ltd") == "Tech Mahindra"
    assert strip_legal_suffixes("Stripe") == "Stripe"
    assert strip_legal_suffixes("0x") == "0x"
    assert strip_legal_suffixes("") == ""
    assert strip_legal_suffixes("Inc.") == ""


def test_slug_to_company_name() -> None:
    """Verify slug to title-cased company name conversion."""
    assert slug_to_company_name("palo-alto-networks") == "Palo Alto Networks"
    assert slug_to_company_name("crowdstrike") == "Crowdstrike"
    assert slug_to_company_name("pure_storage") == "Pure Storage"
    assert slug_to_company_name("") == ""


def test_generate_slug_variations() -> None:
    """Verify candidate slug variation generation."""
    # Multi-word with known abbreviation
    panw_vars = generate_slug_variations("Palo Alto Networks, Inc.")
    assert "paloaltonetworks" in panw_vars
    assert "palo-alto-networks" in panw_vars
    assert "panw" in panw_vars

    # Single-word brand
    stripe_vars = generate_slug_variations("Stripe, Inc.")
    assert "stripe" in stripe_vars

    # Multi-word brand generating initials
    digital_ocean_vars = generate_slug_variations("Digital Ocean, LLC")
    assert "digitalocean" in digital_ocean_vars
    assert "digital-ocean" in digital_ocean_vars
    assert "do" in digital_ocean_vars or "docn" in digital_ocean_vars

    # Reserved words must be omitted
    assert all(v not in RESERVED_SLUGS for v in panw_vars)


# --- 3. Candidate Deduplication ---


def test_deduplicate_candidates() -> None:
    """Verify candidates are deduplicated against existing config and within batch."""
    existing_names = {"databricks", "atlassian"}
    existing_tokens = {
        (ATSProvider.GREENHOUSE, "databricks"),
        (ATSProvider.LEVER, "atlassian"),
    }

    raw_candidates = [
        # Pre-existing in config
        ("Databricks, Inc.", ATSProvider.GREENHOUSE, "databricks"),
        ("Atlassian Corporation", ATSProvider.LEVER, "atlassian"),
        # Valid new candidate
        ("CrowdStrike", ATSProvider.GREENHOUSE, "crowdstrike"),
        # Duplicate within batch
        ("CrowdStrike Inc", ATSProvider.GREENHOUSE, "crowdstrike"),
        # Same company, different provider
        ("CrowdStrike", ATSProvider.LEVER, "crowdstrike"),
    ]

    deduped = deduplicate_candidates(raw_candidates, existing_names, existing_tokens)
    assert len(deduped) == 2
    assert deduped[0] == ("CrowdStrike", ATSProvider.GREENHOUSE, "crowdstrike")
    assert deduped[1] == ("CrowdStrike", ATSProvider.LEVER, "crowdstrike")


# --- 4. ATS Payload Validation ---


@pytest.mark.asyncio
async def test_verify_ats_board_greenhouse() -> None:
    """Verify Greenhouse payload validation."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "active-co" in str(request.url):
            return httpx.Response(200, json={"jobs": [{"id": 1, "title": "Software Engineer"}]})
        elif "empty-co" in str(request.url):
            return httpx.Response(200, json={"jobs": []})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (await verify_ats_board(ATSProvider.GREENHOUSE, "active-co", client)) == 1
        assert (await verify_ats_board(ATSProvider.GREENHOUSE, "empty-co", client)) is None
        assert (await verify_ats_board(ATSProvider.GREENHOUSE, "dead-co", client)) is None


@pytest.mark.asyncio
async def test_verify_ats_board_lever() -> None:
    """Verify Lever payload validation."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "active-lever" in str(request.url):
            return httpx.Response(200, json=[{"id": "abc", "text": "Backend Engineer"}])
        elif "empty-lever" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (await verify_ats_board(ATSProvider.LEVER, "active-lever", client)) == 1
        assert (await verify_ats_board(ATSProvider.LEVER, "empty-lever", client)) is None
        assert (await verify_ats_board(ATSProvider.LEVER, "dead-lever", client)) is None


@pytest.mark.asyncio
async def test_verify_ats_board_ashby() -> None:
    """Verify Ashby payload validation."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "active-ashby" in str(request.url):
            return httpx.Response(200, json={"jobs": [{"id": "1", "title": "Frontend Engineer"}]})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (await verify_ats_board(ATSProvider.ASHBY, "active-ashby", client)) == 1
        assert (await verify_ats_board(ATSProvider.ASHBY, "dead-ashby", client)) is None


@pytest.mark.asyncio
async def test_verify_ats_board_smartrecruiters() -> None:
    """Verify SmartRecruiters payload validation."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "sr-total" in str(request.url):
            return httpx.Response(200, json={"totalFound": 12, "content": [{"name": "Software Engineer"}]})
        elif "sr-content" in str(request.url):
            return httpx.Response(200, json={"totalFound": 0, "content": [{"id": "1", "name": "Backend Developer"}]})
        elif "sr-empty" in str(request.url):
            return httpx.Response(200, json={"totalFound": 0, "content": []})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (await verify_ats_board(ATSProvider.SMARTRECRUITERS, "sr-total", client)) == 12
        assert (await verify_ats_board(ATSProvider.SMARTRECRUITERS, "sr-content", client)) == 1
        assert (await verify_ats_board(ATSProvider.SMARTRECRUITERS, "sr-empty", client)) is None
        assert (await verify_ats_board(ATSProvider.SMARTRECRUITERS, "sr-404", client)) is None


@pytest.mark.asyncio
async def test_verify_ats_board_reserved_and_errors() -> None:
    """Verify reserved slugs and network errors are caught safely."""
    async with httpx.AsyncClient() as client:
        assert (await verify_ats_board(ATSProvider.GREENHOUSE, "embed", client)) is None
        assert (await verify_ats_board(ATSProvider.GREENHOUSE, "", client)) is None


# --- 5. URLScan Fetching & HTTP 429 Backoff Handling ---


@pytest.mark.asyncio
async def test_fetch_urlscan_candidates_with_429_backoff() -> None:
    """Verify URLScan search handles HTTP 429 backoff and extracts candidates."""
    attempt_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        if "greenhouse.io" in str(request.url):
            attempt_count += 1
            if attempt_count == 1:
                return httpx.Response(429, headers={"Retry-After": "0.01"}, text="Too Many Requests")
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "page": {
                                "domain": "boards.greenhouse.io",
                                "url": "https://boards.greenhouse.io/stripe/jobs/101",
                                "title": "Stripe Careers",
                            }
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await fetch_urlscan_candidates(
            client=client,
            providers=[ATSProvider.GREENHOUSE],
            backoff_retries=2,
        )
        assert len(results) == 1
        assert results[0][0] == ATSProvider.GREENHOUSE
        assert results[0][1] == "stripe"
        assert attempt_count == 2  # Proves 429 was handled and retried successfully


# --- 6. Append to Config & Dry Run Integrity ---


def test_append_verified_boards_to_config(tmp_path: Path) -> None:
    """Verify clean insertion of CompanyConfig entries into config.py before closing bracket."""
    sample_config = (
        'import re\n'
        'from gcc_job_radar.models import ATSProvider, CompanyConfig\n\n'
        'COMPANIES: list[CompanyConfig] = [\n'
        '    CompanyConfig(name="Existing Corp", provider=ATSProvider.GREENHOUSE, board_token="existing"),\n'
        ']\n\n'
        'INCLUDE_TITLE_PATTERN = re.compile(r"software")\n'
    )
    config_file = tmp_path / "config.py"
    config_file.write_text(sample_config, encoding="utf-8")

    new_boards = [
        VerifiedATSBoard(
            company_name="CrowdStrike",
            provider=ATSProvider.GREENHOUSE,
            board_token="crowdstrike",
            active_postings=45,
        ),
        VerifiedATSBoard(
            company_name="SentinelOne",
            provider=ATSProvider.LEVER,
            board_token="sentinelone",
            active_postings=20,
        ),
    ]

    appended_count = append_verified_boards_to_config(new_boards, config_path=config_file)
    assert appended_count == 2

    updated_content = config_file.read_text(encoding="utf-8")
    assert 'CompanyConfig(name="CrowdStrike", provider=ATSProvider.GREENHOUSE, board_token="crowdstrike"),' in updated_content
    assert 'CompanyConfig(name="SentinelOne", provider=ATSProvider.LEVER, board_token="sentinelone"),' in updated_content
    assert 'INCLUDE_TITLE_PATTERN' in updated_content

    # Running a second time with the same boards should not duplicate
    re_appended = append_verified_boards_to_config(new_boards, config_path=config_file)
    assert re_appended == 0


@pytest.mark.asyncio
async def test_run_harvest_mass_ats_dry_run_vs_append(tmp_path: Path) -> None:
    """Verify run_harvest_mass_ats honors dry_run flag and appends correctly when false."""
    sample_config = (
        'COMPANIES: list[CompanyConfig] = [\n'
        '    CompanyConfig(name="Alpha", provider=ATSProvider.GREENHOUSE, board_token="alpha"),\n'
        ']\n'
    )
    config_file = tmp_path / "config.py"
    config_file.write_text(sample_config, encoding="utf-8")

    # Mock custom input file
    custom_file = tmp_path / "targets.txt"
    custom_file.write_text("https://boards.greenhouse.io/beta\nhttps://jobs.lever.co/gamma\n", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "boards/beta/jobs" in url_str:
            return httpx.Response(200, json={"jobs": [{"id": 1, "title": "Software Engineer"}]})
        if "postings/gamma" in url_str:
            return httpx.Response(200, json=[{"id": "1", "text": "Backend Developer"}])
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    # 1. Test Dry Run
    dry_results = await run_harvest_mass_ats(
        source=str(custom_file),
        dry_run=True,
        config_path=config_file,
        client=mock_client,
    )
    assert len(dry_results) == 2
    # Verify file was NOT modified in dry run
    assert config_file.read_text(encoding="utf-8") == sample_config

    # 2. Test Real Run (append)
    real_results = await run_harvest_mass_ats(
        source=str(custom_file),
        dry_run=False,
        config_path=config_file,
        client=mock_client,
    )
    assert len(real_results) == 2
    updated = config_file.read_text(encoding="utf-8")
    assert 'board_token="beta"' in updated
    assert 'board_token="gamma"' in updated


@pytest.mark.asyncio
async def test_run_harvest_mass_ats_target_count_limit(tmp_path: Path) -> None:
    """Verify --target-count limits the number of verified boards discovered."""
    custom_file = tmp_path / "many_targets.txt"
    custom_file.write_text(
        "https://boards.greenhouse.io/one\nhttps://boards.greenhouse.io/two\nhttps://boards.greenhouse.io/three\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": [{"id": 1, "title": "Software Engineer"}]})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    results = await run_harvest_mass_ats(
        source=str(custom_file),
        target_count=1,
        dry_run=True,
        client=mock_client,
    )
    # Target count was 1, so it should stop at 1
    assert len(results) == 1


# --- 7. Tech-Role Gate in verify_ats_board ---


@pytest.mark.asyncio
async def test_verify_ats_board_rejects_pure_non_tech_board() -> None:
    """verify_ats_board returns None when all sampled job titles are non-tech disciplines."""
    payload = {
        "jobs": [
            {"id": 1, "title": "Mechanical Engineer Trainee"},
            {"id": 2, "title": "Civil Engineer"},
            {"id": 3, "title": "HVAC Technician"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await verify_ats_board(ATSProvider.GREENHOUSE, "mech-only-board", client)
        assert result is None, "Board with only non-tech titles must be rejected"


@pytest.mark.asyncio
async def test_verify_ats_board_accepts_pure_tech_board() -> None:
    """verify_ats_board returns count when all sampled job titles are tech roles."""
    payload = {
        "jobs": [
            {"id": 1, "title": "Software Engineer Trainee"},
            {"id": 2, "title": "Backend Developer"},
            {"id": 3, "title": "Data Engineer"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await verify_ats_board(ATSProvider.GREENHOUSE, "tech-board", client)
        assert result == 3, "Board with tech titles must be accepted and return count"


@pytest.mark.asyncio
async def test_verify_ats_board_accepts_mixed_board_with_at_least_one_tech_title() -> None:
    """verify_ats_board returns count when at least one of the sampled titles is a tech role."""
    payload = {
        "jobs": [
            {"id": 1, "title": "Mechanical Engineer Trainee"},   # non-tech
            {"id": 2, "title": "Associate Software Engineer"},    # tech ← at least 1 passes
            {"id": 3, "title": "Civil Engineer"},                 # non-tech
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await verify_ats_board(ATSProvider.GREENHOUSE, "mixed-board", client)
        assert result == 3, "Board with ≥1 tech title must be accepted"


@pytest.mark.asyncio
async def test_verify_ats_board_lever_rejects_non_tech() -> None:
    """verify_ats_board rejects a Lever board whose only postings are non-tech."""
    payload = [
        {"id": "abc", "text": "Sales Engineer"},
        {"id": "def", "text": "BDR Representative"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await verify_ats_board(ATSProvider.LEVER, "sales-board", client)
        assert result is None, "Lever board with only Sales/BDR titles must be rejected"


# --- 8. SimplifyJobs Feed Function ---


@pytest.mark.asyncio
async def test_fetch_simplifyjobs_candidates_extracts_ats_slugs() -> None:
    """fetch_simplifyjobs_candidates correctly extracts ATS slugs from SimplifyJobs JSON feeds."""
    from tools.harvest_mass_ats import SIMPLIFYJOBS_FEEDS, fetch_simplifyjobs_candidates

    sample_feed = [
        {
            "company_name": "Stripe Inc",
            "url": "https://boards.greenhouse.io/stripe/jobs/123",
        },
        {
            "company_name": "Atlassian",
            "url": "https://jobs.lever.co/atlassian/abc-def",
        },
        {
            "company_name": "MechCo",
            "url": "https://careers.example.com/jobs/123",  # Non-ATS URL, should be ignored
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        # Return the same feed for both feed URLs
        return httpx.Response(200, json=sample_feed)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candidates = await fetch_simplifyjobs_candidates(client)

    # Two ATS slugs should be extracted; the non-ATS URL should be dropped
    slugs = [(p, s) for _, p, s in candidates]
    assert (ATSProvider.GREENHOUSE, "stripe") in slugs
    assert (ATSProvider.LEVER, "atlassian") in slugs
    # The example.com URL is not a known ATS, must not appear
    assert all("example" not in s for _, s in slugs)


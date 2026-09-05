"""Unit tests for GitHub curated repositories ATS harvester (tools/harvest_github.py)."""

import pytest
from unittest.mock import AsyncMock, patch

from gcc_job_radar.models import ATSProvider
from tools.harvest_github import (
    DirectTokenCandidate,
    build_parser,
    extract_candidate_company_names,
    extract_direct_ats_links,
    is_generic_name,
    is_valid_company_name,
    validate_direct_candidate,
)
from tools.probe_ats import ProbeResult


def test_is_generic_name():
    assert is_generic_name("Apply") is True
    assert is_generic_name("apply") is True
    assert is_generic_name("Link") is True
    assert is_generic_name("Website") is True
    assert is_generic_name("https://example.com") is True
    assert is_generic_name("🔗") is True
    assert is_generic_name("x") is True

    assert is_generic_name("SpaceX") is False
    assert is_generic_name("Databricks") is False
    assert is_generic_name("Almabase") is False
    assert is_generic_name("10up") is False


def test_is_valid_company_name():
    assert is_valid_company_name("Almabase") is True
    assert is_valid_company_name("Adaface") is True
    assert is_valid_company_name("10up") is True
    assert is_valid_company_name("Axios") is True

    # Articles, interview guides, and meta-links should be rejected
    assert is_valid_company_name("HackerNews (2017)") is False
    assert is_valid_company_name("How to hire engineering talent without the BS") is False
    assert is_valid_company_name("Finding a better alternative to the whiteboard interview") is False
    assert is_valid_company_name("Table of Contents") is False
    assert is_valid_company_name("Apply") is False
    assert is_valid_company_name("12345") is False


def test_extract_direct_ats_links_markdown():
    sample_text = """
    ## Job Openings
    - [SpaceX](https://job-boards.greenhouse.io/spacex/jobs/12345)
    - [Sierra](https://jobs.ashbyhq.com/sierra?ref=list)
    - [Albert](https://jobs.lever.co/meetalbert/)
    - [Socotec](https://jobs.smartrecruiters.com/socotec/postings)
    - [Ignored Board](https://boards.greenhouse.io/jobs)
    - [Generic Text](https://boards.greenhouse.io/spacex)
    """
    candidates = extract_direct_ats_links(sample_text, source_label="test_source")
    
    # Should find 4 distinct valid candidates
    tokens = {(c.provider, c.token): c.company_name for c in candidates}
    assert (ATSProvider.GREENHOUSE, "spacex") in tokens
    assert tokens[(ATSProvider.GREENHOUSE, "spacex")] == "SpaceX"

    assert (ATSProvider.ASHBY, "sierra") in tokens
    assert tokens[(ATSProvider.ASHBY, "sierra")] == "Sierra"

    assert (ATSProvider.LEVER, "meetalbert") in tokens
    assert tokens[(ATSProvider.LEVER, "meetalbert")] == "Albert"

    assert (ATSProvider.SMARTRECRUITERS, "socotec") in tokens
    assert tokens[(ATSProvider.SMARTRECRUITERS, "socotec")] == "Socotec"

    # Ignored slugs should not be present
    assert (ATSProvider.GREENHOUSE, "jobs") not in tokens


def test_extract_direct_ats_links_html():
    sample_html = """
    <table>
      <tr>
        <td>SpaceX</td>
        <td><a href="https://boards.greenhouse.io/spacex">Apply Now</a></td>
      </tr>
      <tr>
        <td>Beacon</td>
        <td><a href="https://jobs.ashbyhq.com/beaconsoftware">Beacon Software</a></td>
      </tr>
    </table>
    """
    candidates = extract_direct_ats_links(sample_html, source_label="html_table")
    tokens = {(c.provider, c.token): c.company_name for c in candidates}

    # "Apply Now" is generic, so fallback to slug_to_name("spacex") -> "Spacex"
    assert (ATSProvider.GREENHOUSE, "spacex") in tokens
    assert tokens[(ATSProvider.GREENHOUSE, "spacex")] == "Spacex"

    assert (ATSProvider.ASHBY, "beaconsoftware") in tokens
    assert tokens[(ATSProvider.ASHBY, "beaconsoftware")] == "Beacon Software"


def test_extract_candidate_company_names():
    sample_markdown = """
    # Companies
    - [1000.software](https://www.1000.software/careers) | Krakow, Poland | 2 interviews
    - [Aalyria](https://ats.rippling.com/aalyria-careers/jobs) | Remote | Timeboxed design
    | [23andMe](https://www.23andme.com/careers/) | Mountain View, CA |
    | [Acquia](https://www.acquia.com/careers/open-positions) | Boston, MA |
    * [Adaface](https://www.adaface.com)
    - [How to hire engineering talent](https://example.com/guide) | Blog post
    """
    names = extract_candidate_company_names(sample_markdown)
    assert "1000.software" in names
    assert "Aalyria" in names
    assert "23andMe" in names
    assert "Acquia" in names
    assert "Adaface" in names
    assert "How to hire engineering talent" not in names


@pytest.mark.asyncio
async def test_validate_direct_candidate_success():
    import asyncio
    candidate = DirectTokenCandidate(
        company_name="SpaceX",
        provider=ATSProvider.GREENHOUSE,
        token="spacex",
        source_url="test",
    )
    semaphore = asyncio.Semaphore(5)

    with patch("tools.harvest_github.check_platform", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = 2334
        res = await validate_direct_candidate(candidate, AsyncMock(), semaphore)
        assert res is not None
        assert res.company_name == "SpaceX"
        assert res.provider == ATSProvider.GREENHOUSE
        assert res.board_token == "spacex"
        assert res.active_postings == 2334


@pytest.mark.asyncio
async def test_validate_direct_candidate_empty():
    import asyncio
    candidate = DirectTokenCandidate(
        company_name="Inactive Co",
        provider=ATSProvider.ASHBY,
        token="inactive",
        source_url="test",
    )
    semaphore = asyncio.Semaphore(5)

    with patch("tools.harvest_github.check_platform", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = 0
        res = await validate_direct_candidate(candidate, AsyncMock(), semaphore)
        assert res is None


def test_cli_parser_defaults():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.mode == "all"
    assert args.append is True
    assert args.dry_run is False
    assert args.limit is None
    assert args.concurrency == 40
    assert args.cluster == "3"


def test_cli_parser_overrides():
    parser = build_parser()
    args = parser.parse_args([
        "--mode", "direct-only",
        "--dry-run",
        "--limit", "10",
        "--concurrency", "25",
        "--no-append",
        "--sources", "simplify_internships", "hiring_no_whiteboards",
    ])
    assert args.mode == "direct-only"
    assert args.dry_run is True
    assert args.append is False
    assert args.limit == 10
    assert args.concurrency == 25
    assert args.sources == ["simplify_internships", "hiring_no_whiteboards"]

"""Unit tests for tools/check_links.py and the check-links CLI command."""

from pathlib import Path
import sqlite3
import ssl
from typing import Optional
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from gcc_job_radar.cli import app
from gcc_job_radar.db import init_db, mark_job_status
from tools.check_links import (
    SOFT_404_PHRASES,
    check_url,
    is_generic_career_redirect,
    validate_job_links,
    validate_job_links_async,
)

runner = CliRunner()


# --- Unit Tests: check_url & Mock Responses ---


@pytest.mark.asyncio
async def test_check_url_403_bot_challenge_head() -> None:
    """Verify HTTP 403 on HEAD is explicitly flagged as dead/bot challenge."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        is_dead, reason = await check_url(client, "https://www.glassdoor.com/job-listing/?jl=123")
        assert is_dead is True
        assert reason == "HTTP 403 / Bot Challenge"


@pytest.mark.asyncio
async def test_check_url_403_bot_challenge_get() -> None:
    """Verify HTTP 403 on GET (following HEAD 405) is flagged as dead/bot challenge."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(405, request=request)
        return httpx.Response(403, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        is_dead, reason = await check_url(client, "https://www.glassdoor.com/job-listing/?jl=123")
        assert is_dead is True
        assert reason == "HTTP 403 / Bot Challenge"


@pytest.mark.asyncio
async def test_check_url_404_head() -> None:
    """Verify HTTP 404 on HEAD is flagged as dead."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        is_dead, reason = await check_url(client, "https://boards.greenhouse.io/company/jobs/99999")
        assert is_dead is True
        assert reason == "HTTP 404"


@pytest.mark.asyncio
async def test_check_url_410_head() -> None:
    """Verify HTTP 410 Gone on HEAD is flagged as dead."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(410, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        is_dead, reason = await check_url(client, "https://jobs.lever.co/company/abc-123")
        assert is_dead is True
        assert reason == "HTTP 410"


@pytest.mark.asyncio
async def test_check_url_500_head() -> None:
    """Verify HTTP 500 on HEAD is flagged as dead."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        is_dead, reason = await check_url(client, "https://careers.google.com/jobs/results/123")
        assert is_dead is True
        assert reason == "HTTP 500"


@pytest.mark.asyncio
async def test_check_url_405_fallback_to_get_200() -> None:
    """Verify HTTP 405 on HEAD falls back to GET stream and succeeds if role is active."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(405, request=request)
        return httpx.Response(200, text="<html><body>Apply for Software Engineer 1</body></html>", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        is_dead, reason = await check_url(client, "https://amazon.jobs/en/jobs/12345")
        assert is_dead is False
        assert reason is None


@pytest.mark.asyncio
async def test_check_url_405_fallback_to_get_404() -> None:
    """Verify HTTP 405 on HEAD falls back to GET stream and catches 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(405, request=request)
        return httpx.Response(404, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        is_dead, reason = await check_url(client, "https://amazon.jobs/en/jobs/12345")
        assert is_dead is True
        assert reason == "HTTP 404"


@pytest.mark.asyncio
async def test_check_url_ssl_handshake_error() -> None:
    """Verify SSLError is gracefully caught and flagged as dead link without crashing."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise ssl.SSLError("SSL: CERTIFICATE_VERIFY_FAILED")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        is_dead, reason = await check_url(client, "https://broken-cert.example.com/job/1")
        assert is_dead is True
        assert reason == "SSL Handshake Failed"


@pytest.mark.asyncio
async def test_check_url_timeout_graceful() -> None:
    """Verify connection timeout is caught gracefully and does not falsely dismiss."""

    with patch("httpx.AsyncClient.head", side_effect=httpx.ConnectTimeout("Timed out")):
        with patch("httpx.AsyncClient.stream", side_effect=httpx.ConnectTimeout("Timed out")):
            async with httpx.AsyncClient() as client:
                is_dead, reason = await check_url(client, "https://slow-site.example.com/job/1")
                assert is_dead is False
                assert reason is None


@pytest.mark.asyncio
async def test_check_url_generic_career_redirect() -> None:
    """Verify redirect from deep posting link to generic /careers page is flagged."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://company.com/jobs/deep-link-12345":
            return httpx.Response(302, headers={"Location": "https://company.com/careers"}, request=request)
        return httpx.Response(200, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        is_dead, reason = await check_url(client, "https://company.com/jobs/deep-link-12345")
        assert is_dead is True
        assert reason == "Generic career redirect"


@pytest.mark.parametrize("phrase", SOFT_404_PHRASES)
@pytest.mark.asyncio
async def test_check_url_soft_404_phrases(phrase: str) -> None:
    """Verify all 5 soft-404 phrases are detected in first 16KB of response body."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, request=request)
        html_content = f"<html><body><h1>Notice</h1><p>Sorry, {phrase}! Check other openings.</p></body></html>"
        return httpx.Response(200, text=html_content, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        is_dead, reason = await check_url(client, "https://jobs.lever.co/company/role-123")
        assert is_dead is True
        assert reason == f"Soft-404 ({phrase})"


@pytest.mark.asyncio
async def test_check_url_valid_active_role() -> None:
    """Verify live 200 OK role without expired phrases returns active."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, request=request)
        html_content = "<html><body><h1>Associate Software Engineer</h1><p>Apply now!</p></body></html>"
        return httpx.Response(200, text=html_content, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        is_dead, reason = await check_url(client, "https://job-boards.greenhouse.io/celonis/jobs/7791267003")
        assert is_dead is False
        assert reason is None


@pytest.mark.asyncio
async def test_check_url_invalid_url() -> None:
    """Verify invalid or empty URLs are immediately flagged."""
    async with httpx.AsyncClient() as client:
        assert (await check_url(client, ""))[0] is True
        assert (await check_url(client, "ftp://example.com/job"))[0] is True
        assert (await check_url(client, "not a url"))[0] is True


def test_is_generic_career_redirect_helper() -> None:
    """Test URL comparison helper for generic career landing page redirects."""
    # Greenhouse deep link -> /jobs
    assert is_generic_career_redirect(
        "https://boards.greenhouse.io/stripe/jobs/12345",
        "https://boards.greenhouse.io/stripe/jobs",
    ) is True

    # Custom careers deep link -> /careers
    assert is_generic_career_redirect(
        "https://company.com/job/software-engineer-1",
        "https://company.com/careers",
    ) is True

    # Glassdoor query deep link -> /jobs
    assert is_generic_career_redirect(
        "https://www.glassdoor.com/job-listing/?jl=1010236056374",
        "https://www.glassdoor.com/jobs",
    ) is True

    # Same deep link (no redirect to generic page)
    assert is_generic_career_redirect(
        "https://company.com/careers/swe-1",
        "https://company.com/careers/swe-1",
    ) is False

    # Already was generic landing page
    assert is_generic_career_redirect(
        "https://company.com/careers",
        "https://company.com/careers",
    ) is False


# --- Database & Integration Tests ---


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database populated with test jobs."""
    db_file = tmp_path / "test_jobs.db"
    init_db(db_file)

    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO seen_jobs (id, company, title, location, apply_url, provider, status, notes)
            VALUES 
                ('job_dead_404', 'DeadCorp', 'Software Engineer I', 'Bengaluru', 'https://example.com/jobs/dead_404_role', 'greenhouse', 'NEW', NULL),
                ('job_dead_403', 'BotCorp', 'Associate Developer', 'Remote', 'https://example.com/jobs/dead_403_role', 'lever', 'NEW', NULL),
                ('job_expired_soft', 'ExpiredCorp', 'SDE 1', 'Hyderabad', 'https://example.com/jobs/expired_soft_role', 'ashby', 'NEW', 'Imported from alert'),
                ('job_active', 'ActiveCorp', 'Software Engineer 1', 'Pune', 'https://example.com/jobs/active_role', 'greenhouse', 'NEW', NULL),
                ('job_applied', 'AppliedCorp', 'Software Engineer 1', 'Bengaluru', 'https://example.com/jobs/dead_404_role', 'greenhouse', 'APPLIED', 'Referral submitted')
            """
        )
        conn.commit()

    return db_file


def test_validate_job_links_db_update(temp_db: Path) -> None:
    """Verify dead/blocked/expired jobs are dismissed in DB with formatted notes."""

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "dead_404_role" in url_str:
            return httpx.Response(404, request=request)
        if "dead_403_role" in url_str:
            return httpx.Response(403, request=request)
        if "expired_soft_role" in url_str:
            if request.method == "HEAD":
                return httpx.Response(200, request=request)
            return httpx.Response(200, text="Sorry, this posting has expired.", request=request)
        if request.method == "HEAD":
            return httpx.Response(200, request=request)
        return httpx.Response(200, text="Apply now for active role!", request=request)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    results = validate_job_links(db_path=temp_db, dry_run=False, client=mock_client)
    assert len(results) == 4  # Only 4 'NEW' jobs checked; 'APPLIED' job excluded

    with sqlite3.connect(temp_db) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Job 1 (404) -> DISMISSED
        cursor.execute("SELECT status, notes FROM seen_jobs WHERE id = 'job_dead_404'")
        row = cursor.fetchone()
        assert row["status"] == "DISMISSED"
        assert "Auto-dismissed (dead link: HTTP 404)" in row["notes"]

        # Job 2 (403) -> NEEDS_RESOLVE (fallback to direct ATS search)
        cursor.execute("SELECT status, notes, direct_search_url FROM seen_jobs WHERE id = 'job_dead_403'")
        row = cursor.fetchone()
        assert row["status"] == "NEEDS_RESOLVE"
        assert "Blocked/Redirected (HTTP 403 / Bot Challenge) - fallback to ATS search" in row["notes"]
        assert "BotCorp" in row["direct_search_url"]

        # Job 3 (Soft-404) -> DISMISSED with existing notes preserved
        cursor.execute("SELECT status, notes FROM seen_jobs WHERE id = 'job_expired_soft'")
        row = cursor.fetchone()
        assert row["status"] == "DISMISSED"
        assert row["notes"].startswith("Imported from alert | Auto-dismissed (dead link: Soft-404")

        # Job 4 (Active 200) -> remains NEW
        cursor.execute("SELECT status, notes FROM seen_jobs WHERE id = 'job_active'")
        row = cursor.fetchone()
        assert row["status"] == "NEW"
        assert row["notes"] is None

        # Job 5 (APPLIED) -> untouched
        cursor.execute("SELECT status, notes FROM seen_jobs WHERE id = 'job_applied'")
        row = cursor.fetchone()
        assert row["status"] == "APPLIED"
        assert row["notes"] == "Referral submitted"


def test_validate_job_links_dry_run(temp_db: Path) -> None:
    """Verify --dry-run reports dead jobs without modifying database records."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    results = validate_job_links(db_path=temp_db, dry_run=True, client=mock_client)
    assert len(results) == 4
    assert all(r.is_dead for r in results)
    assert all(not r.dismissed for r in results)

    with sqlite3.connect(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM seen_jobs WHERE status = 'NEW'")
        assert cursor.fetchone()[0] == 4  # All 4 remain NEW


def test_cli_check_links_command(temp_db: Path) -> None:
    """Verify CLI check-links command runs successfully."""
    result = runner.invoke(
        app,
        ["check-links", "--dry-run", "--limit", "2", "--db", str(temp_db)],
    )
    assert result.exit_code == 0
    assert "Link Validator (DRY RUN)" in result.stdout
    assert "Link Check Complete (DRY RUN)" in result.stdout

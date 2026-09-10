"""Unit tests for applied/dismissed company filtering in scan and direct link resolution."""

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from gcc_job_radar.ai_agent import get_configured_companies
from gcc_job_radar.bot_listener import handle_command
from gcc_job_radar.cli import app
from gcc_job_radar.config import CompanyConfig
from gcc_job_radar.db import (
    get_applied_and_dismissed_companies,
    init_db,
    mark_job_status,
    query_jobs,
    record_jobs,
)
from gcc_job_radar.link_resolver import resolve_effective_apply_url
from gcc_job_radar.models import ATSProvider, JobPosting

runner = CliRunner()


@pytest.fixture
def test_db_with_applied_and_dismissed(tmp_path: Path) -> Path:
    """Create a temporary database with APPLIED, DISMISSED, and NEW jobs."""
    db_file = tmp_path / "filter_test.db"
    init_db(db_file)

    jobs = [
        JobPosting(
            id="job_celonis_1",
            company="Celonis",
            title="Associate Java Engineer",
            location="Bengaluru",
            apply_url="https://job-boards.greenhouse.io/celonis/jobs/111",
            provider=ATSProvider.GREENHOUSE,
            published_date="2026-09-08",
        ),
        JobPosting(
            id="job_amazon_1",
            company="Amazon",
            title="Software Development Engineer I",
            location="Bengaluru",
            apply_url="https://amazon.jobs/jobs/222",
            provider=ATSProvider.AMAZON,
            published_date="2026-09-08",
        ),
        JobPosting(
            id="job_handshake_1",
            company="Handshake",
            title="Backend Engineer",
            location="Bengaluru",
            apply_url="https://jobs.ashbyhq.com/handshake/333",
            provider=ATSProvider.ASHBY,
            published_date="2026-09-08",
        ),
    ]
    record_jobs(jobs, db_path=db_file)
    mark_job_status("job_celonis_1", "APPLIED", db_path=db_file)
    mark_job_status("job_amazon_1", "DISMISSED", db_path=db_file)

    return db_file


def test_get_applied_and_dismissed_companies(test_db_with_applied_and_dismissed: Path) -> None:
    """Verify get_applied_and_dismissed_companies returns correct sets."""
    applied, dismissed = get_applied_and_dismissed_companies(test_db_with_applied_and_dismissed)
    assert "celonis" in applied
    assert "amazon" in dismissed
    assert "handshake" not in applied
    assert "handshake" not in dismissed


def test_query_jobs_exclusion(test_db_with_applied_and_dismissed: Path) -> None:
    """Verify query_jobs excludes applied/dismissed companies when flag is set."""
    db = test_db_with_applied_and_dismissed

    # Add another NEW job for Amazon to simulate a newly scraped role at a dismissed company
    new_amazon_job = JobPosting(
        id="job_amazon_2",
        company="Amazon",
        title="SDE-1 Rewards",
        location="Bengaluru",
        apply_url="https://amazon.jobs/jobs/444",
        provider=ATSProvider.AMAZON,
        published_date="2026-09-09",
    )
    record_jobs([new_amazon_job], db_path=db)

    # 1. By default with exclude flag, Amazon is excluded because it's a dismissed company
    filtered = query_jobs(
        status="NEW",
        exclude_applied_or_dismissed_companies=True,
        db_path=db,
    )
    companies = {j["company"].lower() for j in filtered}
    assert "handshake" in companies
    assert "amazon" not in companies
    assert "celonis" not in companies

    # 2. When explicit company is passed, exclusion is bypassed
    amazon_queried = query_jobs(
        company="Amazon",
        status="NEW",
        exclude_applied_or_dismissed_companies=True,
        db_path=db,
    )
    assert len(amazon_queried) == 1
    assert amazon_queried[0]["company"] == "Amazon"


@pytest.mark.asyncio
async def test_bot_scan_excludes_applied_and_dismissed(test_db_with_applied_and_dismissed: Path) -> None:
    """Verify Telegram /scan excludes applied and dismissed companies and reports hidden count."""
    db = test_db_with_applied_and_dismissed

    scanned_jobs = [
        JobPosting(
            id="job_celonis_2",
            company="Celonis",
            title="Associate Software Engineer",
            location="Bengaluru",
            apply_url="https://job-boards.greenhouse.io/celonis/jobs/555",
            provider=ATSProvider.GREENHOUSE,
            published_date="2026-09-09",
        ),
        JobPosting(
            id="job_amazon_3",
            company="Amazon",
            title="SDE-1 Platform",
            location="Bengaluru",
            apply_url="https://amazon.jobs/jobs/666",
            provider=ATSProvider.AMAZON,
            published_date="2026-09-09",
        ),
        JobPosting(
            id="job_handshake_2",
            company="Handshake",
            title="Software Engineer - Entry",
            location="Bengaluru",
            apply_url="https://jobs.ashbyhq.com/handshake/777",
            provider=ATSProvider.ASHBY,
            published_date="2026-09-09",
        ),
    ]

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    import gcc_job_radar.bot_listener as bl
    bl._last_scan_timestamp = 0.0
    bl._is_scanning = False

    with patch("gcc_job_radar.bot_listener.scan_all_companies", return_value=scanned_jobs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await handle_command("/scan", "123456", "token", "123456", client, db_path=db)

    assert len(captured_messages) == 2
    result_text = captured_messages[1]["text"]
    # Handshake is visible
    assert "Handshake" in result_text
    # Celonis & Amazon are hidden
    assert "Celonis" not in result_text
    assert "Amazon" not in result_text
    # Hidden count and /scan all hint is displayed
    assert "role(s) from already applied or dismissed companies were hidden" in result_text
    assert "/scan all" in result_text


@pytest.mark.asyncio
async def test_bot_scan_all_includes_all_companies(test_db_with_applied_and_dismissed: Path) -> None:
    """Verify Telegram /scan all displays all roles including applied/dismissed companies."""
    db = test_db_with_applied_and_dismissed

    scanned_jobs = [
        JobPosting(
            id="job_celonis_3",
            company="Celonis",
            title="Associate Software Engineer",
            location="Bengaluru",
            apply_url="https://job-boards.greenhouse.io/celonis/jobs/888",
            provider=ATSProvider.GREENHOUSE,
            published_date="2026-09-09",
        ),
        JobPosting(
            id="job_handshake_3",
            company="Handshake",
            title="Software Engineer - Entry",
            location="Bengaluru",
            apply_url="https://jobs.ashbyhq.com/handshake/999",
            provider=ATSProvider.ASHBY,
            published_date="2026-09-09",
        ),
    ]

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    import gcc_job_radar.bot_listener as bl
    bl._last_scan_timestamp = 0.0
    bl._is_scanning = False

    with patch("gcc_job_radar.bot_listener.scan_all_companies", return_value=scanned_jobs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await handle_command("/scan all", "123456", "token", "123456", client, db_path=db)

    assert len(captured_messages) == 2
    result_text = captured_messages[1]["text"]
    assert "Celonis" in result_text
    assert "Handshake" in result_text
    assert "All Verified Active Openings (Including Applied/Dismissed)" in result_text


def test_ai_agent_configured_companies_exclusion(test_db_with_applied_and_dismissed: Path) -> None:
    """Verify get_configured_companies filters applied/dismissed companies by default."""
    db = test_db_with_applied_and_dismissed

    active_comps = get_configured_companies(include_all=False, db_path=db)
    names = {c["name"].lower() for c in active_comps}
    assert "celonis" not in names
    assert "amazon" not in names

    all_comps = get_configured_companies(include_all=True, db_path=db)
    all_names = {c["name"].lower() for c in all_comps}
    assert "celonis" in all_names
    assert "amazon" in all_names


def test_direct_link_resolution_never_google_search() -> None:
    """Verify resolve_effective_apply_url redirects to platform or careers portal and NEVER to Google search."""
    # 1. Glassdoor job -> resolves directly to company careers portal (never Glassdoor)
    metaminds_job = {
        "company": "Metaminds Studio",
        "title": "Java Full Stack Developer Intern",
        "apply_url": "https://www.glassdoor.com/job-listing/?jl=1010244212412",
    }
    eff_url, search_url, label = resolve_effective_apply_url(metaminds_job)
    assert eff_url == "https://metaminds.studio"
    assert label == "Official Careers Portal"
    assert "glassdoor.com" not in eff_url

    # 2. Indeed job without known career portal -> redirects to Indeed platform
    indeed_job = {
        "company": "TechVenture Labs",
        "title": "Junior Python Developer",
        "apply_url": "https://www.indeed.com/viewjob?jk=abc12345",
    }
    eff_url, search_url, label = resolve_effective_apply_url(indeed_job)
    assert eff_url == "https://www.indeed.com/viewjob?jk=abc12345"
    assert label == "Apply on Indeed"
    assert "google.com/search" not in eff_url

    # 3. LinkedIn job without known career portal -> redirects to LinkedIn platform
    linkedin_job = {
        "company": "GrowthScale",
        "title": "Full Stack Engineer",
        "apply_url": "https://www.linkedin.com/jobs/view/987654321/",
    }
    eff_url, search_url, label = resolve_effective_apply_url(linkedin_job)
    assert eff_url == "https://www.linkedin.com/jobs/view/987654321/"
    assert label == "Apply on LinkedIn"
    assert "google.com/search" not in eff_url

    # 4. Known company (BT Group) -> redirects to official career portal
    bt_job = {
        "company": "BT Group",
        "title": "Associate Engineer",
        "apply_url": "https://www.glassdoor.com/job-listing/?jl=1010251913049",
    }
    eff_url, search_url, label = resolve_effective_apply_url(bt_job)
    assert eff_url == "https://jobs.bt.com"
    assert label == "Official Careers Portal"
    assert "google.com/search" not in eff_url


def test_cli_scan_company_filter(test_db_with_applied_and_dismissed: Path) -> None:
    """Verify CLI scan hides applied/dismissed companies by default and shows them with --all."""
    db = test_db_with_applied_and_dismissed

    jobs = [
        JobPosting(
            id="job_celonis_cli",
            company="Celonis",
            title="Associate Java Engineer",
            location="Bengaluru",
            apply_url="https://job-boards.greenhouse.io/celonis/jobs/111",
            provider=ATSProvider.GREENHOUSE,
            published_date="2026-09-08",
        ),
        JobPosting(
            id="job_amazon_cli",
            company="Amazon",
            title="Software Development Engineer I",
            location="Bengaluru",
            apply_url="https://amazon.jobs/jobs/222",
            provider=ATSProvider.AMAZON,
            published_date="2026-09-08",
        ),
        JobPosting(
            id="job_handshake_cli",
            company="Handshake",
            title="Backend Engineer",
            location="Bengaluru",
            apply_url="https://jobs.ashbyhq.com/handshake/333",
            provider=ATSProvider.ASHBY,
            published_date="2026-09-08",
        ),
    ]

    dummy_companies = [
        CompanyConfig(name="Celonis", provider=ATSProvider.GREENHOUSE, board_token="celonis"),
        CompanyConfig(name="Amazon", provider=ATSProvider.AMAZON, board_token="amazon"),
        CompanyConfig(name="Handshake", provider=ATSProvider.ASHBY, board_token="handshake"),
    ]

    with patch("gcc_job_radar.cli.scan_all_companies", return_value=jobs),          patch("gcc_job_radar.cli.COMPANIES", dummy_companies),          patch("gcc_job_radar.cli.dispatch_notifications"):

        # 1. Default scan: Celonis (applied) and Amazon (dismissed) are hidden
        res_default = runner.invoke(app, ["scan", "--db", str(db)])
        assert res_default.exit_code == 0
        assert "Handshake" in res_default.output
        assert "Celonis" not in res_default.output
        assert "Amazon" not in res_default.output

        # 2. Scan with --all: Celonis, Amazon, and Handshake are all displayed
        res_all = runner.invoke(app, ["scan", "--all", "--db", str(db)])
        assert res_all.exit_code == 0
        assert "Handshake" in res_all.output
        assert "Celonis" in res_all.output
        assert "Amazon" in res_all.output

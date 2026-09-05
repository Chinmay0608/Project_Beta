"""Unit tests for remote fresher / 0-2 YOE role detection and CLI filtering."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from gcc_job_radar.cli import app
from gcc_job_radar.filters import (
    is_entry_level,
    is_foreign_remote_location,
    is_potential_india_location,
    is_remote_location,
    is_remote_opening,
    matches_india_location,
    matches_target_title,
)
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobOpening, JobPosting

runner = CliRunner()


def test_genuine_remote_india_locations() -> None:
    """Verify detection of valid remote roles targeting India candidates."""
    valid_remote_locs = [
        "Remote - India",
        "India, Remote",
        "India - Remote",
        "Bengaluru (Remote)",
        "Pune (Hybrid / Remote)",
        "Anywhere in India",
        "Remote, India",
        "India (Work from home)",
        "APAC - Remote",
        "Remote, APAC",
    ]
    for loc in valid_remote_locs:
        assert is_remote_location(loc) is True, f"Failed for {loc}"
        assert is_foreign_remote_location(loc) is False, f"Falsely marked foreign: {loc}"
        assert is_potential_india_location(loc) is True, f"Failed potential India check: {loc}"
        assert matches_india_location(loc) is True, f"Failed matches_india_location: {loc}"

        job = JobPosting(
            id="test-1",
            company="TestCorp",
            title="Software Engineer 1",
            location=loc,
            apply_url="https://example.com/apply",
            provider=ATSProvider.GREENHOUSE,
        )
        assert is_remote_opening(job) is True
        # Verify JobOpening alias works identically
        opening = JobOpening(
            id="test-2",
            company="TestCorp",
            title="Software Engineer 1",
            location=loc,
            apply_url="https://example.com/apply",
            provider=ATSProvider.GREENHOUSE,
        )
        assert is_remote_opening(opening) is True


def test_rejection_of_foreign_remote_roles() -> None:
    """Verify rejection of foreign remote locations (US Remote, Remote - EMEA, etc.)."""
    foreign_remote_locs = [
        "US Remote",
        "Remote - US",
        "Remote - USA",
        "Remote (US)",
        "Remote, US",
        "Remote - EMEA",
        "EMEA Remote",
        "UK Remote",
        "Remote - UK",
        "Canada Remote",
        "Remote - Canada",
        "Remote - North America",
        "LATAM Remote",
        "Australia Remote",
        "Germany Remote",
    ]
    for loc in foreign_remote_locs:
        assert is_foreign_remote_location(loc) is True, f"Failed to detect foreign: {loc}"
        assert is_remote_location(loc) is False, f"Should not be valid India remote: {loc}"
        assert is_potential_india_location(loc) is False, f"Should fail India potential: {loc}"
        assert matches_india_location(loc) is False, f"Should fail matches_india_location: {loc}"

        job = JobPosting(
            id="test-foreign",
            company="ForeignCorp",
            title="Software Engineer 1",
            location=loc,
            apply_url="https://example.com/apply",
            provider=ATSProvider.GREENHOUSE,
            is_remote=False,
        )
        assert is_remote_opening(job) is False


def test_onsite_india_locations_not_remote() -> None:
    """Verify that traditional on-site Indian tech hub roles are not falsely tagged as remote."""
    onsite_locs = [
        "Bengaluru, Karnataka, India",
        "Bangalore",
        "Hyderabad, Telangana",
        "Pune, Maharashtra",
        "Gurgaon, Haryana",
        "Noida",
        "Mumbai",
        "Chennai, Tamil Nadu",
    ]
    for loc in onsite_locs:
        assert is_remote_location(loc) is False
        assert is_potential_india_location(loc) is True
        assert matches_india_location(loc) is True

        job = JobPosting(
            id="test-onsite",
            company="TechGCC",
            title="Associate Software Engineer",
            location=loc,
            apply_url="https://example.com/apply",
            provider=ATSProvider.ASHBY,
        )
        assert is_remote_opening(job) is False


def test_fresher_title_variations_and_experience_detection() -> None:
    """Verify detection of various fresher/entry-level designations and 0-2 YOE patterns."""
    fresher_titles = [
        "Associate Software Engineer",
        "Associate Engineer",
        "Graduate Engineer Trainee",
        "GET",
        "GET - Software",
        "SDE 1",
        "SDE-1",
        "SDE I",
        "Software Engineer 1",
        "Software Engineer I",
        "MTS 1",
        "MTS-1",
        "MTS I",
        "Junior Software Engineer",
        "Junior Developer",
        "Junior Engineer",
        "Analyst",
        "Technology Analyst",
        "Software Engineering Analyst",
        "New Grad Software Engineer",
        "Software Intern",
        "Tech Intern",
    ]
    for title in fresher_titles:
        assert is_entry_level(title) is True, f"Failed for title: {title}"

    # Verify content checking for 0-2 YOE
    generic_title = "Software Developer"
    assert is_entry_level(generic_title, "Requirements: 0-2 years of experience in Python") is True
    assert is_entry_level(generic_title, "Freshers eligible to apply. 2025 batch preferred.") is True
    assert is_entry_level(generic_title, "0-1 year of hands-on experience or relevant coursework") is True
    assert is_entry_level(generic_title, "No prior experience required. Training provided.") is True

    # Disqualifications
    assert is_entry_level("Senior Software Engineer") is False
    assert is_entry_level("Staff Software Engineer") is False
    assert is_entry_level("Lead Developer") is False
    assert is_entry_level(generic_title, "Minimum 4+ years of software development experience required") is False
    assert is_entry_level(generic_title, "3-5 years of hands-on experience") is False


def test_cli_scan_remote_only_flag(tmp_path: Path) -> None:
    """Verify CLI scan --remote-only filters out on-site roles."""
    sample_jobs = [
        JobPosting(
            id="job-1",
            company="RemoteCorp",
            title="Software Engineer I",
            location="Remote - India",
            apply_url="https://example.com/job1",
            provider=ATSProvider.GREENHOUSE,
            is_remote=True,
        ),
        JobPosting(
            id="job-2",
            company="OnsiteCorp",
            title="Associate Software Engineer",
            location="Bengaluru, India",
            apply_url="https://example.com/job2",
            provider=ATSProvider.GREENHOUSE,
            is_remote=False,
        ),
    ]

    dummy_company = CompanyConfig(name="RemoteCorp", provider=ATSProvider.GREENHOUSE, board_token="remotecorp")
    with patch("gcc_job_radar.cli.scan_all_companies") as mock_scan, \
         patch("gcc_job_radar.cli.COMPANIES", [dummy_company]):
        mock_scan.return_value = sample_jobs

        test_db = tmp_path / "test_remote.db"
        result = runner.invoke(app, ["scan", "--remote-only", "--company", "RemoteCorp", "--db", str(test_db)])
        assert result.exit_code == 0
        assert "RemoteCorp" in result.output
        assert "OnsiteCorp" not in result.output
        assert "100% remote" in result.output


def test_cli_list_command(tmp_path: Path) -> None:
    """Verify CLI list and list --remote-only functionality."""
    test_db = tmp_path / "test_list.db"
    sample_jobs = [
        JobPosting(
            id="job-remote",
            company="RemoteScale",
            title="Junior Engineer",
            location="India (Remote)",
            apply_url="https://example.com/job-remote",
            provider=ATSProvider.ASHBY,
            is_remote=True,
        ),
        JobPosting(
            id="job-onsite",
            company="CityTech",
            title="SDE 1",
            location="Hyderabad",
            apply_url="https://example.com/job-onsite",
            provider=ATSProvider.LEVER,
            is_remote=False,
        ),
    ]

    from gcc_job_radar.db import record_jobs
    record_jobs(sample_jobs, db_path=test_db)

    # Test full list
    res_all = runner.invoke(app, ["list", "--db", str(test_db)])
    assert res_all.exit_code == 0
    assert "RemoteScale" in res_all.output
    assert "CityTech" in res_all.output

    # Test remote-only list
    res_remote = runner.invoke(app, ["list", "--remote-only", "--db", str(test_db)])
    assert res_remote.exit_code == 0
    assert "RemoteScale" in res_remote.output
    assert "CityTech" not in res_remote.output

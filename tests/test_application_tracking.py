"""Unit tests for Job Application Tracking & Filtering in gcc-job-radar."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from gcc_job_radar.cli import app
from gcc_job_radar.db import (
    get_job_by_id,
    get_jobs_by_status,
    init_db,
    mark_job_status,
    query_jobs,
    record_jobs,
)
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting, JobStatus

runner = CliRunner()


@pytest.fixture
def sample_jobs() -> list[JobPosting]:
    return [
        JobPosting(
            id="job-101",
            company="Databricks",
            title="Software Engineer 1",
            location="Bengaluru, India",
            apply_url="https://boards.greenhouse.io/databricks/jobs/101",
            published_date="2026-09-01",
            provider=ATSProvider.GREENHOUSE,
        ),
        JobPosting(
            id="job-202",
            company="Atlassian",
            title="Associate Software Engineer",
            location="Pune, India",
            apply_url="https://jobs.lever.co/atlassian/job-202",
            published_date="2026-08-30",
            provider=ATSProvider.LEVER,
        ),
        JobPosting(
            id="job-303",
            company="Handshake",
            title="Junior Software Engineer",
            location="Remote - India",
            apply_url="https://jobs.ashbyhq.com/handshake/303",
            published_date="2026-09-02",
            provider=ATSProvider.ASHBY,
            is_remote=True,
        ),
    ]


def test_job_status_transitions_and_metadata(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify state transitions: NEW -> APPLIED -> DISMISSED and field persistence."""
    test_db = tmp_path / "test_status.db"
    record_jobs(sample_jobs, db_path=test_db)

    # 1. Verify default status is NEW
    job1 = get_job_by_id(sample_jobs[0].id, db_path=test_db)
    assert job1 is not None
    assert job1["status"] == "NEW"
    assert job1["applied_at"] is None
    assert job1["notes"] is None
    numeric_id = job1["numeric_id"]
    assert numeric_id is not None

    # 2. Mark as APPLIED by numeric rowid with notes
    ok = mark_job_status(numeric_id, "APPLIED", notes="Referred by Sarah", db_path=test_db)
    assert ok is True

    applied_job = get_job_by_id(numeric_id, db_path=test_db)
    assert applied_job is not None
    assert applied_job["status"] == "APPLIED"
    assert applied_job["applied_at"] is not None
    assert applied_job["notes"] == "Referred by Sarah"

    # 3. Mark as DISMISSED by string id
    ok2 = mark_job_status(sample_jobs[1].id, "DISMISSED", db_path=test_db)
    assert ok2 is True

    dismissed_job = get_job_by_id(sample_jobs[1].id, db_path=test_db)
    assert dismissed_job is not None
    assert dismissed_job["status"] == "DISMISSED"

    # 4. Reject invalid status
    with pytest.raises(ValueError, match="Invalid status"):
        mark_job_status(numeric_id, "EXPLODED", db_path=test_db)

    # 5. Non-existent job returns False
    assert mark_job_status(99999, "APPLIED", db_path=test_db) is False


def test_get_jobs_by_status(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify querying jobs by specific status partitions."""
    test_db = tmp_path / "test_by_status.db"
    record_jobs(sample_jobs, db_path=test_db)

    # Set statuses: Databricks -> APPLIED, Atlassian -> DISMISSED, Handshake -> NEW
    mark_job_status(sample_jobs[0].id, "APPLIED", db_path=test_db)
    mark_job_status(sample_jobs[1].id, "DISMISSED", db_path=test_db)

    applied_list = get_jobs_by_status("APPLIED", db_path=test_db)
    assert len(applied_list) == 1
    assert applied_list[0]["company"] == "Databricks"

    dismissed_list = get_jobs_by_status("DISMISSED", db_path=test_db)
    assert len(dismissed_list) == 1
    assert dismissed_list[0]["company"] == "Atlassian"

    new_list = get_jobs_by_status("NEW", db_path=test_db)
    assert len(new_list) == 1
    assert new_list[0]["company"] == "Handshake"

    all_list = get_jobs_by_status("ALL", db_path=test_db)
    assert len(all_list) == 3


def test_record_jobs_preserves_status_on_rescanning(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify that daily ATS scans and record_jobs do NOT overwrite APPLIED/DISMISSED status."""
    test_db = tmp_path / "test_preserve.db"
    record_jobs(sample_jobs, db_path=test_db)

    # User applied to job 1
    mark_job_status(sample_jobs[0].id, "APPLIED", notes="Sent portfolio", db_path=test_db)
    applied_before = get_job_by_id(sample_jobs[0].id, db_path=test_db)
    assert applied_before["status"] == "APPLIED"
    applied_time = applied_before["applied_at"]

    # Daily scan discovers the exact same active job again and calls record_jobs
    record_jobs(sample_jobs, db_path=test_db)

    applied_after = get_job_by_id(sample_jobs[0].id, db_path=test_db)
    assert applied_after["status"] == "APPLIED"
    assert applied_after["applied_at"] == applied_time
    assert applied_after["notes"] == "Sent portfolio"


def test_cli_apply_command(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify CLI 'apply' marks job and records notes."""
    test_db = tmp_path / "test_cli_apply.db"
    record_jobs(sample_jobs, db_path=test_db)

    job = get_job_by_id(sample_jobs[0].id, db_path=test_db)
    num_id = job["numeric_id"]

    # Apply with notes
    res = runner.invoke(app, ["apply", str(num_id), "--notes", "Applied on company careers portal", "--db", str(test_db)])
    assert res.exit_code == 0
    assert "Marked job" in res.output
    assert "APPLIED" in res.output
    assert "Applied on company careers portal" in res.output

    # Check database
    updated = get_job_by_id(num_id, db_path=test_db)
    assert updated["status"] == "APPLIED"
    assert updated["notes"] == "Applied on company careers portal"
    assert updated["applied_at"] is not None

    # Invalid job ID error handling
    res_err = runner.invoke(app, ["apply", "9999", "--db", str(test_db)])
    assert res_err.exit_code == 1
    assert "Error:" in res_err.output
    assert "not found" in res_err.output


def test_cli_dismiss_and_hide_commands(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify CLI 'dismiss' and 'hide' marks job as DISMISSED."""
    test_db = tmp_path / "test_cli_dismiss.db"
    record_jobs(sample_jobs, db_path=test_db)

    job1 = get_job_by_id(sample_jobs[0].id, db_path=test_db)
    job2 = get_job_by_id(sample_jobs[1].id, db_path=test_db)

    # 1. Dismiss command
    res1 = runner.invoke(app, ["dismiss", str(job1["numeric_id"]), "--db", str(test_db)])
    assert res1.exit_code == 0
    assert "DISMISSED" in res1.output
    assert "no longer appear in scans" in " ".join(res1.output.split())

    updated1 = get_job_by_id(job1["numeric_id"], db_path=test_db)
    assert updated1["status"] == "DISMISSED"

    # 2. Hide alias command
    res2 = runner.invoke(app, ["hide", str(job2["numeric_id"]), "--db", str(test_db)])
    assert res2.exit_code == 0
    assert "DISMISSED" in res2.output

    updated2 = get_job_by_id(job2["numeric_id"], db_path=test_db)
    assert updated2["status"] == "DISMISSED"


def test_scan_ignores_applied_and_dismissed_jobs_by_default(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify CLI 'scan' automatically filters out already APPLIED and DISMISSED jobs."""
    test_db = tmp_path / "test_scan_filter.db"
    record_jobs(sample_jobs, db_path=test_db)

    # Mark Databricks as APPLIED, Atlassian as DISMISSED
    mark_job_status(sample_jobs[0].id, "APPLIED", db_path=test_db)
    mark_job_status(sample_jobs[1].id, "DISMISSED", db_path=test_db)

    dummy_companies = [
        CompanyConfig(name="Databricks", provider=ATSProvider.GREENHOUSE, board_token="databricks"),
        CompanyConfig(name="Atlassian", provider=ATSProvider.LEVER, board_token="atlassian"),
        CompanyConfig(name="Handshake", provider=ATSProvider.ASHBY, board_token="handshake"),
    ]

    with patch("gcc_job_radar.cli.scan_all_companies") as mock_scan, \
         patch("gcc_job_radar.cli.COMPANIES", dummy_companies), \
         patch("gcc_job_radar.cli.dispatch_notifications"):
        mock_scan.return_value = sample_jobs

        res = runner.invoke(app, ["scan", "--db", str(test_db)])
        assert res.exit_code == 0
        # Only Handshake (NEW) should be displayed in results
        assert "Handshake" in res.output
        assert "Databricks" not in res.output
        assert "Atlassian" not in res.output


def test_list_command_status_filtering(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify CLI 'list' default excludes applied/dismissed, and respects --status flag."""
    test_db = tmp_path / "test_list_filter.db"
    record_jobs(sample_jobs, db_path=test_db)

    mark_job_status(sample_jobs[0].id, "APPLIED", db_path=test_db)
    mark_job_status(sample_jobs[1].id, "DISMISSED", db_path=test_db)

    # 1. Default list shows only NEW (unapplied) jobs
    res_default = runner.invoke(app, ["list", "--db", str(test_db)])
    assert res_default.exit_code == 0
    assert "Handshake" in res_default.output
    assert "Databricks" not in res_default.output
    assert "Atlassian" not in res_default.output

    # 2. List with --status applied
    res_applied = runner.invoke(app, ["list", "--status", "applied", "--db", str(test_db)])
    assert res_applied.exit_code == 0
    assert "Databricks" in res_applied.output
    assert "Handshake" not in res_applied.output
    assert "Atlassian" not in res_applied.output

    # 3. List with --status dismissed
    res_dismissed = runner.invoke(app, ["list", "--status", "dismissed", "--db", str(test_db)])
    assert res_dismissed.exit_code == 0
    assert "Atlassian" in res_dismissed.output
    assert "Databricks" not in res_dismissed.output
    assert "Handshake" not in res_dismissed.output

    # 4. List with --status all
    res_all = runner.invoke(app, ["list", "--status", "all", "--db", str(test_db)])
    assert res_all.exit_code == 0
    assert "Handshake" in res_all.output
    assert "Databricks" in res_all.output
    assert "Atlassian" in res_all.output


def test_numeric_id_exact_rowid_no_wildcard_collision(tmp_path: Path) -> None:
    """Regression test: numeric ID '3' must target rowid 3 and NOT match rowid 1 ending in '3'."""
    test_db = tmp_path / "test_collision.db"
    jobs = [
        JobPosting(
            id="greenhouse_celonis_7791267003",  # Ends in '3'
            company="Celonis",
            title="Associate Software Engineer - Java",
            location="Bangalore, India",
            apply_url="https://boards.greenhouse.io/celonis/jobs/7791267003",
            published_date="2026-09-01",
            provider=ATSProvider.GREENHOUSE,
        ),
        JobPosting(
            id="ashby_aiprise_102",
            company="AiPrise",
            title="Software Engineer I",
            location="Bengaluru, India",
            apply_url="https://jobs.ashbyhq.com/aiprise/102",
            published_date="2026-09-01",
            provider=ATSProvider.ASHBY,
        ),
        JobPosting(
            id="lever_veeva_90e4e761",
            company="Veeva",
            title="Associate Software Engineer - Release Engineer",
            location="India - Hyderabad",
            apply_url="https://jobs.lever.co/veeva/90e4e761",
            published_date="2026-09-01",
            provider=ATSProvider.LEVER,
        ),
    ]
    record_jobs(jobs, db_path=test_db)

    # Verify rowids: Celonis is 1, AiPrise is 2, Veeva is 3
    celonis = get_job_by_id(1, db_path=test_db)
    assert celonis["company"] == "Celonis"
    veeva = get_job_by_id(3, db_path=test_db)
    assert veeva["company"] == "Veeva"

    # Invoke dismiss 3 via CLI
    res = runner.invoke(app, ["dismiss", "3", "--db", str(test_db)])
    assert res.exit_code == 0
    assert "Veeva" in res.output
    assert "Celonis" not in res.output

    # Verify Veeva is DISMISSED and Celonis remains NEW
    veeva_updated = get_job_by_id(3, db_path=test_db)
    assert veeva_updated["status"] == "DISMISSED"
    celonis_updated = get_job_by_id(1, db_path=test_db)
    assert celonis_updated["status"] == "NEW"

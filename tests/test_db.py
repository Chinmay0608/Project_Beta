"""Unit tests for SQLite state tracking and persistence."""

from pathlib import Path
import sqlite3
import pytest

from gcc_job_radar.db import filter_new_jobs, get_stats, init_db, make_job_key, record_jobs
from gcc_job_radar.models import ATSProvider, JobPosting


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
    ]


def test_init_db(tmp_path: Path) -> None:
    """Verify schema initialization and index creation."""
    db_file = tmp_path / "test_jobs.db"
    init_db(db_file)
    assert db_file.exists()

    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        assert cursor.fetchone() is not None

        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_seen_jobs_company'")
        assert cursor.fetchone() is not None


def test_filter_and_record_jobs(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify first run flags all as new, second run flags as existing."""
    db_file = tmp_path / "test_jobs.db"

    # 1. First run: all should be new
    new_jobs, existing_jobs = filter_new_jobs(sample_jobs, db_file)
    assert len(new_jobs) == 2
    assert len(existing_jobs) == 0

    # 2. Record jobs into DB
    record_jobs(sample_jobs, db_file)

    # 3. Second run: all should be existing
    new_jobs, existing_jobs = filter_new_jobs(sample_jobs, db_file)
    assert len(new_jobs) == 0
    assert len(existing_jobs) == 2

    # 4. Introduce a 3rd new job
    job_3 = JobPosting(
        id="job-303",
        company="Linear",
        title="Junior Software Engineer",
        location="Remote - India",
        apply_url="https://jobs.ashbyhq.com/linear/job-303",
        published_date="2026-08-25",
        provider=ATSProvider.ASHBY,
    )
    mixed_jobs = sample_jobs + [job_3]
    new_jobs, existing_jobs = filter_new_jobs(mixed_jobs, db_file)
    assert len(new_jobs) == 1
    assert new_jobs[0].id == "job-303"
    assert len(existing_jobs) == 2


def test_get_stats(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify stats calculation and company breakdown."""
    db_file = tmp_path / "test_jobs.db"

    stats_empty = get_stats(db_file)
    assert stats_empty["total_tracked"] == 0
    assert stats_empty["company_breakdown"] == {}

    record_jobs(sample_jobs, db_file)
    stats = get_stats(db_file)
    assert stats["total_tracked"] == 2
    assert stats["company_breakdown"]["Databricks"] == 1
    assert stats["company_breakdown"]["Atlassian"] == 1
    assert stats["first_recorded"] is not None

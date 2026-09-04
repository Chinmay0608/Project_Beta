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


def test_dispatched_alerts_deduplication(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify dispatched_alerts table prevents duplicate notifications per platform."""
    from gcc_job_radar.db import (
        filter_unalerted_jobs,
        record_dispatched_alert,
        record_dispatched_alerts,
    )

    db_file = tmp_path / "test_alerts.db"

    # Initially, all jobs are unalerted for Telegram
    unalerted_tg = filter_unalerted_jobs(sample_jobs, "telegram", db_file)
    assert len(unalerted_tg) == 2

    # Record first job as alerted for Telegram
    from gcc_job_radar.db import make_job_key

    record_dispatched_alert(make_job_key(sample_jobs[0]), "telegram", db_file)

    # Now only 1 job should be unalerted for Telegram
    unalerted_tg = filter_unalerted_jobs(sample_jobs, "telegram", db_file)
    assert len(unalerted_tg) == 1
    assert unalerted_tg[0].id == sample_jobs[1].id

    # Discord should still have 2 unalerted jobs (different platform)
    unalerted_dc = filter_unalerted_jobs(sample_jobs, "discord", db_file)
    assert len(unalerted_dc) == 2

    # Atomically record remaining for Telegram
    record_dispatched_alerts(unalerted_tg, "telegram", db_file)
    unalerted_tg_final = filter_unalerted_jobs(sample_jobs, "telegram", db_file)
    assert len(unalerted_tg_final) == 0


def test_canonicalize_url() -> None:
    """Verify URL canonicalization removes query params, tracking, and trailing slashes."""
    from gcc_job_radar.db import canonicalize_url

    url_1 = "https://job-boards.greenhouse.io/celonis/jobs/7791267003?gh_jid=7791267003"
    url_2 = "https://job-boards.greenhouse.io/celonis/jobs/7791267003/"
    assert canonicalize_url(url_1) == "https://job-boards.greenhouse.io/celonis/jobs/7791267003"
    assert canonicalize_url(url_2) == "https://job-boards.greenhouse.io/celonis/jobs/7791267003"


def test_record_jobs_semantic_deduplication(tmp_path: Path) -> None:
    """Verify recording duplicate jobs with different IDs/URLs updates existing row and does not duplicate."""
    from gcc_job_radar.db import get_latest_jobs, query_jobs

    db_file = tmp_path / "test_dedup.db"

    job_a = JobPosting(
        id="7791267003",
        company="Celonis",
        title="Associate Software Engineer - Java",
        location="Bangalore, India",
        apply_url="https://job-boards.greenhouse.io/celonis/jobs/7791267003?gh_jid=7791267003",
        published_date="2026-08-25",
        provider=ATSProvider.GREENHOUSE,
    )

    # Identical role with different ID and clean URL
    job_b = JobPosting(
        id="test-101",
        company="Celonis",
        title="Associate Software Engineer - Java",
        location="Bangalore, India",
        apply_url="https://job-boards.greenhouse.io/celonis/jobs/7791267003",
        published_date="2026-08-25",
        provider=ATSProvider.GREENHOUSE,
    )

    # Record first job
    record_jobs([job_a], db_file)
    assert get_stats(db_file)["total_tracked"] == 1

    # Record duplicate job
    record_jobs([job_b], db_file)
    assert get_stats(db_file)["total_tracked"] == 1

    # Query should return exactly 1 result
    latest = get_latest_jobs(limit=10, db_path=db_file)
    assert len(latest) == 1
    assert latest[0]["company"] == "Celonis"

    queried = query_jobs(company="Celonis", db_path=db_file)
    assert len(queried) == 1


def test_cleanup_duplicate_jobs(tmp_path: Path) -> None:
    """Verify cleanup_duplicate_jobs deletes duplicates and keeps the latest entry."""
    from gcc_job_radar.db import cleanup_duplicate_jobs

    db_file = tmp_path / "test_cleanup.db"

    # Simulate legacy table without unique index
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE seen_jobs (
                id TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT NOT NULL,
                apply_url TEXT NOT NULL,
                provider TEXT NOT NULL,
                published_date TEXT,
                is_active INTEGER DEFAULT 1,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute(
            """
            INSERT INTO seen_jobs (id, company, title, location, apply_url, provider, published_date, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '2026-09-01 10:00:00', '2026-09-01 10:00:00')
            """,
            ("dup-1", "Celonis", "Associate Software Engineer - Java", "Bangalore, India", "https://job-boards.greenhouse.io/celonis/jobs/7791267003?gh_jid=7791267003", "greenhouse", "2026-08-25"),
        )
        cursor.execute(
            """
            INSERT INTO seen_jobs (id, company, title, location, apply_url, provider, published_date, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '2026-09-02 10:00:00', '2026-09-02 12:00:00')
            """,
            ("dup-2", "Celonis", "Associate Software Engineer - Java", "Bangalore, India", "https://job-boards.greenhouse.io/celonis/jobs/7791267003", "greenhouse", "2026-08-25"),
        )
        conn.commit()

    # Verify 2 rows initially
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM seen_jobs")
        assert cursor.fetchone()[0] == 2

    # Run cleanup
    cleaned = cleanup_duplicate_jobs(db_file)
    assert cleaned >= 1

    # Verify only 1 row remains (the newest one)
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, apply_url, last_seen_at FROM seen_jobs")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "dup-2"
        assert rows[0][1] == "https://job-boards.greenhouse.io/celonis/jobs/7791267003"

    # Now init_db should successfully create the unique index on the cleaned table
    init_db(db_file)
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_seen_jobs_company_title_loc'")
        assert cursor.fetchone() is not None




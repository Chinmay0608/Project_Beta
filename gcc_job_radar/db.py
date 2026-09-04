"""SQLite state tracking and persistence for GCC Job Radar."""

from pathlib import Path
import sqlite3
from typing import Any, Optional

from gcc_job_radar.models import ATSProvider, JobPosting

DEFAULT_DB_PATH = Path("gcc_jobs.db")


def get_db_path(custom_path: Optional[Path] = None) -> Path:
    """Resolve active SQLite database path."""
    return custom_path if custom_path is not None else DEFAULT_DB_PATH


def make_job_key(job: JobPosting) -> str:
    """Build unique canonical key for a posting across all ATS providers."""
    return f"{job.provider.value}_{job.company.lower()}_{job.id}".strip()


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize SQLite database tables and indexes."""
    target_path = get_db_path(db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_jobs (
                id TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT NOT NULL,
                apply_url TEXT NOT NULL,
                provider TEXT NOT NULL,
                published_date TEXT,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_seen_jobs_company ON seen_jobs(company);
            """
        )
        conn.commit()


def filter_new_jobs(
    jobs: list[JobPosting], db_path: Optional[Path] = None
) -> tuple[list[JobPosting], list[JobPosting]]:
    """Partition jobs into newly discovered postings and previously seen postings."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    if not jobs:
        return [], []

    keys = [make_job_key(j) for j in jobs]
    placeholders = ",".join("?" for _ in keys)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT id FROM seen_jobs WHERE id IN ({placeholders})",
            keys,
        )
        seen_ids = {row[0] for row in cursor.fetchall()}

    new_jobs: list[JobPosting] = []
    existing_jobs: list[JobPosting] = []

    for job in jobs:
        if make_job_key(job) in seen_ids:
            existing_jobs.append(job)
        else:
            new_jobs.append(job)

    return new_jobs, existing_jobs


def record_jobs(jobs: list[JobPosting], db_path: Optional[Path] = None) -> None:
    """Record newly seen jobs and update last_seen_at timestamps for active ones."""
    if not jobs:
        return

    init_db(db_path)
    target_path = get_db_path(db_path)

    records = [
        (
            make_job_key(j),
            j.company,
            j.title,
            j.location,
            str(j.apply_url),
            j.provider.value,
            j.published_date or "Active",
        )
        for j in jobs
    ]

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT INTO seen_jobs (
                id, company, title, location, apply_url, provider, published_date, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                last_seen_at = CURRENT_TIMESTAMP,
                title = excluded.title,
                location = excluded.location,
                apply_url = excluded.apply_url,
                published_date = excluded.published_date;
            """,
            records,
        )
        conn.commit()


def get_stats(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Retrieve historical tracking stats from the database."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM seen_jobs")
        total_tracked = cursor.fetchone()[0]

        cursor.execute("SELECT MIN(first_seen_at), MAX(last_seen_at) FROM seen_jobs")
        first_recorded, last_active = cursor.fetchone()

        cursor.execute(
            """
            SELECT company, COUNT(*) 
            FROM seen_jobs 
            GROUP BY company 
            ORDER BY COUNT(*) DESC, company ASC
            """
        )
        company_counts = dict(cursor.fetchall())

    return {
        "total_tracked": total_tracked,
        "company_breakdown": company_counts,
        "first_recorded": first_recorded,
        "last_active": last_active,
        "db_path": str(target_path.resolve()),
    }

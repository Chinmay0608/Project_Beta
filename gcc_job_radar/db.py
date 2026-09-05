from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Optional, Union
import urllib.parse

from gcc_job_radar.models import ATSProvider, JobPosting

DEFAULT_DB_PATH = Path("gcc_jobs.db")


def get_db_path(custom_path: Optional[Path] = None) -> Path:
    """Resolve active SQLite database path."""
    return custom_path if custom_path is not None else DEFAULT_DB_PATH


def canonicalize_url(url: str) -> str:
    """Normalize and strip tracking query parameters (gh_jid, utm_*, etc.) and trailing slashes."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(str(url).strip())
        path = parsed.path.rstrip("/")
        # Standard ATS paths identify the job listing.
        # Query parameters are tracking, referral, or duplication artifacts.
        clean_url = urllib.parse.urlunparse(
            (parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", "")
        )
        return clean_url
    except Exception:
        return str(url).strip().rstrip("/")


def make_job_key(job: JobPosting) -> str:
    """Build unique canonical key for a posting across all ATS providers."""
    return f"{job.provider.value}_{job.company.lower()}_{job.id}".strip()


def cleanup_duplicate_jobs(db_path: Optional[Path] = None) -> int:
    """Delete duplicate job postings from seen_jobs, retaining the newest last_seen_at record."""
    target_path = get_db_path(db_path)
    if not target_path.exists():
        return 0

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        if not cursor.fetchone():
            return 0

        # Step 1: Normalize all existing apply_url values
        cursor.execute("SELECT id, apply_url FROM seen_jobs")
        rows = cursor.fetchall()
        updates = []
        for row_id, raw_url in rows:
            clean = canonicalize_url(raw_url)
            if clean != raw_url:
                updates.append((clean, row_id))
        if updates:
            cursor.executemany("UPDATE seen_jobs SET apply_url = ? WHERE id = ?", updates)

        # Step 2: Delete duplicate records by canonical apply_url (keeping newest last_seen_at)
        cursor.execute(
            """
            DELETE FROM seen_jobs
            WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY lower(company), lower(apply_url)
                               ORDER BY last_seen_at DESC, first_seen_at DESC, id ASC
                           ) as rn
                    FROM seen_jobs
                ) WHERE rn = 1
            );
            """
        )
        deleted_by_url = cursor.rowcount

        # Step 3: Delete duplicate records by semantic role (company, lower(title), lower(location))
        cursor.execute(
            """
            DELETE FROM seen_jobs
            WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY lower(company), lower(title), lower(location)
                               ORDER BY last_seen_at DESC, first_seen_at DESC, id ASC
                           ) as rn
                    FROM seen_jobs
                ) WHERE rn = 1
            );
            """
        )
        deleted_by_semantic = cursor.rowcount
        conn.commit()

        return deleted_by_url + deleted_by_semantic


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize SQLite database tables, indexes, and run deduplication cleanup."""
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
                is_active INTEGER DEFAULT 1,
                is_remote INTEGER DEFAULT 0,
                status TEXT DEFAULT 'NEW',
                applied_at TIMESTAMP NULL,
                notes TEXT NULL,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        # Migrate columns if missing from earlier migrations
        for tbl in ("seen_jobs", "jobs"):
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{tbl}'")
            if cursor.fetchone():
                cursor.execute(f"PRAGMA table_info({tbl})")
                columns = [row[1] for row in cursor.fetchall()]
                if "is_active" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN is_active INTEGER DEFAULT 1")
                if "is_remote" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN is_remote INTEGER DEFAULT 0")
                if "status" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN status TEXT DEFAULT 'NEW'")
                if "applied_at" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN applied_at TIMESTAMP NULL")
                if "notes" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN notes TEXT NULL")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_seen_jobs_status ON seen_jobs(status);")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
        if not cursor.fetchone():
            cursor.execute("CREATE VIEW IF NOT EXISTS jobs AS SELECT rowid AS numeric_id, * FROM seen_jobs;")

        # Backfill is_remote for any pre-existing records matching remote patterns
        cursor.execute(
            """
            UPDATE seen_jobs 
            SET is_remote = 1 
            WHERE is_remote = 0 
              AND (
                  lower(location) LIKE '%remote%' 
                  OR lower(location) LIKE '%wfh%' 
                  OR lower(location) LIKE '%work from home%' 
                  OR lower(location) LIKE '%distributed%'
                  OR lower(location) LIKE '%anywhere in india%'
              )
              AND NOT (
                  lower(location) LIKE '%us remote%'
                  OR lower(location) LIKE '%remote - us%'
                  OR lower(location) LIKE '%remote (us)%'
                  OR lower(location) LIKE '%remote, us%'
                  OR lower(location) LIKE '%remote - usa%'
                  OR lower(location) LIKE '%remote - north america%'
                  OR lower(location) LIKE '%emea remote%'
                  OR lower(location) LIKE '%remote - emea%'
                  OR lower(location) LIKE '%remote - europe%'
                  OR lower(location) LIKE '%uk remote%'
                  OR lower(location) LIKE '%remote - uk%'
                  OR lower(location) LIKE '%canada remote%'
                  OR lower(location) LIKE '%remote - canada%'
                  OR lower(location) LIKE '%germany remote%'
                  OR lower(location) LIKE '%australia remote%'
                  OR lower(location) LIKE '%latam remote%'
                  OR lower(location) LIKE '%remote - latam%'
              )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS dispatched_alerts (
                job_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (job_id, platform)
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dispatched_alerts_platform ON dispatched_alerts(platform);
            """
        )
        conn.commit()

    # Clean up any existing duplicate entries
    cleanup_duplicate_jobs(target_path)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_seen_jobs_company ON seen_jobs(company);
            """
        )
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_seen_jobs_company_title_loc 
            ON seen_jobs(lower(company), lower(title), lower(location));
            """
        )
        conn.commit()


def filter_unalerted_jobs(
    jobs: list[JobPosting], platform: str, db_path: Optional[Path] = None
) -> list[JobPosting]:
    """Filter out jobs that have already been alerted on a specific platform."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    if not jobs:
        return []

    keys = [make_job_key(j) for j in jobs]
    placeholders = ",".join("?" for _ in keys)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT job_id FROM dispatched_alerts WHERE platform = ? AND job_id IN ({placeholders})",
            [platform] + keys,
        )
        sent_ids = {row[0] for row in cursor.fetchall()}

    return [j for j in jobs if make_job_key(j) not in sent_ids]


def record_dispatched_alert(
    job_id: str, platform: str, db_path: Optional[Path] = None
) -> None:
    """Record that an alert has been dispatched for a job on a platform."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO dispatched_alerts (job_id, platform, sent_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (job_id, platform),
        )
        conn.commit()


def record_dispatched_alerts(
    jobs: list[JobPosting], platform: str, db_path: Optional[Path] = None
) -> None:
    """Record multiple dispatched alerts for a platform atomically."""
    if not jobs:
        return

    init_db(db_path)
    target_path = get_db_path(db_path)
    records = [(make_job_key(j), platform) for j in jobs]

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT OR IGNORE INTO dispatched_alerts (job_id, platform, sent_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            records,
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

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, lower(apply_url), lower(company), lower(title), lower(location) FROM seen_jobs"
        )
        records = cursor.fetchall()
        seen_ids = {r[0] for r in records}
        seen_urls = {r[1] for r in records if r[1]}
        seen_semantic = {(r[2], r[3], r[4]) for r in records}

    new_jobs: list[JobPosting] = []
    existing_jobs: list[JobPosting] = []

    for job in jobs:
        clean_url = canonicalize_url(str(job.apply_url)).lower()
        key = make_job_key(job)
        sem_key = (
            job.company.lower().strip(),
            job.title.lower().strip(),
            job.location.lower().strip(),
        )

        if key in seen_ids or clean_url in seen_urls or sem_key in seen_semantic:
            existing_jobs.append(job)
        else:
            new_jobs.append(job)
            seen_ids.add(key)
            seen_urls.add(clean_url)
            seen_semantic.add(sem_key)

    return new_jobs, existing_jobs


def record_jobs(jobs: list[JobPosting], db_path: Optional[Path] = None) -> None:
    """Record newly seen jobs and update last_seen_at timestamps for active ones."""
    if not jobs:
        return

    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()

        # Deduplicate within incoming batch
        seen_sem: set[tuple[str, str, str]] = set()
        seen_urls: set[str] = set()
        deduped_batch: list[tuple[JobPosting, str]] = []

        for j in jobs:
            clean_url = canonicalize_url(str(j.apply_url))
            sem_key = (
                j.company.lower().strip(),
                j.title.lower().strip(),
                j.location.lower().strip(),
            )
            url_key = clean_url.lower().strip()
            if sem_key in seen_sem or url_key in seen_urls:
                continue
            seen_sem.add(sem_key)
            seen_urls.add(url_key)
            deduped_batch.append((j, clean_url))

        for job, clean_url in deduped_batch:
            job_key = make_job_key(job)
            comp_lower = job.company.lower().strip()
            title_lower = job.title.lower().strip()
            loc_lower = job.location.lower().strip()

            cursor.execute(
                """
                SELECT id FROM seen_jobs
                WHERE id = ?
                   OR lower(apply_url) = ?
                   OR (lower(company) = ? AND lower(title) = ? AND lower(location) = ?)
                LIMIT 1
                """,
                (job_key, clean_url.lower(), comp_lower, title_lower, loc_lower),
            )
            matched = cursor.fetchone()

            if matched:
                matched_id = matched[0]
                cursor.execute(
                    """
                    UPDATE seen_jobs SET
                        last_seen_at = CURRENT_TIMESTAMP,
                        company = ?,
                        title = ?,
                        location = ?,
                        apply_url = ?,
                        provider = ?,
                        published_date = COALESCE(NULLIF(?, ''), published_date),
                        is_active = 1,
                        is_remote = ?
                    WHERE id = ?
                    """,
                    (
                        job.company,
                        job.title,
                        job.location,
                        clean_url,
                        job.provider.value,
                        job.published_date or "Active",
                        1 if job.is_remote else 0,
                        matched_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO seen_jobs (
                        id, company, title, location, apply_url, provider, published_date, is_active, is_remote, status, applied_at, notes, first_seen_at, last_seen_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        last_seen_at = CURRENT_TIMESTAMP,
                        company = excluded.company,
                        title = excluded.title,
                        location = excluded.location,
                        apply_url = excluded.apply_url,
                        published_date = excluded.published_date,
                        is_active = 1,
                        is_remote = excluded.is_remote
                    """,
                    (
                        job_key,
                        job.company,
                        job.title,
                        job.location,
                        clean_url,
                        job.provider.value,
                        job.published_date or "Active",
                        1 if job.is_remote else 0,
                        getattr(job, "status", None) or "NEW",
                        getattr(job, "applied_at", None),
                        getattr(job, "notes", None),
                    ),
                )

        conn.commit()

        # Attach persisted database rowid, status, applied_at, and notes back to the JobPosting instances
        cursor.execute("SELECT rowid, id, status, applied_at, notes FROM seen_jobs")
        db_map = {row[1]: (row[0], row[2], row[3], row[4]) for row in cursor.fetchall()}
        for j in jobs:
            key = make_job_key(j)
            if key in db_map:
                rowid, stat, app_at, nts = db_map[key]
                j.numeric_id = rowid
                j.status = stat
                j.applied_at = app_at
                j.notes = nts


def get_stats(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Retrieve historical tracking stats from the database."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM seen_jobs")
        total_tracked = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM seen_jobs WHERE is_remote = 1")
        total_remote = cursor.fetchone()[0]

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
        "total_remote": total_remote,
        "company_breakdown": company_counts,
        "first_recorded": first_recorded,
        "last_active": last_active,
        "db_path": str(target_path.resolve()),
    }


def get_latest_jobs(
    limit: int = 5,
    status: Optional[str] = "NEW",
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Retrieve the most recently recorded or active jobs from the database, deduplicated by role."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    inner_where = "WHERE 1=1"
    params: list[Any] = []
    if status is not None and status.strip():
        stat_norm = status.strip().upper()
        if stat_norm != "ALL":
            inner_where += " AND UPPER(status) = ?"
            params.append(stat_norm)

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT rowid AS numeric_id, id, company, title, location, apply_url, provider, published_date, is_remote, status, applied_at, notes, first_seen_at, last_seen_at
            FROM (
                SELECT rowid, id, company, title, location, apply_url, provider, published_date, is_remote, status, applied_at, notes, first_seen_at, last_seen_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY lower(company), lower(title), lower(location)
                           ORDER BY last_seen_at DESC, first_seen_at DESC
                       ) as rn
                FROM seen_jobs
                {inner_where}
            )
            WHERE rn = 1
            ORDER BY last_seen_at DESC, first_seen_at DESC
            LIMIT ?
            """,
            params + [max(1, limit)],
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def query_jobs(
    title_keyword: Optional[str] = None,
    location: Optional[str] = None,
    company: Optional[str] = None,
    is_remote: Optional[bool] = None,
    status: Optional[str] = "NEW",
    limit: int = 5,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Query jobs from database with optional filters, deduplicated by role."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    inner_where = "WHERE 1=1"
    params: list[Any] = []

    if status is not None and status.strip():
        stat_norm = status.strip().upper()
        if stat_norm != "ALL":
            inner_where += " AND UPPER(status) = ?"
            params.append(stat_norm)

    if company and company.strip():
        inner_where += " AND company LIKE ?"
        params.append(f"%{company.strip()}%")

    if title_keyword and title_keyword.strip():
        inner_where += " AND title LIKE ?"
        params.append(f"%{title_keyword.strip()}%")

    if is_remote is True:
        inner_where += " AND is_remote = 1"
    elif is_remote is False:
        inner_where += " AND is_remote = 0"

    if location and location.strip():
        loc_str = location.strip().lower()
        if "bangalore" in loc_str or "bengaluru" in loc_str:
            inner_where += " AND (location LIKE ? OR location LIKE ?)"
            params.extend(["%bangalore%", "%bengaluru%"])
        elif "gurgaon" in loc_str or "gurugram" in loc_str:
            inner_where += " AND (location LIKE ? OR location LIKE ?)"
            params.extend(["%gurgaon%", "%gurugram%"])
        else:
            inner_where += " AND location LIKE ?"
            params.append(f"%{loc_str}%")

    query = f"""
        SELECT rowid AS numeric_id, id, company, title, location, apply_url, provider, published_date, is_remote, status, applied_at, notes, first_seen_at, last_seen_at
        FROM (
            SELECT rowid, id, company, title, location, apply_url, provider, published_date, is_remote, status, applied_at, notes, first_seen_at, last_seen_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY lower(company), lower(title), lower(location)
                       ORDER BY last_seen_at DESC, first_seen_at DESC
                   ) as rn
            FROM seen_jobs
            {inner_where}
        )
        WHERE rn = 1
        ORDER BY last_seen_at DESC, first_seen_at DESC
        LIMIT ?
    """
    params.append(max(1, limit))

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


VALID_JOB_STATUSES = {"NEW", "APPLIED", "INTERVIEWING", "REJECTED", "DISMISSED"}


def mark_job_status(
    job_id: Union[int, str],
    status: str,
    notes: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Update tracking status (NEW, APPLIED, INTERVIEWING, REJECTED, DISMISSED) and notes for a job.

    Accepts numeric rowid (e.g. 12 or "12") or string ID (e.g. "greenhouse_celonis_7791267003").
    """
    if not status or not isinstance(status, str):
        raise ValueError("Job status must be a non-empty string.")

    status_norm = status.strip().upper()
    if status_norm not in VALID_JOB_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_JOB_STATUSES))}"
        )

    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        is_num = isinstance(job_id, int) or (isinstance(job_id, str) and str(job_id).strip().isdigit())
        if is_num:
            where_clause = "rowid = ? OR id = ? OR id LIKE ?"
            match_params: list[Any] = [int(job_id), str(job_id).strip(), f"%_{str(job_id).strip()}"]
        else:
            raw_id = str(job_id).strip()
            where_clause = "id = ? OR id LIKE ?"
            match_params = [raw_id, f"%_{raw_id}"]

        if status_norm == "APPLIED":
            now_iso = datetime.now(timezone.utc).isoformat()
            if notes is not None:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, applied_at = COALESCE(applied_at, ?), notes = ? WHERE {where_clause}",
                    [status_norm, now_iso, notes] + match_params,
                )
            else:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, applied_at = COALESCE(applied_at, ?) WHERE {where_clause}",
                    [status_norm, now_iso] + match_params,
                )
        else:
            if notes is not None:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, notes = ? WHERE {where_clause}",
                    [status_norm, notes] + match_params,
                )
            else:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ? WHERE {where_clause}",
                    [status_norm] + match_params,
                )

        conn.commit()
        return cursor.rowcount > 0


def get_job_by_id(
    job_id: Union[int, str],
    db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Retrieve a single job dictionary by numeric rowid or string ID."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        is_num = isinstance(job_id, int) or (isinstance(job_id, str) and str(job_id).strip().isdigit())
        if is_num:
            cursor.execute(
                f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE rowid = ? OR id = ? OR id LIKE ? LIMIT 1",
                (int(job_id), str(job_id).strip(), f"%_{str(job_id).strip()}"),
            )
        else:
            raw_id = str(job_id).strip()
            cursor.execute(
                f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE id = ? OR id LIKE ? LIMIT 1",
                (raw_id, f"%_{raw_id}"),
            )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_jobs_by_status(
    status: str,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Retrieve jobs with a specific status ('NEW', 'APPLIED', 'INTERVIEWING', 'REJECTED', 'DISMISSED', or 'ALL')."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    status_norm = status.strip().upper()
    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        if status_norm == "ALL":
            query = f"SELECT rowid AS numeric_id, * FROM {table_name} ORDER BY last_seen_at DESC"
            params: list[Any] = []
        else:
            query = f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE UPPER(status) = ? ORDER BY last_seen_at DESC"
            params = [status_norm]

        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

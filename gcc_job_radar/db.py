from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from typing import Any, Optional, Union
import urllib.parse

from gcc_job_radar.models import ATSProvider, JobPosting

DEFAULT_DB_PATH = Path("gcc_jobs.db")


def get_db_path(custom_path: Optional[Path] = None) -> Path:
    """Resolve active SQLite database path."""
    return custom_path if custom_path is not None else DEFAULT_DB_PATH


def canonicalize_url(url: str) -> str:
    """Normalize and strip tracking query parameters (gh_jid, utm_*, etc.) while preserving job IDs for platforms like Glassdoor and Indeed."""
    if not url:
        return ""
    try:
        url_str = str(url).strip()
        parsed = urllib.parse.urlparse(url_str)
        netloc = parsed.netloc.lower()

        # Preserve Glassdoor job ID in query param (?jl=...)
        if "glassdoor." in netloc:
            m_jl = re.search(r"(?:jl|jobListingId)=([0-9]+)", url_str)
            if m_jl:
                return f"https://www.glassdoor.com/job-listing/?jl={m_jl.group(1)}"

        # Preserve Indeed job ID in query param (?jk=...)
        if "indeed." in netloc:
            query_dict = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
            jk_val = query_dict.get("jk", [None])[0]
            if jk_val:
                return f"https://www.indeed.com/viewjob?jk={jk_val}"

        # Canonicalize LinkedIn job URLs: https://www.linkedin.com/jobs/view/<id>/
        m_li = re.search(r"linkedin\.com/(?:comm/)?jobs/view/([0-9]+)", url_str)
        if m_li:
            return f"https://www.linkedin.com/jobs/view/{m_li.group(1)}/"

        path = parsed.path.rstrip("/")
        # Standard ATS paths (Greenhouse, Lever, Ashby, Workday) identify the job listing in the path.
        clean_url = urllib.parse.urlunparse(
            (parsed.scheme.lower(), netloc, path, "", "", "")
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

        # Step 4: Delete cross-platform duplicate records for same (company, title) in India/Remote,
        # ensuring that APPLIED/INTERVIEWING/REJECTED/DISMISSED statuses and canonical ATS links are preserved.
        deleted_by_role = 0
        cursor.execute("PRAGMA table_info(seen_jobs)")
        cols = {row[1] for row in cursor.fetchall()}
        if "status" in cols:
            cursor.execute(
                """
                DELETE FROM seen_jobs
                WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY lower(company), lower(title)
                                   ORDER BY 
                                       CASE 
                                           WHEN status = 'APPLIED' THEN 1
                                           WHEN status = 'INTERVIEWING' THEN 2
                                           WHEN status = 'REJECTED' THEN 3
                                           WHEN status = 'DISMISSED' THEN 4
                                           ELSE 5 
                                       END ASC,
                                       CASE WHEN provider != 'email_alert' THEN 1 ELSE 2 END ASC,
                                       CASE WHEN lower(location) IN ('india', 'remote') THEN 2 ELSE 1 END ASC,
                                       last_seen_at DESC,
                                       first_seen_at DESC,
                                       id ASC
                               ) as rn
                        FROM seen_jobs
                        WHERE (lower(location) LIKE '%india%' OR lower(location) LIKE '%remote%' OR (is_remote IS NOT NULL AND is_remote = 1))
                    ) WHERE rn = 1
                )
                AND (lower(location) LIKE '%india%' OR lower(location) LIKE '%remote%' OR (is_remote IS NOT NULL AND is_remote = 1));
                """
            )
            deleted_by_role = cursor.rowcount
        conn.commit()

        return deleted_by_url + deleted_by_semantic + deleted_by_role


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
                direct_search_url TEXT NULL,
                relevance_score INTEGER DEFAULT 0,
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
                if "direct_search_url" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN direct_search_url TEXT NULL")
                if "relevance_score" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN relevance_score INTEGER DEFAULT 0")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_seen_jobs_status ON seen_jobs(status);")
        # Recreate jobs view to expose direct_search_url and rowid as numeric_id
        cursor.execute("DROP VIEW IF EXISTS jobs;")
        cursor.execute("CREATE VIEW jobs AS SELECT rowid AS numeric_id, * FROM seen_jobs;")

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
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_emails (
                uid TEXT PRIMARY KEY,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_seen_emails_uid ON seen_emails(uid);
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
        comp_lower = job.company.lower().strip()
        title_lower = job.title.lower().strip()
        loc_lower = job.location.lower().strip()

        cursor.execute(
            """
            SELECT id FROM seen_jobs
            WHERE id = ?
               OR lower(apply_url) = ?
               OR (lower(company) = ? AND lower(title) = ? AND lower(location) = ?)
               OR (lower(company) = ? AND lower(title) = ? AND (
                   lower(location) = 'india' OR ? = 'india'
                   OR lower(location) LIKE '%' || ? || '%'
                   OR ? LIKE '%' || lower(location) || '%'
               ))
            LIMIT 1
            """,
            (
                key, clean_url.lower(),
                comp_lower, title_lower, loc_lower,
                comp_lower, title_lower, loc_lower, loc_lower, loc_lower,
            ),
        )

        if cursor.fetchone() or key in seen_ids or clean_url in seen_urls or sem_key in seen_semantic:
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

        from gcc_job_radar.relevance import score_job_posting

        for job, clean_url in deduped_batch:
            job_key = make_job_key(job)
            comp_lower = job.company.lower().strip()
            title_lower = job.title.lower().strip()
            loc_lower = job.location.lower().strip()
            job_score = getattr(job, "relevance_score", None)
            if job_score is None or job_score == 0:
                job_score = score_job_posting(job)

            cursor.execute(
                """
                SELECT id, status, provider, apply_url, location FROM seen_jobs
                WHERE id = ?
                   OR lower(apply_url) = ?
                   OR (lower(company) = ? AND lower(title) = ? AND lower(location) = ?)
                   OR (lower(company) = ? AND lower(title) = ? AND (
                       lower(location) = 'india' OR ? = 'india'
                       OR lower(location) LIKE '%' || ? || '%'
                       OR ? LIKE '%' || lower(location) || '%'
                   ))
                ORDER BY
                    CASE
                        WHEN status = 'APPLIED' THEN 1
                        WHEN status = 'INTERVIEWING' THEN 2
                        WHEN status = 'REJECTED' THEN 3
                        WHEN status = 'DISMISSED' THEN 4
                        ELSE 5
                    END ASC,
                    CASE WHEN provider != 'email_alert' THEN 1 ELSE 2 END ASC
                LIMIT 1
                """,
                (
                    job_key, clean_url.lower(),
                    comp_lower, title_lower, loc_lower,
                    comp_lower, title_lower, loc_lower, loc_lower, loc_lower,
                ),
            )
            matched = cursor.fetchone()

            if matched:
                matched_id, existing_status, existing_provider, existing_url, existing_loc = matched
                target_status = existing_status if existing_status != "NEW" else job.status
                target_url = (
                    existing_url
                    if existing_provider != "email_alert" and job.provider == ATSProvider.EMAIL_ALERT
                    else clean_url
                )
                target_provider = (
                    existing_provider
                    if existing_provider != "email_alert" and job.provider == ATSProvider.EMAIL_ALERT
                    else job.provider.value
                )
                target_loc = (
                    existing_loc
                    if existing_loc.lower() != "india" and loc_lower == "india"
                    else job.location
                )

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
                        is_remote = ?,
                        status = ?,
                        direct_search_url = COALESCE(?, direct_search_url),
                        relevance_score = ?
                    WHERE id = ?
                    """,
                    (
                        job.company,
                        job.title,
                        target_loc,
                        target_url,
                        target_provider,
                        job.published_date or "Active",
                        1 if job.is_remote else 0,
                        target_status,
                        getattr(job, "direct_search_url", None),
                        job_score,
                        matched_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO seen_jobs (
                        id, company, title, location, apply_url, provider, published_date, is_active, is_remote, status, applied_at, notes, direct_search_url, relevance_score, first_seen_at, last_seen_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        last_seen_at = CURRENT_TIMESTAMP,
                        company = excluded.company,
                        title = excluded.title,
                        location = excluded.location,
                        apply_url = excluded.apply_url,
                        published_date = excluded.published_date,
                        is_active = 1,
                        is_remote = excluded.is_remote,
                        direct_search_url = COALESCE(excluded.direct_search_url, seen_jobs.direct_search_url),
                        relevance_score = excluded.relevance_score
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
                        getattr(job, "direct_search_url", None),
                        job_score,
                    ),
                )

        conn.commit()

        # Attach persisted database rowid, status, applied_at, notes, direct_search_url, and relevance_score back to the JobPosting instances
        cursor.execute(
            "SELECT rowid, id, lower(apply_url), lower(company), lower(title), lower(location), status, applied_at, notes, direct_search_url, relevance_score FROM seen_jobs"
        )
        rows = cursor.fetchall()
        id_map = {r[1]: (r[0], r[6], r[7], r[8], r[9], r[10]) for r in rows}
        url_map = {r[2]: (r[0], r[6], r[7], r[8], r[9], r[10]) for r in rows if r[2]}
        role_map = {(r[3], r[4], r[5]): (r[0], r[6], r[7], r[8], r[9], r[10]) for r in rows}

        for j in jobs:
            clean_u = canonicalize_url(str(j.apply_url)).lower()
            sem_k = (
                j.company.lower().strip(),
                j.title.lower().strip(),
                j.location.lower().strip(),
            )
            meta = id_map.get(make_job_key(j)) or url_map.get(clean_u) or role_map.get(sem_k)
            if meta:
                setattr(j, "numeric_id", meta[0])
                setattr(j, "status", meta[1])
                setattr(j, "applied_at", meta[2])
                setattr(j, "notes", meta[3])
                setattr(j, "direct_search_url", meta[4])
                setattr(j, "relevance_score", meta[5] if meta[5] is not None else 0)


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

        cursor.execute("SELECT status, COUNT(*) FROM seen_jobs GROUP BY status")
        status_counts = dict(cursor.fetchall())

    return {
        "total_tracked": total_tracked,
        "total_remote": total_remote,
        "status_counts": status_counts,
        "active_count": status_counts.get("NEW", 0),
        "needs_resolve_count": status_counts.get("NEEDS_RESOLVE", 0),
        "dismissed_count": status_counts.get("DISMISSED", 0),
        "applied_count": status_counts.get("APPLIED", 0),
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
            SELECT rowid AS numeric_id, id, company, title, location, apply_url, provider, published_date, is_remote, status, applied_at, notes, direct_search_url, relevance_score, first_seen_at, last_seen_at
            FROM (
                SELECT rowid, id, company, title, location, apply_url, provider, published_date, is_remote, status, applied_at, notes, direct_search_url, relevance_score, first_seen_at, last_seen_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY lower(company), lower(title), lower(location)
                           ORDER BY relevance_score DESC, last_seen_at DESC, first_seen_at DESC
                       ) as rn
                FROM seen_jobs
                {inner_where}
            )
            WHERE rn = 1
            ORDER BY relevance_score DESC, last_seen_at DESC, first_seen_at DESC
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
    min_score: Optional[int] = None,
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

    if min_score is not None and min_score > 0:
        inner_where += " AND relevance_score >= ?"
        params.append(min_score)

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
        SELECT rowid AS numeric_id, id, company, title, location, apply_url, provider, published_date, is_remote, status, applied_at, notes, direct_search_url, relevance_score, first_seen_at, last_seen_at
        FROM (
            SELECT rowid, id, company, title, location, apply_url, provider, published_date, is_remote, status, applied_at, notes, direct_search_url, relevance_score, first_seen_at, last_seen_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY lower(company), lower(title), lower(location)
                       ORDER BY relevance_score DESC, last_seen_at DESC, first_seen_at DESC
                   ) as rn
            FROM seen_jobs
            {inner_where}
        )
        WHERE rn = 1
        ORDER BY relevance_score DESC, last_seen_at DESC, first_seen_at DESC
        LIMIT ?
    """
    params.append(max(1, limit))

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


VALID_JOB_STATUSES = {"NEW", "APPLIED", "INTERVIEWING", "REJECTED", "DISMISSED", "NEEDS_RESOLVE"}


def update_job_direct_search_url(
    job_id: Union[int, str],
    direct_search_url: str,
    db_path: Optional[Path] = None,
) -> bool:
    """Update direct_search_url for a job by numeric rowid or string ID."""
    target_job = get_job_by_id(job_id, db_path=db_path)
    if not target_job:
        return False

    init_db(db_path)
    target_path = get_db_path(db_path)
    target_rowid = target_job["numeric_id"]

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"
        cursor.execute(
            f"UPDATE {table_name} SET direct_search_url = ? WHERE rowid = ?",
            (direct_search_url, target_rowid),
        )
        conn.commit()
        return cursor.rowcount > 0


def mark_job_status(
    job_id: Union[int, str],
    status: str,
    notes: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Update tracking status (NEW, APPLIED, INTERVIEWING, REJECTED, DISMISSED, NEEDS_RESOLVE) and notes for a job.

    Accepts numeric rowid (e.g. 12 or "12") or string ID (e.g. "greenhouse_celonis_7791267003").
    """
    if not status or not isinstance(status, str):
        raise ValueError("Job status must be a non-empty string.")

    status_norm = status.strip().upper()
    if status_norm not in VALID_JOB_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_JOB_STATUSES))}"
        )

    target_job = get_job_by_id(job_id, db_path=db_path)
    if not target_job:
        return False

    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        target_rowid = target_job["numeric_id"]
        now_iso = datetime.now(timezone.utc).isoformat()

        if status_norm == "APPLIED":
            if notes is not None:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, applied_at = COALESCE(applied_at, ?), notes = ? WHERE rowid = ?",
                    (status_norm, now_iso, notes, target_rowid),
                )
            else:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, applied_at = COALESCE(applied_at, ?) WHERE rowid = ?",
                    (status_norm, now_iso, target_rowid),
                )
        else:
            if notes is not None:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, notes = ? WHERE rowid = ?",
                    (status_norm, notes, target_rowid),
                )
            else:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ? WHERE rowid = ?",
                    (status_norm, target_rowid),
                )

        conn.commit()
        return cursor.rowcount > 0


def purge_or_dismiss_job(
    job_id: Union[int, str],
    reason: str,
    hard_delete: bool = False,
    db_path: Optional[Path] = None,
) -> bool:
    """Purge (delete) or dismiss a job by ID, recording the reason.

    If hard_delete is True, permanently deletes the record from seen_jobs.
    If hard_delete is False, marks status as 'DISMISSED' and sets/appends reason to notes.
    Accepts numeric rowid or string ID.
    """
    target_job = get_job_by_id(job_id, db_path=db_path)
    if not target_job:
        return False

    init_db(db_path)
    target_path = get_db_path(db_path)
    target_rowid = target_job["numeric_id"]

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        if hard_delete:
            cursor.execute(f"DELETE FROM {table_name} WHERE rowid = ?", (target_rowid,))
            conn.commit()
            return cursor.rowcount > 0
        else:
            existing_notes = target_job.get("notes") or ""
            note_str = f"Auto-dismissed: {reason.strip()}"
            if existing_notes and note_str not in existing_notes:
                final_notes = f"{existing_notes} | {note_str}"
            else:
                final_notes = note_str

            cursor.execute(
                f"UPDATE {table_name} SET status = 'DISMISSED', is_active = 0, notes = ? WHERE rowid = ?",
                (final_notes, target_rowid),
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
            # 1. Exact numeric rowid match first
            cursor.execute(
                f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE rowid = ? LIMIT 1",
                (int(job_id),),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)

            # 2. Fallback to exact string id match if an ATS ID is numeric
            cursor.execute(
                f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE id = ? LIMIT 1",
                (str(job_id).strip(),),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        else:
            raw_id = str(job_id).strip()
            # 1. Exact string id match
            cursor.execute(
                f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE id = ? LIMIT 1",
                (raw_id,),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)

            # 2. Match by apply URL or canonical apply URL
            clean_url = canonicalize_url(raw_id).lower()
            cursor.execute(
                f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE lower(apply_url) = ? OR lower(apply_url) = ? OR lower(apply_url) = ? LIMIT 1",
                (raw_id.lower(), raw_id.lower().rstrip("/"), clean_url),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)

            # 3. Match ATS specific ID suffix with escaped underscore
            cursor.execute(
                f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE id LIKE ? ESCAPE '\\' LIMIT 1",
                (f"%\\_{raw_id}",),
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


def find_jobs_by_selector(
    selector: str,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Resolve one or more jobs by numeric rowids, string IDs, or company/title text search.

    Supports:
    - Single numeric ID: "42", "#42"
    - Multiple comma- or space-separated numeric IDs: "1, 2, 3", "1 4 7", "#1, #2", "1 and 4"
    - Exact string ID / URL: "email_alert_...", "greenhouse_celonis_..."
    - Company name or role title substring match: "Devmani Traders", "BT Group", "Associate Engineer"
    """
    if not selector or not str(selector).strip():
        return []

    init_db(db_path)
    target_path = get_db_path(db_path)
    raw = str(selector).strip()

    # 1. Check if input is a list of comma-, semicolon-, or whitespace-separated numeric tokens
    tokens = [
        re.sub(r"^#", "", t).strip()
        for t in re.split(r"[,;\s]+", raw)
        if t.strip() and t.lower() not in ("and", "&")
    ]

    if tokens and all(t.isdigit() for t in tokens):
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for tok in tokens:
            job = get_job_by_id(tok, db_path=db_path)
            if job and job["id"] not in seen_ids:
                seen_ids.add(job["id"])
                results.append(job)
        if results:
            return results

    # 2. Try single exact ID lookup (handles single number, #ID, or full unique key)
    cleaned_single = re.sub(r"^#", "", raw).strip()
    single_job = get_job_by_id(cleaned_single, db_path=db_path)
    if single_job:
        return [single_job]

    # 3. Check for multiple comma- or 'and'-separated company/role queries (e.g. "uipath, celonis", "BT Group and Devmani Traders")
    sub_queries = [
        s.strip()
        for s in re.split(r"[,;]|\s+(?:and|&)\s+", raw, flags=re.IGNORECASE)
        if s.strip() and s.lower() not in ("and", "&")
    ]

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        if len(sub_queries) > 1:
            multi_results: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for sq in sub_queries:
                cleaned_sq = re.sub(r"^#", "", sq).strip()
                if cleaned_sq.isdigit():
                    j = get_job_by_id(cleaned_sq, db_path=db_path)
                    if j and j["id"] not in seen_ids:
                        seen_ids.add(j["id"])
                        multi_results.append(j)
                    continue

                term = f"%{sq.lower()}%"
                cursor.execute(
                    f"""
                    SELECT rowid AS numeric_id, * FROM {table_name}
                    WHERE lower(company) LIKE ? OR lower(title) LIKE ?
                    ORDER BY last_seen_at DESC LIMIT 25
                    """,
                    (term, term),
                )
                for r in cursor.fetchall():
                    jd = dict(r)
                    if jd["id"] not in seen_ids:
                        seen_ids.add(jd["id"])
                        multi_results.append(jd)

            if multi_results:
                return multi_results

        # 4. Fallback: Search by company name or title keyword in seen_jobs
        search_term = f"%{raw.lower()}%"
        cursor.execute(
            f"""
            SELECT rowid AS numeric_id, * FROM {table_name}
            WHERE lower(company) LIKE ? OR lower(title) LIKE ?
            ORDER BY last_seen_at DESC LIMIT 25
            """,
            (search_term, search_term),
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def is_email_seen(
    uid: str,
    db_path: Optional[Path] = None,
    account: Optional[str] = None,
) -> bool:
    """Check if an email UID has already been processed for a given account."""
    init_db(db_path)
    target_path = get_db_path(db_path)
    clean_u = str(uid).strip()
    keys = [f"{account}:{clean_u}"] if account else [clean_u]
    if account and account.lower() == "chinmay8064@gmail.com":
        keys.append(clean_u)
    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in keys)
        cursor.execute(f"SELECT 1 FROM seen_emails WHERE uid IN ({placeholders}) LIMIT 1", keys)
        return cursor.fetchone() is not None


def filter_unseen_email_uids(
    uids: list[str],
    db_path: Optional[Path] = None,
    account: Optional[str] = None,
) -> list[str]:
    """Filter out email UIDs that have already been recorded in seen_emails for a given account."""
    if not uids:
        return []
    init_db(db_path)
    target_path = get_db_path(db_path)
    clean_uids = [str(u).strip() for u in uids if str(u).strip()]
    if not clean_uids:
        return []

    # Map candidate keys to check in DB
    # If account is given, check f"{account}:{u}".
    # If account is chinmay8064@gmail.com, also check bare u for backward compatibility.
    keys_to_uid: dict[str, str] = {}
    for u in clean_uids:
        if account:
            keys_to_uid[f"{account}:{u}"] = u
            if account.lower() == "chinmay8064@gmail.com":
                keys_to_uid[u] = u
        else:
            keys_to_uid[u] = u

    all_keys = list(keys_to_uid.keys())
    seen_keys: set[str] = set()
    chunk_size = 500
    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        for i in range(0, len(all_keys), chunk_size):
            chunk = all_keys[i : i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            cursor.execute(
                f"SELECT uid FROM seen_emails WHERE uid IN ({placeholders})",
                chunk,
            )
            seen_keys.update(row[0] for row in cursor.fetchall())

    seen_uids = {keys_to_uid[k] for k in seen_keys if k in keys_to_uid}
    return [u for u in clean_uids if u not in seen_uids]


def record_seen_email_uids(
    uids: list[str],
    db_path: Optional[Path] = None,
    account: Optional[str] = None,
) -> None:
    """Record email UIDs into seen_emails so they are skipped in subsequent syncs."""
    if not uids:
        return
    init_db(db_path)
    target_path = get_db_path(db_path)
    clean_keys = [
        (f"{account}:{str(u).strip()}" if account else str(u).strip(),)
        for u in uids
        if str(u).strip()
    ]
    if not clean_keys:
        return

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT OR IGNORE INTO seen_emails (uid, processed_at)
            VALUES (?, CURRENT_TIMESTAMP)
            """,
            clean_keys,
        )
        conn.commit()


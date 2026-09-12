"""Automated job link validator and dead role dismissal tool for GCC Job Radar.

Verifies URLs of active unapplied jobs in gcc_jobs.db using dual-step async checks:
1. Fast HEAD request (falling back to streaming GET on HTTP 405 Method Not Allowed).
2. Definite dead status codes (HTTP 403 / Bot Challenge, HTTP 404, 410, 500+).
3. Generic career site redirects (redirected from deep link to /jobs, /careers, /openings).
4. Soft-404 detection by streaming first 16KB of HTML response.
5. Graceful handling of SSL handshake errors and connection timeouts.
"""

import argparse
import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import ssl
import sys
from typing import Any, Callable, Optional
import urllib.parse

import httpx

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from gcc_job_radar.db import (
    get_db_path,
    init_db,
    mark_job_status,
    update_job_direct_search_url,
)
from gcc_job_radar.display import console
from gcc_job_radar.link_resolver import (
    build_direct_careers_search_url,
    build_direct_search_url,
    is_aggregator_url,
    is_direct_ats_url,
    is_glassdoor_url,
    resolve_company_career_portal,
    unwrap_destination_url,
)

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 25
DEFAULT_TIMEOUT = 10.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SOFT_404_PHRASES = [
    "job is no longer available",
    "position has been filled",
    "this posting has expired",
    "job not found",
    "no longer accepting applications",
]


# Ensure httpx.SSLError is present for callers and exception matching
if not hasattr(httpx, "SSLError"):
    class _HttpxSSLError(httpx.ConnectError, ssl.SSLError):
        """Mock/compat SSLError for httpx."""
        pass

    httpx.SSLError = _HttpxSSLError  # type: ignore[attr-defined]


@dataclass
class LinkCheckResult:
    """Represents the validation result for a single job link."""

    numeric_id: int
    id: str
    company: str
    title: str
    apply_url: str
    is_dead: bool
    reason: Optional[str] = None
    dismissed: bool = False
    needs_resolve: bool = False
    direct_search_url: Optional[str] = None
    notes: Optional[str] = None


def is_generic_career_redirect(original_url: str, final_url: str) -> bool:
    """Check if deep apply URL was redirected to a generic jobs/careers/openings landing page."""
    if not original_url or not final_url:
        return False

    orig_parsed = urllib.parse.urlparse(str(original_url).strip())
    final_parsed = urllib.parse.urlparse(str(final_url).strip())

    orig_path = orig_parsed.path.rstrip("/").lower()
    final_path = final_parsed.path.rstrip("/").lower()

    # Check if final path ends in /jobs, /careers, or /openings
    ends_in_generic = bool(re.search(r"/(?:jobs|careers|openings)$", final_path))
    if not ends_in_generic:
        return False

    orig_segments = [s for s in orig_path.split("/") if s]
    final_segments = [s for s in final_path.split("/") if s]

    # If original had deeper segments or had query parameters that were stripped
    if orig_path != final_path or bool(orig_parsed.query):
        if len(orig_segments) > len(final_segments) or (
            orig_path != final_path and not re.search(r"/(?:jobs|careers|openings)$", orig_path)
        ):
            return True
        if bool(orig_parsed.query) and not bool(final_parsed.query):
            return True

    return False


async def check_url(client: httpx.AsyncClient, url: str) -> tuple[bool, Optional[str]]:
    """Verify if a URL is active or dead/expired/blocked.

    Dual-step verification:
    1. Fast HEAD request (falling back to GET if HTTP 405 Method Not Allowed).
    2. Flag definite dead status codes: HTTP 403, 404, 410, 500+.
    3. Flag generic career site redirects (status 200 ending in /jobs, /careers, /openings).
    4. Flag soft-404 expired role text by streaming first 16KB of HTML responses.

    Returns:
        (is_dead, reason) where is_dead is True if the link is broken/expired/blocked,
        and reason is a descriptive string (or None if active).
    """
    if not url or not str(url).strip().startswith(("http://", "https://")):
        return True, "Invalid URL"

    clean_url = str(url).strip()

    # Step 1: Fast HEAD request
    head_resp = None
    try:
        head_resp = await client.head(clean_url, follow_redirects=True)
    except (httpx.SSLError, ssl.SSLError):
        return True, "SSL Handshake Failed"
    except httpx.ConnectError as exc:
        if isinstance(getattr(exc, "__cause__", None), ssl.SSLError) or "ssl" in str(exc).lower():
            return True, "SSL Handshake Failed"
        head_resp = None
    except (httpx.TimeoutException, httpx.ConnectTimeout):
        head_resp = None
    except (httpx.RequestError, Exception):
        head_resp = None

    if head_resp is not None:
        # HTTP 403 / Bot Challenge
        if head_resp.status_code == 403:
            return True, "HTTP 403 / Bot Challenge"

        # Definite dead status codes
        if head_resp.status_code in (404, 410) or head_resp.status_code >= 500:
            return True, f"HTTP {head_resp.status_code}"

        # Generic career site redirect on HEAD
        if head_resp.status_code == 200 and is_generic_career_redirect(clean_url, str(head_resp.url)):
            return True, "Generic career redirect"

        # If HTTP 405 Method Not Allowed, or if 200 (need body inspection), continue to GET

    # Step 2: Streaming GET inspection (first 16KB)
    try:
        async with client.stream("GET", clean_url, follow_redirects=True) as get_resp:
            # HTTP 403 / Bot Challenge
            if get_resp.status_code == 403:
                return True, "HTTP 403 / Bot Challenge"

            # Definite dead status codes on GET
            if get_resp.status_code in (404, 410) or get_resp.status_code >= 500:
                return True, f"HTTP {get_resp.status_code}"

            # Generic career redirect on GET
            if is_generic_career_redirect(clean_url, str(get_resp.url)):
                return True, "Generic career redirect"

            # If not 200, do not falsely flag
            if get_resp.status_code != 200:
                return False, None

            # Stream first 16KB of HTML response
            chunk_size = 16384  # 16KB
            body_chunks = []
            total_bytes = 0
            async for chunk in get_resp.aiter_bytes():
                body_chunks.append(chunk)
                total_bytes += len(chunk)
                if total_bytes >= chunk_size:
                    break

            body_text = b"".join(body_chunks).decode("utf-8", errors="ignore").lower()
            for phrase in SOFT_404_PHRASES:
                if phrase in body_text:
                    return True, f"Soft-404 ({phrase})"

            return False, None

    except (httpx.SSLError, ssl.SSLError):
        return True, "SSL Handshake Failed"
    except httpx.ConnectError as exc:
        if isinstance(getattr(exc, "__cause__", None), ssl.SSLError) or "ssl" in str(exc).lower():
            return True, "SSL Handshake Failed"
        return False, None
    except (httpx.TimeoutException, httpx.ConnectTimeout):
        return False, None
    except (httpx.RequestError, Exception):
        return False, None


async def validate_job_links_async(
    db_path: Optional[Path] = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    limit: Optional[int] = None,
    dry_run: bool = False,
    client: Optional[httpx.AsyncClient] = None,
    progress_callback: Optional[Callable[[int, int, LinkCheckResult], None]] = None,
) -> list[LinkCheckResult]:
    """Validate URLs of active unapplied jobs in gcc_jobs.db asynchronously."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    query = """
        SELECT rowid AS numeric_id, id, company, title, location, apply_url, notes, status
        FROM seen_jobs
        WHERE UPPER(status) = 'NEW'
        ORDER BY last_seen_at DESC, first_seen_at DESC
    """
    if limit is not None and limit > 0:
        query += f" LIMIT {int(limit)}"

    import sqlite3

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query)
        jobs = [dict(r) for r in cursor.fetchall()]

    if not jobs:
        return []

    semaphore = asyncio.Semaphore(concurrency)
    results: list[LinkCheckResult] = []
    total = len(jobs)
    completed = 0

    own_client = False
    if client is None:
        limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
        timeout = httpx.Timeout(DEFAULT_TIMEOUT, connect=5.0)
        client = httpx.AsyncClient(limits=limits, timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True)
        own_client = True

    try:
        async def _check_job(job: dict[str, Any]) -> LinkCheckResult:
            nonlocal completed
            async with semaphore:
                is_dead, reason = await check_url(client, job["apply_url"])
                dismissed = False
                needs_resolve = False
                direct_search: Optional[str] = None
                old_notes = job.get("notes")

                if is_dead:
                    is_aggregator = is_aggregator_url(job["apply_url"])
                    is_glassdoor = is_glassdoor_url(job["apply_url"])
                    is_bot_or_redirect = reason in ("HTTP 403 / Bot Challenge", "Generic career redirect")
                    if (is_aggregator or is_glassdoor or is_bot_or_redirect) and job.get("company") and job.get("title"):
                        needs_resolve = True
                        portal = resolve_company_career_portal(job["company"]) if is_glassdoor else None
                        direct_search = (
                            build_direct_careers_search_url(job["company"], job["title"])
                            if is_glassdoor
                            else build_direct_search_url(job["company"], job["title"])
                        )
                        if not dry_run:
                            resolve_note = (
                                f"Blocked ({reason}) - official careers portal resolved"
                                if portal
                                else f"Blocked/Redirected ({reason}) - fallback to ATS search"
                            )
                            new_notes = f"{old_notes} | {resolve_note}" if old_notes else resolve_note
                            mark_job_status(job["numeric_id"], status="NEEDS_RESOLVE", notes=new_notes, db_path=db_path)
                            update_job_direct_search_url(job["numeric_id"], direct_search, db_path=db_path)
                    elif not dry_run:
                        dismissal_note = f"Auto-dismissed (dead link: {reason})"
                        new_notes = f"{old_notes} | {dismissal_note}" if old_notes else dismissal_note
                        mark_job_status(job["numeric_id"], status="DISMISSED", notes=new_notes, db_path=db_path)
                        dismissed = True

                res = LinkCheckResult(
                    numeric_id=job["numeric_id"],
                    id=job["id"],
                    company=job["company"],
                    title=job["title"],
                    apply_url=job["apply_url"],
                    is_dead=is_dead,
                    reason=reason,
                    dismissed=dismissed,
                    needs_resolve=needs_resolve,
                    direct_search_url=direct_search,
                    notes=old_notes,
                )
                completed += 1
                if progress_callback:
                    progress_callback(completed, total, res)
                return res

        tasks = [_check_job(j) for j in jobs]
        results = await asyncio.gather(*tasks)
    finally:
        if own_client:
            await client.aclose()

    return list(results)


def validate_job_links(
    db_path: Optional[Path] = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    limit: Optional[int] = None,
    dry_run: bool = False,
    client: Optional[httpx.AsyncClient] = None,
    progress_callback: Optional[Callable[[int, int, LinkCheckResult], None]] = None,
) -> list[LinkCheckResult]:
    """Synchronous wrapper for validate_job_links_async."""
    return asyncio.run(
        validate_job_links_async(
            db_path=db_path,
            concurrency=concurrency,
            limit=limit,
            dry_run=dry_run,
            client=client,
            progress_callback=progress_callback,
        )
    )


def render_link_check_results(results: list[LinkCheckResult], dry_run: bool = False) -> None:
    """Render Rich table of link verification results and output summary."""
    from rich.table import Table

    table = Table(
        title="Link Verification Results",
        show_header=True,
        header_style="bold cyan",
        show_lines=False,
    )
    table.add_column("#", style="dim", width=5, justify="right")
    table.add_column("Company", style="bold white", width=22)
    table.add_column("Title", style="cyan", width=36)
    table.add_column("Status", width=10)
    table.add_column("Reason", width=32)
    table.add_column("Action", width=24)

    dead_count = 0
    dismissed_count = 0
    needs_resolve_count = 0
    active_count = 0

    for r in sorted(results, key=lambda x: (not x.is_dead, x.numeric_id)):
        if r.is_dead:
            dead_count += 1
            status_str = "[bold red]DEAD[/bold red]"
            clean_reason = (r.reason or "Unknown")[:30]
            reason_str = f"[red]{clean_reason}[/red]"
            if r.needs_resolve:
                needs_resolve_count += 1
                if dry_run:
                    action_str = "[yellow]WOULD RESOLVE (DRY RUN)[/yellow]"
                else:
                    action_str = "[cyan]NEEDS RESOLVE[/cyan]"
            elif dry_run:
                action_str = "[magenta]WOULD DISMISS (DRY RUN)[/magenta]"
            else:
                action_str = "[yellow]DISMISSED[/yellow]"
                dismissed_count += 1
        else:
            active_count += 1
            status_str = "[bold green]ACTIVE[/bold green]"
            reason_str = "[dim]OK (200)[/dim]"
            action_str = "[dim green]KEPT[/dim green]"

        table.add_row(
            str(r.numeric_id),
            r.company[:20],
            r.title[:34],
            status_str,
            reason_str,
            action_str,
        )

    console.print()
    console.print(table)
    console.print()

    # Summary
    if dry_run:
        console.print(
            f"[bold cyan][*][/bold cyan] Link Check Complete (DRY RUN): "
            f"[bold white]{len(results)}[/bold white] checked, "
            f"[bold red]{dead_count}[/bold red] dead/blocked "
            f"([bold cyan]{needs_resolve_count}[/bold cyan] fallback candidates), "
            f"[bold green]{active_count}[/bold green] active. "
            f"(0 modified in database)"
        )
    else:
        console.print(
            f"[bold green][+][/bold green] Link Check Complete: "
            f"[bold white]{len(results)}[/bold white] checked, "
            f"[bold red]{dead_count}[/bold red] dead/blocked "
            f"([bold yellow]{dismissed_count}[/bold yellow] auto-dismissed, "
            f"[bold cyan]{needs_resolve_count}[/bold cyan] set to NEEDS_RESOLVE), "
            f"[bold green]{active_count}[/bold green] active."
        )


def run_link_checker(
    db_path: Optional[Path] = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    limit: Optional[int] = None,
    dry_run: bool = False,
) -> list[LinkCheckResult]:
    """Execute link checker with progress display and console output."""
    mode_str = " (DRY RUN)" if dry_run else ""
    console.print(
        f"[bold cyan][*][/bold cyan] Starting Link Validator{mode_str} "
        f"(Concurrency: {concurrency}, Limit: {limit or 'ALL'})..."
    )

    results = validate_job_links(
        db_path=db_path,
        concurrency=concurrency,
        limit=limit,
        dry_run=dry_run,
    )

    if not results:
        console.print("[yellow]No active unapplied jobs found in database to verify.[/yellow]")
        return []

    render_link_check_results(results, dry_run=dry_run)
    return results


def main() -> None:
    """CLI entrypoint when invoked directly via python tools/check_links.py."""
    parser = argparse.ArgumentParser(
        description="Verify URLs of active unapplied jobs in gcc_jobs.db and auto-dismiss dead postings."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Check links and display report without updating database status.",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Maximum concurrent HTTP requests (default: {DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Maximum unapplied jobs to verify (default: check all).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Custom path to SQLite database file.",
    )

    args = parser.parse_args()
    run_link_checker(
        db_path=args.db,
        concurrency=args.concurrency,
        limit=args.limit,
        dry_run=args.dry_run,
    )


def resolve_database_links(
    db_path: Optional[Path] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    only_missing: bool = False,
) -> list[dict[str, Any]]:
    """Inspect and resolve direct ATS links and fallback search queries for jobs in the database."""
    import sqlite3
    import unicodedata

    init_db(db_path)
    target_path = get_db_path(db_path)

    where_clause = "WHERE 1=1"
    if only_missing:
        where_clause += " AND (direct_search_url IS NULL OR direct_search_url = '')"

    query = f"""
        SELECT rowid AS numeric_id, id, company, title, apply_url, status, notes, direct_search_url
        FROM seen_jobs
        {where_clause}
        ORDER BY last_seen_at DESC, first_seen_at DESC
    """
    if limit is not None and limit > 0:
        query += f" LIMIT {int(limit)}"

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query)
        jobs = [dict(r) for r in cursor.fetchall()]

    if not jobs:
        console.print("[yellow]No jobs found matching criteria to resolve.[/yellow]")
        return []

    resolved_count = 0
    updated_url_count = 0
    updates: list[dict[str, Any]] = []

    for job in jobs:
        company = job.get("company") or ""
        title = job.get("title") or ""
        orig_url = job.get("apply_url") or ""
        is_glassdoor = is_glassdoor_url(orig_url)
        is_agg = is_aggregator_url(orig_url)

        unwrapped = unwrap_destination_url(orig_url)
        portal = resolve_company_career_portal(company) if (is_glassdoor or is_agg or "bt" in company.lower()) else None
        if unwrapped and is_direct_ats_url(unwrapped):
            new_apply_url = unwrapped
        elif portal:
            new_apply_url = portal
        elif is_glassdoor or is_agg:
            new_apply_url = orig_url
        else:
            new_apply_url = orig_url

        if is_glassdoor or is_agg or portal:
            direct_search = build_direct_careers_search_url(company, title)
        else:
            direct_search = build_direct_search_url(company, title)

        url_changed = new_apply_url != orig_url
        search_updated = job.get("direct_search_url") != direct_search

        if url_changed:
            updated_url_count += 1
        if search_updated:
            resolved_count += 1

        updates.append(
            {
                "numeric_id": job["numeric_id"],
                "id": job["id"],
                "company": company,
                "title": title,
                "orig_url": orig_url,
                "new_apply_url": new_apply_url,
                "direct_search_url": direct_search,
                "url_changed": url_changed,
            }
        )

    if not dry_run and updates:
        with sqlite3.connect(target_path) as conn:
            cursor = conn.cursor()
            for u in updates:
                cursor.execute(
                    """
                    UPDATE seen_jobs SET
                        apply_url = ?,
                        direct_search_url = ?
                    WHERE id = ?
                    """,
                    (u["new_apply_url"], u["direct_search_url"], u["id"]),
                )
            conn.commit()

    mode_label = " (DRY RUN)" if dry_run else ""
    console.print(
        f"[bold green][+][/bold green] Resolution Complete{mode_label}: "
        f"[bold white]{len(updates)}[/bold white] jobs processed, "
        f"[bold cyan]{updated_url_count}[/bold cyan] direct ATS URLs unwrapped, "
        f"[bold yellow]{resolved_count}[/bold yellow] search fallbacks generated."
    )

    return updates


resolve_links = resolve_database_links


if __name__ == "__main__":
    main()

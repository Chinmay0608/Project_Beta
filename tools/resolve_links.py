"""CLI utility to resolve destination ATS links and populate direct ATS search URLs.

Scans active and unapplied jobs in gcc_jobs.db, unwraps nested aggregator redirect URLs
to direct ATS targets, and populates formatted Google ATS search links in direct_search_url.
"""

import argparse
import logging
from pathlib import Path
import sqlite3
import sys
from typing import Any, Optional
import unicodedata

# Reconfigure stdout for Windows console to handle UTF-8 / Asian punctuation characters safely
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from gcc_job_radar.db import get_db_path, init_db
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


def resolve_database_links(
    db_path: Optional[Path] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    only_missing: bool = False,
) -> list[dict[str, Any]]:
    """Inspect and resolve direct ATS links and fallback search queries for jobs in the database."""
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

    # Render summary table
    from rich.table import Table

    table = Table(title="Direct URL Resolution Summary", show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right", width=5)
    table.add_column("Company", width=22, style="bold white")
    table.add_column("Title", width=30, style="cyan")
    table.add_column("Direct ATS / Portal", width=22)
    table.add_column("Direct Search Fallback", width=45, style="dim")

    for u in updates[:25]:
        if u["url_changed"]:
            ats_str = "[bold green]YES (Resolved)[/bold green]"
        elif is_glassdoor_url(u["orig_url"]):
            ats_str = "[yellow]Glassdoor (Search FB)[/yellow]"
        else:
            ats_str = "[dim]None (Aggregator)[/dim]"

        safe_comp = unicodedata.normalize("NFKD", str(u["company"])).encode("ascii", "ignore").decode("ascii")
        safe_title = unicodedata.normalize("NFKD", str(u["title"])).encode("ascii", "ignore").decode("ascii")

        table.add_row(
            str(u["numeric_id"]),
            safe_comp[:20],
            safe_title[:28],
            ats_str,
            u["direct_search_url"][:42] + "...",
        )

    console.print()
    console.print(table)
    console.print()

    mode_label = " (DRY RUN)" if dry_run else ""
    console.print(
        f"[bold green][+][/bold green] Resolution Complete{mode_label}: "
        f"[bold white]{len(updates)}[/bold white] jobs processed, "
        f"[bold cyan]{updated_url_count}[/bold cyan] direct ATS URLs unwrapped, "
        f"[bold yellow]{resolved_count}[/bold yellow] search fallbacks generated."
    )

    return updates


resolve_links = resolve_database_links


def main() -> None:
    """CLI entrypoint for resolve-links."""
    parser = argparse.ArgumentParser(
        description="Resolve direct ATS URLs and populate direct company career search queries."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Inspect and display resolutions without writing to database.",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Maximum jobs to process (default: all).",
    )
    parser.add_argument(
        "--missing-only",
        "-m",
        action="store_true",
        default=False,
        help="Only resolve jobs currently lacking a direct search URL.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Custom path to SQLite database file.",
    )

    args = parser.parse_args()
    resolve_database_links(
        db_path=args.db,
        limit=args.limit,
        dry_run=args.dry_run,
        only_missing=args.missing_only,
    )


if __name__ == "__main__":
    main()

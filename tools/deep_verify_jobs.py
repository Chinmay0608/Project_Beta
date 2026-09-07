"""CLI utility to crawl job links, read full descriptions, and purge/dismiss unqualified roles for Class of 2027.

Usage:
    python tools/deep_verify_jobs.py [--batch 2027] [--concurrency 25] [--dry-run] [--hard-delete] [--limit 50]
"""

import argparse
import asyncio
import logging
from pathlib import Path
import re
import sys
from typing import Any, Optional

import httpx

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from gcc_job_radar.db import get_db_path, init_db, purge_or_dismiss_job
from gcc_job_radar.deep_verifier import (
    DEFAULT_HEADERS,
    VerificationResult,
    evaluate_for_2027_candidate,
    fetch_job_content,
)

try:
    from rich import box
    from rich.console import Console
    from rich.table import Table

    console = Console()
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

    class FallbackConsole:
        def print(self, *args: Any, **kwargs: Any) -> None:
            clean_args = [
                re.sub(r"\[/?(?:bold|green|yellow|red|cyan|magenta|dim)[^\]]*\]", "", str(a))
                for a in args
            ]
            print(*clean_args)

    console = FallbackConsole()

logger = logging.getLogger("deep_verify_jobs")


async def run_deep_verification(
    batch_year: int = 2027,
    concurrency: int = 25,
    dry_run: bool = False,
    hard_delete: bool = False,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> dict[str, int]:
    """Audit and prune jobs in database against Class of 2027 conditions."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    import sqlite3

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        tbl_name = "seen_jobs" if cursor.fetchone() else "jobs"

        query = f"""
            SELECT rowid AS numeric_id, id, company, title, location, apply_url, provider, direct_search_url, notes, status
            FROM {tbl_name}
            WHERE status IN ('NEW', 'NEEDS_RESOLVE')
            ORDER BY last_seen_at DESC
        """
        if limit:
            query += f" LIMIT {max(1, limit)}"

        cursor.execute(query)
        jobs = [dict(row) for row in cursor.fetchall()]

    total_jobs = len(jobs)
    console.print(
        f"\n[bold cyan]=== GCC JOB RADAR DEEP VERIFIER (CLASS OF {batch_year}) ===[/bold cyan]\n"
        f"[*] [bold white]Candidate Graduation Year:[/bold white] [bold green]{batch_year}[/bold green] (Pre-final / Penultimate year student)\n"
        f"[*] [bold white]Active Jobs to Audit:[/bold white] [bold yellow]{total_jobs}[/bold yellow]\n"
        f"[*] [bold white]Concurrency Workers:[/bold white] {concurrency}\n"
        f"[*] [bold white]Action Mode:[/bold white] {'DRY RUN (preview only)' if dry_run else ('HARD DELETE from DB' if hard_delete else 'DISMISS with reason in notes')}\n"
    )

    if not jobs:
        console.print("[yellow][!] No active jobs with status NEW or NEEDS_RESOLVE found to verify.[/yellow]")
        return {"checked": 0, "kept": 0, "dismissed": 0, "unverified": 0}

    semaphore = asyncio.Semaphore(concurrency)
    results: list[tuple[dict[str, Any], VerificationResult]] = []
    completed_count = 0

    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits, timeout=12.0, headers=DEFAULT_HEADERS, follow_redirects=True) as client:

        async def _verify_single(job: dict[str, Any]) -> None:
            nonlocal completed_count
            job_url = job.get("apply_url") or job.get("direct_search_url") or ""
            provider = job.get("provider")

            async with semaphore:
                content, status_marker = await fetch_job_content(
                    url=job_url,
                    provider=provider,
                    client=client,
                )
                verdict = evaluate_for_2027_candidate(
                    title=job.get("title", ""),
                    location=job.get("location", ""),
                    description=content,
                    fetch_status=status_marker,
                )
                results.append((job, verdict))
                completed_count += 1

                # Live progress indicator every 10 jobs
                if completed_count % 10 == 0 or completed_count == total_jobs:
                    action_label = "KEPT" if verdict.is_eligible else "DISMISSED"
                    color = "green" if verdict.is_eligible else "red"
                    console.print(
                        f"  [{completed_count}/{total_jobs}] [{color}]{action_label}[/{color}] "
                        f"#{job.get('numeric_id')} {job.get('company')} - {job.get('title')[:35]} "
                        f"([dim]{verdict.reason[:40]}[/dim])"
                    )

        tasks = [_verify_single(j) for j in jobs]
        await asyncio.gather(*tasks)

    # Compile and display summary table
    table = Table(
        title=f"Deep Verification Audit Results (Class of {batch_year})",
        show_header=True,
        header_style="bold cyan",
        box=box.ASCII if HAVE_RICH else None,
    )
    table.add_column("ID", justify="right", width=5)
    table.add_column("Company", style="bold white", width=16)
    table.add_column("Title", width=22)
    table.add_column("Decision", justify="center", width=11)
    table.add_column("Reason / Notes", style="dim")

    kept_count = 0
    dismissed_count = 0
    unverified_count = 0

    for job, verdict in sorted(results, key=lambda x: (x[1].is_eligible, x[0].get("numeric_id", 0))):
        jid = str(job.get("numeric_id") or job.get("id"))
        comp = (job.get("company") or "")[:18]
        pos_title = (job.get("title") or "")[:28]

        if verdict.is_eligible:
            if verdict.batch_fit == "UNVERIFIED_CHALLENGE":
                unverified_count += 1
                decision_str = "[yellow]PROTECTED[/yellow]"
            else:
                kept_count += 1
                decision_str = "[bold green]KEPT[/bold green]"
        else:
            dismissed_count += 1
            decision_str = "[bold red]DISMISSED[/bold red]"

            # Execute removal if not dry-run
            if not dry_run:
                purge_or_dismiss_job(
                    job_id=job.get("numeric_id") or job.get("id"),
                    reason=verdict.reason,
                    hard_delete=hard_delete,
                    db_path=db_path,
                )

        table.add_row(jid, comp, pos_title, decision_str, verdict.reason[:40])

    console.print()
    console.print(table)
    console.print()

    console.print(
        f"[bold cyan]Audit Summary:[/bold cyan]\n"
        f"[*] [bold white]Total Audited:[/bold white] {total_jobs}\n"
        f"[*] [bold green]Kept (2027 Eligible):[/bold green] {kept_count}\n"
        f"[*] [bold red]Dismissed / Purged:[/bold red] {dismissed_count}\n"
        f"[*] [bold yellow]Protected / Unverified (Anti-Bot):[/bold yellow] {unverified_count}\n"
    )

    if dry_run:
        console.print("[yellow][*] Dry-run complete: 0 database modifications were made.[/yellow]")
    else:
        action_word = "permanently purged from" if hard_delete else "marked as DISMISSED in"
        console.print(f"[bold green][OK] Successfully {action_word} database ({dismissed_count} disqualified roles removed).[/bold green]")

    return {
        "checked": total_jobs,
        "kept": kept_count,
        "dismissed": dismissed_count,
        "unverified": unverified_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Deep Description Crawler & Verifier for Class of 2027.")
    parser.add_argument("--batch", "-b", type=int, default=2027, help="Target student graduation year (default: 2027).")
    parser.add_argument("--concurrency", "-c", type=int, default=25, help="Concurrent link crawler requests (default: 25).")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Audit and display results without modifying the database.")
    parser.add_argument("--hard-delete", action="store_true", default=False, help="Permanently delete unqualified rows from seen_jobs instead of marking DISMISSED.")
    parser.add_argument("--limit", "-l", type=int, default=None, help="Maximum number of active jobs to audit.")
    parser.add_argument("--db-path", type=Path, default=None, help="Custom path to SQLite database.")

    args = parser.parse_args()

    asyncio.run(
        run_deep_verification(
            batch_year=args.batch,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
            hard_delete=args.hard_delete,
            limit=args.limit,
            db_path=args.db_path,
        )
    )


if __name__ == "__main__":
    main()

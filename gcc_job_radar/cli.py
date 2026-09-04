"""CLI entrypoint and command interface for GCC Job Radar."""

import asyncio
import csv
import json
from pathlib import Path
from typing import Optional
import typer
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from gcc_job_radar import __version__
from gcc_job_radar.config import COMPANIES
from gcc_job_radar.db import filter_new_jobs, get_stats, init_db, record_jobs
from gcc_job_radar.display import console, render_banner, render_results, render_stats
from gcc_job_radar.engine import scan_all_companies
from gcc_job_radar.models import JobPosting
from gcc_job_radar.notifier import dispatch_notifications

app = typer.Typer(
    name="gcc-job-radar",
    help="CLI tool to fetch verified entry-level tech roles in India from non-Indian tech companies & GCCs.",
    add_completion=False,
)


def export_json(jobs: list[JobPosting], path: Path) -> None:
    """Export postings to a formatted JSON file."""
    data = [job.model_dump(mode="json") for job in jobs]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    console.print(f"[bold green][+][/bold green] Exported {len(jobs)} postings to JSON: [cyan]{path}[/cyan]")


def export_csv(jobs: list[JobPosting], path: Path) -> None:
    """Export postings to a CSV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["company", "title", "location", "apply_url", "published_date", "provider"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for job in jobs:
            writer.writerow(
                {
                    "company": job.company,
                    "title": job.title,
                    "location": job.location,
                    "apply_url": str(job.apply_url),
                    "published_date": job.published_date or "Active",
                    "provider": job.provider.value,
                }
            )
    console.print(f"[bold green][+][/bold green] Exported {len(jobs)} postings to CSV: [cyan]{path}[/cyan]")


@app.command("scan")
def scan(
    company: Optional[str] = typer.Option(
        None,
        "--company",
        "-c",
        help="Filter scan to a specific company by name or board token.",
    ),
    concurrency: int = typer.Option(
        15,
        "--concurrency",
        help="Maximum concurrent HTTP requests to ATS endpoints.",
    ),
    new_only: bool = typer.Option(
        False,
        "--new-only",
        "-n",
        help="Only display and export postings not seen in previous runs.",
    ),
    stats: bool = typer.Option(
        False,
        "--stats",
        help="Display historical database statistics and exit.",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
    notify_discord: Optional[str] = typer.Option(
        None,
        "--notify-discord",
        help="Discord webhook URL to alert on newly detected postings.",
    ),
    notify_telegram_token: Optional[str] = typer.Option(
        None,
        "--notify-telegram-token",
        help="Telegram bot token to alert on newly detected postings.",
    ),
    notify_telegram_chat: Optional[str] = typer.Option(
        None,
        "--notify-telegram-chat",
        help="Telegram chat ID or channel to alert on newly detected postings.",
    ),
    json_path: Optional[Path] = typer.Option(
        None,
        "--json",
        "-j",
        help="Path to export results to a JSON file.",
    ),
    csv_path: Optional[Path] = typer.Option(
        None,
        "--csv",
        help="Path to export results to a CSV file.",
    ),
) -> None:
    """Scan canonical ATS endpoints for verified entry-level tech roles in India."""
    if stats:
        db_statistics = get_stats(db_path)
        render_stats(db_statistics)
        raise typer.Exit()

    init_db(db_path)

    target_companies = COMPANIES
    if company:
        query = company.strip().lower()
        target_companies = [
            c for c in COMPANIES if query in c.name.lower() or query in c.board_token.lower()
        ]
        if not target_companies:
            console.print(
                f"[bold red]Error:[/bold red] Company matching [yellow]'{company}'[/yellow] not found in registry."
            )
            console.print(
                f"[dim]Available companies: {', '.join([c.name for c in COMPANIES])}[/dim]"
            )
            raise typer.Exit(code=1)

    render_banner(len(target_companies))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Scanning ATS endpoints...", total=len(target_companies))

        def on_progress(comp_name: str, current: int, total: int) -> None:
            progress.update(
                task_id,
                completed=current,
                description=f"Scanning [cyan]{comp_name}[/cyan] ({current}/{total})...",
            )

        all_jobs = asyncio.run(
            scan_all_companies(
                companies=target_companies,
                concurrency=concurrency,
                on_progress=on_progress,
            )
        )
        progress.update(task_id, description="[bold green]Scan completed![/bold green]")

    new_jobs, existing_jobs = filter_new_jobs(all_jobs, db_path)

    # Dispatch notifications if new postings exist
    if new_jobs:
        asyncio.run(
            dispatch_notifications(
                new_jobs=new_jobs,
                discord_webhook=notify_discord,
                telegram_token=notify_telegram_token,
                telegram_chat_id=notify_telegram_chat,
            )
        )

    # Persist all current active jobs
    record_jobs(all_jobs, db_path)

    if new_only:
        render_results(new_jobs, is_new_only=True)
        jobs_to_export = new_jobs
    else:
        render_results(all_jobs, is_new_only=False)
        jobs_to_export = all_jobs

    if json_path:
        export_json(jobs_to_export, json_path)
    if csv_path:
        export_csv(jobs_to_export, csv_path)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit.",
        is_eager=True,
    ),
    company: Optional[str] = typer.Option(
        None,
        "--company",
        "-c",
        help="Filter scan to a specific company by name or board token.",
    ),
    concurrency: int = typer.Option(
        15,
        "--concurrency",
        help="Maximum concurrent HTTP requests to ATS endpoints.",
    ),
    new_only: bool = typer.Option(
        False,
        "--new-only",
        "-n",
        help="Only display and export postings not seen in previous runs.",
    ),
    stats: bool = typer.Option(
        False,
        "--stats",
        help="Display historical database statistics and exit.",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
    notify_discord: Optional[str] = typer.Option(
        None,
        "--notify-discord",
        help="Discord webhook URL to alert on newly detected postings.",
    ),
    notify_telegram_token: Optional[str] = typer.Option(
        None,
        "--notify-telegram-token",
        help="Telegram bot token to alert on newly detected postings.",
    ),
    notify_telegram_chat: Optional[str] = typer.Option(
        None,
        "--notify-telegram-chat",
        help="Telegram chat ID or channel to alert on newly detected postings.",
    ),
    json_path: Optional[Path] = typer.Option(
        None,
        "--json",
        "-j",
        help="Path to export results to a JSON file.",
    ),
    csv_path: Optional[Path] = typer.Option(
        None,
        "--csv",
        help="Path to export results to a CSV file.",
    ),
) -> None:
    """Root entrypoint: runs scan when no subcommand is given."""
    if version:
        console.print(f"[bold green]gcc-job-radar[/bold green] version [cyan]{__version__}[/cyan]")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        scan(
            company=company,
            concurrency=concurrency,
            new_only=new_only,
            stats=stats,
            db_path=db_path,
            notify_discord=notify_discord,
            notify_telegram_token=notify_telegram_token,
            notify_telegram_chat=notify_telegram_chat,
            json_path=json_path,
            csv_path=csv_path,
        )


if __name__ == "__main__":
    app()

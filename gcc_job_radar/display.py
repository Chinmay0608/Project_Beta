"""Rich terminal output rendering and tables for GCC Job Radar."""

from __future__ import annotations

from typing import Any, Optional
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gcc_job_radar.filters import is_remote_opening
from gcc_job_radar.link_resolver import resolve_effective_apply_url
from gcc_job_radar.models import JobPosting

import io
import sys

def create_safe_console() -> Console:
    """Initialize a Rich Console that operates safely in headless and non-UTF8 environments.

    Ensures standard streams are configured to UTF-8 with 'replace' error handling,
    and sets force_terminal=False when running without a physical TTY (Docker/Render/CI).
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None:
            if hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass
            elif hasattr(stream, "buffer"):
                try:
                    setattr(sys, stream_name, io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace"))
                except Exception:
                    pass

    is_tty = False
    if sys.stdout is not None and hasattr(sys.stdout, "isatty"):
        try:
            is_tty = sys.stdout.isatty()
        except Exception:
            is_tty = False

    return Console(
        highlight=False,
        force_terminal=False if not is_tty else None,
        color_system="auto" if is_tty else None,
    )


# Safe UTF-8 / headless terminal console configuration
console = create_safe_console()


def render_banner(total_companies: int, provider: Optional[str] = None) -> None:
    """Render a styled introduction banner in the terminal."""
    if provider:
        coverage_line = (
            f"[bold white]Coverage:[/bold white] Monitoring [bold cyan]{total_companies}[/bold cyan] "
            f"verified boards on [bold magenta]{provider.upper()}[/bold magenta] ATS API"
        )
    else:
        coverage_line = (
            f"[bold white]Coverage:[/bold white] Monitoring [bold cyan]{total_companies}[/bold cyan] "
            "tier-1 foreign GCCs & US/EU tech enterprises across canonical ATS APIs ([magenta]Greenhouse, Lever, Ashby, Workday, SmartRecruiters[/magenta])"
        )
    content = (
        "[bold white]Target:[/bold white] [green]Verified Entry-Level Tech Roles[/green] "
        "(SDE-1, Junior Engineer, Associate, Fresher, Tech Intern)\n"
        "[bold white]Hubs:[/bold white] [yellow]Bengaluru, Hyderabad, Pune, Gurgaon, Noida, Mumbai, Chennai, Remote India[/yellow]\n"
        f"{coverage_line}"
    )
    console.print(
        Panel(
            content,
            title="[bold cyan]GCC JOB RADAR - INDIA TECH[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print()


def render_results(jobs: list[JobPosting], is_new_only: bool = False) -> None:
    """Render results table or a helpful notice if no matches are found."""
    if not jobs:
        if is_new_only:
            msg = (
                "[bold yellow]No newly discovered entry-level roles since your last scan.[/bold yellow]\n\n"
                "[dim]- Any existing roles are already tracked in your local database.\n"
                "- Run without `--new-only` to view all currently active matching postings.\n"
                "- Check again later or schedule periodic runs to catch new openings as soon as they are posted.[/dim]"
            )
        else:
            msg = (
                "[bold yellow]No entry-level tech roles currently open matching strict criteria.[/bold yellow]\n\n"
                "[dim]- Senior, Lead, Staff, and numeral levels II+ were strictly filtered out.\n"
                "- Foreign GCC fresher & SDE-1 hiring cycles typically open in batches/quarters.\n"
                "- Run this radar regularly or schedule periodic automated checks to catch new batches immediately.[/dim]"
            )
        console.print(
            Panel(
                msg,
                title="[bold yellow]Scan Summary[/bold yellow]",
                border_style="yellow",
                padding=(1, 2),
            )
        )
        return

    heading = "Newly Discovered Entry-Level Openings" if is_new_only else "Verified Entry-Level Openings"
    table = Table(
        title=f"[bold green]{heading} ({len(jobs)})[/bold green]",
        box=box.ROUNDED,
        header_style="bold magenta",
        title_justify="left",
    )

    table.add_column("ID", style="bold green", justify="right", no_wrap=True)
    table.add_column("Score", justify="right", no_wrap=True)
    table.add_column("Company", style="bold white", no_wrap=True)
    table.add_column("Position", style="cyan")
    table.add_column("Location", style="yellow")
    table.add_column("ATS", style="magenta", justify="center")

    has_non_new_status = any(getattr(j, "status", "NEW").upper() != "NEW" for j in jobs)
    if has_non_new_status:
        table.add_column("Status", style="magenta", justify="center")

    table.add_column("Date", style="dim", justify="center", no_wrap=True)
    table.add_column("Apply Link", style="blue", overflow="fold")

    remote_count = 0
    for idx, job in enumerate(jobs, start=1):
        is_rem = getattr(job, "is_remote", False) or is_remote_opening(job)
        loc_str = job.location.strip()
        if is_rem:
            remote_count += 1
            if "remote" not in loc_str.lower():
                loc_display = f"{loc_str} [bold green](Remote)[/bold green]"
            else:
                loc_display = f"[bold green]{loc_str}[/bold green]"
        else:
            loc_display = loc_str

        display_id = str(getattr(job, "numeric_id", None) or idx)
        score_val = getattr(job, "relevance_score", 0) or 0
        if score_val >= 70:
            score_styled = f"[bold green]{score_val}[/bold green]"
        elif score_val >= 40:
            score_styled = f"[bold yellow]{score_val}[/bold yellow]"
        elif score_val > 0:
            score_styled = f"[cyan]{score_val}[/cyan]"
        else:
            score_styled = "[dim]0[/dim]"

        apply_url_str = str(job.apply_url)
        hyperlink = f"[link={apply_url_str}][underline]{apply_url_str}[/underline][/link]"

        row_cells = [
            display_id,
            score_styled,
            job.company,
            job.title,
            loc_display,
            job.provider.value.upper(),
        ]
        if has_non_new_status:
            stat = getattr(job, "status", "NEW").upper()
            if stat == "APPLIED":
                stat_styled = "[bold green]APPLIED[/bold green]"
            elif stat == "DISMISSED":
                stat_styled = "[bold yellow]DISMISSED[/bold yellow]"
            elif stat == "REJECTED":
                stat_styled = "[bold red]REJECTED[/bold red]"
            elif stat == "INTERVIEWING":
                stat_styled = "[bold cyan]INTERVIEWING[/bold cyan]"
            elif stat == "NEEDS_RESOLVE":
                stat_styled = "[bold yellow]NEEDS_RESOLVE[/bold yellow]"
            else:
                stat_styled = f"[dim]{stat}[/dim]"
            row_cells.append(stat_styled)

        row_cells.extend([
            job.published_date or "Active",
            hyperlink,
        ])
        table.add_row(*row_cells)

    console.print()
    console.print(table)

    # Print explicit clickable URLs list for terminals that don't support table OSC 8 hyperlinks or truncate them
    console.print("\n[bold cyan]Direct Apply Links:[/bold cyan]")
    for idx, job in enumerate(jobs, start=1):
        display_id = str(getattr(job, "numeric_id", None) or idx)
        score_val = getattr(job, "relevance_score", 0) or 0
        score_tag = f"[bold green]{score_val} pts[/bold green]" if score_val >= 70 else f"[yellow]{score_val} pts[/yellow]" if score_val >= 40 else f"[dim]{score_val} pts[/dim]"
        effective_url, direct_search, label = resolve_effective_apply_url(job)
        fallback_msg = (
            f"\n     [dim]Direct search fallback:[/dim] [cyan]{direct_search}[/cyan]"
            if direct_search and direct_search != effective_url
            else ""
        )
        console.print(
            f"  {display_id}. [{score_tag}] [bold white]{job.company}[/bold white] - [cyan]{job.title}[/cyan]\n"
            f"     [bold underline blue]{effective_url}[/bold underline blue] [dim]({label})[/dim]{fallback_msg}"
        )
    label = "new" if is_new_only else "active"
    remote_summary = f" ([bold cyan]{remote_count}[/bold cyan] 100% remote)" if remote_count > 0 else ""
    console.print(
        f"\n[bold green][+][/bold green] Found [bold green]{len(jobs)}[/bold green] {label} entry-level opening(s){remote_summary}."
    )


def render_stats(stats: dict[str, Any]) -> None:
    """Render database tracking statistics."""
    total = stats.get("total_tracked", 0)
    db_path = stats.get("db_path", "")
    first_seen = stats.get("first_recorded") or "N/A"
    last_seen = stats.get("last_active") or "N/A"
    breakdown = stats.get("company_breakdown", {})

    header = (
        f"[bold white]Total Historically Tracked Roles:[/bold white] [bold green]{total}[/bold green]\n"
        f"[bold white]First Recorded:[/bold white] [cyan]{first_seen}[/cyan] | "
        f"[bold white]Last Active:[/bold white] [cyan]{last_seen}[/cyan]\n"
        f"[bold white]Database Location:[/bold white] [dim]{db_path}[/dim]"
    )

    console.print(
        Panel(
            header,
            title="[bold cyan]GCC Job Radar - Database Statistics[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    status_counts = stats.get("status_counts", {})
    pipeline_table = Table(
        title="[bold cyan]Application Pipeline Status[/bold cyan]",
        box=box.SIMPLE,
        header_style="bold magenta",
    )
    pipeline_table.add_column("Status", style="bold white")
    pipeline_table.add_column("Count", style="green", justify="right")
    pipeline_table.add_column("Percentage", style="yellow", justify="right")

    pipeline_stages = [
        ("NEW", "NEW (Unapplied)"),
        ("APPLIED", "APPLIED (Awaiting Follow-up)"),
        ("INTERVIEWING", "INTERVIEWING"),
        ("REJECTED", "REJECTED"),
        ("DISMISSED", "DISMISSED"),
        ("NEEDS_RESOLVE", "NEEDS_RESOLVE"),
    ]
    for st, label in pipeline_stages:
        cnt = status_counts.get(st, 0)
        pct = f"{(cnt / total * 100):.1f}%" if total > 0 else "0.0%"
        pipeline_table.add_row(label, str(cnt), pct)

    console.print(pipeline_table)

    if breakdown:
        table = Table(
            title="[bold cyan]Historical Openings by Company[/bold cyan]",
            box=box.SIMPLE,
            header_style="bold magenta",
        )
        table.add_column("Company", style="bold white")
        table.add_column("Tracked Roles", style="green", justify="right")

        for company, count in breakdown.items():
            table.add_row(company, str(count))

        console.print(table)
    else:
        console.print("[dim]No historical postings recorded yet. Run a scan to populate the database.[/dim]\n")


def render_stale_applications(stale_jobs: list[dict[str, Any]], days: int = 7) -> None:
    """Render stale applications awaiting follow-up."""
    if not stale_jobs:
        console.print(
            Panel(
                f"[bold green]No stale applications found![/bold green]\n\n"
                f"[dim]All tracked applications have been submitted less than {days} day(s) ago or updated.[/dim]",
                title="[bold cyan]Application Follow-up Tracker[/bold cyan]",
                border_style="green",
                padding=(1, 2),
            )
        )
        return

    table = Table(
        title=f"[bold yellow]Applications Pending Follow-up (>= {days} days) ({len(stale_jobs)})[/bold yellow]",
        box=box.ROUNDED,
        header_style="bold magenta",
    )
    table.add_column("ID", style="bold green", justify="right", no_wrap=True)
    table.add_column("Score", justify="right", no_wrap=True)
    table.add_column("Company", style="bold white", no_wrap=True)
    table.add_column("Position", style="cyan")
    table.add_column("Applied Date", style="yellow")
    table.add_column("Elapsed", justify="right")
    table.add_column("Notes", style="dim")

    for idx, j in enumerate(stale_jobs, start=1):
        display_id = str(j.get("numeric_id") or idx)
        score_val = j.get("relevance_score", 0) or 0
        if score_val >= 70:
            score_styled = f"[bold green]{score_val}[/bold green]"
        elif score_val >= 40:
            score_styled = f"[bold yellow]{score_val}[/bold yellow]"
        elif score_val > 0:
            score_styled = f"[cyan]{score_val}[/cyan]"
        else:
            score_styled = "[dim]0[/dim]"

        elapsed = j.get("days_elapsed", 0)
        elapsed_styled = f"[bold red]{elapsed}d ago[/bold red]" if elapsed >= 14 else f"[yellow]{elapsed}d ago[/yellow]"
        applied_at = str(j.get("applied_at", ""))[:10]
        notes = str(j.get("notes") or "-")

        table.add_row(
            display_id,
            score_styled,
            str(j.get("company", "")),
            str(j.get("title", "")),
            applied_at,
            elapsed_styled,
            notes,
        )

    console.print(table)
    console.print("\n[bold cyan]Direct Outreach Links:[/bold cyan]")
    for idx, j in enumerate(stale_jobs, start=1):
        display_id = str(j.get("numeric_id") or idx)
        eff_url, direct_search, label = resolve_effective_apply_url(j)
        fallback_msg = (
            f"\n     [dim]Direct search fallback:[/dim] [cyan]{direct_search}[/cyan]"
            if direct_search and direct_search != eff_url
            else ""
        )
        console.print(
            f"  {display_id}. [bold white]{j.get('company')}[/bold white] - [cyan]{j.get('title')}[/cyan]\n"
            f"     [bold underline blue]{eff_url}[/bold underline blue] [dim]({label})[/dim]{fallback_msg}"
        )


def render_dormant_companies(entries: list[dict[str, Any]]) -> None:
    """Render list of paused / dormant companies."""
    if not entries:
        console.print(
            Panel(
                "[bold green]No dormant companies found![/bold green]\n\n"
                "[dim]All registered target companies are currently active in the scanning radar.[/dim]",
                title="[bold cyan]Dormant Companies Registry[/bold cyan]",
                border_style="green",
                padding=(1, 2),
            )
        )
        return

    table = Table(
        title=f"[bold yellow]Dormant / Paused Companies ({len(entries)})[/bold yellow]",
        box=box.ROUNDED,
        header_style="bold magenta",
    )
    table.add_column("Company", style="bold white", no_wrap=True)
    table.add_column("Reason", style="yellow")
    table.add_column("Zero-Match Scans", justify="right", style="cyan")
    table.add_column("Paused Date", style="dim")
    table.add_column("Notes", style="dim")

    for entry in entries:
        table.add_row(
            str(entry.get("company_name", "")),
            str(entry.get("reason", "") or "-"),
            str(entry.get("consecutive_zero_scans", 0)),
            str(entry.get("paused_at", ""))[:10] if entry.get("paused_at") else "-",
            str(entry.get("notes", "") or "-"),
        )

    console.print(table)
    console.print("\n[dim]To reactivate any company, run: [cyan]gcc-job-radar reactivate <company_name>[/cyan][/dim]\n")

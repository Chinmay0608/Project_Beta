"""Rich terminal output rendering and tables for GCC Job Radar."""

from typing import Any
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gcc_job_radar.models import JobPosting

# Safe UTF-8 / Windows terminal console configuration
console = Console(highlight=False)


def render_banner(total_companies: int) -> None:
    """Render a styled introduction banner in the terminal."""
    content = (
        "[bold white]Target:[/bold white] [green]Verified Entry-Level Tech Roles[/green] "
        "(SDE-1, Junior Engineer, Associate, Fresher, Tech Intern)\n"
        "[bold white]Hubs:[/bold white] [yellow]Bengaluru, Hyderabad, Pune, Gurgaon, Noida, Mumbai, Chennai, Remote India[/yellow]\n"
        f"[bold white]Coverage:[/bold white] Monitoring [bold cyan]{total_companies}[/bold cyan] "
        "tier-1 foreign GCCs & US/EU tech enterprises across canonical ATS APIs ([magenta]Greenhouse, Lever, Ashby[/magenta])"
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

    table.add_column("Company", style="bold white", no_wrap=True)
    table.add_column("Position", style="cyan")
    table.add_column("Location", style="yellow")
    table.add_column("ATS", style="magenta", justify="center")
    table.add_column("Date", style="dim", justify="center", no_wrap=True)
    table.add_column("Apply Link", style="blue", overflow="fold")

    for job in jobs:
        apply_url_str = str(job.apply_url)
        hyperlink = f"[link={apply_url_str}][underline]{apply_url_str}[/underline][/link]"
        table.add_row(
            job.company,
            job.title,
            job.location,
            job.provider.value.upper(),
            job.published_date or "Active",
            hyperlink,
        )

    console.print()
    console.print(table)

    # Print explicit clickable URLs list for terminals that don't support table OSC 8 hyperlinks or truncate them
    console.print("\n[bold cyan]Direct Apply Links:[/bold cyan]")
    for idx, job in enumerate(jobs, start=1):
        apply_url_str = str(job.apply_url)
        console.print(
            f"  {idx}. [bold white]{job.company}[/bold white] - [cyan]{job.title}[/cyan]\n"
            f"     [bold underline blue]{apply_url_str}[/bold underline blue]"
        )
    label = "new" if is_new_only else "active"
    console.print(
        f"\n[bold green][+][/bold green] Found [bold green]{len(jobs)}[/bold green] {label} entry-level opening(s)."
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

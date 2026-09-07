"""Scale gcc_job_radar/config.py to 5,000+ verified active companies in one command.

Harvests direct ATS listings and high-probability enterprise targets from:
1. SimplifyJobs repositories (Summer 2025, New Grad, Summer 2024 - 50,000+ listings)
2. outscal/OpenJobs directory (12,144 tech companies with direct ATS links)
3. SEC EDGAR public corporation ticker registry (10,415 public tech & enterprise firms)
4. Curated Global Capability Centers & enterprise tech cohorts

Probes concurrently with 80 workers across Greenhouse, Lever, Ashby, and SmartRecruiters,
verifying that every single added board has active job postings, and appends them cleanly
to gcc_job_radar/config.py until the target of 5,000 companies is reached.
"""

import argparse
import asyncio
import logging
from pathlib import Path
import re
import sys
from typing import Any, Optional

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.models import ATSProvider
from tools.harvest_mass_ats import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_HEADERS,
    VerifiedATSBoard,
    append_verified_boards_to_config,
    extract_slug_from_url,
    generate_slug_variations,
    strip_legal_suffixes,
    verify_ats_board,
)

try:
    from rich.console import Console

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

logger = logging.getLogger("scale_to_5000")

DEFAULT_GOAL = 5000
DEFAULT_CONCURRENCY = 80
DEFAULT_TIMEOUT = httpx.Timeout(5.0, connect=3.0)


async def fetch_direct_ats_candidates(client: httpx.AsyncClient) -> list[tuple[str, ATSProvider, str]]:
    """Harvest thousands of direct ATS boards from SimplifyJobs and OpenJobs."""
    candidates: list[tuple[str, ATSProvider, str]] = []
    seen: set[tuple[ATSProvider, str]] = set()

    sources = [
        ("SimplifyJobs Summer 2025", "https://raw.githubusercontent.com/SimplifyJobs/Summer2025-Internships/dev/.github/scripts/listings.json"),
        ("SimplifyJobs New Grad", "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"),
        ("SimplifyJobs Summer 2024", "https://raw.githubusercontent.com/SimplifyJobs/Summer2024-Internships/dev/.github/scripts/listings.json"),
    ]

    for label, url in sources:
        try:
            console.print(f"[cyan][*] Ingesting {label}...[/cyan]")
            resp = await client.get(url, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                count = 0
                for item in data:
                    item_url = item.get("url") or ""
                    ext = extract_slug_from_url(item_url)
                    if ext:
                        prov, slug = ext
                        key = (prov, slug.lower())
                        if key not in seen:
                            seen.add(key)
                            c_name = item.get("company_name") or slug
                            candidates.append((c_name, prov, slug))
                            count += 1
                console.print(f"    [green]-> Extracted {count} unique ATS targets from {label}[/green]")
        except Exception as exc:
            console.print(f"[yellow][!] Could not fetch {label}: {exc}[/yellow]")

    # OpenJobs 12,144 tech companies
    try:
        console.print("[cyan][*] Ingesting outscal/OpenJobs (12,144 tech companies)...[/cyan]")
        resp = await client.get("https://raw.githubusercontent.com/outscal/OpenJobs/main/data/companies_v2.json", timeout=15.0)
        if resp.status_code == 200:
            data = resp.json()
            count = 0
            for c in data:
                c_name = c.get("name") or ""
                urls = (c.get("ats_links") or []) + (c.get("list_urls") or [])
                for u in urls:
                    ext = extract_slug_from_url(u)
                    if ext:
                        prov, slug = ext
                        key = (prov, slug.lower())
                        if key not in seen:
                            seen.add(key)
                            candidates.append((c_name or slug, prov, slug))
                            count += 1
                        break
            console.print(f"    [green]-> Extracted {count} unique ATS targets from OpenJobs[/green]")
    except Exception as exc:
        console.print(f"[yellow][!] Could not fetch OpenJobs: {exc}[/yellow]")

    return candidates


async def fetch_sec_edgar_candidates(client: httpx.AsyncClient) -> list[str]:
    """Fetch 10,000+ public enterprise company names from official SEC EDGAR registry."""
    try:
        console.print("[cyan][*] Ingesting SEC EDGAR Official Registry (10,415 public corporations)...[/cyan]")
        resp = await client.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": "gcc-job-radar/mass-scaler research@example.com"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            names = [v.get("title", "") for v in data.values() if v.get("title")]
            console.print(f"    [green]-> Extracted {len(names)} public corporate candidates[/green]")
            return names
    except Exception as exc:
        console.print(f"[yellow][!] Could not fetch SEC EDGAR: {exc}[/yellow]")
    return []


async def run_scale_to_goal(
    goal: int = DEFAULT_GOAL,
    concurrency: int = DEFAULT_CONCURRENCY,
    dry_run: bool = False,
    config_path: Optional[Path] = None,
) -> None:
    """Scale config.COMPANIES to goal verified companies."""
    cfg_file = config_path or DEFAULT_CONFIG_PATH
    current_count = len(COMPANIES)

    console.print(
        f"\n[bold cyan]=== GCC JOB RADAR 5,000+ COMPANY ACCELERATOR ===[/bold cyan]\n"
        f"• [bold white]Current Active Companies:[/bold white] [bold green]{current_count}[/bold green]\n"
        f"• [bold white]Target Goal:[/bold white] [bold yellow]{goal}[/bold yellow]\n"
        f"• [bold white]Companies Needed:[/bold white] [bold magenta]{max(0, goal - current_count)}[/bold magenta]\n"
        f"• [bold white]Concurrency Workers:[/bold white] {concurrency}\n"
        f"• [bold white]Mode:[/bold white] {'DRY RUN (preview only)' if dry_run else 'AUTO-APPEND to config.py'}\n"
    )

    if current_count >= goal:
        console.print(f"[bold green][OK] Goal already satisfied! Total companies: {current_count} >= {goal}[/bold green]")
        return

    needed = goal - current_count

    # Build existing lookup sets
    existing_tokens: set[tuple[ATSProvider, str]] = {(c.provider, c.board_token.lower()) for c in COMPANIES}
    existing_names: set[str] = {strip_legal_suffixes(c.name).lower() for c in COMPANIES}

    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits, timeout=DEFAULT_TIMEOUT, headers=DEFAULT_HEADERS, follow_redirects=True) as client:
        # 1. Harvest Direct ATS Candidates
        direct_candidates = await fetch_direct_ats_candidates(client)

        # 2. Harvest SEC EDGAR Candidates
        sec_names = await fetch_sec_edgar_candidates(client)

        all_candidates: list[tuple[str, ATSProvider, str]] = []
        seen_batch_tokens: set[tuple[ATSProvider, str]] = set(existing_tokens)

        # Add direct ATS candidates
        for name, prov, slug in direct_candidates:
            token_key = (prov, slug.lower())
            if token_key not in seen_batch_tokens:
                seen_batch_tokens.add(token_key)
                clean_name = strip_legal_suffixes(name) or name.strip()
                all_candidates.append((clean_name, prov, slug))

        # Add SEC EDGAR candidate variations across Greenhouse, Lever, Ashby, SmartRecruiters
        for name in sec_names:
            clean_name = strip_legal_suffixes(name) or name.strip()
            if clean_name.lower() in existing_names:
                continue
            for var in generate_slug_variations(clean_name)[:2]:
                for prov in (ATSProvider.GREENHOUSE, ATSProvider.LEVER, ATSProvider.ASHBY, ATSProvider.SMARTRECRUITERS):
                    token_key = (prov, var.lower())
                    if token_key not in seen_batch_tokens:
                        seen_batch_tokens.add(token_key)
                        all_candidates.append((clean_name, prov, var))

        console.print(
            f"\n[bold cyan][*][/bold cyan] Total deduplicated candidate boards queued for probing: "
            f"[bold white]{len(all_candidates)}[/bold white]...\n"
        )

        semaphore = asyncio.Semaphore(concurrency)
        verified_boards: list[VerifiedATSBoard] = []
        verified_names: set[str] = set(existing_names)
        stop_event = asyncio.Event()
        checked_count = 0
        total_to_check = len(all_candidates)

        async def _probe(name: str, prov: ATSProvider, slug: str) -> None:
            nonlocal checked_count
            if stop_event.is_set():
                return

            async with semaphore:
                if stop_event.is_set():
                    return

                active_count = await verify_ats_board(prov, slug, client)
                checked_count += 1

                if active_count and active_count > 0:
                    clean_name = strip_legal_suffixes(name) or name.strip()
                    clean_lower = clean_name.lower()

                    # Deduplicate name in batch
                    if clean_lower in verified_names:
                        clean_name = f"{clean_name} ({slug})"

                    verified_names.add(clean_name.lower())
                    board = VerifiedATSBoard(
                        company_name=clean_name,
                        provider=prov,
                        board_token=slug,
                        active_postings=active_count,
                    )
                    verified_boards.append(board)

                    # Print progress every 50 discovered or first
                    if len(verified_boards) % 50 == 0 or len(verified_boards) == 1:
                        current_total = current_count + len(verified_boards)
                        console.print(
                            f"  [bold green][+][/bold green] Found [bold white]{len(verified_boards)}[/bold white] verified "
                            f"(Total: [bold cyan]{current_total}[/bold cyan] / {goal}) "
                            f"-> Latest: [bold white]{clean_name}[/bold white] ({prov.value}: {slug}, {active_count} jobs) "
                            f"[{checked_count}/{total_to_check} probed]"
                        )

                    if len(verified_boards) >= needed:
                        stop_event.set()

        tasks = [_probe(n, p, s) for n, p, s in all_candidates]
        await asyncio.gather(*tasks)

        console.print(
            f"\n[bold green][OK] Probing complete![/bold green] "
            f"Discovered [bold white]{len(verified_boards)}[/bold white] new live ATS boards.\n"
        )

        if dry_run:
            console.print(f"[yellow][*] Dry-run mode: {len(verified_boards)} verified boards found but NOT written to config.py.[/yellow]")
        else:
            if verified_boards:
                appended = append_verified_boards_to_config(verified_boards, config_path=cfg_file)
                new_total = current_count + appended
                console.print(
                    f"[bold green][+][/bold green] Successfully appended [bold white]{appended}[/bold white] "
                    f"verified boards to [cyan]{cfg_file.name}[/cyan]!\n"
                    f"[bold cyan][*] New Total Active Companies: {new_total}[/bold cyan] (Goal: {goal})"
                )
            else:
                console.print("[yellow][!] No new boards met verification criteria to append.[/yellow]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scale gcc_job_radar/config.py to 5,000+ verified active companies.")
    parser.add_argument("--goal", "-g", type=int, default=DEFAULT_GOAL, help=f"Target total company count (default: {DEFAULT_GOAL}).")
    parser.add_argument("--concurrency", "-c", type=int, default=DEFAULT_CONCURRENCY, help=f"Concurrent HTTP probing connections (default: {DEFAULT_CONCURRENCY}).")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Probe and preview discovered boards without modifying config.py.")
    parser.add_argument("--config-path", type=Path, default=None, help="Custom path to config.py.")
    args = parser.parse_args()

    asyncio.run(
        run_scale_to_goal(
            goal=args.goal,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
            config_path=args.config_path,
        )
    )


if __name__ == "__main__":
    main()

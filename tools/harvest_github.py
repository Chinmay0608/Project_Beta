"""Automated GitHub Curated Repositories ATS Harvester for gcc-job-radar.

Discovers unmonitored companies and active ATS job boards directly from curated,
high-reputation tech hiring repositories (SimplifyJobs, Poteto Hiring Without Whiteboards,
Awesome Remote Job, Easy Application, RemoteInTech) with zero CAPTCHAs.

Supports:
1. Direct ATS Link Extraction (Greenhouse, Ashby, Lever, SmartRecruiters)
2. Curated Company Directory Probing (via probe_company)
3. Dynamic Exclusion against gcc_job_radar.config.COMPANIES
4. Live REST validation and direct appending to config.py
"""

import argparse
import asyncio
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import logging
from pathlib import Path
import re
import sys
from typing import Optional
from urllib.parse import urlparse

import httpx

# Ensure project root is in sys.path when invoked directly
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.models import ATSProvider
from tools.discover_ats import IGNORED_SLUGS, slug_to_name
from tools.probe_ats import (
    ProbeResult,
    append_to_config,
    check_platform,
    deduplicate_results,
    probe_company,
)

try:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

    class FallbackConsole:  # type: ignore[no-redef]
        def print(self, *args, **kwargs) -> None:
            clean_args = [
                re.sub(r"\[/?(?:bold|green|yellow|red|cyan|magenta|dim)[^\]]*\]", "", str(a))
                for a in args
            ]
            print(*clean_args)

    console = FallbackConsole()

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 40
DEFAULT_TIMEOUT = 15.0

# Curated repository source URLs
CURATED_SOURCES = {
    "simplify_internships": {
        "name": "SimplifyJobs - Summer Internships",
        "url": "https://raw.githubusercontent.com/SimplifyJobs/Summer2025-Internships/dev/README.md",
        "type": "markdown_and_html",
    },
    "simplify_newgrad": {
        "name": "SimplifyJobs - New Grad Positions",
        "url": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
        "type": "markdown_and_html",
    },
    "speedyapply_college": {
        "name": "SpeedyApply - 2025 College Jobs",
        "url": "https://raw.githubusercontent.com/speedyapply/2025-SWE-College-Jobs/main/README.md",
        "type": "markdown_and_html",
    },
    "hiring_no_whiteboards": {
        "name": "Poteto - Hiring Without Whiteboards",
        "url": "https://raw.githubusercontent.com/poteto/hiring-without-whiteboards/master/README.md",
        "type": "markdown_and_html",
    },
    "easy_application": {
        "name": "J-Delaney - Easy Application",
        "url": "https://raw.githubusercontent.com/j-delaney/easy-application/master/README.md",
        "type": "markdown_and_html",
    },
    "awesome_remote_job": {
        "name": "Awesome Remote Job (Remote DNA)",
        "url": "https://raw.githubusercontent.com/lukasz-madon/awesome-remote-job/master/README.md",
        "type": "markdown_and_html",
    },
}

# Regex for direct ATS board links
GH_PATTERN = re.compile(
    r"https?://(?:job-)?boards\.greenhouse\.io/([a-zA-Z0-9\-_]+)", re.IGNORECASE
)
ASHBY_PATTERN = re.compile(
    r"https?://jobs\.ashbyhq\.com/([a-zA-Z0-9\-_]+)", re.IGNORECASE
)
LEVER_PATTERN = re.compile(
    r"https?://jobs\.lever\.co/([a-zA-Z0-9\-_]+)", re.IGNORECASE
)
SR_PATTERN = re.compile(
    r"https?://jobs\.smartrecruiters\.com/([a-zA-Z0-9\-_]+)", re.IGNORECASE
)

GENERIC_LINK_NAMES = {
    "apply",
    "apply now",
    "apply here",
    "click here",
    "link",
    "careers",
    "jobs",
    "website",
    "site",
    "here",
    "view",
    "posting",
    "openings",
    "internship",
    "new grad",
    "url",
    "simplify",
}

NON_COMPANY_TITLE_TERMS = [
    "interview",
    "how to",
    "guide",
    "article",
    "hacker news",
    "hackernews",
    "reddit",
    "medium",
    "blog",
    "github",
    "table of contents",
    "contributing",
    "license",
]


@dataclass
class DirectTokenCandidate:
    """Represents an extracted direct ATS link candidate."""

    company_name: str
    provider: ATSProvider
    token: str
    source_url: str


class HTMLLinkExtractor(HTMLParser):
    """Extracts raw href links and their anchor text from HTML content."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []  # (text, href)
        self._current_href: Optional[str] = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "a":
            for attr_name, attr_val in attrs:
                if attr_name.lower() == "href" and attr_val:
                    self._current_href = attr_val
                    self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current_href is not None:
            anchor_text = " ".join(self._current_text).strip()
            self.links.append((anchor_text, self._current_href))
            self._current_href = None
            self._current_text = []


def is_generic_name(name: str) -> bool:
    """Check if the anchor text is a generic placeholder rather than a company name."""
    cleaned = name.strip().lower()
    if not cleaned or len(cleaned) < 2:
        return True
    if cleaned in GENERIC_LINK_NAMES:
        return True
    if any(cleaned.startswith(p) for p in ["apply", "view", "link to", "click here"]):
        return True
    if cleaned.startswith("http") or cleaned.startswith("www."):
        return True
    # Strip emojis and punctuation: if nothing remains or only symbols
    alnum = re.sub(r"[^a-zA-Z0-9]", "", cleaned)
    if not alnum:
        return True
    return False


def is_valid_company_name(name: str) -> bool:
    """Validate if candidate name looks like a legitimate company rather than an article/guide."""
    cleaned = name.strip()
    if len(cleaned) < 2 or len(cleaned) > 50:
        return False
    lower = cleaned.lower()
    if any(term in lower for term in NON_COMPANY_TITLE_TERMS):
        return False
    if is_generic_name(cleaned):
        return False
    # Must have at least one alphabet character
    if not any(c.isalpha() for c in cleaned):
        return False
    return True


def extract_direct_ats_links(text: str, source_label: str = "") -> list[DirectTokenCandidate]:
    """Extract direct ATS candidate tokens and associated names from Markdown and HTML text."""
    candidates: list[DirectTokenCandidate] = []
    seen_tokens: set[tuple[ATSProvider, str]] = set()

    # 1. Extract markdown links: [Link Text](URL)
    md_links = re.findall(r"\[([^\]]+)\]\((https?://[^)]+)\)", text)
    for anchor_text, url in md_links:
        for provider, pattern in [
            (ATSProvider.GREENHOUSE, GH_PATTERN),
            (ATSProvider.ASHBY, ASHBY_PATTERN),
            (ATSProvider.LEVER, LEVER_PATTERN),
            (ATSProvider.SMARTRECRUITERS, SR_PATTERN),
        ]:
            m = pattern.search(url)
            if m:
                token = m.group(1).strip().lower()
                if token and token not in IGNORED_SLUGS:
                    key = (provider, token)
                    if key not in seen_tokens:
                        seen_tokens.add(key)
                        company_name = (
                            anchor_text.strip()
                            if not is_generic_name(anchor_text)
                            else slug_to_name(token)
                        )
                        candidates.append(
                            DirectTokenCandidate(
                                company_name=company_name,
                                provider=provider,
                                token=token,
                                source_url=source_label,
                            )
                        )

    # 2. Extract HTML links: <a href="URL">Anchor Text</a>
    html_parser = HTMLLinkExtractor()
    try:
        html_parser.feed(text)
        for anchor_text, url in html_parser.links:
            for provider, pattern in [
                (ATSProvider.GREENHOUSE, GH_PATTERN),
                (ATSProvider.ASHBY, ASHBY_PATTERN),
                (ATSProvider.LEVER, LEVER_PATTERN),
                (ATSProvider.SMARTRECRUITERS, SR_PATTERN),
            ]:
                m = pattern.search(url)
                if m:
                    token = m.group(1).strip().lower()
                    if token and token not in IGNORED_SLUGS:
                        key = (provider, token)
                        if key not in seen_tokens:
                            seen_tokens.add(key)
                            company_name = (
                                anchor_text.strip()
                                if not is_generic_name(anchor_text)
                                else slug_to_name(token)
                            )
                            candidates.append(
                                DirectTokenCandidate(
                                    company_name=company_name,
                                    provider=provider,
                                    token=token,
                                    source_url=source_label,
                                )
                            )
    except Exception as exc:
        logger.debug("HTML link extraction notice: %s", exc)

    # 3. Fallback: Catch any standalone raw URLs not wrapped in Markdown/HTML
    raw_urls = re.findall(r"https?://[^\s\"'>)]+", text)
    for url in raw_urls:
        for provider, pattern in [
            (ATSProvider.GREENHOUSE, GH_PATTERN),
            (ATSProvider.ASHBY, ASHBY_PATTERN),
            (ATSProvider.LEVER, LEVER_PATTERN),
            (ATSProvider.SMARTRECRUITERS, SR_PATTERN),
        ]:
            m = pattern.search(url)
            if m:
                token = m.group(1).strip().lower()
                if token and token not in IGNORED_SLUGS:
                    key = (provider, token)
                    if key not in seen_tokens:
                        seen_tokens.add(key)
                        candidates.append(
                            DirectTokenCandidate(
                                company_name=slug_to_name(token),
                                provider=provider,
                                token=token,
                                source_url=source_label,
                            )
                        )

    return candidates


def extract_candidate_company_names(text: str) -> list[str]:
    """Extract candidate company names from markdown tables and list items.

    Matches lines like:
    - `- [Company](url) | Location | ...`
    - `| [Company](url) | Location | ...`
    - `* [Company](url)`
    """
    candidates: list[str] = []
    seen: set[str] = set()

    for line in text.splitlines():
        line_clean = line.strip()
        if not line_clean.startswith(("- [", "| [", "* [", "|[", "1. [", "2. [", "3. [")):
            continue

        m = re.search(r"\[([^\]]+)\]\((https?://[^)]+)\)", line_clean)
        if m:
            name = m.group(1).strip()
            if is_valid_company_name(name):
                lower_name = name.lower()
                if lower_name not in seen:
                    seen.add(lower_name)
                    candidates.append(name)

    return candidates


def get_existing_exclusions() -> tuple[set[str], set[str]]:
    """Return set of existing board tokens and lowercased company names from config.COMPANIES."""
    existing_tokens = {c.board_token.strip().lower() for c in COMPANIES}
    existing_names = {c.name.strip().lower() for c in COMPANIES}
    return existing_tokens, existing_names


async def fetch_source_text(
    url: str,
    client: httpx.AsyncClient,
    retries: int = 2,
) -> Optional[str]:
    """Fetch raw file content from GitHub with retries and timeout."""
    for attempt in range(retries + 1):
        try:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 404:
                logger.warning("Source returned 404: %s", url)
                return None
        except Exception as exc:
            if attempt == retries:
                logger.warning("Failed to fetch %s: %s", url, exc)
            await asyncio.sleep(1.0)
    return None


async def validate_direct_candidate(
    candidate: DirectTokenCandidate,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> Optional[ProbeResult]:
    """Validate a candidate ATS token via check_platform under semaphore."""
    async with semaphore:
        try:
            active_postings = await check_platform(
                candidate.provider, candidate.token, client
            )
            if active_postings is not None and active_postings > 0:
                return ProbeResult(
                    company_name=candidate.company_name,
                    provider=candidate.provider,
                    board_token=candidate.token,
                    active_postings=active_postings,
                )
        except Exception as exc:
            logger.debug("Validation error for %s (%s): %s", candidate.token, candidate.provider, exc)
    return None


async def probe_company_candidate(
    name: str,
    client: httpx.AsyncClient,
    existing_names: set[str],
    semaphore: asyncio.Semaphore,
) -> Optional[ProbeResult]:
    """Probe an unmonitored company name under semaphore."""
    async with semaphore:
        try:
            return await probe_company(name, client, existing_names)
        except Exception as exc:
            logger.debug("Probe error for company '%s': %s", name, exc)
    return None


async def harvest_curated_repos(
    sources: Optional[list[str]] = None,
    mode: str = "all",
    limit: Optional[int] = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT,
    append: bool = True,
    dry_run: bool = False,
    cluster: str = "3",
) -> list[ProbeResult]:
    """Execute asynchronous ATS discovery across curated GitHub repositories."""
    console.print(
        "[bold cyan]=== GITHUB CURATED REPOSITORIES ATS HARVESTER ===[/bold cyan]"
    )

    existing_tokens, existing_names = get_existing_exclusions()
    console.print(
        f"[*] Initialized dynamic exclusion: [bold green]{len(existing_tokens)}[/bold green] tokens, "
        f"[bold green]{len(existing_names)}[/bold green] company names from config.COMPANIES"
    )

    selected_sources = {}
    if sources:
        for s in sources:
            s_key = s.lower().strip()
            if s_key in CURATED_SOURCES:
                selected_sources[s_key] = CURATED_SOURCES[s_key]
            else:
                console.print(f"[bold yellow][!] Unknown source:[/bold yellow] '{s}', skipping.")
    if not selected_sources:
        selected_sources = CURATED_SOURCES

    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    http_timeout = httpx.Timeout(timeout, connect=5.0)

    direct_candidates: list[DirectTokenCandidate] = []
    company_name_candidates: list[str] = []

    async with httpx.AsyncClient(limits=limits, timeout=http_timeout) as client:
        # Phase 1: Ingest Curated Sources
        console.print(f"\n[bold]Phase 1: Ingesting {len(selected_sources)} Curated GitHub Repositories...[/bold]")
        for key, src in selected_sources.items():
            console.print(f"  Fetching [cyan]{src['name']}[/cyan] ({src['url']})...")
            text = await fetch_source_text(src["url"], client)
            if not text:
                console.print("    [red]Failed to retrieve content.[/red]")
                continue

            # Direct ATS links
            if mode in ("all", "direct-only"):
                extracted_direct = extract_direct_ats_links(text, source_label=src["name"])
                # Filter out existing tokens immediately
                new_direct = [
                    c for c in extracted_direct
                    if c.token not in existing_tokens
                    and c.company_name.strip().lower() not in existing_names
                ]
                direct_candidates.extend(new_direct)
                console.print(
                    f"    -> Extracted [bold green]{len(extracted_direct)}[/bold green] direct ATS links "
                    f"([bold yellow]{len(new_direct)}[/bold yellow] unmonitored)"
                )

            # Curated company names
            if mode in ("all", "names-only"):
                extracted_names = extract_candidate_company_names(text)
                new_names = [
                    n for n in extracted_names
                    if n.strip().lower() not in existing_names
                ]
                company_name_candidates.extend(new_names)
                console.print(
                    f"    -> Extracted [bold green]{len(extracted_names)}[/bold green] company names "
                    f"([bold yellow]{len(new_names)}[/bold yellow] unmonitored)"
                )

        # Deduplicate direct candidates by (provider, token)
        seen_cand_keys = set()
        unique_direct: list[DirectTokenCandidate] = []
        for c in direct_candidates:
            k = (c.provider, c.token)
            if k not in seen_cand_keys:
                seen_cand_keys.add(k)
                unique_direct.append(c)

        # Deduplicate company names
        unique_names = list(dict.fromkeys(company_name_candidates))

        if limit is not None and limit > 0:
            unique_direct = unique_direct[:limit]
            unique_names = unique_names[:limit]

        semaphore = asyncio.Semaphore(concurrency)
        all_results: list[ProbeResult] = []

        # Phase 2: Validate Direct ATS Links
        if mode in ("all", "direct-only") and unique_direct:
            console.print(
                f"\n[bold]Phase 2: Validating {len(unique_direct)} Unique Direct ATS Candidates...[/bold]"
            )
            tasks = [
                validate_direct_candidate(cand, client, semaphore)
                for cand in unique_direct
            ]
            direct_results = await asyncio.gather(*tasks)
            validated_direct = [r for r in direct_results if r is not None]
            for r in validated_direct:
                console.print(
                    f"  [bold green][VALIDATED][/bold green] {r.company_name} -> "
                    f"[cyan]{r.provider.value}[/cyan] ({r.board_token}) with "
                    f"[bold yellow]{r.active_postings}[/bold yellow] live jobs"
                )
                # Add to exclusions to prevent duplicate probing in names phase
                existing_tokens.add(r.board_token.lower())
                existing_names.add(r.company_name.lower())
            all_results.extend(validated_direct)

        # Phase 3: Probe Curated Company Names
        if mode in ("all", "names-only") and unique_names:
            # Filter names that might have been validated in direct phase
            remaining_names = [n for n in unique_names if n.lower() not in existing_names]
            console.print(
                f"\n[bold]Phase 3: Probing {len(remaining_names)} Curated Tech Company Names across ATS APIs...[/bold]"
            )
            name_tasks = [
                probe_company_candidate(name, client, existing_names, semaphore)
                for name in remaining_names
            ]
            name_results = await asyncio.gather(*name_tasks)
            probed_hits = [r for r in name_results if r is not None]
            for r in probed_hits:
                console.print(
                    f"  [bold green][DISCOVERED][/bold green] {r.company_name} -> "
                    f"[cyan]{r.provider.value}[/cyan] ({r.board_token}) with "
                    f"[bold yellow]{r.active_postings}[/bold yellow] live jobs"
                )
            all_results.extend(probed_hits)

    # Final Deduplication against active registry
    canonical_names = {c.name.strip().lower() for c in COMPANIES}
    canonical_tokens = {(c.provider, c.board_token.strip().lower()) for c in COMPANIES}
    verified_results = deduplicate_results(all_results, canonical_names, canonical_tokens)

    # Display Results Table
    console.print("\n" + "=" * 60)
    console.print(
        f"[bold]HARVEST SUMMARY: [bold green]{len(verified_results)}[/bold green] "
        f"NEW VERIFIED LIVE COMPANIES FOUND[/bold]"
    )
    console.print("=" * 60)

    if verified_results:
        if HAVE_RICH:
            table = Table(title="Discovered & Verified ATS Job Boards", show_header=True)
            table.add_column("Company Name", style="bold white")
            table.add_column("ATS Provider", style="cyan")
            table.add_column("Board Token", style="yellow")
            table.add_column("Active Jobs", justify="right", style="green")
            for r in verified_results:
                table.add_row(r.company_name, r.provider.value, r.board_token, str(r.active_postings))
            console.print(table)
        else:
            for r in verified_results:
                console.print(
                    f"  * {r.company_name:25} | {r.provider.value:16} | "
                    f"{r.board_token:20} | {r.active_postings:4} jobs"
                )

        if append and not dry_run:
            appended_count = append_to_config(verified_results, cluster=cluster)
            console.print(
                f"\n[bold green][+][/bold green] Appended [bold]{appended_count}[/bold] "
                f"new companies to gcc_job_radar/config.py (cluster='{cluster}')"
            )
        elif dry_run:
            console.print(
                f"\n[bold yellow][*][/bold yellow] Dry-run mode: "
                f"{len(verified_results)} companies verified but config.py was NOT modified."
            )

    return verified_results


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser for tools/harvest_github.py."""
    parser = argparse.ArgumentParser(
        description="Harvest unmonitored ATS boards from curated GitHub repositories."
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=list(CURATED_SOURCES.keys()),
        default=None,
        help="Specific curated sources to harvest (default: all sources).",
    )
    parser.add_argument(
        "--mode",
        choices=["all", "direct-only", "names-only"],
        default="all",
        help="Harvesting mode: 'direct-only' (fast direct links), 'names-only' (probe names), or 'all' (default).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of candidates processed per phase (useful for quick testing).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Max concurrent async HTTP connections (default: {DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP request timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run discovery and display discovered companies table without modifying config.py.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        default=True,
        help="Automatically append verified companies to gcc_job_radar/config.py (default: True).",
    )
    parser.add_argument(
        "--no-append",
        action="store_false",
        dest="append",
        help="Do not modify gcc_job_radar/config.py after discovery.",
    )
    parser.add_argument(
        "--cluster",
        type=str,
        default="3",
        help="Cluster tag assigned to appended companies (default: '3').",
    )
    return parser


def main() -> None:
    """Main CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args()

    try:
        asyncio.run(
            harvest_curated_repos(
                sources=args.sources,
                mode=args.mode,
                limit=args.limit,
                concurrency=args.concurrency,
                timeout=args.timeout,
                append=args.append,
                dry_run=args.dry_run,
                cluster=args.cluster,
            )
        )
    except KeyboardInterrupt:
        console.print("\n[bold yellow][!] Harvesting interrupted by user.[/bold yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()

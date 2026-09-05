"""Automated Y Combinator company directory harvester for gcc-job-radar.

Fetches open-source YC company datasets, normalizes company names by stripping
legal suffixes and punctuation, filters out inactive/dead entries, deduplicates
against the active registry in gcc_job_radar/config.py, and optionally pipes
targets directly into tools/probe_ats.py.
"""

import argparse
import logging
from pathlib import Path
import re
import subprocess
import sys
from typing import Optional

import httpx

# Ensure project root is in sys.path when invoked directly
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from gcc_job_radar.config import COMPANIES

try:
    from rich.console import Console

    console = Console()
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

    class FallbackConsole:  # type: ignore[no-redef]
        def print(self, *args, **kwargs) -> None:
            clean_args = [
                re.sub(r"\[/?(?:bold|green|yellow|red|cyan|magenta)[^\]]*\]", "", str(a))
                for a in args
            ]
            print(*clean_args)

    console = FallbackConsole()

logger = logging.getLogger(__name__)

PRIMARY_SOURCE = "https://raw.githubusercontent.com/akshaybhalotia/yc_companies/main/data/yc_companies.json"
FALLBACK_SOURCE = "https://raw.githubusercontent.com/alecf/yc-companies/master/data/companies.json"
CANONICAL_LIVE_SOURCE = "https://yc-oss.github.io/api/companies/all.json"

DEFAULT_SOURCES = [
    PRIMARY_SOURCE,
    FALLBACK_SOURCE,
    CANONICAL_LIVE_SOURCE,
]

DEFAULT_TIMEOUT = 60.0
DEFAULT_OUTPUT = Path("targets_yc.txt")
PROBE_SCRIPT_PATH = Path(__file__).resolve().parent / "probe_ats.py"

LEGAL_SUFFIXES = [
    "inc",
    "inc.",
    "corporation",
    "corp",
    "corp.",
    "llc",
    "llc.",
    "ltd",
    "ltd.",
    "technologies",
    "technology",
    "solutions",
    "software",
    "systems",
    "group",
    "global",
    "holdings",
    "co",
    "co.",
    "company",
    "enterprises",
    "services",
    "labs",
]


def normalize_company_name(name: str) -> str:
    """Normalize and clean company name.

    Strips punctuation artifacts, legal and corporate suffixes (Inc., LLC, Corp,
    Ltd, Technologies, Software), and interior whitespace anomalies.
    """
    if not name or not isinstance(name, str):
        return ""

    # Strip surrounding quotes and whitespace
    cleaned = name.strip().strip("'\"`")

    # Tokenize words
    words = [w for w in cleaned.split() if w]
    if not words:
        return ""

    # Prune trailing corporate/legal suffixes
    while words:
        last_token = words[-1].lower().rstrip(".,")
        if last_token in LEGAL_SUFFIXES:
            words.pop()
            if words:
                # Strip trailing punctuation from new end token (e.g. "Stripe," -> "Stripe")
                words[-1] = words[-1].rstrip(".,;")
        else:
            break

    if not words:
        return ""

    result = " ".join(words).strip(" ,.-_/\\\"'()[]{}")

    # Reject if no alphanumeric characters or too short
    if len(result) < 2 or not any(c.isalnum() for c in result):
        return ""

    return result


def is_inactive_entry(company: dict) -> bool:
    """Check if a YC directory entry is marked dead, inactive, or closed."""
    status = str(company.get("status", "")).strip().lower()
    if status in {"inactive", "dead", "closed", "defunct"}:
        return True

    if company.get("dead") is True or company.get("is_dead") is True:
        return True

    if company.get("active") is False or company.get("is_active") is False:
        return True

    return False


def fetch_yc_data(
    client: Optional[httpx.Client] = None,
    sources: Optional[list[str]] = None,
) -> list[dict]:
    """Fetch raw company list from primary and fallback sources using httpx.Client."""
    source_list = sources or DEFAULT_SOURCES
    owns_client = False

    if client is None:
        client = httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        owns_client = True

    try:
        for url in source_list:
            console.print(f"[cyan][*] Fetching YC companies from: {url}...[/cyan]")
            try:
                response = client.get(url)
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except Exception as json_err:
                        console.print(f"[yellow][!] Failed to parse JSON from {url}: {json_err}[/yellow]")
                        continue

                    if isinstance(data, list):
                        console.print(
                            f"[bold green][+][/bold green] Successfully downloaded {len(data)} companies from {url}"
                        )
                        return data
                    elif isinstance(data, dict):
                        for key in ("companies", "data", "items"):
                            if key in data and isinstance(data[key], list):
                                console.print(
                                    f"[bold green][+][/bold green] Successfully downloaded {len(data[key])} "
                                    f"companies from {url} (key: '{key}')"
                                )
                                return data[key]
                else:
                    console.print(
                        f"[yellow][!] HTTP {response.status_code} from source: {url}. Trying fallback...[/yellow]"
                    )
            except httpx.RequestError as exc:
                console.print(f"[yellow][!] Network error querying {url}: {exc}. Trying fallback...[/yellow]")

        console.print("[bold red][X] All configured YC directory sources failed.[/bold red]")
        return []
    finally:
        if owns_client:
            client.close()


def harvest_companies(
    raw_data: list[dict],
    existing_names: Optional[set[str]] = None,
    limit: Optional[int] = None,
) -> list[str]:
    """Filter, normalize, and deduplicate company names against active registry.

    Args:
        raw_data: List of raw company dictionaries from YC sources.
        existing_names: Set of lowercased existing company names to skip.
        limit: Optional integer cap for number of harvested targets.

    Returns:
        List of cleaned, unique company names not currently in the active registry.
    """
    if existing_names is None:
        existing_names = {c.name.strip().lower() for c in COMPANIES}

    seen: set[str] = set()
    harvested: list[str] = []

    for entry in raw_data:
        if not isinstance(entry, dict):
            continue

        if is_inactive_entry(entry):
            continue

        raw_name = entry.get("name") or entry.get("company_name") or ""
        clean_name = normalize_company_name(str(raw_name))
        if not clean_name:
            continue

        clean_lower = clean_name.lower()
        if clean_lower in existing_names or clean_lower in seen:
            continue

        seen.add(clean_lower)
        harvested.append(clean_name)

        if limit is not None and len(harvested) >= limit:
            break

    return harvested


def save_targets(targets: list[str], output_path: Path) -> int:
    """Save target company names to text file, one per line."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(targets) + ("\n" if targets else "")
    output_path.write_text(content, encoding="utf-8")
    return len(targets)


def pipe_to_probe(target_file: Path, append: bool = True) -> int:
    """Invoke tools/probe_ats.py as a subprocess with the harvested target file."""
    if not target_file.exists():
        console.print(f"[bold red][X] Target file not found: {target_file}[/bold red]")
        return 1

    cmd = [
        sys.executable,
        str(PROBE_SCRIPT_PATH),
        "--file",
        str(target_file),
    ]
    if append:
        cmd.append("--append")

    console.print(f"[bold green][*] Piping {target_file} to probe_ats.py...[/bold green]")
    try:
        res = subprocess.run(cmd, check=False)
        return res.returncode
    except KeyboardInterrupt:
        console.print("\n[yellow][!] ATS probing interrupted by user.[/yellow]")
        return 130


def parse_args(args: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments for harvest_yc."""
    parser = argparse.ArgumentParser(
        description="Harvester for Y Combinator directory datasets to discover high-growth tech companies.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Target file destination path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Optional maximum number of candidate companies to harvest.",
    )
    parser.add_argument(
        "--pipe-to-probe",
        action="store_true",
        help="Seamlessly invoke tools/probe_ats.py with --file <output> --append after harvesting.",
    )
    return parser.parse_args(args)


def main() -> None:
    """Main CLI execution flow."""
    args = parse_args()

    console.print("[bold cyan]=== Y COMBINATOR DIRECTORY HARVESTER ===[/bold cyan]")
    raw_data = fetch_yc_data()
    if not raw_data:
        console.print("[bold red][X] Harvest aborted: unable to download dataset.[/bold red]")
        sys.exit(1)

    existing_names = {c.name.strip().lower() for c in COMPANIES}
    console.print(f"[cyan][*] Active registry contains {len(existing_names)} existing companies.[/cyan]")

    targets = harvest_companies(raw_data, existing_names=existing_names, limit=args.limit)
    console.print(f"[bold green][+] Harvested {len(targets)} new candidate company targets![/bold green]")

    saved_count = save_targets(targets, args.output)
    console.print(f"[bold green][+] Saved {saved_count} target(s) to: [cyan]{args.output}[/cyan][/bold green]")

    if args.pipe_to_probe:
        if not targets:
            console.print("[yellow][!] No new targets to probe. Skipping ATS probe step.[/yellow]")
            return

        exit_code = pipe_to_probe(args.output, append=True)
        if exit_code != 0:
            sys.exit(exit_code)


if __name__ == "__main__":
    main()

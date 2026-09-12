#!/usr/bin/env python3
"""Ingest and probe active hiring boards from tools/raw_dump.txt into gcc_job_radar/config.py.

Workflow:
1. Parse and sanitize tools/raw_dump.txt.
2. Filter out fee-based bootcamps, placement programs, and non-tech job titles.
3. Extract unique tech company entities and derive potential ATS tokens/slugs.
4. Concurrently probe Ashby, Greenhouse, Lever, and SmartRecruiters board endpoints via httpx.AsyncClient.
5. Append newly discovered active boards directly into COMPANIES inside gcc_job_radar/config.py.
"""

import asyncio
import logging
import os
from pathlib import Path
import re
import sys
from typing import NamedTuple, Optional
import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

# Ensure standard streams use utf-8 on Windows
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.models import ATSProvider, CompanyConfig

logger = logging.getLogger("ingest_manual_dump")

# Keywords that indicate commercial bootcamps, paid courses, or placement marketing
PROMO_KEYWORDS = [
    "placement program", "fast track", "scholarship", "₹", "rs.", "mentorship",
    "become job-ready", "pay after placement", "bootcamp", "training program",
    "skills gaps", "course fee", "registration fee", "pay after hire",
]

# Non-tech roles to filter out
NON_TECH_TITLES = [
    "operations", "founder’s office", "founders office", "telecaller", "recruiter",
    "talent acquisition", "sales", "marketing", "human resources", "hr intern",
    "customer support", "customer success", "customer experience", "content writer",
    "graphic design", "field executive", "legal", "finance intern", "account executive",
    "business development", "bdr", "sdr", "counselor",
]

# Date format at the start of raw dump lines
DATE_PAT = re.compile(
    r"^(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\s+202\d)\s*(Linkedin|General guide|Naukri|Indeed|Glassdoor|Internshala)?\s*(.*)$",
    re.IGNORECASE,
)

# Tech role regex for splitting company name from role title
TITLE_PATTERN = re.compile(
    r"(.*?)("
    r"Software\s+Development\s+Engineer(?:[-\s]?[1I])?|"
    r"Software\s+Engineer(?:[-\s]?[1I])?|"
    r"Software\s+Developer|"
    r"Full[\s\-]?Stack\s+(?:Software\s+)?(?:Engineer|Developer)(?:\s+Intern)?|"
    r"Associate\s+(?:Software\s+|SW\s+|Systems?\s+)?(?:Engineer|Analyst|Developer)|"
    r"Associate\s+Engineer|"
    r"Frontend\s+(?:Engineer|Developer)|"
    r"Jr\s+Frontend[_\s]Developer|"
    r"Backend\s+(?:Engineer|Developer)|"
    r"Java\s+Backend\s+Engineer|"
    r"Data\s+(?:Analyst|Analayst|Engineer|Operations\s+Analyst)|"
    r"Data\s+&\s+Analytics\s+Specialist|"
    r"Tech\s+Data\s+Analyst|"
    r"Search\s+&\s+Data\s+Engineering|"
    r"Business\s+Intelligence\s+Intern|"
    r"QA\s+(?:Test\s+)?Engineer(?:\s+Intern)?|"
    r"Apprentice\s+Tech|"
    r"Intern\s*-\s*(?:Software|Product\s+Analyst)|"
    r"AI\s+Developer|"
    r"Developer\s+Intern(?:ship)?|"
    r"Engineer(?:\s+Intern)?"
    r")(.*)",
    re.IGNORECASE,
)


class DiscoveredBoard(NamedTuple):
    company_name: str
    provider: str
    board_token: str
    active_jobs: int


def sanitize_raw_dump(dump_path: Path) -> dict[str, str]:
    """Parse raw dump text and extract valid tech company candidate entities."""
    if not dump_path.exists():
        logger.error("Raw dump file not found: %s", dump_path)
        return {}

    text = dump_path.read_text(encoding="utf-8")
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    candidates: dict[str, str] = {}

    for line in lines:
        lower = line.lower()
        if any(p in lower for p in PROMO_KEYWORDS):
            continue
        if any(nt in lower for nt in NON_TECH_TITLES):
            continue

        m = DATE_PAT.match(line)
        raw = m.group(3).strip() if m else line

        # Strip lead portal tags
        raw = re.sub(r"^(?:Angellist[- ]Wellfound|Linkedin|Naukri|Indeed|Glassdoor|Internshala)\s*", "", raw, flags=re.I)

        tm = TITLE_PATTERN.match(raw)
        if tm:
            company_raw = tm.group(1).strip()
        else:
            continue

        # Clean corporate suffixes
        clean_name = re.sub(
            r"(?i)\b(pvt\.?\s*ltd\.?|private\s+limited|inc\.?|llc|llp|technologies|solutions|group|systems?|india)\b",
            "",
            company_raw,
        ).strip(" ,.-/()")

        if 2 <= len(clean_name) <= 35 and not any(k in clean_name.lower() for k in ("linkedin", "naukri", "fast track")):
            candidates[clean_name] = company_raw

    return candidates


def generate_slugs(name: str) -> list[str]:
    """Generate ATS URL slugs for a company name."""
    base = re.sub(r"[^a-zA-Z0-9]+", "", name.lower())
    hyphen = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    slugs = [base]
    if hyphen and hyphen != base:
        slugs.append(hyphen)
    if len(base) <= 12:
        slugs.extend([f"{base}hq", f"{base}tech", f"{base}jobs"])
    return list(dict.fromkeys(s for s in slugs if len(s) >= 2))


async def probe_ats_endpoints(
    client: httpx.AsyncClient,
    company_name: str,
    slug: str,
    existing_keys: set[tuple[str, str]],
    semaphore: asyncio.Semaphore,
) -> Optional[DiscoveredBoard]:
    """Probe 4 major ATS endpoints for active job listings."""
    providers = [
        ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}"),
        ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"),
        ("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json"),
        ("smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"),
    ]

    async with semaphore:
        for provider, url in providers:
            if (provider, slug.lower()) in existing_keys:
                continue

            try:
                res = await client.get(url, timeout=3.5)
                if res.status_code == 200:
                    data = res.json()
                    jobs: list = []
                    if isinstance(data, list):
                        jobs = data
                    elif isinstance(data, dict):
                        jobs = data.get("jobs") or data.get("content") or []

                    if len(jobs) > 0:
                        return DiscoveredBoard(
                            company_name=company_name,
                            provider=provider,
                            board_token=slug,
                            active_jobs=len(jobs),
                        )
            except Exception:
                continue

    return None


async def discover_new_boards(
    candidates: dict[str, str],
    existing_keys: set[tuple[str, str]],
    existing_names: set[str],
) -> list[DiscoveredBoard]:
    """Concurrently probe all candidate companies across ATS providers."""
    semaphore = asyncio.Semaphore(35)
    discovered: list[DiscoveredBoard] = []
    seen_found: set[tuple[str, str]] = set()
    active_names = set(existing_names)

    # Silence verbose httpx request logs
    logging.getLogger("httpx").setLevel(logging.WARNING)

    async with httpx.AsyncClient() as client:
        tasks = []
        for name in sorted(candidates):
            if name.strip().lower() in active_names:
                continue
            for slug in generate_slugs(name):
                tasks.append(probe_ats_endpoints(client, name, slug, existing_keys, semaphore))

        print(f"Probing {len(tasks)} ATS endpoints across {len(candidates)} candidate companies...", flush=True)

        for future in asyncio.as_completed(tasks):
            result = await future
            if result is not None:
                key = (result.provider, result.board_token.lower())
                name_key = result.company_name.strip().lower()
                if key not in seen_found and key not in existing_keys and name_key not in active_names:
                    seen_found.add(key)
                    active_names.add(name_key)
                    discovered.append(result)
                    print(
                        f"  [+] Discovered: {result.company_name} ({result.provider.upper()}: {result.board_token}) "
                        f"-> {result.active_jobs} active postings",
                        flush=True,
                    )

    return discovered


def append_boards_to_config(new_boards: list[DiscoveredBoard], config_path: Path) -> int:
    """Safely append discovered boards to COMPANIES list in gcc_job_radar/config.py."""
    if not new_boards:
        print("No new active boards to append.")
        return 0

    content = config_path.read_text(encoding="utf-8")

    # Find the closing bracket of the COMPANIES list
    marker = "\n]\n\n# Strict entry-level tech title positive pattern"
    if marker not in content:
        pattern = re.compile(r"(\n\]\s*\n\s*#\s*Strict entry-level)", re.MULTILINE)
        m = pattern.search(content)
        if not m:
            logger.error("Could not find insertion marker in %s", config_path)
            return 0
        insert_pos = m.start()
    else:
        insert_pos = content.index(marker)

    lines_to_add: list[str] = []
    for b in new_boards:
        provider_enum = f"ATSProvider.{b.provider.upper()}"
        clean_company = b.company_name.replace('"', '\\"')
        lines_to_add.append(
            f'    CompanyConfig(name="{clean_company}", provider={provider_enum}, board_token="{b.board_token}"),'
        )

    block = "\n" + "\n".join(lines_to_add)
    updated_content = content[:insert_pos] + block + content[insert_pos:]
    config_path.write_text(updated_content, encoding="utf-8")

    print(f"Successfully appended {len(lines_to_add)} new boards into {config_path.name}.", flush=True)
    return len(lines_to_add)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    dump_file = PROJECT_ROOT / "tools" / "raw_dump.txt"
    config_file = PROJECT_ROOT / "gcc_job_radar" / "config.py"

    existing_names = {c.name.strip().lower() for c in COMPANIES}
    existing_keys = {
        (str(c.provider).lower().split(".")[-1], c.board_token.lower())
        for c in COMPANIES
    }

    print(f"Ingesting {dump_file.name} (baseline: {len(COMPANIES)} existing boards)...", flush=True)
    candidates = sanitize_raw_dump(dump_file)
    print(f"Extracted {len(candidates)} unique tech company candidates after filtering.", flush=True)

    new_boards = asyncio.run(discover_new_boards(candidates, existing_keys, existing_names))
    print(f"\nTotal newly discovered active ATS boards: {len(new_boards)}", flush=True)

    if new_boards:
        added_count = append_boards_to_config(new_boards, config_file)
        print(f"Registry updated: {len(COMPANIES) + added_count} total configured companies.", flush=True)


if __name__ == "__main__":
    main()
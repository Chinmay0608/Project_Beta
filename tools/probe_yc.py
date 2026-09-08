"""High-Performance Accelerated ATS Prober & Scaler for GCC Job Radar.

Features:
1. Multi-source Ingestion: Curated Enterprise Cohorts, 50k Tech Names, and YC Targets.
2. High-speed Queue Pipeline: 80 concurrent async workers pulling from an asyncio.Queue.
3. Dormant Company Protection: Never re-adds Backblaze or any dormant company.
4. Real Tech Filter: Validates active postings with is_tech_role().
5. Instant Real-time Config Append: Safely writes each verified company immediately to config.py.
6. Clean Exit: Automatically terminates once TARGET_TOTAL is achieved.
"""

import asyncio
from pathlib import Path
import re
import sys

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.dormant_companies import DORMANT_COMPANIES, is_dormant_company
from gcc_job_radar.filters import is_tech_role
from tools.harvest_mass_ats import ENTERPRISE_TECH_COHORTS, RESERVED_SLUGS

TARGET_TOTAL = 5005
CONFIG_PATH = PROJECT_ROOT / "gcc_job_radar" / "config.py"
CONCURRENCY = 80
TIMEOUT_SECONDS = 2.5

existing_keys = {
    (c.provider.value.lower() if hasattr(c.provider, "value") else str(c.provider).lower(), c.board_token.strip().lower())
    for c in COMPANIES
}
existing_names = {c.name.strip().lower() for c in COMPANIES}
dormant_slugs = {c.board_token.strip().lower() for c in DORMANT_COMPANIES}


def get_candidate_slugs(name: str) -> list[str]:
    base = re.sub(r"[^a-zA-Z0-9]+", "", name.lower())
    if not base or len(base) < 2 or base in RESERVED_SLUGS:
        return []

    slugs = [base]
    hyphen = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    if hyphen != base and len(hyphen) >= 2 and hyphen not in RESERVED_SLUGS:
        slugs.append(hyphen)

    if len(base) <= 12:
        for suffix in ("hq", "ai", "io", "tech"):
            cand = f"{base}{suffix}"
            if cand not in RESERVED_SLUGS:
                slugs.append(cand)

    return list(dict.fromkeys(slugs))


def has_tech_role(jobs: list) -> bool:
    """Ensure at least one job matches real tech / software / data / AI roles."""
    for j in jobs[:50]:
        t = ""
        d = ""
        if isinstance(j, dict):
            t = j.get("title") or j.get("name") or j.get("text") or ""
            d = j.get("department") or j.get("team") or ""
            if isinstance(d, dict):
                d = d.get("name") or d.get("label") or ""
            elif isinstance(d, list) and d:
                d = d[0].get("name", "") if isinstance(d[0], dict) else str(d[0])
        elif isinstance(j, str):
            t = j
        if t and is_tech_role(str(t), str(d)):
            return True
    return False


def append_single_to_config(name: str, provider: str, slug: str):
    """Safely append a single verified company entry to config.py."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    marker = "COMPANIES: list[CompanyConfig] = ["
    if marker not in content:
        return

    entry = f'\n    CompanyConfig(name="{name}", provider="{provider}", board_token="{slug}"),'
    new_content = content.replace(marker, marker + entry, 1)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)


async def main():
    current_count = len(COMPANIES)
    needed = max(0, TARGET_TOTAL - current_count)
    print(f"Current Registry: {current_count} | Needed to reach {TARGET_TOTAL}: {needed}", flush=True)

    if needed == 0:
        print(f"[+] Milestone of {TARGET_TOTAL} already accomplished!", flush=True)
        return

    candidate_names: list[str] = []
    seen_cand_names: set[str] = set()

    def add_candidate(cand: str):
        c_clean = cand.strip()
        c_low = c_clean.lower()
        if c_clean and c_low not in existing_names and c_low not in seen_cand_names and not is_dormant_company(c_clean):
            seen_cand_names.add(c_low)
            candidate_names.append(c_clean)

    # 1. Enterprise cohorts
    for n in ENTERPRISE_TECH_COHORTS:
        add_candidate(n)

    # 2. 50k Clean Tech Names (ordered starting from index 1670 onwards)
    names_file = PROJECT_ROOT / "tools" / "50k_clean_tech_names.txt"
    if names_file.exists():
        with open(names_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        ordered = lines[1670:] + lines[:1670]
        for n in ordered:
            add_candidate(n)

    # 3. Targets YC
    yc_file = PROJECT_ROOT / "targets_yc.txt"
    if yc_file.exists():
        with open(yc_file, "r", encoding="utf-8") as f:
            for line in f:
                add_candidate(line.strip())

    print(f"Total deduplicated candidate names queued: {len(candidate_names)}", flush=True)

    queue: asyncio.Queue = asyncio.Queue(maxsize=1500)
    added: list[tuple[str, str, str, int]] = []
    stop_event = asyncio.Event()
    append_lock = asyncio.Lock()

    limits = httpx.Limits(max_connections=CONCURRENCY * 2, max_keepalive_connections=CONCURRENCY)
    client = httpx.AsyncClient(limits=limits, timeout=TIMEOUT_SECONDS, follow_redirects=True)

    async def worker():
        while not stop_event.is_set():
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if stop_event.is_set():
                    break
                continue

            if item is None:
                queue.task_done()
                break

            n, prov, slug, url = item

            if (prov, slug) in existing_keys or slug in dormant_slugs or n.lower() in existing_names:
                queue.task_done()
                continue

            try:
                r = await client.get(url)
                if r.status_code == 200:
                    d = r.json()
                    jobs = d if isinstance(d, list) else d.get("jobs") or d.get("content") or []
                    if len(jobs) > 0 and has_tech_role(jobs):
                        async with append_lock:
                            if (prov, slug) not in existing_keys and n.lower() not in existing_names:
                                existing_keys.add((prov, slug))
                                existing_names.add(n.lower())
                                append_single_to_config(n, prov, slug)
                                added.append((n, prov, slug, len(jobs)))
                                print(
                                    f"  [+] Added ({len(added)}/{needed}): {n} ({prov}: {slug}) -> {len(jobs)} roles",
                                    flush=True,
                                )
                                if len(added) >= needed:
                                    stop_event.set()
            except Exception:
                pass
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(CONCURRENCY)]

    try:
        for n in candidate_names:
            if stop_event.is_set():
                break
            if n.lower() in existing_names:
                continue

            for s in get_candidate_slugs(n):
                if stop_event.is_set():
                    break
                if s in dormant_slugs:
                    continue

                endpoints = [
                    ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs"),
                    ("lever", f"https://api.lever.co/v0/postings/{s}?mode=json"),
                    ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{s}"),
                    ("smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{s}/postings"),
                ]
                for prov, url in endpoints:
                    if (prov, s) in existing_keys:
                        continue
                    await queue.put((n, prov, s, url))

        await queue.join()
    finally:
        stop_event.set()
        for _ in range(CONCURRENCY):
            try:
                await asyncio.wait_for(queue.put(None), timeout=0.2)
            except Exception:
                pass
        for w in workers:
            w.cancel()
        await client.aclose()

    print(f"\n[+] Scaler Complete! Added {len(added)} verified active tech companies directly to {CONFIG_PATH}!", flush=True)
    print(f"[+] Final Registry Count: {current_count + len(added)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
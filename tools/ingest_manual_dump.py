import asyncio
import re
import httpx
from gcc_job_radar.config import COMPANIES, CompanyConfig

TARGET_TOTAL = 5005
CONFIG_PATH = "gcc_job_radar/config.py"

existing_keys = {(c.provider.lower(), c.board_token.lower()) for c in COMPANIES}
existing_names = {c.name.lower() for c in COMPANIES}

# Block non-software engineering keywords and spam from the dump
EXCLUDE_WORDS = {
    "placement", "mentorship", "scholarship", "training", "course",
    "mis executive", "operations", "business analyst", "pricing",
    "customer support", "recruiter", "talent acquisition"
}

def extract_companies(raw_text: str) -> list[str]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    candidate_names = set()

    for line in lines:
        # Ignore dates, platforms, skills, and fees
        if re.search(r"\b(202\d|Linkedin|Naukri|Internshala|General guide|Indeed|Angellist|₹)\b", line, re.IGNORECASE):
            continue
        if any(w in line.lower() for w in EXCLUDE_WORDS):
            continue

        # Clean corporate suffixes
        name = re.sub(
            r"(?i)\b(pvt ltd|private limited|llp|inc|corporation|technologies|solutions|technologies pvt ltd)\b",
            "",
            line
        ).strip(" ,.-/")
        
        # Keep valid company tokens
        if name and 2 < len(name) < 40 and not any(c in name for c in ["\t", "  "]):
            if name.lower() not in existing_names:
                candidate_names.add(name)

    return sorted(candidate_names)

def clean_slugs(name: str) -> list[str]:
    base = re.sub(r"[^a-zA-Z0-9]+", "", name.lower())
    hyphen = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    slugs = [base]
    if hyphen != base:
        slugs.append(hyphen)
    if len(base) <= 12:
        slugs.extend([f"{base}hq", f"{base}tech", f"{base}ai"])
    return list(dict.fromkeys(slugs))

async def check_board(client: httpx.AsyncClient, name: str, slug: str, semaphore: asyncio.Semaphore):
    if not slug or len(slug) < 2:
        return None

    providers = [
        ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}"),
        ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"),
        ("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json"),
        ("smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"),
    ]

    async with semaphore:
        for provider, url in providers:
            if (provider, slug) in existing_keys or name.lower() in existing_names:
                continue
            try:
                res = await client.get(url, timeout=4.5)
                if res.status_code == 200:
                    data = res.json()
                    jobs = data if isinstance(data, list) else data.get("jobs") or data.get("content") or []
                    if len(jobs) > 0:
                        return (name, provider, slug, len(jobs))
            except Exception:
                continue
    return None

def append_to_config(entries):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    marker = "COMPANIES: list[CompanyConfig] = ["
    if marker not in content:
        print("[!] Could not find COMPANIES marker in config.py")
        return

    insert_block = "\n" + "\n".join(
        f'    CompanyConfig(name="{name}", provider="{provider}", board_token="{slug}"),'
        for name, provider, slug, _ in entries
    )

    new_content = content.replace(marker, marker + insert_block, 1)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)

async def main():
    import sys
    raw_path = sys.argv[1] if len(sys.argv) > 1 else "tools/raw_dump.txt"

    try:
        with open(raw_path, "r", encoding="utf-8") as f:
            raw_text = f.read()
    except FileNotFoundError:
        print(f"[!] Please save your raw text dump into {raw_path}")
        return

    candidates = extract_companies(raw_text)
    print(f"Extracted {len(candidates)} distinct candidate companies from dump.")
    
    semaphore = asyncio.Semaphore(50)
    added = []

    async with httpx.AsyncClient() as client:
        tasks = []
        for name in candidates:
            for slug in clean_slugs(name):
                tasks.append(check_board(client, name, slug, semaphore))

        results = await asyncio.gather(*tasks)

        for r in results:
            if r is not None:
                n, p, s, count = r
                if (p, s) not in existing_keys and n.lower() not in existing_names:
                    existing_keys.add((p, s))
                    existing_names.add(n.lower())
                    added.append(r)
                    print(f"  [+] Active Board Verified: {n} ({p}: {s}) -> {count} jobs")

    if added:
        append_to_config(added)
        print(f"\n[+] Successfully appended {len(added)} companies to {CONFIG_PATH}!")
        print(f"[+] Updated Registry Count: {len(COMPANIES) + len(added)}")
    else:
        print("[*] No new unverified boards found in this batch.")

if __name__ == "__main__":
    asyncio.run(main())
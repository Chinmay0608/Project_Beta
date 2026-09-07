import asyncio
import re
import httpx
from gcc_job_radar.config import COMPANIES, CompanyConfig

TARGET_TOTAL = 5005
CONFIG_PATH = "gcc_job_radar/config.py"

existing_keys = {(c.provider.lower(), c.board_token.lower()) for c in COMPANIES}
existing_names = {c.name.lower() for c in COMPANIES}

def clean_hyphen_slug(name: str) -> str:
    # Convert "Acryl Data" -> "acryl-data"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    return slug

async def check_board(client: httpx.AsyncClient, name: str, slug: str, semaphore: asyncio.Semaphore):
    if not slug or len(slug) < 2 or "-" not in slug:
        return None
        
    providers = [
        ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"),
        ("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json"),
        ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}"),
        ("smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"),
    ]
    
    async with semaphore:
        for provider, url in providers:
            if (provider, slug) in existing_keys or name.lower() in existing_names:
                continue
            try:
                res = await client.get(url, timeout=5.0)
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
        print("[!] Could not find COMPANIES list marker in config.py")
        return
        
    insert_block = "\n" + "\n".join(
        f'    CompanyConfig(name="{name}", provider="{provider}", board_token="{slug}"),'
        for name, provider, slug, _ in entries
    )
    
    new_content = content.replace(marker, marker + insert_block, 1)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)

async def main():
    current_count = len(COMPANIES)
    needed = max(0, TARGET_TOTAL - current_count)
    print(f"Current Registry: {current_count} | Needed to reach 5,000+: {needed}")
    
    if needed == 0:
        print("[+] 5,000 target reached.")
        return

    with open("tools/50k_clean_tech_names.txt", "r", encoding="utf-8") as f:
        names = [line.strip() for line in f if line.strip() and " " in line.strip()]

    semaphore = asyncio.Semaphore(60)
    batch_size = 2500
    added = []

    async with httpx.AsyncClient() as client:
        for i in range(0, len(names), batch_size):
            batch = names[i:i + batch_size]
            print(f"Scanning hyphenated names {i} to {i + len(batch)}...")
            
            tasks = [check_board(client, n, clean_hyphen_slug(n), semaphore) for n in batch]
            results = await asyncio.gather(*tasks)
            
            for r in results:
                if r is not None:
                    n, p, s, count = r
                    if (p, s) not in existing_keys and n.lower() not in existing_names:
                        existing_keys.add((p, s))
                        existing_names.add(n.lower())
                        added.append(r)
                        print(f"  [+] Added: {n} ({p}: {s}) -> {count} roles")
                        if len(added) >= needed:
                            break
            if len(added) >= needed:
                break

    if added:
        append_to_config(added)
        print(f"\n[+] Appended {len(added)} companies to {CONFIG_PATH}!")
        print(f"[+] Total Registry: {current_count + len(added)}")

if __name__ == "__main__":
    asyncio.run(main())
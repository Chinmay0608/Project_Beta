import asyncio
import re
import httpx
from gcc_job_radar.config import COMPANIES, CompanyConfig

TARGET_TOTAL = 5005
CONFIG_PATH = "gcc_job_radar/config.py"

existing_keys = {(c.provider.lower(), c.board_token.lower()) for c in COMPANIES}
existing_names = {c.name.lower() for c in COMPANIES}

def get_candidate_slugs(name: str) -> list[str]:
    base = re.sub(r"[^a-zA-Z0-9]+", "", name.lower())
    if not base or len(base) < 2:
        return []
    
    slugs = [base]
    # Common company slug variants used by tech boards
    if len(base) <= 12:
        slugs.extend([f"{base}hq", f"{base}ai", f"{base}io", f"{base}tech"])
    return slugs

async def check_board(client: httpx.AsyncClient, name: str, slug: str, semaphore: asyncio.Semaphore):
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
    print(f"Current Registry: {current_count} | Needed to cross 5,000+: {needed}")
    
    if needed == 0:
        print("[+] 5,000 milestone already accomplished!")
        return

    with open("tools/50k_clean_tech_names.txt", "r", encoding="utf-8") as f:
        names = [line.strip() for line in f if line.strip()]

    # Filter out companies already in registry
    candidates = [n for n in names if n.lower() not in existing_names]
    print(f"Local candidate names available: {len(candidates)}")

    semaphore = asyncio.Semaphore(75)
    added = []
    batch_size = 500

    async with httpx.AsyncClient() as client:
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            tasks = []
            for n in batch:
                for slug in get_candidate_slugs(n):
                    tasks.append(check_board(client, n, slug, semaphore))
            
            results = await asyncio.gather(*tasks)
            
            for r in results:
                if r is not None:
                    n, p, s, count = r
                    if (p, s) not in existing_keys and n.lower() not in existing_names:
                        existing_keys.add((p, s))
                        existing_names.add(n.lower())
                        added.append(r)
                        print(f"  [+] Added ({len(added)}/{needed}): {n} ({p}: {s}) -> {count} roles")
                        if len(added) >= needed:
                            break
            if len(added) >= needed:
                break

    if added:
        append_to_config(added)
        print(f"\n[+] Successfully appended {len(added)} verified active companies to {CONFIG_PATH}!")
        print(f"[+] Final Registry Count: {current_count + len(added)}")

if __name__ == "__main__":
    asyncio.run(main())
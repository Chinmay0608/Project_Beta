import csv
import re

TECH_KEYWORDS = {
    "software", "apps", "mobile", "web", "application platforms",
    "enterprise", "games", "security", "analytics", "saas",
    "cloud", "data", "information technology", "artificial intelligence",
    "machine learning", "developer", "fintech"
}

clean_names = set()

with open("tools/50k_companies.csv", mode="r", encoding="utf-8", errors="ignore") as f:
    reader = csv.DictReader(f)
    for row in reader:
        name = (row.get("name") or "").strip()
        category_list = (row.get("category_list") or "").lower()

        if any(kw in category_list for kw in TECH_KEYWORDS):
            cleaned = re.sub(
                r"(?i)\b(inc|incorporated|ltd|limited|llc|technologies|solutions|corp|corporation|private limited|pvt ltd|co)\b",
                "",
                name,
            ).strip(" ,.-")

            if cleaned and len(cleaned) > 2:
                clean_names.add(cleaned)

with open("tools/50k_clean_tech_names.txt", "w", encoding="utf-8") as f:
    for item in sorted(clean_names):
        f.write(f"{item}\n")

print(f"[+] Successfully extracted {len(clean_names)} pure tech company names into tools/50k_clean_tech_names.txt!")
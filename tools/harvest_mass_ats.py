"""Automated ATS Mass Harvester & Prober for GCC Job Radar.

Scales config.COMPANIES towards 5,000+ verified targets via:
1. URLScan.io search harvesting (Greenhouse, Lever, Ashby, SmartRecruiters) with HTTP 429 backoff.
2. Enterprise cohort streaming (Nifty 500 tech, S&P 1500 enterprise tech) with legal suffix normalization.
3. Candidate slug variation generation and deduplication against existing config.COMPANIES.
4. Concurrent verification (concurrency: 60) via direct ATS JSON endpoints requiring HTTP 200
   and non-empty job payloads.
5. Clean append formatting for gcc_job_radar/config.py with full dry-run support.
"""

import argparse
import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import sys
from typing import Any, Callable, Optional
import urllib.parse

import httpx

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.models import ATSProvider

try:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

    class FallbackConsole:  # type: ignore[no-redef]
        def print(self, *args: Any, **kwargs: Any) -> None:
            clean_args = [
                re.sub(r"\[/?(?:bold|green|yellow|red|cyan|magenta|dim)[^\]]*\]", "", str(a))
                for a in args
            ]
            print(*clean_args)

    console = FallbackConsole()  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "gcc_job_radar" / "config.py"
DEFAULT_CONCURRENCY = 60
DEFAULT_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) gcc-job-radar/mass-harvester/1.0",
    "Accept": "application/json, text/plain, */*",
}

URLSCAN_SEARCH_URL = "https://urlscan.io/api/v1/search/"

RESERVED_SLUGS: set[str] = {
    "embed",
    "search",
    "jobs",
    "careers",
    "openings",
    "api",
    "apply",
    "assets",
    "static",
    "css",
    "js",
    "v1",
    "v2",
    "v0",
    "privacy",
    "terms",
    "about",
    "login",
    "help",
    "support",
    "dashboard",
    "widget",
    "iframe",
    "webhook",
    "internal",
    "feed",
    "rss",
    "app",
    "boards",
    "posting",
    "postings",
    "company",
    "companies",
    "job-board",
    "index",
    "home",
    "admin",
    "auth",
}

LEGAL_SUFFIXES: list[str] = [
    "inc",
    "inc.",
    "incorporated",
    "corp",
    "corp.",
    "corporation",
    "llc",
    "llc.",
    "ltd",
    "ltd.",
    "limited",
    "technologies",
    "technology",
    "solutions",
    "software",
    "systems",
    "group",
    "global",
    "holdings",
    "international",
    "services",
    "enterprises",
    "labs",
    "co",
    "co.",
    "company",
    "pvt",
    "pvt ltd",
    "private limited",
    "gmbh",
    "ag",
    "sa",
    "bv",
    "plc",
]

KNOWN_ABBREVIATIONS: dict[str, list[str]] = {
    "palo alto networks": ["panw", "paloaltonetworks"],
    "crowdstrike": ["crwd", "crowdstrike"],
    "snowflake": ["snow", "snowflake"],
    "datadog": ["ddog", "datadog"],
    "servicenow": ["now", "servicenow"],
    "cloudflare": ["net", "cloudflare"],
    "arista networks": ["anet", "arista"],
    "pure storage": ["pstg", "purestorage"],
    "sentinelone": ["s", "sentinelone"],
    "digitalocean": ["docn", "digitalocean"],
    "fastly": ["fsly", "fastly"],
    "nutanix": ["ntnx", "nutanix"],
    "mongodb": ["mgo", "mongodb"],
    "couchbase": ["couchbase"],
    "confluent": ["cflt", "confluent"],
    "elastic": ["estc", "elastic"],
    "gitlab": ["gtlb", "gitlab"],
    "hashicorp": ["hcp", "hashicorp"],
    "dynatrace": ["dt", "dynatrace"],
    "new relic": ["newrelic"],
    "pagerduty": ["pd", "pagerduty"],
    "box": ["box"],
    "dropbox": ["dbx", "dropbox"],
    "smartsheet": ["smartsheet"],
    "asana": ["asana"],
    "monday.com": ["monday"],
    "toast": ["tst", "toast"],
    "marqeta": ["mq", "marqeta"],
    "affirm": ["afrm", "affirm"],
    "robinhood": ["hood", "robinhood"],
    "sofi": ["sofi"],
    "flexport": ["flexport"],
    "wayfair": ["w", "wayfair"],
    "shopify": ["shop", "shopify"],
    "synopsys": ["snps", "synopsys"],
    "cadence design systems": ["cdns", "cadence"],
    "marvell technology": ["mrvl", "marvell"],
    "lam research": ["lrcx", "lamresearch"],
    "kla": ["kla", "klac"],
    "arm": ["arm"],
    "asml": ["asml"],
    "qualcomm": ["qcom", "qualcomm"],
    "broadcom": ["avgo", "broadcom"],
    "amd": ["amd", "advanced-micro-devices"],
    "intel": ["intc", "intel"],
    "roblox": ["rblx", "roblox"],
    "unity technologies": ["u", "unity"],
    "epic games": ["epicgames"],
    "electronic arts": ["ea"],
    "take-two interactive": ["take-two", "ttwo"],
    "veeva systems": ["veeva"],
    "illumina": ["ilmn", "illumina"],
    "tempus ai": ["tempus"],
    "oscar health": ["oscar"],
    "thoughtworks": ["thoughtworks"],
    "epam systems": ["epam"],
    "endava": ["endava"],
    "globant": ["globant"],
    "nagarro": ["nagarro"],
    "infosys": ["infosys"],
    "wipro": ["wipro"],
    "tata consultancy services": ["tcs"],
    "hcltech": ["hcl", "hcltech"],
    "tech mahindra": ["techmahindra"],
    "l&t technology services": ["ltts"],
    "mphasis": ["mphasis"],
    "persistent systems": ["persistent"],
    "coforge": ["coforge"],
    "birlasoft": ["birlasoft"],
    "cyient": ["cyient"],
    "kpit technologies": ["kpit"],
    "tata elxsi": ["tataelxsi"],
    "zensar technologies": ["zensar"],
    "sonata software": ["sonata-software"],
}

# Curated enterprise tech cohorts across S&P 1500, Nifty 500, and global enterprise GCCs
ENTERPRISE_TECH_COHORTS: list[str] = [
    # Enterprise Cloud, Security, & Infrastructure
    "Palo Alto Networks",
    "CrowdStrike",
    "SentinelOne",
    "Zscaler",
    "Fortinet",
    "Okta",
    "CyberArk",
    "Rapid7",
    "Qualys",
    "Tenable",
    "Wiz",
    "Snyk",
    "Datadog",
    "Cloudflare",
    "Akamai",
    "DigitalOcean",
    "Fastly",
    "Nutanix",
    "Pure Storage",
    "Arista Networks",
    "GitLab",
    "Dynatrace",
    "New Relic",
    "PagerDuty",
    "Rubrik",
    "Cohesity",
    "F5",
    "Check Point Software",
    "Proofpoint",
    "Sophos",
    "Darktrace",
    "SailPoint",
    "OneTrust",
    "Varonis Systems",
    "Tanium",
    "Armis",
    "Lacework",
    "Aqua Security",
    "Axonius",
    "Exabeam",
    # Data Platforms, AI, & Analytics
    "Snowflake",
    "Databricks",
    "MongoDB",
    "Elastic",
    "Confluent",
    "Couchbase",
    "Teradata",
    "Alteryx",
    "Cloudera",
    "Neo4j",
    "SingleStore",
    "ClickHouse",
    "Pinecone",
    "Weaviate",
    "Qdrant",
    "Chroma",
    "LangChain",
    "Weights & Biases",
    "Scale AI",
    "Hugging Face",
    "Together AI",
    "Anyscale",
    "OctoAI",
    "Snorkel AI",
    "Labelbox",
    "Dataiku",
    "DataRobot",
    "Domino Data Lab",
    "ThoughtSpot",
    "Starburst Data",
    "Dremio",
    "Fivetran",
    "dbt Labs",
    "Monte Carlo",
    "Census",
    "Hightouch",
    "Airbyte",
    # Enterprise SaaS & Productivity
    "ServiceNow",
    "Workday",
    "HubSpot",
    "Zendesk",
    "Asana",
    "Monday.com",
    "Smartsheet",
    "Box",
    "Dropbox",
    "DocuSign",
    "Zoom Video Communications",
    "Twilio",
    "RingCentral",
    "Five9",
    "Freshworks",
    "Sprinklr",
    "Gainsight",
    "Braze",
    "Amplitude",
    "Mixpanel",
    "FullStory",
    "Pendo",
    "WalkMe",
    "UserTesting",
    "Qualtrics",
    "SurveyMonkey",
    "Lucid Software",
    "Miro",
    "Figma",
    "Canva",
    "InVision",
    "Airtable",
    "ClickUp",
    "Coda",
    "Notion",
    "Grammarly",
    "Loom",
    # FinTech, Payments, & Commerce
    "Stripe",
    "Block",
    "Adyen",
    "Toast",
    "Marqeta",
    "Affirm",
    "Robinhood",
    "SoFi Technologies",
    "Plaid",
    "Brex",
    "Ramp",
    "Klarna",
    "Chime",
    "Revolut",
    "Monzo Bank",
    "Wise",
    "Remitly",
    "Flywire",
    "Bill.com",
    "Coupa Software",
    "Carta",
    "Navan",
    "Deel",
    "Rippling",
    "Gusto",
    "Justworks",
    "Paychex",
    "Shopify",
    "BigCommerce",
    "Lightspeed Commerce",
    "CommerceHub",
    "Fabric",
    "Klaviyo",
    "Yotpo",
    "Attentive",
    "Bazaarvoice",
    # Semiconductor, EDA, & Systems
    "Broadcom",
    "Qualcomm",
    "Advanced Micro Devices",
    "Intel",
    "Synopsys",
    "Cadence Design Systems",
    "Marvell Technology",
    "Lam Research",
    "KLA Corporation",
    "Applied Materials",
    "ASML",
    "ARM Holdings",
    "Analog Devices",
    "Texas Instruments",
    "Microchip Technology",
    "NXP Semiconductors",
    "ON Semiconductor",
    "Skyworks Solutions",
    "Qorvo",
    "Silicon Labs",
    "Lattice Semiconductor",
    "Monolithic Power Systems",
    "Cirrus Logic",
    "Rambus",
    "Ambarella",
    # Healthcare Tech & Life Sciences
    "Veeva Systems",
    "Epic Systems",
    "Cerner",
    "Athenahealth",
    "Doximity",
    "GoodRx",
    "Teladoc Health",
    "Amwell",
    "Tempus AI",
    "Flatiron Health",
    "Komodo Health",
    "Verily Life Sciences",
    "Illumina",
    "10x Genomics",
    "Invitae",
    "Roivant Sciences",
    "Schrodinger",
    "Recursion Pharmaceuticals",
    "Exscientia",
    "BenevolentAI",
    # Gaming, Consumer Tech, & Digital Media
    "Roblox",
    "Unity Technologies",
    "Epic Games",
    "Electronic Arts",
    "Take-Two Interactive",
    "Spotify Technology",
    "Pinterest",
    "Snap",
    "Reddit",
    "Duolingo",
    "Match Group",
    "Bumble",
    "Zillow Group",
    "Redfin",
    "Compass",
    "Opendoor Technologies",
    "DoorDash",
    "Instacart",
    "Lyft",
    "Uber Technologies",
    "Airbnb",
    # Global IT Services & Tech Consultancies (Nifty / Global)
    "Tata Consultancy Services",
    "Infosys",
    "Wipro",
    "HCLTech",
    "Tech Mahindra",
    "L&T Technology Services",
    "Mphasis",
    "Persistent Systems",
    "Coforge",
    "Birlasoft",
    "Cyient",
    "KPIT Technologies",
    "Tata Elxsi",
    "Zensar Technologies",
    "Sonata Software",
    "Happiest Minds",
    "LTIMindtree",
    "Thoughtworks",
    "EPAM Systems",
    "Endava",
    "Globant",
    "Nagarro",
]


@dataclass
class VerifiedATSBoard:
    """Represents a verified, active enterprise ATS board."""

    company_name: str
    provider: ATSProvider
    board_token: str
    active_postings: int
    source: str = "urlscan"


def strip_legal_suffixes(name: str) -> str:
    """Strip corporate and legal suffixes (Inc, LLC, Corp, Ltd, Technologies, etc.)."""
    if not name or not isinstance(name, str):
        return ""

    cleaned = name.strip().strip("'\"`")
    words = [w for w in cleaned.split() if w]
    if not words:
        return ""

    while words:
        last = words[-1].lower().rstrip(".,;")
        if last in LEGAL_SUFFIXES:
            words.pop()
            if words:
                words[-1] = words[-1].rstrip(".,;")
        else:
            break

    if not words:
        return ""

    result = " ".join(words).strip(" ,.-_/\\\"'()[]{}")
    if len(result) < 2 or not any(c.isalnum() for c in result):
        return ""

    return result


def slug_to_company_name(slug: str) -> str:
    """Derive clean, title-cased company name from an ATS board token/slug."""
    if not slug:
        return ""
    clean = re.sub(r"[-_]+", " ", slug).strip()
    return " ".join(w.capitalize() for w in clean.split() if w)


def generate_slug_variations(name: str) -> list[str]:
    """Generate normalized slug variations for a company name.

    Returns up to 4 top probable variations:
    1. Lowercase alphanumeric
    2. Hyphenated lowercase
    3. Known abbreviation / acronym
    4. Underscore lowercase
    """
    cleaned = strip_legal_suffixes(name)
    if not cleaned:
        return []

    variations: list[str] = []

    # 1. Lowercase alphanumeric
    alpha = re.sub(r"[^a-zA-Z0-9]", "", cleaned).lower()
    if alpha and len(alpha) >= 2:
        variations.append(alpha)

    # 2. Hyphenated lowercase
    hyphen = re.sub(r"[^a-zA-Z0-9]+", "-", cleaned.strip()).strip("-").lower()
    if hyphen and hyphen != alpha and len(hyphen) >= 2:
        variations.append(hyphen)

    # 3. Known abbreviation / acronym
    cleaned_lower = cleaned.lower()
    orig_lower = name.strip().lower()
    abbr_list: list[str] = []
    for key, val in KNOWN_ABBREVIATIONS.items():
        if key in (cleaned_lower, orig_lower):
            abbr_list.extend(val)
            break

    for ab in abbr_list:
        ab_clean = ab.strip().lower()
        if ab_clean and ab_clean not in variations:
            variations.append(ab_clean)

    # Initials for multi-word names
    words = re.findall(r"[a-zA-Z0-9]+", cleaned)
    if len(words) >= 2:
        initials = "".join(w[0] for w in words).lower()
        if 2 <= len(initials) <= 5 and initials not in variations:
            variations.append(initials)

    # 4. Underscore lowercase
    underscore = re.sub(r"[^a-zA-Z0-9]+", "_", cleaned.strip()).strip("_").lower()
    if underscore and underscore not in variations and underscore != alpha:
        variations.append(underscore)

    # Discard any that collide with reserved slugs
    clean_vars = [v for v in variations if v not in RESERVED_SLUGS and len(v) >= 2]
    return list(dict.fromkeys(clean_vars))[:4]


def extract_slug_from_url(
    url: str,
    provider: Optional[ATSProvider] = None,
) -> Optional[tuple[ATSProvider, str]]:
    """Extract ATS provider and board slug from a URL.

    Filters out generic landing pages, static assets, and reserved paths.
    """
    if not url or not isinstance(url, str):
        return None

    clean_url = url.strip()
    try:
        parsed = urllib.parse.urlparse(clean_url)
    except Exception:
        return None

    netloc = parsed.netloc.lower()
    path = parsed.path.strip("/")
    segments = [s.strip() for s in path.split("/") if s.strip()]

    # Check query param for embed: ?for=slug
    if parsed.query:
        qp = urllib.parse.parse_qs(parsed.query)
        if "for" in qp and qp["for"]:
            candidate = qp["for"][0].strip().lower()
            if candidate and candidate not in RESERVED_SLUGS and re.match(r"^[a-zA-Z0-9_\-]+$", candidate):
                if "greenhouse.io" in netloc:
                    return ATSProvider.GREENHOUSE, candidate

    detected_provider: Optional[ATSProvider] = provider
    candidate_slug: Optional[str] = None

    if "greenhouse.io" in netloc:
        detected_provider = ATSProvider.GREENHOUSE
        if segments:
            # e.g. /stripe or /v1/boards/stripe/jobs or /embed/job_board?for=stripe
            if segments[0] in ("v1", "embed") and len(segments) > 2 and segments[1] == "boards":
                candidate_slug = segments[2]
            elif segments[0] not in RESERVED_SLUGS:
                candidate_slug = segments[0]

    elif "lever.co" in netloc:
        detected_provider = ATSProvider.LEVER
        if segments:
            # e.g. /atlassian or /v0/postings/atlassian
            if segments[0] in ("v0", "api") and len(segments) > 2 and segments[1] == "postings":
                candidate_slug = segments[2]
            elif segments[0] not in RESERVED_SLUGS:
                candidate_slug = segments[0]

    elif "ashbyhq.com" in netloc:
        detected_provider = ATSProvider.ASHBY
        if segments:
            # e.g. /linear or /posting-api/job-board/linear
            if segments[0] in ("posting-api", "api") and len(segments) > 2:
                candidate_slug = segments[2]
            elif segments[0] not in RESERVED_SLUGS:
                candidate_slug = segments[0]

    elif "smartrecruiters.com" in netloc:
        detected_provider = ATSProvider.SMARTRECRUITERS
        if segments:
            # e.g. /BoschGroup or /v1/companies/BoschGroup/postings
            if segments[0] in ("v1", "api") and len(segments) > 2 and segments[1] == "companies":
                candidate_slug = segments[2]
            elif segments[0] not in RESERVED_SLUGS:
                candidate_slug = segments[0]

    if detected_provider and candidate_slug:
        candidate_slug = candidate_slug.lower()
        if (
            candidate_slug not in RESERVED_SLUGS
            and len(candidate_slug) >= 2
            and re.match(r"^[a-zA-Z0-9_\-]+$", candidate_slug)
        ):
            return detected_provider, candidate_slug

    return None


async def verify_ats_board(
    provider: ATSProvider,
    slug: str,
    client: httpx.AsyncClient,
) -> Optional[int]:
    """Verify an ATS job board by issuing a direct REST API call.

    Requires HTTP 200 response and valid non-empty active postings.
    """
    if not slug or slug.lower() in RESERVED_SLUGS:
        return None

    clean_slug = slug.strip()

    try:
        url: str
        params: Optional[dict[str, Any]] = None

        if provider == ATSProvider.GREENHOUSE:
            url = f"https://boards-api.greenhouse.io/v1/boards/{clean_slug}/jobs"
        elif provider == ATSProvider.LEVER:
            url = f"https://api.lever.co/v0/postings/{clean_slug}?mode=json"
        elif provider == ATSProvider.ASHBY:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{clean_slug}"
        elif provider == ATSProvider.SMARTRECRUITERS:
            url = f"https://api.smartrecruiters.com/v1/companies/{clean_slug}/postings"
            params = {"limit": 5}
        else:
            return None

        # Issue GET request to validate payload
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return None

        data = resp.json()

        if provider in (ATSProvider.GREENHOUSE, ATSProvider.ASHBY):
            if isinstance(data, dict) and isinstance(data.get("jobs"), list):
                count = len(data["jobs"])
                return count if count > 0 else None

        elif provider == ATSProvider.LEVER:
            if isinstance(data, list):
                count = len(data)
                return count if count > 0 else None

        elif provider == ATSProvider.SMARTRECRUITERS:
            if isinstance(data, dict):
                total_found = data.get("totalFound", 0)
                content = data.get("content", [])
                if isinstance(total_found, int) and total_found > 0:
                    return total_found
                if isinstance(content, list) and len(content) > 0:
                    return len(content)

    except Exception as exc:
        logger.debug("Verification error for %s on %s: %s", clean_slug, provider, exc)

    return None


async def fetch_urlscan_candidates(
    client: httpx.AsyncClient,
    providers: Optional[list[ATSProvider]] = None,
    max_results_per_provider: int = 100,
    backoff_retries: int = 3,
) -> list[tuple[ATSProvider, str, str]]:
    """Harvest ATS candidate slugs from URLScan.io search API with HTTP 429 backoff handling.

    Returns list of (provider, slug, raw_title) tuples.
    """
    target_providers = providers or [
        ATSProvider.GREENHOUSE,
        ATSProvider.LEVER,
        ATSProvider.ASHBY,
        ATSProvider.SMARTRECRUITERS,
    ]

    provider_queries: dict[ATSProvider, str] = {
        ATSProvider.GREENHOUSE: "page.domain:boards.greenhouse.io OR page.domain:job-boards.greenhouse.io",
        ATSProvider.LEVER: "page.domain:jobs.lever.co",
        ATSProvider.ASHBY: "page.domain:jobs.ashbyhq.com",
        ATSProvider.SMARTRECRUITERS: "page.domain:jobs.smartrecruiters.com",
    }

    candidates: list[tuple[ATSProvider, str, str]] = []
    seen_tokens: set[tuple[ATSProvider, str]] = set()

    for prov in target_providers:
        query = provider_queries.get(prov)
        if not query:
            continue

        url = f"{URLSCAN_SEARCH_URL}?q={urllib.parse.quote(query)}&size={max_results_per_provider}"
        delay = 2.0

        for attempt in range(backoff_retries + 1):
            try:
                resp = await client.get(url)
                if resp.status_code == 429:
                    if attempt == backoff_retries:
                        logger.warning("URLScan 429 rate limit exceeded for %s; skipping query.", prov.value)
                        break
                    retry_after = resp.headers.get("Retry-After")
                    sleep_sec = float(retry_after) if retry_after and retry_after.isdigit() else delay
                    logger.warning(
                        "URLScan 429 Too Many Requests for %s. Backing off for %.1fs (attempt %d/%d)...",
                        prov.value,
                        sleep_sec,
                        attempt + 1,
                        backoff_retries,
                    )
                    await asyncio.sleep(sleep_sec)
                    delay *= 2.0
                    continue

                if resp.status_code != 200:
                    logger.warning("URLScan search returned HTTP %d for %s", resp.status_code, prov.value)
                    break

                data = resp.json()
                results = data.get("results", []) if isinstance(data, dict) else []

                for item in results:
                    if not isinstance(item, dict):
                        continue
                    page = item.get("page", {})
                    task = item.get("task", {})

                    page_url = page.get("url") or task.get("url") or ""
                    page_title = page.get("title") or ""

                    extracted = extract_slug_from_url(page_url, provider=prov)
                    if extracted:
                        ext_prov, ext_slug = extracted
                        token_key = (ext_prov, ext_slug)
                        if token_key not in seen_tokens:
                            seen_tokens.add(token_key)
                            candidates.append((ext_prov, ext_slug, page_title))

                break  # Successful response, proceed to next provider

            except Exception as exc:
                logger.warning("Error fetching URLScan candidates for %s: %s", prov.value, exc)
                break

    return candidates


def deduplicate_candidates(
    candidates: list[tuple[str, ATSProvider, str]],
    existing_names: set[str],
    existing_tokens: set[tuple[ATSProvider, str]],
) -> list[tuple[str, ATSProvider, str]]:
    """Deduplicate candidate entries against existing config.COMPANIES and within batch."""
    deduped: list[tuple[str, ATSProvider, str]] = []
    seen_tokens = set(existing_tokens)

    for name, provider, slug in candidates:
        name_clean = strip_legal_suffixes(name) or name.strip()
        name_key = name_clean.lower()
        token_key = (provider, slug.lower())

        if name_key in existing_names or token_key in seen_tokens:
            continue

        seen_tokens.add(token_key)
        deduped.append((name_clean, provider, slug))

    return deduped


def append_verified_boards_to_config(
    verified_boards: list[VerifiedATSBoard],
    config_path: Optional[Path] = None,
) -> int:
    """Cleanly append newly verified CompanyConfig entries to gcc_job_radar/config.py.

    Inserts before closing bracket of COMPANIES list, preserving formatting.
    """
    target_path = config_path or DEFAULT_CONFIG_PATH
    if not target_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {target_path}")

    content = target_path.read_text(encoding="utf-8")

    # Locate insertion point right before the closing bracket of COMPANIES list
    # Format: COMPANIES: list[CompanyConfig] = [\n ... \n]
    match = re.search(r"(COMPANIES:\s*list\[CompanyConfig\]\s*=\s*\[)(.*?)(\n\])", content, re.DOTALL)
    if not match:
        raise ValueError(f"Could not locate COMPANIES list in {target_path}")

    existing_tokens = set()
    for token_match in re.finditer(r'provider=ATSProvider\.(\w+),\s*board_token="([^"]+)"', match.group(2)):
        existing_tokens.add((token_match.group(1).upper(), token_match.group(2).lower()))

    from gcc_job_radar.dormant_companies import DORMANT_COMPANIES
    dormant_names = {c.name.lower().strip() for c in DORMANT_COMPANIES} | {
        c.board_token.lower().strip() for c in DORMANT_COMPANIES
    }

    new_entries: list[str] = []
    for b in verified_boards:
        if b.company_name.lower().strip() in dormant_names or b.board_token.lower().strip() in dormant_names:
            continue
        provider_name = b.provider.name
        token_key = (provider_name.upper(), b.board_token.lower())
        if token_key in existing_tokens:
            continue
        existing_tokens.add(token_key)
        safe_name = b.company_name.replace('"', '\\"')
        new_entries.append(
            f'    CompanyConfig(name="{safe_name}", provider=ATSProvider.{provider_name}, board_token="{b.board_token}"),\n'
        )

    if not new_entries:
        return 0

    insert_text = "".join(new_entries)
    # Insert before the closing ] at match.start(3)
    new_content = content[: match.start(3)] + "\n" + insert_text + content[match.start(3) :]
    target_path.write_text(new_content, encoding="utf-8")
    return len(new_entries)


async def run_harvest_mass_ats(
    source: str = "all",
    target_count: Optional[int] = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    dry_run: bool = False,
    config_path: Optional[Path] = None,
    client: Optional[httpx.AsyncClient] = None,
    progress_callback: Optional[Callable[[int, int, VerifiedATSBoard], None]] = None,
) -> list[VerifiedATSBoard]:
    """Orchestrate mass ATS harvesting, variation generation, and concurrent verification."""
    target_cfg = config_path or DEFAULT_CONFIG_PATH

    # Load existing registry keys for deduplication
    existing_names: set[str] = {strip_legal_suffixes(c.name).lower() for c in COMPANIES}
    existing_tokens: set[tuple[ATSProvider, str]] = {(c.provider, c.board_token.lower()) for c in COMPANIES}

    own_client = False
    if client is None:
        limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
        client = httpx.AsyncClient(limits=limits, timeout=DEFAULT_TIMEOUT, headers=DEFAULT_HEADERS, follow_redirects=True)
        own_client = True

    try:
        raw_candidates: list[tuple[str, ATSProvider, str]] = []

        # 1. Harvest from URLScan API if source is "all" or "urlscan"
        if source in ("all", "urlscan"):
            console.print("[bold cyan][*][/bold cyan] Harvesting candidate slugs from URLScan.io registries...")
            urlscan_hits = await fetch_urlscan_candidates(client=client)
            for prov, slug, title in urlscan_hits:
                c_name = strip_legal_suffixes(title) if title else slug_to_company_name(slug)
                raw_candidates.append((c_name, prov, slug))
            console.print(f"    [green]Found {len(urlscan_hits)} candidates from URLScan.[/green]")

        # 2. Harvest from Enterprise Cohorts if source is "all" or "cohorts"
        if source in ("all", "cohorts"):
            console.print("[bold cyan][*][/bold cyan] Generating candidate slug variants from Enterprise Tech cohorts...")
            cohort_count = 0
            for comp_name in ENTERPRISE_TECH_COHORTS:
                variants = generate_slug_variations(comp_name)
                for var in variants:
                    for prov in (
                        ATSProvider.GREENHOUSE,
                        ATSProvider.LEVER,
                        ATSProvider.ASHBY,
                        ATSProvider.SMARTRECRUITERS,
                    ):
                        raw_candidates.append((comp_name, prov, var))
                        cohort_count += 1
            console.print(f"    [green]Generated {cohort_count} prober variations from {len(ENTERPRISE_TECH_COHORTS)} cohort firms.[/green]")

        # 3. Harvest from custom file if source is a file path
        if source not in ("all", "urlscan", "cohorts"):
            src_path = Path(source)
            if src_path.exists():
                console.print(f"[bold cyan][*][/bold cyan] Reading candidates from custom source file: {src_path}...")
                for line in src_path.read_text(encoding="utf-8").splitlines():
                    clean_line = line.strip()
                    if not clean_line or clean_line.startswith("#"):
                        continue
                    # Check if URL
                    if clean_line.startswith(("http://", "https://")):
                        extracted = extract_slug_from_url(clean_line)
                        if extracted:
                            raw_candidates.append((slug_to_company_name(extracted[1]), extracted[0], extracted[1]))
                    else:
                        for var in generate_slug_variations(clean_line):
                            for prov in (
                                ATSProvider.GREENHOUSE,
                                ATSProvider.LEVER,
                                ATSProvider.ASHBY,
                                ATSProvider.SMARTRECRUITERS,
                            ):
                                raw_candidates.append((clean_line, prov, var))

        # Deduplicate candidates against existing COMPANIES and within batch
        candidates = deduplicate_candidates(raw_candidates, existing_names, existing_tokens)
        console.print(
            f"[bold cyan][*][/bold cyan] Total deduplicated probe targets: [bold white]{len(candidates)}[/bold white] "
            f"(Concurrency: {concurrency})..."
        )

        if not candidates:
            console.print("[yellow]No new candidate boards to verify.[/yellow]")
            return []

        semaphore = asyncio.Semaphore(concurrency)
        verified: list[VerifiedATSBoard] = []
        verified_tokens: set[tuple[ATSProvider, str]] = set(existing_tokens)
        stop_event = asyncio.Event()
        completed = 0
        total = len(candidates)

        async def _probe_task(name: str, prov: ATSProvider, slug: str) -> Optional[VerifiedATSBoard]:
            nonlocal completed
            if stop_event.is_set():
                return None

            async with semaphore:
                if stop_event.is_set():
                    return None

                active_count = await verify_ats_board(prov, slug, client)
                completed += 1

                if active_count is not None and active_count > 0:
                    token_key = (prov, slug.lower())
                    if token_key not in verified_tokens:
                        verified_tokens.add(token_key)
                        board = VerifiedATSBoard(
                            company_name=name,
                            provider=prov,
                            board_token=slug,
                            active_postings=active_count,
                        )
                        verified.append(board)
                        if progress_callback:
                            progress_callback(completed, total, board)

                        if target_count is not None and len(verified) >= target_count:
                            stop_event.set()

                        return board

                return None

        tasks = [_probe_task(n, p, s) for n, p, s in candidates]
        await asyncio.gather(*tasks)

        if target_count is not None and len(verified) > target_count:
            verified = verified[:target_count]

        # Output Summary
        from rich import box

        table = Table(
            title="Verified ATS Discovery Results",
            show_header=True,
            header_style="bold cyan",
            box=box.ASCII,
        )
        table.add_column("#", justify="right", width=5)
        table.add_column("Company", style="bold white", width=26)
        table.add_column("ATS Provider", style="magenta", width=18)
        table.add_column("Board Slug", style="cyan", width=22)
        table.add_column("Active Roles", justify="right", style="green", width=14)

        for i, b in enumerate(verified, start=1):
            clean_comp = re.sub(r"[^\x20-\x7E]+", "", b.company_name)[:24]
            clean_slug = re.sub(r"[^\x20-\x7E]+", "", b.board_token)[:20]
            table.add_row(str(i), clean_comp, b.provider.value, clean_slug, str(b.active_postings))

        console.print()
        console.print(table)
        console.print()

        if dry_run:
            console.print(
                f"[bold cyan][*][/bold cyan] Harvest Complete (DRY RUN): "
                f"[bold white]{len(verified)}[/bold white] verified boards found. "
                f"(0 added to {target_cfg.name})"
            )
        else:
            appended = append_verified_boards_to_config(verified, config_path=target_cfg)
            console.print(
                f"[bold green][+][/bold green] Harvest Complete: "
                f"[bold white]{len(verified)}[/bold white] verified boards found, "
                f"[bold green]{appended}[/bold green] successfully appended to [cyan]{target_cfg.name}[/cyan]!"
            )

        return verified

    finally:
        if own_client:
            await client.aclose()


def main() -> None:
    """CLI entrypoint for tools/harvest_mass_ats.py."""
    parser = argparse.ArgumentParser(
        description="Automated ATS Mass Harvester & Prober for scaling config.COMPANIES."
    )
    parser.add_argument(
        "--source",
        "-s",
        type=str,
        default="all",
        help="Source: 'all', 'urlscan', 'cohorts', or path to custom text/URL list (default: all).",
    )
    parser.add_argument(
        "--target-count",
        "-n",
        type=int,
        default=None,
        help="Maximum verified companies to discover before stopping.",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Concurrent HTTP probe requests (default: {DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Probe and display verified candidates without appending to config.py.",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=None,
        help="Custom path to gcc_job_radar/config.py file.",
    )

    args = parser.parse_args()

    asyncio.run(
        run_harvest_mass_ats(
            source=args.source,
            target_count=args.target_count,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
            config_path=args.config_path,
        )
    )


if __name__ == "__main__":
    main()

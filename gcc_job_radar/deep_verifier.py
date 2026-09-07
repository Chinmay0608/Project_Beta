"""Deep Job Description Crawler & Verifier for Class of 2027 Eligibility.

Navigates into job posting links, extracts full job descriptions via direct ATS APIs
or clean HTML scraping, and evaluates them against Class of 2027 candidate criteria:
1. Active job status (purges closed/expired postings).
2. Class of 2027 / pre-final year batch compatibility (purges roles restricted to past 2024/2025 passouts).
3. Experience threshold (purges roles requiring >= 2.5 or 3+ YOE).
4. Work authorization and location eligibility (purges US-only citizenship/clearance).
5. Tech relevance (purges non-tech roles).
"""

from dataclasses import asdict, dataclass
import html
import logging
import re
from typing import Any, Optional
import urllib.parse

import httpx
import orjson

from gcc_job_radar.filters import (
    IMMEDIATE_FULLTIME_JOIN_PATTERN,
    PAST_BATCH_EXCLUSION_PATTERN,
    STUDENT_2027_ELIGIBILITY_PATTERN,
    is_foreign_remote_location,
    requires_experienced_candidate,
)
from gcc_job_radar.models import ATSProvider

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 gcc-job-radar/deep-verifier"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_HTML_STRIP_REGEX = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", re.IGNORECASE)
_WHITESPACE_REGEX = re.compile(r"\s+")

# Stage 1: Closed / Expired Posting Signatures
CLOSED_JOB_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\bjob\s+(?:is\s+)?no\s+longer\s+available\b"),
    re.compile(r"(?i)\b(?:this\s+)?position\s+(?:has\s+been\s+|is\s+)?filled\b"),
    re.compile(r"(?i)\bapplications?\s+(?:are\s+)?closed\b"),
    re.compile(r"(?i)\bthis\s+posting\s+has\s+expired\b"),
    re.compile(r"(?i)\bthis\s+job\s+(?:posting\s+)?is\s+no\s+longer\s+(?:active|accepting\s+applications)\b"),
    re.compile(r"(?i)\bno\s+longer\s+accepting\s+applications\b"),
    re.compile(r"(?i)\bthe\s+job\s+you\s+are\s+looking\s+for\s+has\s+expired\b"),
    re.compile(r"(?i)\bthis\s+listing\s+has\s+been\s+removed\b"),
    re.compile(r"(?i)\bpage\s+not\s+found\b"),
]

# Stage 4: US / Foreign Citizenship & Security Clearance Barriers
US_CITIZENSHIP_SECURITY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\bu\.?s\.?\s+citizenship\s+(?:is\s+)?required\b"),
    re.compile(r"(?i)\b(?:must\s+be\s+a\s+)?u\.?s\.?\s+citizen\s+only\b"),
    re.compile(r"(?i)\bactive\s+(?:secret|top\s+secret|ts/sci|\w+\s+level)\s+security\s+clearance\b"),
    re.compile(r"(?i)\bsecurity\s+clearance\s+(?:is\s+)?required\b"),
    re.compile(r"(?i)\bmust\s+be\s+(?:legally\s+)?authorized\s+to\s+work\s+in\s+the\s+u\.?s\.?\s+without\s+sponsorship\b"),
    re.compile(r"(?i)\bno\s+visa\s+sponsorship\s+(?:available|provided)\s+for\s+this\s+role\b"),
    re.compile(r"(?i)\bapplicants\s+must\s+be\s+currently\s+authorized\s+to\s+work\s+in\s+the\s+united\s+states\b"),
    re.compile(r"(?i)\bwork\s+authorization\s+in\s+(?:the\s+)?(?:uk|united\s+kingdom|germany|canada)\s+required\b"),
]

# Stage 3: Explicit Seniority Title Exclusions
SENIORITY_TITLE_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(
        senior|sr\.?|lead|principal|staff|distinguished|fellow|
        director|architect|manager|head\s+of|tech\s+lead|executive|vp|vice\s+president|
        ii|iii|iv|v|vi|2|3|4|5|6
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Stage 5: Non-Tech / Excluded Profession Patterns
NON_TECH_TITLE_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(
        (?:tele)?sales|marketing|hr|recruiter|recruiting|talent|account\s+executive|
        customer\s+support|customer\s+success|customer\s+experience|support\s+specialist|
        operations|finance|legal|compliance|business\s+development|bdr|sdr|
        solutions?\s+engineer(?:ing)?|sales\s+engineer(?:ing)?|pre[- ]?sales|post[- ]?sales|
        excellence\s+center|center\s+of\s+excellence|se\s+excellence|support\s+engineer(?:ing)?|
        android|representative|cold\s+call(?:er|ing)?|telecaller|telecalling|
        content\s+writer|copywriter|graphic\s+designer
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)


@dataclass
class VerificationResult:
    """Outcome of deep job description verification."""

    is_eligible: bool
    reason: str
    confidence: str = "HIGH"  # HIGH, MEDIUM, LOW
    batch_fit: str = "2027_COMPATIBLE"  # 2027_STRONG, 2027_COMPATIBLE, PAST_BATCH_ONLY, IMMEDIATE_FULLTIME, EXPERIENCE_EXCLUDED, VISA_RESTRICTED, CLOSED, UNVERIFIED_CHALLENGE, NON_TECH

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def clean_html_to_text(html_content: str) -> str:
    """Convert raw HTML or formatted text into clean readable plain text."""
    if not html_content or not isinstance(html_content, str):
        return ""
    # Unescape HTML entities first
    unescaped = html.unescape(html_content)
    # Strip script, style, and HTML tags
    stripped = _HTML_STRIP_REGEX.sub(" ", unescaped)
    # Normalize whitespace
    return _WHITESPACE_REGEX.sub(" ", stripped).strip()


async def fetch_job_content(
    url: str,
    provider: Optional[ATSProvider | str] = None,
    board_token: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = 10.0,
) -> tuple[str, str]:
    """Retrieve full description text for a job link.

    Queries canonical ATS REST APIs directly where supported for unblocked, structured
    text, falling back to clean HTTP HTML text extraction.

    Returns:
        (content_text, status_code_or_marker)
        Markers: "OK", "UNVERIFIED_CHALLENGE", "NOT_FOUND", "ERROR"
    """
    if not url or not str(url).strip():
        return "", "NOT_FOUND"

    clean_url = str(url).strip()
    owns_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True)
        owns_client = True

    try:
        parsed = urllib.parse.urlparse(clean_url)
        netloc = parsed.netloc.lower()
        path = parsed.path.strip("/")
        segments = [s for s in path.split("/") if s]

        prov_str = str(provider).lower() if provider else ""

        # 1. Greenhouse direct REST API
        if "greenhouse.io" in netloc or prov_str == "greenhouse":
            # Extract board slug and job ID
            # Shapes: /v1/boards/{board}/jobs/{id} or /{board}/jobs/{id}
            b_slug = board_token
            j_id = None
            if "jobs" in segments:
                idx = segments.index("jobs")
                if idx > 0 and not b_slug:
                    b_slug = segments[idx - 1]
                if idx + 1 < len(segments):
                    j_id = segments[idx + 1]

            if b_slug and j_id:
                api_url = f"https://boards-api.greenhouse.io/v1/boards/{b_slug}/jobs/{j_id}"
                try:
                    resp = await client.get(api_url)
                    if resp.status_code == 200:
                        data = orjson.loads(resp.content)
                        raw_desc = data.get("content") or ""
                        return clean_html_to_text(raw_desc), "OK"
                    elif resp.status_code in (404, 410):
                        return "", "CLOSED_HTTP_404"
                except Exception as e:
                    logger.debug("Greenhouse API lookup failed for %s: %s", api_url, e)

        # 2. Lever direct REST API
        elif "lever.co" in netloc or prov_str == "lever":
            # Shapes: https://jobs.lever.co/{board}/{id}
            b_slug = board_token or (segments[0] if len(segments) >= 2 else None)
            j_id = segments[1] if len(segments) >= 2 else None
            if b_slug and j_id:
                api_url = f"https://api.lever.co/v0/postings/{b_slug}/{j_id}?mode=json"
                try:
                    resp = await client.get(api_url)
                    if resp.status_code == 200:
                        data = orjson.loads(resp.content)
                        desc_parts = [data.get("descriptionPlain") or data.get("description") or ""]
                        for lst in data.get("lists") or []:
                            if isinstance(lst, dict):
                                desc_parts.append(lst.get("text", ""))
                                desc_parts.append(lst.get("content", ""))
                        full_desc = " ".join(desc_parts)
                        return clean_html_to_text(full_desc), "OK"
                    elif resp.status_code in (404, 410):
                        return "", "CLOSED_HTTP_404"
                except Exception as e:
                    logger.debug("Lever API lookup failed for %s: %s", api_url, e)

        # 3. SmartRecruiters direct REST API
        elif "smartrecruiters.com" in netloc or prov_str == "smartrecruiters":
            # Shapes: https://jobs.smartrecruiters.com/{company}/{id}
            b_slug = board_token or (segments[0] if len(segments) >= 2 else None)
            j_id = segments[1] if len(segments) >= 2 else None
            if b_slug and j_id:
                api_url = f"https://api.smartrecruiters.com/v1/companies/{b_slug}/postings/{j_id}"
                try:
                    resp = await client.get(api_url)
                    if resp.status_code == 200:
                        data = orjson.loads(resp.content)
                        job_ad = data.get("jobAd") or {}
                        sections = job_ad.get("sections") or {}
                        desc_parts = []
                        for sec in sections.values():
                            if isinstance(sec, dict):
                                desc_parts.append(sec.get("text", ""))
                        full_desc = " ".join(desc_parts)
                        return clean_html_to_text(full_desc), "OK"
                    elif resp.status_code in (404, 410):
                        return "", "CLOSED_HTTP_404"
                except Exception as e:
                    logger.debug("SmartRecruiters API lookup failed for %s: %s", api_url, e)

        # 4. Standard Web HTML Crawl (Generic URLs, Ashby, Workday, Career Portals)
        try:
            page_resp = await client.get(clean_url)
            if page_resp.status_code in (403, 429):
                # Cloudflare Bot Challenge / Anti-Scraping Protection
                body_sample = page_resp.text[:600].lower()
                if "cloudflare" in body_sample or "challenge" in body_sample or "turnstile" in body_sample:
                    return "", "UNVERIFIED_CHALLENGE"
                return "", "UNVERIFIED_CHALLENGE"

            if page_resp.status_code in (404, 410):
                return "", "CLOSED_HTTP_404"

            if page_resp.status_code == 200:
                raw_text = clean_html_to_text(page_resp.text)
                return raw_text, "OK"

            return "", f"HTTP_{page_resp.status_code}"

        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as conn_err:
            logger.debug("Connection error crawling %s: %s", clean_url, conn_err)
            return "", "CONNECTION_TIMEOUT"

    except Exception as exc:
        logger.debug("Unexpected error fetching job content for %s: %s", clean_url, exc)
        return "", "ERROR"
    finally:
        if owns_client:
            await client.aclose()


def evaluate_for_2027_candidate(
    title: str,
    location: str,
    description: str,
    fetch_status: str = "OK",
) -> VerificationResult:
    """Evaluate job posting requirements specifically for a Class of 2027 undergraduate candidate.

    Multi-stage verification pipeline:
    - Stage 1: Closed / Expired posting detection.
    - Stage 2: Batch constraints (allows 2027/internships, rejects 2024/2025 past batches or degree-in-hand fulltime).
    - Stage 3: Experience level (demands <= 2 YOE, rejects >= 2.5 or 3+ YOE).
    - Stage 4: Location & Work Authorization (requires India/remote, rejects US-only/security clearance).
    - Stage 5: Engineering/Tech domain check (rejects pure sales/operations/non-tech).
    """
    clean_title = (title or "").strip()
    clean_loc = (location or "").strip()
    clean_desc = (description or "").strip()

    # Special handling: Cloudflare / Bot challenge protection
    if fetch_status == "UNVERIFIED_CHALLENGE":
        return VerificationResult(
            is_eligible=True,
            reason="Protected by Cloudflare/anti-bot challenge (kept as unverified)",
            confidence="LOW",
            batch_fit="UNVERIFIED_CHALLENGE",
        )

    # HTTP 404 / 410 explicitly denotes dead/expired postings
    if fetch_status in ("CLOSED_HTTP_404", "HTTP_410"):
        return VerificationResult(
            is_eligible=False,
            reason="Job posting link is dead/expired (HTTP 404/410)",
            confidence="HIGH",
            batch_fit="CLOSED",
        )

    # Stage 1: Closed / Expired Posting Text Inspection
    if clean_desc:
        for p in CLOSED_JOB_PATTERNS:
            if p.search(clean_desc):
                return VerificationResult(
                    is_eligible=False,
                    reason="Job is no longer accepting applications (posting closed/expired)",
                    confidence="HIGH",
                    batch_fit="CLOSED",
                )

    # Stage 2: Batch Fit & Student Availability (Class of 2027)
    is_internship = bool(
        re.search(
            r"(?i)\b(?:intern|internship|co[- ]?op|apprentice|summer\s+intern|graduate\s+intern|student\s+trainee)\b",
            clean_title,
        )
    )

    if clean_desc:
        # Check for explicit past batch locks (e.g. "2024 batch only", "2025 batch only", "must have graduated in 2025")
        if PAST_BATCH_EXCLUSION_PATTERN.search(clean_desc):
            return VerificationResult(
                is_eligible=False,
                reason="Restricted to past graduation batches (e.g. 2024/2025 passouts only)",
                confidence="HIGH",
                batch_fit="PAST_BATCH_ONLY",
            )

        # For non-internship full-time roles, check if immediate graduation/degree certificate is required
        if not is_internship and IMMEDIATE_FULLTIME_JOIN_PATTERN.search(clean_desc):
            return VerificationResult(
                is_eligible=False,
                reason="Requires immediate full-time joining or degree certificate in hand",
                confidence="HIGH",
                batch_fit="IMMEDIATE_FULLTIME",
            )

    # Stage 3: Experience Level & Seniority Exclusions
    sanitized_title = re.sub(r"(?i)\bmember\s+of\s+technical\s+staff\b", "mts_role", clean_title)
    if clean_title and SENIORITY_TITLE_PATTERN.search(sanitized_title):
        return VerificationResult(
            is_eligible=False,
            reason="Seniority designation in title (Senior, Lead, Staff, Manager)",
            confidence="HIGH",
            batch_fit="EXPERIENCE_EXCLUDED",
        )

    if clean_desc and requires_experienced_candidate(clean_desc):
        return VerificationResult(
            is_eligible=False,
            reason="Demands experienced candidate (>= 2.5 or 3+ years of experience)",
            confidence="HIGH",
            batch_fit="EXPERIENCE_EXCLUDED",
        )

    # Stage 4: Work Authorization & Location Eligibility
    if is_foreign_remote_location(clean_loc):
        return VerificationResult(
            is_eligible=False,
            reason="Remote location restricted to foreign region (US/Europe/EMEA only)",
            confidence="HIGH",
            batch_fit="FOREIGN_LOCATION",
        )

    if clean_desc:
        for p in US_CITIZENSHIP_SECURITY_PATTERNS:
            if p.search(clean_desc):
                # Ensure it doesn't also explicitly state India is eligible
                if not re.search(r"(?i)\b(?:india|bengaluru|bangalore|hyderabad|pune|gurgaon|noida)\b", clean_desc):
                    return VerificationResult(
                        is_eligible=False,
                        reason="Requires US citizenship, security clearance, or US work authorization",
                        confidence="HIGH",
                        batch_fit="VISA_RESTRICTED",
                    )

    # Stage 5: Tech & Engineering Relevance
    if clean_title and NON_TECH_TITLE_PATTERN.search(clean_title):
        return VerificationResult(
            is_eligible=False,
            reason="Non-tech profession or excluded category (Sales, Marketing, HR, Operations)",
            confidence="HIGH",
            batch_fit="NON_TECH",
        )

    # Determine batch_fit tag
    batch_fit = "2027_COMPATIBLE"
    if clean_desc and STUDENT_2027_ELIGIBILITY_PATTERN.search(clean_desc):
        batch_fit = "2027_STRONG"
    elif is_internship:
        batch_fit = "INTERNSHIP_2027_FIT"

    return VerificationResult(
        is_eligible=True,
        reason="Verified active and eligible for Class of 2027 / student candidate",
        confidence="HIGH",
        batch_fit=batch_fit,
    )

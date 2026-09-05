import html
import re
from gcc_job_radar.config import (
    EXCLUDE_TITLE_PATTERN,
    INCLUDE_TITLE_PATTERN,
    LOCATION_PATTERN,
)

# Pattern to mask 'member of technical staff' before checking exclusions so 'staff' is not falsely triggered
_MTS_MASK_PATTERN = re.compile(r"(?i)\bmember\s+of\s+technical\s+staff\b")

# Disqualify roles requiring 3+ or more years of experience or experienced mid-level ranges (e.g. 2-4+ yrs)
EXPERIENCE_DISQUALIFY_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(?:
        2\s*(?:-|–|—|to)\s*(?:[4-9]|\d{2,})\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+(?:relevant|hands[- ]on|work|professional|industry|technical|software|engineering|coding|\w+)){0,3}\s+experience |
        (?:[3-9]|\d{2,})\+?\s*(?:-\s*\d+\s*)?(?:years?|yrs?)(?:\s+of)?(?:\s+(?:relevant|hands[- ]on|work|professional|industry|technical|software|engineering|coding|\w+)){0,3}\s+experience |
        (?:minimum|at\s+least)\s+(?:of\s+)?(?:[3-9]|\d{2,})\s*(?:years?|yrs?)(?:\s+of)?(?:\s+\w+){0,3}\s+experience |
        experience\s*:\s*(?:[3-9]|\d{2,})\+?\s*(?:years?|yrs?) |
        (?:[3-9]|\d{2,})\s*(?:to|-|–|—)\s*\d+\s*(?:years?|yrs?)(?:\s+of)?(?:\s+\w+){0,3}\s+experience
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

_ENTRY_PREFIX_PATTERN = re.compile(r"(?i)[0-2]\s*(?:-|to)\s*$")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def requires_experienced_candidate(content: str) -> bool:
    """Check if job description demands experienced candidates (>= 3 years experience).

    Returns True if description indicates candidate must have >= 3 years experience.
    Genuine entry-level/fresher roles (0-1 yrs, 0-2 yrs, 1-3 yrs, degrees) return False.
    """
    if not content or not content.strip():
        return False
    unescaped = html.unescape(content)
    clean_text = _HTML_TAG_PATTERN.sub(" ", unescaped)

    for m in EXPERIENCE_DISQUALIFY_PATTERN.finditer(clean_text):
        start = m.start()
        prefix = clean_text[max(0, start - 10):start]
        # Ignore if part of an entry-level range like 0-3 years or 1-3 years
        if _ENTRY_PREFIX_PATTERN.search(prefix):
            continue
        return True
    return False


def matches_target_title(title: str) -> bool:
    """Check if job title is strictly an entry-level tech position.

    Exclusion rules take strict precedence over inclusion rules.
    """
    if not title or not title.strip():
        return False

    clean_title = title.strip()

    # Mask "Member of Technical Staff" temporarily so "Staff" exclusion rule doesn't falsely flag MTS 1
    sanitized_for_exclusion = _MTS_MASK_PATTERN.sub("mts_role", clean_title)

    # If any exclusion keyword/numeral matches, immediately disqualify
    if EXCLUDE_TITLE_PATTERN.search(sanitized_for_exclusion):
        return False

    # Check if positive entry-level pattern matches
    return bool(INCLUDE_TITLE_PATTERN.search(clean_title))


INDIA_LOCATION_KEYWORDS: tuple[str, ...] = (
    "india",
    "bangalore",
    "bengaluru",
    "hyderabad",
    "pune",
    "noida",
    "gurgaon",
    "gurugram",
    "delhi",
    "ncr",
    "mumbai",
    "chennai",
    "secunderabad",
    "madras",
    "thane",
)

REMOTE_KEYWORDS: tuple[str, ...] = (
    "remote",
    "anywhere in india",
    "distributed",
    "work from home",
    "remote - india",
    "india - remote",
    "apac - remote",
    "remote, india",
    "wfh",
    "virtual",
    "telecommute",
)

FOREIGN_REMOTE_EXCLUSIONS: tuple[str, ...] = (
    "us remote",
    "remote - us",
    "remote (us)",
    "remote, us",
    "remote - usa",
    "remote - north america",
    "emea remote",
    "remote - emea",
    "remote - europe",
    "uk remote",
    "remote - uk",
    "canada remote",
    "remote - canada",
    "germany remote",
    "australia remote",
    "latam remote",
    "remote - latam",
)

# Positive regex check for fresher and 0-2 years of experience indicators
FRESHER_EXPERIENCE_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(
        (?:0\s*(?:-|–|—|to)\s*[1-2]|1\s*(?:-|–|—|to)\s*2|\b0\b|\b1\b|\b2\b)\s*(?:years?|yrs?)(?:\s+of)?(?:\s+\w+){0,3}\s+experience |
        (?:0\s*(?:-|–|—|to)\s*1|0\s*(?:-|–|—|to)\s*2|1\s*(?:-|–|—|to)\s*2)\s*(?:years?|yrs?) |
        freshers?\s+(?:are\s+)?(?:eligible|welcome) |
        (?:2024|2025|2026|2027)\s+batch |
        (?:class|graduating|pass[- ]?out)\s+of\s+(?:2024|2025|2026|2027) |
        (?:0\s*years?|no(?:\s+prior)?)\s+experience\s+(?:required|needed) |
        (?:0\s*yoe|1\s*yoe|2\s*yoe|0-1\s*yoe|0-2\s*yoe|1-2\s*yoe)
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)


def is_foreign_remote_location(location: str) -> bool:
    """Check if location explicitly restricts remote work to a non-Indian region."""
    if not location or not location.strip():
        return False
    loc_lower = location.lower()
    # If explicitly mentioning India or Indian hubs, it is not an exclusively foreign remote role
    if "india" in loc_lower or any(
        city in loc_lower
        for city in (
            "bengaluru",
            "bangalore",
            "hyderabad",
            "pune",
            "delhi",
            "gurgaon",
            "gurugram",
            "noida",
            "mumbai",
            "chennai",
            "secunderabad",
        )
    ):
        return False
    return any(ex in loc_lower for ex in FOREIGN_REMOTE_EXCLUSIONS)


def is_remote_location(location: str) -> bool:
    """Check if location string denotes a remote work arrangement valid for India."""
    if not location or not location.strip():
        return False
    loc_lower = location.lower()
    if is_foreign_remote_location(loc_lower):
        return False
    return any(kw in loc_lower for kw in REMOTE_KEYWORDS)


def is_remote_opening(job: object) -> bool:
    """Determine if a job opening is a remote role eligible for candidates in India.

    Inspects job.location and raw payload metadata (e.g. is_remote, workplace_type).
    """
    if job is None:
        return False

    # Check is_remote attribute if set directly
    if getattr(job, "is_remote", False):
        loc = getattr(job, "location", "")
        if loc and is_foreign_remote_location(str(loc)):
            return False
        return True

    # Extract location string from object, dict, or string
    loc = getattr(job, "location", None)
    if loc is None and isinstance(job, dict):
        loc = job.get("location")
        if isinstance(loc, dict):
            loc = loc.get("name") or loc.get("text") or ""
    if loc is None and isinstance(job, str):
        loc = job

    if loc and isinstance(loc, str):
        if is_remote_location(loc):
            return True

    # Check payload metadata (workplace_type, workplaceType, etc.)
    metadata = getattr(job, "extra", None) or (job if isinstance(job, dict) else {})
    if isinstance(metadata, dict):
        workplace_type = str(
            metadata.get("workplace_type")
            or metadata.get("workplaceType")
            or metadata.get("telecommute")
            or ""
        ).lower()
        if workplace_type in {"remote", "virtual", "telecommute", "distributed"}:
            if loc and is_foreign_remote_location(str(loc)):
                return False
            return True

    return False


def is_potential_india_location(location: str) -> bool:
    """Fast short-circuit check: discard non-India locations via string check before regex evaluation."""
    if not location or not location.strip():
        return False
    loc_lower = location.lower()

    # Reject foreign-restricted remote locations (e.g. US Remote, Remote - EMEA)
    if is_foreign_remote_location(loc_lower):
        return False

    # Match Indian tech cities or India
    if any(kw in loc_lower for kw in INDIA_LOCATION_KEYWORDS):
        return True

    # Regional or global remote roles (e.g. APAC - Remote, Global Remote, Anywhere in India)
    if any(rk in loc_lower for rk in ("remote", "distributed", "anywhere", "wfh")):
        if any(reg in loc_lower for reg in ("apac", "asia", "global", "worldwide")):
            return True

    return False


def matches_india_location(location: str) -> bool:
    """Check if location string matches target Indian tech hubs or India remote."""
    if not is_potential_india_location(location):
        return False

    clean_location = location.strip()
    if LOCATION_PATTERN.search(clean_location):
        return True

    # Allow regional remote locations paired with global/APAC eligibility
    loc_lower = clean_location.lower()
    if any(rk in loc_lower for rk in ("remote", "distributed", "anywhere", "wfh")):
        if any(reg in loc_lower for reg in ("apac", "asia", "global", "worldwide")):
            return True

    return False


def is_entry_level(job_or_title: object, content: str = "") -> bool:
    """Check if a job role targets freshers / entry-level / 0-2 YOE candidates.

    High-recall matching for terms like 'Associate Software Engineer', 'Graduate
    Engineer Trainee', 'GET', 'SDE 1', 'SDE-1', 'Software Engineer 1', 'MTS 1',
    'Junior Software Engineer', 'Analyst', and 0-2 years of experience requirements.
    """
    if isinstance(job_or_title, str):
        title = job_or_title
    elif hasattr(job_or_title, "title") and not callable(getattr(job_or_title, "title")):
        title = str(getattr(job_or_title, "title", ""))
        if not content:
            content = getattr(job_or_title, "content", "") or getattr(job_or_title, "description", "")
    else:
        title = str(job_or_title)

    if not title or not title.strip():
        return False

    clean_title = title.strip()
    sanitized = _MTS_MASK_PATTERN.sub("mts_role", clean_title)

    # If title contains explicit senior/staff exclusions, reject immediately
    if EXCLUDE_TITLE_PATTERN.search(sanitized):
        return False

    title_matches = matches_target_title(clean_title)

    # High-recall matching for additional entry-level / fresher / analyst title terms
    if not title_matches:
        if re.search(
            r"(?i)\b(?:analyst(?:\s*[-–—]?\s*(?:1|i)\b)?|associate\s+(?:software\s+)?engineer|junior\s+(?:software\s+)?(?:engineer|developer)|graduate\s+engineer\s+trainee|\bget\b|sde\s*[-–—]?\s*1|software\s+engineer\s+1|mts\s*[-–—]?\s*1)\b",
            clean_title,
        ):
            title_matches = True

    if not title_matches:
        # If title is generic (e.g. "Software Engineer"), check if content explicitly specifies 0-2 YOE / freshers eligible
        if content and FRESHER_EXPERIENCE_PATTERN.search(content) and not requires_experienced_candidate(content):
            return True
        return False

    # If title matches, verify content doesn't require experienced candidate (3+ years)
    if content and content.strip():
        if requires_experienced_candidate(content):
            return False

    return True


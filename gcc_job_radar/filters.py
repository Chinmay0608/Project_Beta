import html
import re
from gcc_job_radar.config import (
    EXCLUDE_TITLE_PATTERN,
    INCLUDE_TITLE_PATTERN,
    LOCATION_PATTERN,
)

# Pattern to mask 'member of technical staff' before checking exclusions so 'staff' is not falsely triggered
_MTS_MASK_PATTERN = re.compile(r"(?i)\bmember\s+of\s+technical\s+staff\b")

# Disqualify roles requiring 3+ or more years of experience (freshers / entry-level strictly want 0-2 yrs)
EXPERIENCE_DISQUALIFY_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(?:
        (?:[3-9]|\d{2,})\+?\s*(?:-\s*\d+\s*)?(?:years?|yrs?)(?:\s+of)?(?:\s+(?:relevant|hands[- ]on|work|professional|industry|technical|software|engineering|coding|\w+)){0,3}\s+experience |
        (?:minimum|at\s+least)\s+(?:of\s+)?(?:[3-9]|\d{2,})\s*(?:years?|yrs?)(?:\s+of)?(?:\s+\w+){0,3}\s+experience |
        experience\s*:\s*(?:[3-9]|\d{2,})\+?\s*(?:years?|yrs?) |
        (?:[3-9]|\d{2,})\s*(?:to|-)\s*\d+\s*(?:years?|yrs?)(?:\s+of)?(?:\s+\w+){0,3}\s+experience
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


def matches_india_location(location: str) -> bool:
    """Check if location string matches target Indian tech hubs or India remote."""
    if not location or not location.strip():
        return False

    clean_location = location.strip()
    return bool(LOCATION_PATTERN.search(clean_location))

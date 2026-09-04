"""Title and location filtering logic for entry-level tech roles in India."""

import re
from gcc_job_radar.config import (
    EXCLUDE_TITLE_PATTERN,
    INCLUDE_TITLE_PATTERN,
    LOCATION_PATTERN,
)

# Pattern to mask 'member of technical staff' before checking exclusions so 'staff' is not falsely triggered
_MTS_MASK_PATTERN = re.compile(r"(?i)\bmember\s+of\s+technical\s+staff\b")


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

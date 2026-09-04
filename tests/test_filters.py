"""Unit tests for title and location filtering logic."""

import pytest
from gcc_job_radar.filters import matches_india_location, matches_target_title


@pytest.mark.parametrize(
    "title",
    [
        "SDE 1",
        "SDE-1",
        "SDE I",
        "Software Engineer 1",
        "Software Engineer I",
        "Software Development Engineer I",
        "Software Developer 1",
        "Associate Software Engineer",
        "Associate QA Engineer",
        "Associate Systems Engineer",
        "Junior Software Engineer",
        "Junior Developer",
        "Junior Engineer",
        "Graduate Software Engineer",
        "Graduate Technical Intern",
        "Graduate Software Developer",
        "Fresher",
        "Entry Level Software Engineer",
        "Entry-Level Software Developer",
        "MTS 1",
        "MTS-1",
        "MTS I",
        "Member of Technical Staff 1",
        "Software Intern",
        "Engineering Intern",
        "Tech Intern",
        "SWE Intern",
        "Data Intern",
    ],
)
def test_matches_target_title_positive(title: str) -> None:
    """Verify that legitimate entry-level tech roles pass the title filter."""
    assert matches_target_title(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "Senior Software Engineer",
        "Sr. Software Engineer",
        "Sr Software Engineer",
        "Staff Software Engineer",
        "Staff Engineer",
        "Principal Software Engineer",
        "Lead Software Engineer",
        "Lead Developer",
        "Tech Lead",
        "Engineering Manager",
        "Software Engineering Manager",
        "Director of Engineering",
        "Software Architect",
        "Chief Architect",
        "VP of Engineering",
        "Head of Engineering",
        "Software Engineer 2",
        "Software Engineer II",
        "Software Engineer III",
        "Software Engineer IV",
        "Software Engineer 3",
        "Software Engineer 4",
        "SDE 2",
        "SDE II",
        "SDE 3",
        "SDE III",
        "Member of Technical Staff 2",
        "Member of Technical Staff II",
        "Senior Member of Technical Staff",
        "Technical Recruiter",
        "Recruiting Coordinator",
        "Talent Acquisition Specialist",
        "HR Operations Associate",
        "Sales Associate",
        "Account Executive",
        "Customer Support Associate",
        "Customer Success Specialist",
        "Associate System Engineer, SE Excellence Center - ANZ",
        "Solutions Engineer",
        "Associate Solutions Engineer",
        "Pre-Sales Engineer",
        "Sales Engineer",
        "Technical Support Engineer",
        "UI Engineer",  # Not explicitly entry level; verifies 'UI' does not falsely match roman 'ii'
        "Frontend Engineer",
        "Backend Developer",
        "",
        "   ",
    ],
)
def test_matches_target_title_negative(title: str) -> None:
    """Verify that senior, staff, lead, numeral II+, pre-sales, and non-tech titles are disqualified."""
    assert matches_target_title(title) is False


@pytest.mark.parametrize(
    "content,expected",
    [
        ("4+ years of experience (technology industry preferred)", True),
        ("3+ years of experience in distributed systems", True),
        ("5+ years of software engineering experience", True),
        ("3-5 years of hands-on experience", True),
        ("minimum 4 years of experience", True),
        ("at least 3 years of experience", True),
        ("experience: 5+ yrs", True),
        ("1-3 years of hands-on software engineering experience", False),
        ("0-2 years of experience", False),
        ("0-1 years of experience", False),
        ("1+ years of experience", False),
        ("Freshers and 2024/2025 graduates welcome", False),
        ("BS in Computer Science or equivalent practical experience", False),
        ("<p>• 4+ years of experience (technology industry preferred)</p>", True),
        ("&lt;li&gt;Minimum 3+ years of experience&lt;/li&gt;", True),
        ("", False),
        ("   ", False),
    ],
)
def test_requires_experienced_candidate(content: str, expected: bool) -> None:
    """Verify that roles requiring >= 3 years experience are disqualified while freshers pass."""
    from gcc_job_radar.filters import requires_experienced_candidate
    assert requires_experienced_candidate(content) is expected


@pytest.mark.parametrize(
    "location",
    [
        "Bengaluru, Karnataka, India",
        "Bengaluru",
        "Bangalore",
        "Bangalore, India",
        "Hyderabad, Telangana",
        "Hyderabad",
        "Secunderabad",
        "Pune, Maharashtra",
        "Pune",
        "Gurgaon, Haryana",
        "Gurugram",
        "Noida, Uttar Pradesh",
        "Noida",
        "Delhi NCR",
        "New Delhi, India",
        "Mumbai, Maharashtra",
        "Navi Mumbai",
        "Chennai, Tamil Nadu",
        "Chennai",
        "Remote - India",
        "India (Remote)",
        "Bengaluru / Hybrid",
        "Remote, India",
    ],
)
def test_matches_india_location_positive(location: str) -> None:
    """Verify that Indian tech hubs and India Remote locations match."""
    assert matches_india_location(location) is True


@pytest.mark.parametrize(
    "location",
    [
        "San Francisco, CA",
        "San Jose, California",
        "New York, NY",
        "Austin, TX",
        "Seattle, WA",
        "London, United Kingdom",
        "Dublin, Ireland",
        "Amsterdam, Netherlands",
        "Berlin, Germany",
        "Singapore",
        "Tokyo, Japan",
        "Sydney, Australia",
        "Toronto, ON, Canada",
        "Remote - US",
        "Remote - North America",
        "Remote - EMEA",
        "",
        "   ",
    ],
)
def test_matches_india_location_negative(location: str) -> None:
    """Verify that non-Indian locations are rejected."""
    assert matches_india_location(location) is False

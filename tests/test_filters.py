"""Unit tests for title and location filtering logic."""

from typing import Optional

import pytest
from gcc_job_radar.filters import (
    is_potential_india_location,
    is_tech_role,
    is_valid_get_role,
    matches_india_location,
    matches_target_title,
)


@pytest.mark.parametrize(
    "title",
    [
        "SDE 1",
        "SDE-1",
        "SDE I",
        "SDE-I",
        "Software Engineer 1",
        "Software Engineer I",
        "Software Development Engineer I",
        "Software Development Engineer - I",
        "Software Developer 1",
        "Engineer 1",
        "Engineer I",
        "Associate Software Engineer",
        "Associate QA Engineer",
        "Associate Systems Engineer",
        "Associate Backend Engineer",
        "Associate Frontend Engineer",
        "Associate Full Stack Engineer",
        "Associate Cloud Engineer",
        "Associate Data Engineer",
        "Associate Platform Engineer",
        "Associate SDET",
        "Associate Developer",
        "Associate Programmer",
        "Junior Software Engineer",
        "Junior Developer",
        "Junior Engineer",
        "Jr. Software Engineer",
        "Graduate Engineer Trainee",
        "GET",
        "GET - Software",
        "Software Engineer Trainee",
        "Engineering Trainee",
        "Graduate Software Engineer",
        "Graduate Technical Intern",
        "Graduate Software Developer",
        "Fresher",
        "Entry Level Software Engineer",
        "Entry-Level Software Developer",
        "Technology Analyst",
        "Software Engineering Analyst",
        "Graduate Analyst",
        "New Grad Software Engineer",
        "New Grad Software Engineer (2026)",
        "University Graduate Software Engineer",
        "Campus Hire Developer",
        "Early Career Software Engineer",
        "MTS 1",
        "MTS-1",
        "MTS I",
        "MTS-I",
        "Member of Technical Staff 1",
        "Member of Technical Staff - I",
        "Software Intern",
        "Engineering Intern",
        "Tech Intern",
        "SWE Intern",
        "Data Intern",
        "Software Apprentice",
        "Engineering Apprentice",
        "Co-op Engineer",
    ],
)
def test_matches_target_title_positive(title: str) -> None:
    """Verify that legitimate entry-level tech roles pass the title filter."""
    assert matches_target_title(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "Senior Software Engineer",
        "Senior Software Engineer I",
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
        "SE Excellence Center",
        "Solutions Engineer",
        "Solutions Engineer I",
        "Associate Solutions Engineer",
        "Pre-Sales Engineer",
        "Sales Engineer",
        "Technical Support Engineer",
        "UI Engineer",  # Not explicitly entry level; verifies 'UI' does not falsely match roman 'ii'
        "Frontend Engineer",
        "Backend Developer",
        "Android Developer",
        "Android Developer Intern",
        "Android Engineer I",
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
        ("2 - 4 years of relevant experience", True),
        ("2-4 years of experience", True),
        ("2 to 5 years of software engineering experience", True),
        ("2 - 5 years of hands-on experience", True),
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


def test_is_potential_india_location_fast_short_circuit() -> None:
    """Verify fast string short-circuit checks discard foreign locations without regex."""
    assert is_potential_india_location("Bengaluru, India") is True
    assert is_potential_india_location("Hyderabad, Telangana") is True
    assert is_potential_india_location("Pune") is True
    assert is_potential_india_location("India (Remote)") is True

    assert is_potential_india_location("San Francisco, CA") is False
    assert is_potential_india_location("London, UK") is False
    assert is_potential_india_location("Sydney, Australia") is False
    assert is_potential_india_location("") is False


# ==============================================================================
# Fractional / range experience rejection
# ==============================================================================

@pytest.mark.parametrize(
    "content,expected",
    [
        # Fractional range: min ≥ 2.5 → reject
        ("Experience: 2.6 – 5 Years", True),
        ("Experience: 2.5 - 4 years", True),
        ("Experience: 3.0 to 6 years", True),
        # Fractional range: min < 2.5 → accept
        ("Experience: 0 – 2 Years", False),
        ("Experience: 1 - 2 years", False),
        ("0 to 2 years of experience", False),
        # N+ pattern: N ≥ 2.5 → reject
        ("2.5+ years experience", True),
        ("3+ years of experience", True),
        # N+ pattern: N < 2.5 → accept
        ("1+ years of experience", False),
        ("2 years experience", False),
        # Minimum pattern: min ≥ 2.5 → reject
        ("minimum 3 years of experience", True),
        ("min 2.5 years", True),
        # Minimum pattern: min < 2.5 → accept
        ("minimum 1 year of experience", False),
        # No experience context
        ("Freshers eligible", False),
        ("Entry-level role, 0–1 year preferred", False),
    ],
)
def test_requires_experienced_candidate_fractional(content: str, expected: bool) -> None:
    """Verify fractional and range experience patterns are correctly detected."""
    from gcc_job_radar.filters import requires_experienced_candidate
    assert requires_experienced_candidate(content) is expected


@pytest.mark.parametrize(
    "title,snippet,expected",
    [
        # Generic title + snippet rejecting: should be disqualified
        ("Full Stack Developer", "Experience: 2.6 – 5 Years | Bengaluru", False),
        ("Software Engineer", "3+ years of experience required", False),
        # Generic title + snippet accepting: should qualify
        ("Full Stack Developer", "0 – 2 Years | Freshers welcome", True),
        ("Software Engineer", "Freshers are eligible | 2025 batch welcome", True),
        # Title-only card (no snippet): falls back to title heuristics
        ("Software Engineer I", None, True),
        ("Senior Software Engineer", None, False),
        # Entry-level title overrides snippet ambiguity
        ("Software Engineer Trainee", "Experience: 2 - 3 years", True),  # title wins as entry
    ],
)
def test_is_entry_level_with_snippet(title: str, snippet: Optional[str], expected: bool) -> None:
    """Verify is_entry_level correctly uses the snippet content fallback for generic titles."""
    from gcc_job_radar.filters import is_entry_level
    assert is_entry_level(title, content=snippet) is expected


# ==============================================================================
# is_tech_role – board-level gate for "Real Roles Only"
# ==============================================================================


@pytest.mark.parametrize(
    "title,department",
    [
        ("Associate Software Engineer", ""),
        ("Software Engineer Trainee", ""),
        ("SDE 1", "Engineering"),
        ("SWE Intern", ""),
        ("Backend Developer", ""),
        ("Frontend Engineer", ""),
        ("Full Stack Developer", ""),
        ("Data Engineer", ""),
        ("Data Scientist", ""),
        ("Data Analyst", "Data Science"),
        ("ML Engineer", ""),
        ("Machine Learning Engineer", ""),
        ("AI Engineer", ""),
        ("Cloud Engineer", ""),
        ("DevOps Engineer", ""),
        ("Site Reliability Engineer", ""),
        ("Platform Engineer", ""),
        ("SRE Intern", ""),
        ("SDET", "Quality Engineering"),
        ("Test Automation Engineer", ""),
        ("QA Engineer", ""),
        ("Cybersecurity Analyst", "Security Engineering"),
        ("Security Engineer", ""),
        ("Firmware Engineer", ""),
        ("Embedded Software Engineer", ""),
        ("Infrastructure Engineer", ""),
    ],
)
def test_is_tech_role_positive(title: str, department: str) -> None:
    """Verify that software/data/AI/ML/cloud/DevOps roles are accepted."""
    assert is_tech_role(title, department) is True


@pytest.mark.parametrize(
    "title,department",
    [
        ("Mechanical Engineer Trainee", ""),
        ("Graduate Engineer Trainee – BU Quay and Horizontal", "Mechanical Engineering"),
        ("Civil Engineer", ""),
        ("Structural Engineer", ""),
        ("Electrical Engineer", ""),
        ("HVAC Engineer", ""),
        ("AutoCAD Designer", ""),
        ("Piping Designer", ""),
        ("Instrumentation Engineer", ""),
        ("Teamcenter Admin", ""),
        ("Telecaller", ""),
        ("Nursing Assistant", ""),
        ("BDR Representative", ""),
        ("SDR Associate", ""),
        ("Field Engineer", ""),
        ("Sales Engineer", ""),
        ("", ""),
        ("   ", ""),
    ],
)
def test_is_tech_role_negative(title: str, department: str) -> None:
    """Verify that non-software engineering disciplines are rejected."""
    assert is_tech_role(title, department) is False


def test_is_tech_role_with_department_override() -> None:
    """Department context can tip ambiguous titles toward or away from tech."""
    # Generic 'Engineer' title passes if department is clearly IT
    assert is_tech_role("Engineer", "Software Engineering") is True
    # Mechanical dept must be rejected even if 'engineer' word present
    assert is_tech_role("Engineer", "Mechanical Engineering") is False


# ==============================================================================
# Indian Tech Trainee / GET Filter Hardening Tests
# ==============================================================================


@pytest.mark.parametrize(
    "title,description,expected",
    [
        # Tech GET roles accepted
        ("GET - Software", "", True),
        ("Graduate Engineer Trainee (IT)", "", True),
        ("Graduate Engineer Trainee - Cloud & DevOps", "", True),
        ("GET - Data Engineering", "", True),
        ("Software Engineer Trainee", "", True),
        ("Engineering Trainee", "Python, React, and SQL development", True),
        ("Graduate Engineer Trainee", "Java backend development and microservices", True),
        # Non-tech GET roles rejected
        ("Graduate Engineer Trainee - Mechanical", "", False),
        ("GET - Civil Engineering", "", False),
        ("Graduate Engineer Trainee – BU Quay and Horizontal & Yard", "", False),
        ("Graduate Engineer Trainee (Electrical)", "", False),
        ("GET - Piping & Instrumentation", "", False),
        ("Engineering Trainee - AutoCAD", "", False),
        ("Graduate Engineer Trainee", "HVAC maintenance and plant operations", False),
        ("GET", "Mechanical workshop and machinery maintenance", False),
    ],
)
def test_is_valid_get_role(title: str, description: str, expected: bool) -> None:
    """Verify GET roles are strictly accepted for tech and rejected for non-software disciplines."""
    assert is_valid_get_role(title, description) is expected


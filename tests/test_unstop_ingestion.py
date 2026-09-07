"""Unit tests for Unstop early-career tech ingestion tool."""

from pathlib import Path
import httpx
import pytest

from gcc_job_radar.db import get_stats, init_db
from gcc_job_radar.models import ATSProvider
from tools.ingest_unstop import (
    build_job_posting_from_unstop,
    fetch_unstop_page,
    is_qualified_unstop_item,
    run_unstop_ingestion,
)


def test_is_qualified_unstop_item_positive() -> None:
    """Verify genuine software engineering / internship roles pass qualification."""
    valid_internship = {
        "id": 1001,
        "title": "Software Engineering Intern (Summer 2026/2027)",
        "status": "OPEN",
        "regn_open": 1,
        "type": "internship",
        "locations": ["Bengaluru, Karnataka, India"],
        "organisation": {"name": "Tech Corp"},
        "workfunction": "IT & Software Development",
        "tags": ["Python", "React", "Class of 2027"],
        "details": "Looking for freshers and pre-final year B.Tech students (2026/2027 batch). 0 years experience.",
    }
    is_qual, reason = is_qualified_unstop_item(valid_internship)
    assert is_qual is True
    assert reason == "Qualified"


def test_is_qualified_unstop_item_negative_disciplines() -> None:
    """Verify non-software roles (MBA, Sales, Recruiter, Back Office, Mechanical) are disqualified."""
    non_tech_titles = [
        "Sales Executive Trainee",
        "IT Technical Recruiter",
        "Talent Acquisition Associate",
        "Back Office Executive",
        "Field Executive",
        "Customer Support Executive",
        "Telecaller / Customer Care",
        "Operations Executive",
    ]
    for idx, t in enumerate(non_tech_titles, start=2000):
        item = {
            "id": idx,
            "title": t,
            "status": "OPEN",
            "regn_open": 1,
            "locations": ["Delhi, India"],
            "workfunction": "Operations",
        }
        is_qual, reason = is_qualified_unstop_item(item)
        assert is_qual is False, f"Expected {t} to be disqualified, but passed"

    mech_item = {
        "id": 1003,
        "title": "Graduate Engineer Trainee - Mechanical Operations",
        "status": "OPEN",
        "regn_open": 1,
        "locations": ["Pune, India"],
        "workfunction": "Plant & Production",
    }
    is_qual, reason = is_qualified_unstop_item(mech_item)
    assert is_qual is False
    assert "GET" in reason or "Non-tech" in reason or "pattern" in reason


def test_is_qualified_unstop_item_closed_registration() -> None:
    """Verify ended/finished registrations are disqualified."""
    closed_item = {
        "id": 1004,
        "title": "Associate Software Engineer",
        "status": "FINISHED",
        "regn_open": 0,
        "locations": ["Hyderabad, India"],
    }
    is_qual, reason = is_qualified_unstop_item(closed_item)
    assert is_qual is False
    assert "closed" in reason.lower() or "not open" in reason.lower()


def test_is_qualified_unstop_item_experienced_rejection() -> None:
    """Verify senior experience requirements (3+ years) are disqualified."""
    senior_item = {
        "id": 1005,
        "title": "Software Engineer",
        "status": "OPEN",
        "regn_open": 1,
        "locations": ["Bengaluru, India"],
        "details": "Requires minimum 4+ years of hands-on experience in Java microservices.",
    }
    is_qual, reason = is_qualified_unstop_item(senior_item)
    assert is_qual is False
    assert "experienced" in reason.lower()


def test_build_job_posting_from_unstop() -> None:
    """Verify JobPosting normalization, description enrichment, and stack relevance scoring."""
    item = {
        "id": 1745484,
        "title": "Software Engineering Internship",
        "seo_url": "https://unstop.com/internships/software-engineering-internship-codesharks-1745484",
        "locations": [{"id": 1, "name": "Noida"}, "Remote"],
        "organisation": {"name": "Codesharks Technologies"},
        "details": "Develop microservices using Java, Spring Boot, and React with Docker deployment.",
        "jobDetail": {"description": "Hands-on experience with Kafka and MySQL."},
        "required_skills": [{"name": "Java"}, {"name": "React"}, {"name": "Docker"}],
        "tags": ["Full Stack", "Summer 2026"],
        "updated_at": "2026-09-05",
    }
    posting = build_job_posting_from_unstop(item)
    assert posting is not None
    assert posting.id == "unstop_1745484"
    assert posting.company == "Codesharks Technologies"
    assert posting.title == "Software Engineering Internship"
    assert str(posting.apply_url) == "https://unstop.com/internships/software-engineering-internship-codesharks-1745484"
    assert posting.provider == ATSProvider.UNSTOP
    assert posting.is_remote is True
    assert "Noida" in posting.location
    assert posting.description is not None
    assert "Spring Boot" in posting.description
    assert "Kafka" in posting.description
    # Score should be high due to Java, Spring Boot, React, Docker (+25, +25, +25, +15)
    assert posting.relevance_score is not None
    assert posting.relevance_score >= 60


@pytest.mark.asyncio
async def test_fetch_unstop_page_mock() -> None:
    """Verify fetch_unstop_page parses paginated API response."""
    mock_payload = {
        "data": {
            "current_page": 1,
            "data": [
                {"id": 101, "title": "Developer Intern"},
                {"id": 102, "title": "QA Automation Intern"},
            ],
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await fetch_unstop_page(client, opportunity_type="internships", search_term="software")

    assert len(items) == 2
    assert items[0]["id"] == 101


@pytest.mark.asyncio
async def test_run_unstop_ingestion_dry_run_vs_persist(tmp_path: Path) -> None:
    """Verify dry-run mode returns postings without writing to DB, while normal mode persists."""
    db_file = tmp_path / "test_unstop.db"
    init_db(db_file)

    mock_items = [
        {
            "id": 555,
            "title": "Software Developer Intern (Summer 2027)",
            "status": "OPEN",
            "regn_open": 1,
            "type": "internship",
            "locations": ["Bengaluru, India"],
            "organisation": {"name": "Innovate India"},
            "seo_url": "https://unstop.com/internships/swe-555",
            "workfunction": "Software Development",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"data": mock_items}})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    # 1. Test Dry Run
    dry_results = await run_unstop_ingestion(
        opportunities=["internships"],
        search_terms=["software"],
        pages_per_term=1,
        dry_run=True,
        db_path=db_file,
        client=mock_client,
    )
    assert len(dry_results) == 1
    stats_after_dry = get_stats(db_file)
    assert stats_after_dry["total_tracked"] == 0  # No DB writes in dry run

    # 2. Test Real Ingestion (persist)
    real_results = await run_unstop_ingestion(
        opportunities=["internships"],
        search_terms=["software"],
        pages_per_term=1,
        dry_run=False,
        db_path=db_file,
        client=mock_client,
    )
    assert len(real_results) == 1
    stats_after_real = get_stats(db_file)
    assert stats_after_real["total_tracked"] == 1
    assert stats_after_real["company_breakdown"]["Innovate India"] == 1

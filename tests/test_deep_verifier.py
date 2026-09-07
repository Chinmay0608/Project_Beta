"""Unit tests for gcc_job_radar/deep_verifier.py and 2027 graduate candidate evaluation."""

from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from gcc_job_radar.db import (
    get_job_by_id,
    init_db,
    mark_job_status,
    purge_or_dismiss_job,
    record_jobs,
)
from gcc_job_radar.deep_verifier import (
    clean_html_to_text,
    evaluate_for_2027_candidate,
    fetch_job_content,
)
from gcc_job_radar.models import ATSProvider, JobPosting
from tools.deep_verify_jobs import run_deep_verification


# --- 1. Evaluator Unit Tests (Class of 2027 Conditions) ---


def test_evaluate_summer_2026_2027_internship_passes() -> None:
    """Summer 2026/2027 software internships must pass evaluation for 2027 candidate."""
    desc = """
    We are looking for Summer 2026 Engineering Interns to join our core backend platform in Bangalore.
    Requirements:
    - Currently enrolled in a Bachelor's or Master's program in Computer Science, graduating in 2027.
    - Strong problem solving skills and foundational knowledge of Python or Java.
    - Freshers and pre-final year students are welcome to apply!
    """
    verdict = evaluate_for_2027_candidate(
        title="Software Engineering Intern - Summer 2026",
        location="Bangalore, India",
        description=desc,
    )
    assert verdict.is_eligible is True
    assert "2027" in verdict.batch_fit or "INTERNSHIP" in verdict.batch_fit
    assert verdict.confidence == "HIGH"


def test_evaluate_prefinal_penultimate_year_passes() -> None:
    """Pre-final year and penultimate year student roles must pass with strong match."""
    desc = """
    Opportunity for pre-final year and penultimate year students.
    Work with our distributed systems team on microservices architecture.
    Duration: 6-month internship with pre-placement offer (PPO) opportunities.
    """
    verdict = evaluate_for_2027_candidate(
        title="Software Developer Intern",
        location="Hyderabad, India",
        description=desc,
    )
    assert verdict.is_eligible is True
    assert verdict.batch_fit == "2027_STRONG"


def test_evaluate_past_batch_lock_fails() -> None:
    """Roles restricted strictly to past graduation batches (2024/2025 passouts) must be rejected."""
    desc = """
    Eligibility Criteria:
    - Only for 2024 / 2025 batch pass-outs.
    - Candidates from earlier batches are not eligible.
    - Must have completed degree in 2024 or 2025.
    """
    verdict = evaluate_for_2027_candidate(
        title="Associate Software Engineer",
        location="Pune, India",
        description=desc,
    )
    assert verdict.is_eligible is False
    assert verdict.batch_fit == "PAST_BATCH_ONLY"
    assert "past graduation batches" in verdict.reason.lower()


def test_evaluate_immediate_fulltime_degree_required_fails() -> None:
    """Roles demanding immediate full-time start with degree certificate in hand must fail for 2027 student."""
    desc = """
    Candidate must be available to join immediately for full-time work.
    Degree in hand required at the time of interview.
    No pursuing students eligible. Must have provisional degree certificate.
    """
    verdict = evaluate_for_2027_candidate(
        title="Software Engineer 1",
        location="Bengaluru, India",
        description=desc,
    )
    assert verdict.is_eligible is False
    assert verdict.batch_fit == "IMMEDIATE_FULLTIME"
    assert "immediate full-time" in verdict.reason.lower()


def test_evaluate_experience_inflation_fails() -> None:
    """Roles demanding 3+ years or >= 2.5 years experience must be disqualified."""
    desc = """
    Requirements:
    - 3-5 years of professional software engineering experience in production.
    - Hands-on experience designing large-scale distributed databases.
    - Minimum 3 years experience with Kubernetes.
    """
    verdict = evaluate_for_2027_candidate(
        title="Software Engineer - Backend",
        location="Gurgaon, India",
        description=desc,
    )
    assert verdict.is_eligible is False
    assert verdict.batch_fit == "EXPERIENCE_EXCLUDED"
    assert "experienced candidate" in verdict.reason.lower()


def test_evaluate_us_citizenship_security_clearance_fails() -> None:
    """Roles requiring US Citizenship or active US Security Clearance must be disqualified."""
    desc = """
    Role Overview:
    Must be a U.S. Citizen only due to government contract compliance.
    Active Secret Security Clearance is required.
    Must be legally authorized to work in the U.S. without sponsorship.
    """
    verdict = evaluate_for_2027_candidate(
        title="Cloud Infrastructure Associate",
        location="Remote",
        description=desc,
    )
    assert verdict.is_eligible is False
    assert verdict.batch_fit == "VISA_RESTRICTED"
    assert "us citizenship" in verdict.reason.lower() or "security clearance" in verdict.reason.lower()


def test_evaluate_expired_closed_job_fails() -> None:
    """Job postings displaying closed/expired signals must be flagged and disqualified."""
    # Text in page
    desc = "We appreciate your interest. This job is no longer available. Please check other openings."
    verdict = evaluate_for_2027_candidate(
        title="QA Automation Intern",
        location="Bengaluru, India",
        description=desc,
    )
    assert verdict.is_eligible is False
    assert verdict.batch_fit == "CLOSED"
    assert "closed/expired" in verdict.reason.lower()

    # HTTP 404 status
    v_404 = evaluate_for_2027_candidate(
        title="Associate Developer",
        location="India",
        description="",
        fetch_status="CLOSED_HTTP_404",
    )
    assert v_404.is_eligible is False
    assert v_404.batch_fit == "CLOSED"


def test_evaluate_cloudflare_challenge_protected() -> None:
    """Links blocked by Cloudflare challenge must NOT be auto-dismissed (kept as unverified)."""
    verdict = evaluate_for_2027_candidate(
        title="Software Engineer Intern",
        location="Noida, India",
        description="",
        fetch_status="UNVERIFIED_CHALLENGE",
    )
    assert verdict.is_eligible is True
    assert verdict.batch_fit == "UNVERIFIED_CHALLENGE"
    assert "challenge" in verdict.reason.lower()
    assert verdict.confidence == "LOW"


def test_evaluate_senior_title_and_non_tech_fails() -> None:
    """Senior titles or non-tech jobs must be rejected."""
    v_senior = evaluate_for_2027_candidate(
        title="Senior Principal Software Engineer",
        location="India",
        description="Write code.",
    )
    assert v_senior.is_eligible is False
    assert v_senior.batch_fit == "EXPERIENCE_EXCLUDED"

    v_sales = evaluate_for_2027_candidate(
        title="Telesales Representative / Cold Caller",
        location="India",
        description="Call customers.",
    )
    assert v_sales.is_eligible is False
    assert v_sales.batch_fit == "NON_TECH"


# --- 2. Content Retrieval Tests ---


@pytest.mark.asyncio
async def test_fetch_job_content_greenhouse_api() -> None:
    """Verify Greenhouse direct detail API extraction."""
    def mock_transport(request: httpx.Request) -> httpx.Response:
        assert "boards-api.greenhouse.io" in str(request.url)
        return httpx.Response(
            200,
            json={"content": "<p>Summer 2026 Intern</p><p>Graduating in 2027.</p>"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)) as client:
        content, status = await fetch_job_content(
            url="https://boards.greenhouse.io/stripe/jobs/123456",
            provider=ATSProvider.GREENHOUSE,
            client=client,
        )
        assert status == "OK"
        assert "Summer 2026 Intern" in content
        assert "Graduating in 2027" in content


@pytest.mark.asyncio
async def test_fetch_job_content_lever_api() -> None:
    """Verify Lever direct detail API extraction."""
    def mock_transport(request: httpx.Request) -> httpx.Response:
        assert "api.lever.co" in str(request.url)
        return httpx.Response(
            200,
            json={
                "descriptionPlain": "Software Engineer Intern for 2027 Batch.",
                "lists": [{"text": "Qualifications", "content": "Enrolled in University"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)) as client:
        content, status = await fetch_job_content(
            url="https://jobs.lever.co/netflix/abc-123-xyz",
            provider=ATSProvider.LEVER,
            client=client,
        )
        assert status == "OK"
        assert "Software Engineer Intern" in content
        assert "Qualifications" in content


@pytest.mark.asyncio
async def test_fetch_job_content_cloudflare_challenge() -> None:
    """Verify HTTP 403 / Cloudflare challenge is returned as UNVERIFIED_CHALLENGE."""
    def mock_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html><title>Attention Required! | Cloudflare</title></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)) as client:
        content, status = await fetch_job_content(
            url="https://example.com/careers/job-101",
            provider=ATSProvider.EMAIL_ALERT,
            client=client,
        )
        assert status == "UNVERIFIED_CHALLENGE"
        assert content == ""


# --- 3. Database Purge & Dismissal Tests ---


@pytest.fixture
def test_db_for_purge(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_purge.db"
    init_db(db_file)
    jobs = [
        JobPosting(
            id="job-eligible-01",
            company="Google",
            title="Software Engineering Intern",
            location="Bengaluru, India",
            apply_url="https://job-boards.greenhouse.io/google/jobs/101",
            provider=ATSProvider.GREENHOUSE,
            status="NEW",
        ),
        JobPosting(
            id="job-expired-02",
            company="Acme Corp",
            title="Associate Engineer",
            location="Remote",
            apply_url="https://example.com/expired-job",
            provider=ATSProvider.EMAIL_ALERT,
            status="NEW",
        ),
    ]
    record_jobs(jobs, db_file)
    return db_file


def test_purge_or_dismiss_job_dismissal(test_db_for_purge: Path) -> None:
    """Verify soft dismissal updates status to DISMISSED and records reason in notes."""
    success = purge_or_dismiss_job(
        job_id=2,
        reason="Posting expired/closed",
        hard_delete=False,
        db_path=test_db_for_purge,
    )
    assert success is True
    job = get_job_by_id(2, db_path=test_db_for_purge)
    assert job is not None
    assert job["status"] == "DISMISSED"
    assert job["is_active"] == 0
    assert "Auto-dismissed: Posting expired/closed" in job["notes"]


def test_purge_or_dismiss_job_hard_delete(test_db_for_purge: Path) -> None:
    """Verify hard delete permanently removes the row from the database."""
    success = purge_or_dismiss_job(
        job_id=2,
        reason="Posting expired/closed",
        hard_delete=True,
        db_path=test_db_for_purge,
    )
    assert success is True
    job = get_job_by_id(2, db_path=test_db_for_purge)
    assert job is None


@pytest.mark.asyncio
async def test_run_deep_verification_runner(test_db_for_purge: Path) -> None:
    """Verify run_deep_verification audits active jobs and dismisses non-eligible roles."""
    def mock_fetch(url: str, **kwargs):
        if "expired-job" in url:
            return "This job is no longer available.", "OK"
        return "Summer 2026 Intern for 2027 Batch in Bangalore.", "OK"

    with patch("tools.deep_verify_jobs.fetch_job_content", side_effect=mock_fetch):
        summary = await run_deep_verification(
            batch_year=2027,
            concurrency=5,
            dry_run=False,
            hard_delete=False,
            db_path=test_db_for_purge,
        )

    assert summary["checked"] == 2
    assert summary["kept"] == 1
    assert summary["dismissed"] == 1

    # Check database state
    j1 = get_job_by_id(1, db_path=test_db_for_purge)
    j2 = get_job_by_id(2, db_path=test_db_for_purge)
    assert j1["status"] == "NEW"
    assert j2["status"] == "DISMISSED"

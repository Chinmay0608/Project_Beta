"""Tests for stack-relevance scoring engine, database persistence, and CLI integration."""

from pathlib import Path
from typer.testing import CliRunner
from gcc_job_radar.cli import app
from gcc_job_radar.db import init_db, query_jobs, record_jobs, get_latest_jobs
from gcc_job_radar.models import ATSProvider, JobPosting
from gcc_job_radar.relevance import calculate_relevance_score, score_job_posting

runner = CliRunner()


def test_calculate_relevance_score_bounds_and_empty() -> None:
    """Ensure score is always bounded between 0 and 100, and empty input is 0."""
    assert calculate_relevance_score("", "") == 0
    assert calculate_relevance_score("", "", []) == 0


def test_calculate_relevance_score_java_spring_boot() -> None:
    """Java + Spring Boot 3 + MySQL + Docker + Kafka should produce high score."""
    score = calculate_relevance_score(
        title="Backend Software Engineer (Java / Spring Boot)",
        description="Looking for fresher/junior engineer with strong Java, Spring Boot 3, MySQL, Kafka, and Docker experience.",
    )
    assert score >= 70
    assert score <= 100


def test_calculate_relevance_score_mern_stack() -> None:
    """MERN stack roles should score very high."""
    score = calculate_relevance_score(
        title="Full Stack Developer (MERN)",
        description="Build web applications with MongoDB, Express.js, React, and Node.js. CI/CD with GitHub Actions.",
    )
    assert score >= 70
    assert score <= 100


def test_calculate_relevance_score_disjoint_stack() -> None:
    """Non-target stacks (iOS Swift, Ruby on Rails, Embedded) without core stack get 0 or very low score."""
    score_ios = calculate_relevance_score(
        title="iOS Developer",
        description="Deep knowledge of Swift, SwiftUI, and Xcode.",
    )
    assert score_ios == 0

    score_ruby = calculate_relevance_score(
        title="Ruby on Rails Developer",
        description="Maintain legacy Rails application and PostgreSQL database.",
    )
    # Ruby penalty (-20) plus PostgreSQL (+10) -> bounded to 0
    assert score_ruby == 0


def test_score_job_posting() -> None:
    """score_job_posting should attach relevance_score to JobPosting and dict."""
    job = JobPosting(
        id="job-java-1",
        company="Acme Corp",
        title="Junior Java Developer",
        location="Bengaluru, India",
        apply_url="https://acme.com/jobs/1",
        provider=ATSProvider.GREENHOUSE,
    )
    score = score_job_posting(job)
    assert score > 0
    assert job.relevance_score == score

    job_dict = {
        "title": "React Frontend Developer",
        "description": "Experience in React and TypeScript.",
    }
    dict_score = score_job_posting(job_dict)
    assert dict_score >= 20


def test_db_persistence_and_min_score_query(tmp_path: Path) -> None:
    """Verify relevance_score is saved to DB and query_jobs / get_latest_jobs filter and order correctly."""
    db_file = tmp_path / "test_relevance.db"
    init_db(db_file)

    high_job = JobPosting(
        id="high-1",
        company="TechGCC",
        title="Java Spring Boot Backend Engineer",
        location="Bengaluru",
        apply_url="https://techgcc.com/jobs/1",
        provider=ATSProvider.GREENHOUSE,
    )
    low_job = JobPosting(
        id="low-1",
        company="OtherGCC",
        title="Embedded Systems Firmware Engineer",
        location="Pune",
        apply_url="https://othergcc.com/jobs/2",
        provider=ATSProvider.LEVER,
    )

    record_jobs([low_job, high_job], db_path=db_file)

    # Both recorded
    all_jobs = query_jobs(limit=10, db_path=db_file)
    assert len(all_jobs) == 2
    # Highest score first
    assert all_jobs[0]["company"] == "TechGCC"
    assert all_jobs[0]["relevance_score"] > all_jobs[1]["relevance_score"]

    # Filter with min_score
    filtered_jobs = query_jobs(min_score=40, limit=10, db_path=db_file)
    assert len(filtered_jobs) == 1
    assert filtered_jobs[0]["company"] == "TechGCC"

    # get_latest_jobs also orders by relevance_score DESC
    latest = get_latest_jobs(limit=5, db_path=db_file)
    assert len(latest) == 2
    assert latest[0]["company"] == "TechGCC"


def test_cli_list_min_score(tmp_path: Path) -> None:
    """Verify gcc-job-radar list --min-score filters low score roles."""
    db_file = tmp_path / "test_cli_list.db"
    init_db(db_file)

    high_job = JobPosting(
        id="cli-high-1",
        company="AlphaCorp",
        title="Java Spring Developer",
        location="Hyderabad",
        apply_url="https://alpha.com/apply",
        provider=ATSProvider.GREENHOUSE,
    )
    low_job = JobPosting(
        id="cli-low-1",
        company="BetaCorp",
        title="Hardware Testing Technician",
        location="Bengaluru",
        apply_url="https://beta.com/apply",
        provider=ATSProvider.ASHBY,
    )
    record_jobs([high_job, low_job], db_path=db_file)

    # List all
    res_all = runner.invoke(app, ["list", "--db", str(db_file)])
    assert res_all.exit_code == 0
    assert "AlphaCorp" in res_all.output
    assert "BetaCorp" in res_all.output

    # List min-score 40
    res_filtered = runner.invoke(app, ["list", "--min-score", "40", "--db", str(db_file)])
    assert res_filtered.exit_code == 0
    assert "AlphaCorp" in res_filtered.output
    assert "BetaCorp" not in res_filtered.output

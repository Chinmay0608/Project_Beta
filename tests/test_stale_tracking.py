"""Tests for application stale tracking, pipeline stats, CLI stale commands, and bot /followups."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from typer.testing import CliRunner

from gcc_job_radar.bot_listener import handle_command
from gcc_job_radar.cli import app
from gcc_job_radar.db import (
    get_stale_applications,
    get_stats,
    init_db,
    mark_job_status,
    record_jobs,
)
from gcc_job_radar.models import ATSProvider, JobPosting

runner = CliRunner()


def test_get_stale_applications_and_stats(tmp_path: Path) -> None:
    """Test get_stale_applications filtering, days calculation, and get_stats pipeline counts."""
    db_file = tmp_path / "test_stale.db"
    init_db(db_file)

    now = datetime.now(timezone.utc)
    ten_days_ago = (now - timedelta(days=10)).isoformat()
    two_days_ago = (now - timedelta(days=2)).isoformat()

    job_stale = JobPosting(
        id="stale-1",
        company="OldAppliedCorp",
        title="Software Engineer 1",
        location="Bengaluru",
        apply_url="https://old.com/job/1",
        provider=ATSProvider.GREENHOUSE,
    )
    job_recent = JobPosting(
        id="recent-1",
        company="RecentAppliedCorp",
        title="Associate Developer",
        location="Hyderabad",
        apply_url="https://recent.com/job/2",
        provider=ATSProvider.LEVER,
    )
    job_interview = JobPosting(
        id="interview-1",
        company="InterviewCorp",
        title="Junior Backend Engineer",
        location="Pune",
        apply_url="https://interview.com/job/3",
        provider=ATSProvider.ASHBY,
    )

    record_jobs([job_stale, job_recent, job_interview], db_path=db_file)

    # Mark statuses
    mark_job_status("greenhouse_oldappliedcorp_stale-1", "APPLIED", notes="Sent referral", db_path=db_file)
    mark_job_status("lever_recentappliedcorp_recent-1", "APPLIED", notes="Applied via careers", db_path=db_file)
    mark_job_status("ashby_interviewcorp_interview-1", "INTERVIEWING", notes="Tech screen scheduled", db_path=db_file)

    # Manually backdate applied_at in SQLite for testing
    import sqlite3
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "UPDATE seen_jobs SET applied_at = ? WHERE id = 'greenhouse_oldappliedcorp_stale-1'",
            (ten_days_ago,),
        )
        conn.execute(
            "UPDATE seen_jobs SET applied_at = ? WHERE id = 'lever_recentappliedcorp_recent-1'",
            (two_days_ago,),
        )
        conn.commit()

    # Query stale with threshold = 7 days
    stale_7d = get_stale_applications(days=7, db_path=db_file)
    assert len(stale_7d) == 1
    assert stale_7d[0]["company"] == "OldAppliedCorp"
    assert stale_7d[0]["days_elapsed"] >= 9
    assert stale_7d[0]["notes"] == "Sent referral"

    # Query stale with threshold = 1 day (both applied roles qualify)
    stale_1d = get_stale_applications(days=1, db_path=db_file)
    assert len(stale_1d) == 2
    # Oldest first
    assert stale_1d[0]["company"] == "OldAppliedCorp"
    assert stale_1d[1]["company"] == "RecentAppliedCorp"

    # Verify get_stats returns full application pipeline
    stats = get_stats(db_path=db_file)
    assert stats["total_tracked"] == 3
    assert stats["applied_count"] == 2
    assert stats["interviewing_count"] == 1
    assert stats["rejected_count"] == 0
    assert stats["active_count"] == 0


def test_cli_stale_command(tmp_path: Path) -> None:
    """Verify gcc-job-radar stale command and export flags."""
    db_file = tmp_path / "cli_stale.db"
    init_db(db_file)

    ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    job = JobPosting(
        id="stale-cli",
        company="StaleTech",
        title="Backend Dev",
        location="Bengaluru",
        apply_url="https://staletech.com/apply",
        provider=ATSProvider.GREENHOUSE,
    )
    record_jobs([job], db_path=db_file)
    mark_job_status("greenhouse_staletech_stale-cli", "APPLIED", notes="Follow up with recruiter", db_path=db_file)

    import sqlite3
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "UPDATE seen_jobs SET applied_at = ? WHERE id = 'greenhouse_staletech_stale-cli'",
            (ten_days_ago,),
        )
        conn.commit()

    json_out = tmp_path / "stale.json"
    res = runner.invoke(app, ["stale", "--db", str(db_file), "--json", str(json_out)])
    assert res.exit_code == 0
    assert "StaleTech" in res.output
    assert "Pending Follow-up" in res.output
    assert json_out.exists()


def test_cli_list_stale_flag(tmp_path: Path) -> None:
    """Verify gcc-job-radar list --stale flag."""
    db_file = tmp_path / "cli_list_stale.db"
    init_db(db_file)

    res = runner.invoke(app, ["list", "--stale", "--db", str(db_file)])
    assert res.exit_code == 0
    assert "No stale applications found" in res.output


@pytest.mark.asyncio
async def test_bot_followups_command(tmp_path: Path) -> None:
    """Verify Telegram /followups command sends formatted pending follow-ups."""
    db_file = tmp_path / "bot_stale.db"
    init_db(db_file)

    eight_days_ago = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    job = JobPosting(
        id="bot-stale-1",
        company="BotStaleCorp",
        title="Junior SDE",
        location="Bengaluru",
        apply_url="https://botstale.com/job/1",
        provider=ATSProvider.GREENHOUSE,
    )
    record_jobs([job], db_path=db_file)
    mark_job_status("greenhouse_botstalecorp_bot-stale-1", "APPLIED", notes="Referral on LinkedIn", db_path=db_file)

    import sqlite3
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "UPDATE seen_jobs SET applied_at = ? WHERE id = 'greenhouse_botstalecorp_bot-stale-1'",
            (eight_days_ago,),
        )
        conn.commit()

    client_mock = AsyncMock()
    client_mock.post.return_value.status_code = 200

    await handle_command(
        command_text="/followups",
        chat_id="12345",
        bot_token="FAKE_TOKEN",
        allowed_chat_id="12345",
        client=client_mock,
        db_path=db_file,
    )

    client_mock.post.assert_called_once()
    payload = client_mock.post.call_args[1]["json"]
    assert "BotStaleCorp" in payload["text"]
    assert "Pending Follow-up" in payload["text"]
    assert "8 days ago" in payload["text"]

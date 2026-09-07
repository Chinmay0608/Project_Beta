"""Tests for daily digest notification mode across Discord and Telegram."""

from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from typer.testing import CliRunner

from gcc_job_radar.cli import app
from gcc_job_radar.db import filter_unalerted_jobs, init_db, record_jobs
from gcc_job_radar.models import ATSProvider, JobPosting
from gcc_job_radar.notifier import (
    dispatch_notifications,
    group_jobs_by_company,
    send_discord_digest,
    send_telegram_digest,
)

runner = CliRunner()


def test_group_jobs_by_company_and_relevance_sorting() -> None:
    """Ensure jobs are grouped by company and sorted by relevance score."""
    job1 = JobPosting(
        id="1",
        company="AlphaCorp",
        title="Junior Developer",
        location="Bengaluru",
        apply_url="https://alpha.com/1",
        provider=ATSProvider.GREENHOUSE,
        relevance_score=30,
    )
    job2 = JobPosting(
        id="2",
        company="AlphaCorp",
        title="Java Spring Engineer",
        location="Bengaluru",
        apply_url="https://alpha.com/2",
        provider=ATSProvider.GREENHOUSE,
        relevance_score=90,
    )
    job3 = JobPosting(
        id="3",
        company="BetaCorp",
        title="Full Stack MERN",
        location="Pune",
        apply_url="https://beta.com/3",
        provider=ATSProvider.LEVER,
        relevance_score=75,
    )

    grouped = group_jobs_by_company([job1, job3, job2])
    # AlphaCorp has top job with score 90, so AlphaCorp is first
    assert grouped[0][0] == "AlphaCorp"
    # Within AlphaCorp, job2 (90) comes before job1 (30)
    assert grouped[0][1][0].id == "2"
    assert grouped[0][1][1].id == "1"

    # BetaCorp is second
    assert grouped[1][0] == "BetaCorp"
    assert grouped[1][1][0].id == "3"


@pytest.mark.asyncio
async def test_send_discord_digest() -> None:
    """Test send_discord_digest creates consolidated company fields and score tags."""
    job = JobPosting(
        id="d1",
        company="Databricks",
        title="Software Engineer 1",
        location="Bengaluru",
        apply_url="https://databricks.com/1",
        provider=ATSProvider.GREENHOUSE,
        relevance_score=85,
    )
    client_mock = AsyncMock()
    client_mock.post.return_value.status_code = 200

    ok = await send_discord_digest("https://discord.com/api/webhooks/test", [job], client_mock)
    assert ok is True
    client_mock.post.assert_called_once()
    payload = client_mock.post.call_args[1]["json"]
    assert "embeds" in payload
    embed = payload["embeds"][0]
    assert "Daily Digest" in embed["title"]
    field = embed["fields"][0]
    assert "Databricks" in field["name"]
    assert "[85 pts]" in field["value"]
    assert "Software Engineer 1" in field["value"]


@pytest.mark.asyncio
async def test_send_telegram_digest() -> None:
    """Test send_telegram_digest formats grouped HTML message."""
    job = JobPosting(
        id="t1",
        company="Celonis",
        title="Graduate Java Engineer",
        location="Bengaluru",
        apply_url="https://celonis.com/1",
        provider=ATSProvider.GREENHOUSE,
        relevance_score=95,
    )
    client_mock = AsyncMock()
    client_mock.post.return_value.status_code = 200

    ok = await send_telegram_digest("FAKE_BOT_TOKEN", "12345", [job], client_mock)
    assert ok is True
    client_mock.post.assert_called_once()
    payload = client_mock.post.call_args[1]["json"]
    assert "Daily Digest" in payload["text"]
    assert "Celonis" in payload["text"]
    assert "[95 pts]" in payload["text"]
    assert "Graduate Java Engineer" in payload["text"]


@pytest.mark.asyncio
async def test_dispatch_notifications_digest_flow(tmp_path: Path) -> None:
    """Test dispatch_notifications in digest mode marks jobs alerted in DB."""
    db_file = tmp_path / "test_digest_flow.db"
    init_db(db_file)

    job = JobPosting(
        id="flow-1",
        company="Snowflake",
        title="Associate SDE",
        location="Pune",
        apply_url="https://snowflake.com/1",
        provider=ATSProvider.GREENHOUSE,
        relevance_score=80,
    )
    record_jobs([job], db_path=db_file)

    with patch("gcc_job_radar.notifier.send_discord_digest", new_callable=AsyncMock) as mock_discord, patch(
        "gcc_job_radar.notifier.send_telegram_digest", new_callable=AsyncMock
    ) as mock_telegram:
        mock_discord.return_value = True
        mock_telegram.return_value = True

        await dispatch_notifications(
            new_jobs=[job],
            discord_webhook="https://discord.com/fake",
            telegram_token="FAKE_TOKEN",
            telegram_chat_id="12345",
            db_path=db_file,
            digest=True,
        )

        mock_discord.assert_called_once()
        mock_telegram.assert_called_once()

    # Verify both channels are marked alerted in database
    unalerted_discord = filter_unalerted_jobs([job], "discord", db_path=db_file)
    unalerted_telegram = filter_unalerted_jobs([job], "telegram", db_path=db_file)
    assert len(unalerted_discord) == 0
    assert len(unalerted_telegram) == 0


def test_cli_digest_flag(tmp_path: Path) -> None:
    """Verify CLI accepts --digest flag."""
    db_file = tmp_path / "cli_digest.db"
    init_db(db_file)

    sample_job = JobPosting(
        id="cli-d-1",
        company="Uber",
        title="Software Engineer 1",
        location="Hyderabad",
        apply_url="https://uber.com/1",
        provider=ATSProvider.GREENHOUSE,
    )

    async def mock_scan(*args, **kwargs):
        return [sample_job]

    with patch("gcc_job_radar.cli.scan_all_companies", side_effect=mock_scan), patch(
        "gcc_job_radar.cli.dispatch_notifications"
    ) as mock_dispatch:
        res = runner.invoke(app, ["--digest", "--company", "uber", "--db", str(db_file)])
        assert res.exit_code == 0
        mock_dispatch.assert_called_once()
        # Ensure digest=True was passed to dispatch_notifications
        _, kwargs = mock_dispatch.call_args
        assert kwargs.get("digest") is True

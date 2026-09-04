"""Unit tests for interactive Telegram bot listener."""

import json
from pathlib import Path
from unittest.mock import patch
import httpx
import pytest

from gcc_job_radar.bot_listener import format_jobs_html, handle_command
from gcc_job_radar.db import init_db, record_jobs
from gcc_job_radar.models import ATSProvider, JobPosting


@pytest.fixture
def sample_jobs() -> list[JobPosting]:
    return [
        JobPosting(
            id="test-101",
            company="Celonis",
            title="Associate Software Engineer - Java",
            location="Bangalore, India",
            apply_url="https://job-boards.greenhouse.io/celonis/jobs/7791267003",
            published_date="2026-08-25",
            provider=ATSProvider.GREENHOUSE,
        )
    ]


@pytest.mark.asyncio
async def test_unauthorized_user() -> None:
    """Verify unauthorized users receive access denied response."""
    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/scan",
            chat_id="999999",  # Unauthorized
            bot_token="test_token",
            allowed_chat_id="123456",  # Authorized
            client=client,
        )

        assert len(captured_messages) == 1
        assert "Access Denied" in captured_messages[0]["text"]
        assert captured_messages[0]["chat_id"] == "999999"


@pytest.mark.asyncio
async def test_help_command() -> None:
    """Verify /help returns available commands."""
    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/help",
            chat_id="123456",
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
        )

        assert len(captured_messages) == 1
        text = captured_messages[0]["text"]
        assert "/scan" in text
        assert "/check" in text
        assert "/stats" in text
        assert "/latest" in text


@pytest.mark.asyncio
async def test_stats_command(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify /stats formats historical metrics."""
    db_file = tmp_path / "bot_stats.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/stats",
            chat_id="123456",
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
            db_path=db_file,
        )

        assert len(captured_messages) == 1
        text = captured_messages[0]["text"]
        assert "Total Roles Tracked:</b> 1" in text
        assert "Celonis" in text


@pytest.mark.asyncio
async def test_latest_command(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify /latest returns recently recorded jobs."""
    db_file = tmp_path / "bot_latest.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/latest",
            chat_id="123456",
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
            db_path=db_file,
        )

        assert len(captured_messages) == 1
        text = captured_messages[0]["text"]
        assert "Celonis" in text
        assert "Associate Software Engineer - Java" in text
        assert "href=\"https://job-boards.greenhouse.io/celonis/jobs/7791267003\"" in text


@pytest.mark.asyncio
async def test_check_command(sample_jobs: list[JobPosting]) -> None:
    """Verify /check company triggers scan and replies with results."""
    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async def mock_scan(*args, **kwargs):
        return sample_jobs

    with patch("gcc_job_radar.bot_listener.scan_all_companies", side_effect=mock_scan):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await handle_command(
                command_text="/check celonis",
                chat_id="123456",
                bot_token="test_token",
                allowed_chat_id="123456",
                client=client,
            )

            # Expected 2 messages: Scanning message + Results message
            assert len(captured_messages) == 2
            assert "Scanning" in captured_messages[0]["text"]
            assert "Celonis" in captured_messages[1]["text"]
            assert "Associate Software Engineer" in captured_messages[1]["text"]

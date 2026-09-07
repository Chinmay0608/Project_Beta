"""Unit tests for interactive Telegram bot listener."""

import json
from pathlib import Path
from unittest.mock import patch
import httpx
import pytest

from gcc_job_radar.bot_listener import (
    build_job_inline_keyboard,
    format_jobs_html,
    handle_callback_query,
    handle_command,
)
from gcc_job_radar.db import get_job_by_id, init_db, mark_job_status, record_jobs
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
        assert "/clear" in text
        assert "/list" in text



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

            assert len(captured_messages) == 2
            assert "Scanning" in captured_messages[0]["text"]
            assert "Celonis" in captured_messages[1]["text"]
            assert "Associate Software Engineer" in captured_messages[1]["text"]


@pytest.mark.asyncio
async def test_scan_debounce(sample_jobs: list[JobPosting]) -> None:
    """Verify /scan debounces immediate re-executions to avoid duplicate scans."""
    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async def mock_scan(*args, **kwargs):
        return sample_jobs

    import gcc_job_radar.bot_listener as bl
    bl._last_scan_timestamp = 0.0
    bl._is_scanning = False

    with patch("gcc_job_radar.bot_listener.scan_all_companies", side_effect=mock_scan):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            # First scan should succeed
            await handle_command("/scan", "123456", "token", "123456", client)
            # Second scan immediately after should trigger debounce
            await handle_command("/scan", "123456", "token", "123456", client)

            # Check that debounce message was sent
            assert any("just completed seconds ago" in msg["text"] for msg in captured_messages)


@pytest.mark.asyncio
async def test_clear_command() -> None:
    """Verify /clear resets chat history and replies."""
    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/clear",
            chat_id="123456",
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
        )

        assert len(captured_messages) == 1
        text = captured_messages[0]["text"]
        assert "Chat history cleared" in text


def test_build_job_inline_keyboard_standard(sample_jobs: list[JobPosting]) -> None:
    """Verify standard inline keyboard contains Apply, Dismiss, and Applied buttons."""
    job = sample_jobs[0]
    markup = build_job_inline_keyboard(job)

    assert "inline_keyboard" in markup
    rows = markup["inline_keyboard"]
    assert len(rows) == 2

    # Row 1: Apply URL button
    assert len(rows[0]) == 1
    assert rows[0][0]["text"] == "Apply"
    assert rows[0][0]["url"] == "https://job-boards.greenhouse.io/celonis/jobs/7791267003"

    # Row 2: Dismiss and Applied callback buttons
    assert len(rows[1]) == 2
    assert rows[1][0]["text"] == "Dismiss"
    assert rows[1][0]["callback_data"] == f"dismiss:{job.id}"
    assert rows[1][1]["text"] == "Applied"
    assert rows[1][1]["callback_data"] == f"applied:{job.id}"


def test_build_job_inline_keyboard_with_search_url_or_needs_resolve() -> None:
    """Verify conditional 'Search Direct ATS' button is added when direct_search_url or NEEDS_RESOLVE present."""
    job_with_search = JobPosting(
        id="test-resolve",
        company="BT Group",
        title="Associate Engineer",
        location="Bengaluru",
        apply_url="https://jobs.bt.com/123",
        provider=ATSProvider.EMAIL_ALERT,
        status="NEEDS_RESOLVE",
        direct_search_url="https://jobs.bt.com/search/?q=123",
    )
    markup = build_job_inline_keyboard(job_with_search)
    rows = markup["inline_keyboard"]

    # Row 1 has both Apply and Search Direct ATS
    assert len(rows[0]) == 2
    assert rows[0][0]["text"] == "Apply"
    assert rows[0][1]["text"] == "Search Direct ATS"
    assert rows[0][1]["url"] == "https://jobs.bt.com/search/?q=123"


@pytest.mark.asyncio
async def test_callback_query_dismiss(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify dismiss callback updates DB status to DISMISSED, edits message, and answers query."""
    db_file = tmp_path / "bot_cb_dismiss.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    job = get_job_by_id(sample_jobs[0].id, db_path=db_file)
    assert job["status"] == "NEW"
    rowid = job["numeric_id"]

    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        data = json.loads(request.content.decode("utf-8"))
        captured_requests.append({"url": str(request.url), "data": data})
        return httpx.Response(200, json={"ok": True})

    callback_query = {
        "id": "cb_query_999",
        "from": {"id": 123456},
        "data": f"dismiss:{rowid}",
        "message": {
            "message_id": 42,
            "chat": {"id": 123456},
            "text": "🚀 Celonis\n💼 Associate Software Engineer",
        },
    }

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await handle_callback_query(
            callback_query=callback_query,
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
            db_path=db_file,
        )

        assert result is True

        # Verify state change in seen_jobs
        updated_job = get_job_by_id(rowid, db_path=db_file)
        assert updated_job["status"] == "DISMISSED"

        # Verify Telegram Bot API calls (editMessageText and answerCallbackQuery)
        urls = [req["url"] for req in captured_requests]
        assert any("editMessageText" in u for u in urls)
        assert any("answerCallbackQuery" in u for u in urls)

        edit_call = next(req for req in captured_requests if "editMessageText" in req["url"])
        assert "[DISMISSED]" in edit_call["data"]["text"]
        assert "<s>" in edit_call["data"]["text"]
        assert edit_call["data"]["message_id"] == 42

        answer_call = next(req for req in captured_requests if "answerCallbackQuery" in req["url"])
        assert answer_call["data"]["callback_query_id"] == "cb_query_999"
        assert "Dismissed" in answer_call["data"]["text"]


@pytest.mark.asyncio
async def test_callback_query_applied(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify applied callback updates DB status to APPLIED, records timestamp, and edits message."""
    db_file = tmp_path / "bot_cb_applied.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    job = get_job_by_id(sample_jobs[0].id, db_path=db_file)
    assert job["status"] == "NEW"
    rowid = job["numeric_id"]

    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        data = json.loads(request.content.decode("utf-8"))
        captured_requests.append({"url": str(request.url), "data": data})
        return httpx.Response(200, json={"ok": True})

    callback_query = {
        "id": "cb_query_888",
        "from": {"id": 123456},
        "data": f"applied:{rowid}",
        "message": {
            "message_id": 43,
            "chat": {"id": 123456},
            "text": "🚀 Celonis\n💼 Associate Software Engineer",
        },
    }

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await handle_callback_query(
            callback_query=callback_query,
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
            db_path=db_file,
        )

        assert result is True

        # Verify state change in seen_jobs
        updated_job = get_job_by_id(rowid, db_path=db_file)
        assert updated_job["status"] == "APPLIED"
        assert updated_job["applied_at"] is not None

        # Verify Telegram Bot API calls
        edit_call = next(req for req in captured_requests if "editMessageText" in req["url"])
        assert "[APPLIED]" in edit_call["data"]["text"]
        assert edit_call["data"]["message_id"] == 43

        answer_call = next(req for req in captured_requests if "answerCallbackQuery" in req["url"])
        assert answer_call["data"]["callback_query_id"] == "cb_query_888"
        assert "Applied" in answer_call["data"]["text"]


@pytest.mark.asyncio
async def test_callback_query_unauthorized(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify unauthorized user callback does not modify database."""
    db_file = tmp_path / "bot_cb_unauth.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        data = json.loads(request.content.decode("utf-8"))
        captured_requests.append({"url": str(request.url), "data": data})
        return httpx.Response(200, json={"ok": True})

    callback_query = {
        "id": "cb_query_unauth",
        "from": {"id": 999999},  # Unauthorized
        "data": "dismiss:1",
        "message": {
            "message_id": 44,
            "chat": {"id": 999999},
            "text": "Some text",
        },
    }

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await handle_callback_query(
            callback_query=callback_query,
            bot_token="test_token",
            allowed_chat_id="123456",  # Authorized is 123456
            client=client,
            db_path=db_file,
        )

        assert result is False

        # Verify DB was NOT modified
        job = get_job_by_id(1, db_path=db_file)
        assert job["status"] == "NEW"

        # Verify rejection alert answered
        answer_call = next(req for req in captured_requests if "answerCallbackQuery" in req["url"])
        assert "Access Denied" in answer_call["data"]["text"]


@pytest.mark.asyncio
async def test_stats_command_reports_active_needs_resolve_dismissed(tmp_path: Path) -> None:
    """Verify /stats reports breakdown of active, needs_resolve, applied, and dismissed jobs."""
    db_file = tmp_path / "bot_stats_breakdown.db"
    init_db(db_file)

    jobs = [
        JobPosting(
            id="j1",
            company="Alpha",
            title="SWE 1",
            location="Remote",
            apply_url="https://alpha.com/1",
            provider=ATSProvider.GREENHOUSE,
            status="NEW",
        ),
        JobPosting(
            id="j2",
            company="Beta",
            title="SWE 2",
            location="Bangalore",
            apply_url="https://beta.com/2",
            provider=ATSProvider.LEVER,
            status="NEEDS_RESOLVE",
        ),
        JobPosting(
            id="j3",
            company="Gamma",
            title="SWE 3",
            location="Pune",
            apply_url="https://gamma.com/3",
            provider=ATSProvider.ASHBY,
            status="DISMISSED",
        ),
        JobPosting(
            id="j4",
            company="Delta",
            title="SWE 4",
            location="Noida",
            apply_url="https://delta.com/4",
            provider=ATSProvider.SMARTRECRUITERS,
            status="APPLIED",
        ),
    ]
    record_jobs(jobs, db_file)
    mark_job_status("j2", "NEEDS_RESOLVE", db_path=db_file)
    mark_job_status("j3", "DISMISSED", db_path=db_file)
    mark_job_status("j4", "APPLIED", db_path=db_file)

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
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
        assert "Total Roles Tracked:</b> 4" in text
        assert "Active (New):</b> 1" in text
        assert "Needs Resolve:</b> 1" in text
        assert "Applied:</b> 1" in text
        assert "Dismissed:</b> 1" in text


@pytest.mark.asyncio
async def test_latest_command_with_inline_keyboard(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify /latest attaches interactive inline keyboard with Apply, Dismiss, Applied buttons."""
    db_file = tmp_path / "bot_latest_kb.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
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
        msg = captured_messages[0]
        assert "reply_markup" in msg
        kb = msg["reply_markup"]["inline_keyboard"]
        assert len(kb) == 2
        assert kb[0][0]["text"] == "Apply"
        assert kb[1][0]["text"] == "Dismiss"
        assert kb[1][1]["text"] == "Applied"




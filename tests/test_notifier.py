from pathlib import Path
import httpx
import pytest

from gcc_job_radar.models import ATSProvider, JobPosting
from gcc_job_radar.notifier import (
    dispatch_notifications,
    send_discord_notification,
    send_telegram_notification,
)


@pytest.fixture
def sample_jobs() -> list[JobPosting]:
    return [
        JobPosting(
            id="job-101",
            company="Databricks",
            title="Software Engineer 1",
            location="Bengaluru, India",
            apply_url="https://boards.greenhouse.io/databricks/jobs/101",
            published_date="2026-09-01",
            provider=ATSProvider.GREENHOUSE,
        ),
        JobPosting(
            id="job-202",
            company="Atlassian",
            title="Associate Software Engineer",
            location="Pune, India",
            apply_url="https://jobs.lever.co/atlassian/job-202",
            published_date="2026-08-30",
            provider=ATSProvider.LEVER,
        ),
    ]


@pytest.mark.asyncio
async def test_send_discord_notification_success(sample_jobs: list[JobPosting]) -> None:
    """Verify Discord payload includes correct embeds, colors, and fields."""
    captured_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured_payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(204)

    webhook_url = "https://discord.com/api/webhooks/12345/test-token"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await send_discord_notification(webhook_url, sample_jobs, client)
        assert ok is True
        assert len(captured_payloads) == 1

        payload = captured_payloads[0]
        assert "embeds" in payload
        assert len(payload["embeds"]) == 2

        embed = payload["embeds"][0]
        assert embed["title"] == "🚀 Databricks - Software Engineer 1"
        assert embed["color"] == 0x00FF88
        assert any(f["name"] == "🏢 Company" and f["value"] == "Databricks" for f in embed["fields"])
        assert any("boards.greenhouse.io" in f["value"] for f in embed["fields"])


@pytest.mark.asyncio
async def test_send_discord_notification_failure(sample_jobs: list[JobPosting]) -> None:
    """Verify Discord returns False on HTTP 400 without crashing."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Bad Request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await send_discord_notification("https://discord.com/api/webhooks/fail", sample_jobs, client)
        assert ok is False


@pytest.mark.asyncio
async def test_send_telegram_notification_success(sample_jobs: list[JobPosting]) -> None:
    """Verify Telegram payload includes HTML-formatted text and proper parse_mode."""
    captured_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured_payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await send_telegram_notification("bot123456:ABC-DEF", "987654321", sample_jobs, client)
        assert ok is True
        assert len(captured_payloads) == 1

        payload = captured_payloads[0]
        assert payload["chat_id"] == "987654321"
        assert payload["parse_mode"] == "HTML"
        assert "<b>1. Databricks</b>" in payload["text"]
        assert "href=\"https://boards.greenhouse.io/databricks/jobs/101\"" in payload["text"]


@pytest.mark.asyncio
async def test_send_telegram_notification_failure(sample_jobs: list[JobPosting]) -> None:
    """Verify Telegram returns False on HTTP error."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await send_telegram_notification("invalid_token", "123", sample_jobs, client)
        assert ok is False


@pytest.mark.asyncio
async def test_dispatch_notifications_with_env(
    sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify dispatch_notifications falls back to environment variables when flags omitted."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/mocked")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock_bot_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "mock_chat_id")

    called_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))
        if "discord.com" in str(request.url):
            return httpx.Response(204)
        return httpx.Response(200, json={"ok": True})

    # Patch httpx.AsyncClient in notifier module
    import gcc_job_radar.notifier as notifier

    original_async_client = httpx.AsyncClient

    def mock_client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client_factory)

    test_db = tmp_path / "notifier_env_test.db"
    await dispatch_notifications(sample_jobs, db_path=test_db)
    assert any("discord.com" in u for u in called_urls)
    assert any("telegram.org" in u for u in called_urls)


@pytest.mark.asyncio
async def test_dispatch_notifications_explicit_args(
    sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify dispatch_notifications sends alerts when tokens are passed directly as arguments."""
    called_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))
        if "discord.com" in str(request.url):
            return httpx.Response(204)
        return httpx.Response(200, json={"ok": True})

    original_async_client = httpx.AsyncClient

    def mock_client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client_factory)

    test_db = tmp_path / "notifier_args_test.db"
    await dispatch_notifications(
        sample_jobs,
        discord_webhook="https://discord.com/api/webhooks/mocked_arg",
        telegram_token="mock_arg_token",
        telegram_chat_id="mock_arg_chat",
        db_path=test_db,
    )
    assert any("discord.com" in u for u in called_urls)
    assert any("telegram.org" in u for u in called_urls)


@pytest.mark.asyncio
async def test_dispatch_notifications_deduplication(
    sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify dispatch_notifications does not re-alert on previously dispatched jobs."""
    called_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    original_async_client = httpx.AsyncClient

    def mock_client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client_factory)

    test_db = tmp_path / "notifier_dedup_test.db"

    # First dispatch: sends 1 telegram message (since telegram doesn't chunk sample_jobs into multiples)
    await dispatch_notifications(
        sample_jobs,
        telegram_token="mock_token",
        telegram_chat_id="mock_chat",
        db_path=test_db,
    )
    assert len(called_urls) == 1

    # Second dispatch with identical db: should NOT call telegram API again
    await dispatch_notifications(
        sample_jobs,
        telegram_token="mock_token",
        telegram_chat_id="mock_chat",
        db_path=test_db,
    )
    assert len(called_urls) == 1


@pytest.mark.asyncio
async def test_dispatch_notifications_no_channels(
    sample_jobs: list[JobPosting], tmp_path: Path
) -> None:
    """Verify dispatch_notifications cleanly returns without making any HTTP requests when unconfigured."""
    test_db = tmp_path / "notifier_no_channels.db"
    # Should complete without error and without making any requests
    await dispatch_notifications(sample_jobs, db_path=test_db)



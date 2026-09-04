"""Global pytest fixtures and test environment isolation."""

import pytest


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests never inherit real credentials or send live notifications.

    Clears sensitive environment variables before each test and restores them after.
    """
    sensitive_keys = [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "DISCORD_WEBHOOK_URL",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "PRIMARY_LLM_PROVIDER",
    ]
    for key in sensitive_keys:
        monkeypatch.delenv(key, raising=False)

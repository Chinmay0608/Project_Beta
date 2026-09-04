"""Unit tests for the conversational AI agent and tool calling integration."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from gcc_job_radar.ai_agent import (
    ChatHistoryManager,
    ask_ai_agent,
    clear_chat_history,
    convert_markdown_tables_to_cards,
    execute_tool,
    format_jobs_html,
    markdown_to_telegram_html,
)
from gcc_job_radar.db import init_db, query_jobs, record_jobs
from gcc_job_radar.models import ATSProvider, JobPosting


@pytest.fixture
def sample_jobs() -> list[JobPosting]:
    return [
        JobPosting(
            id="job-python-01",
            company="Celonis",
            title="Associate Python Engineer",
            location="Bengaluru, Karnataka, India",
            apply_url="https://job-boards.greenhouse.io/celonis/jobs/101",
            published_date="2026-08-25",
            provider=ATSProvider.GREENHOUSE,
        ),
        JobPosting(
            id="job-java-02",
            company="Databricks",
            title="Graduate Software Engineer - Java",
            location="Hyderabad, Telangana, India",
            apply_url="https://job-boards.greenhouse.io/databricks/jobs/102",
            published_date="2026-08-26",
            provider=ATSProvider.GREENHOUSE,
        ),
        JobPosting(
            id="job-pune-03",
            company="Snowflake",
            title="Software QA Engineer 1",
            location="Pune, Maharashtra, India",
            apply_url="https://job-boards.greenhouse.io/snowflake/jobs/103",
            published_date="2026-08-27",
            provider=ATSProvider.GREENHOUSE,
        ),
    ]


# 1. Database Querying Tests


def test_query_jobs_filters(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify db.query_jobs filters correctly by company, title, and location."""
    db_file = tmp_path / "test_query.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    # Filter by company
    celonis_jobs = query_jobs(company="Celonis", db_path=db_file)
    assert len(celonis_jobs) == 1
    assert celonis_jobs[0]["company"] == "Celonis"

    # Filter by title keyword
    python_jobs = query_jobs(title_keyword="python", db_path=db_file)
    assert len(python_jobs) == 1
    assert "Python" in python_jobs[0]["title"]

    # Filter by location
    pune_jobs = query_jobs(location="Pune", db_path=db_file)
    assert len(pune_jobs) == 1
    assert "Pune" in pune_jobs[0]["location"]

    # Filter by combined parameters
    none_jobs = query_jobs(company="Celonis", location="Pune", db_path=db_file)
    assert len(none_jobs) == 0

    # Test limit
    all_jobs = query_jobs(limit=2, db_path=db_file)
    assert len(all_jobs) == 2


# 2. History Manager Tests


def test_chat_history_manager() -> None:
    """Verify conversational memory tracks, truncates, and clears messages."""
    mgr = ChatHistoryManager(max_turns=3)
    chat_id = "test-chat-123"

    mgr.add_turn(chat_id, "user", "Hello")
    mgr.add_turn(chat_id, "assistant", "Hi there!")
    history = mgr.get_history(chat_id)
    assert len(history) == 2
    assert history[0]["content"] == "Hello"
    assert history[1]["content"] == "Hi there!"

    # Test clearing
    mgr.clear(chat_id)
    assert len(mgr.get_history(chat_id)) == 0


# 3. Formatting Tests


def test_markdown_to_telegram_html() -> None:
    """Verify markdown tags convert to safe Telegram HTML."""
    md = "Hello **world** with *italics* and `code` and [link](https://example.com)"
    res = markdown_to_telegram_html(md)
    assert "<b>world</b>" in res
    assert "<i>italics</i>" in res
    assert "<code>code</code>" in res
    assert '<a href="https://example.com">link</a>' in res

    # Verify HTML escaping for raw angled brackets
    raw = "If 5 < 10 & 10 > 2 then **valid**"
    res2 = markdown_to_telegram_html(raw)
    assert "&lt;" in res2
    assert "&amp;" in res2
    assert "<b>valid</b>" in res2


def test_markdown_table_to_cards_conversion() -> None:
    """Verify markdown tables (like those returned by LLMs) convert to mobile-friendly cards."""
    table_md = (
        "Here is the latest opening:\n\n"
        "| Company | Role | Location | Posted |\n"
        "|---------|------|----------|---------|\n"
        "| Celonis | Associate Software Engineer – Java | Bangalore, India | 25 Aug 2026 |\n\n"
        "[Apply here](https://job-boards.greenhouse.io/celonis/jobs/7791267003)"
    )
    html_res = markdown_to_telegram_html(table_md)
    # Ensure raw table pipes were converted away
    assert "| Company |" not in html_res
    assert "|---------" not in html_res
    assert "🏢 <b>Celonis</b>" in html_res
    assert "💼 Associate Software Engineer" in html_res
    assert "📍 Bangalore, India • 📅 25 Aug 2026" in html_res
    assert '<a href="https://job-boards.greenhouse.io/celonis/jobs/7791267003">Apply here</a>' in html_res


def test_markdown_table_with_inline_links() -> None:
    """Verify markdown tables containing apply URLs convert directly to cards with action links."""
    table_md = (
        "| Company | Role | Location | Apply |\n"
        "|---|---|---|---|\n"
        "| Databricks | Software Engineer 1 | Bangalore | https://databricks.com/jobs/101 |\n"
    )
    html_res = markdown_to_telegram_html(table_md)
    assert "🏢 <b>Databricks</b>" in html_res
    assert "💼 Software Engineer 1" in html_res
    assert '<a href="https://databricks.com/jobs/101">Apply on ATS</a>' in html_res


def test_markdown_bullets_and_italics_safe() -> None:
    """Verify asterisk bullets do not accidentally close or mangle subsequent italics."""
    md = "* **Associate Software Engineer - Java** at **Celonis** (Bangalore, India) — *Published Aug 25, 2026*"
    html_res = markdown_to_telegram_html(md)
    assert html_res.startswith("• ")
    assert "<b>Associate Software Engineer - Java</b>" in html_res
    assert "<b>Celonis</b>" in html_res
    assert "<i>Published Aug 25, 2026</i>" in html_res
    assert "</i>Published" not in html_res


def test_format_jobs_html(sample_jobs: list[JobPosting]) -> None:
    """Verify job listing formatter generates readable HTML with URLs."""
    job_dicts = [
        {
            "company": j.company,
            "title": j.title,
            "location": j.location,
            "apply_url": str(j.apply_url),
            "published_date": j.published_date,
        }
        for j in sample_jobs
    ]
    res = format_jobs_html(job_dicts, "Test Results")
    assert "Test Results (3)" in res
    assert "Celonis" in res
    assert "Databricks" in res
    assert "href=\"https://job-boards.greenhouse.io/celonis/jobs/101\"" in res


# 4. Tool Execution Tests


@pytest.mark.asyncio
async def test_execute_tool(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify tool execution router executes db queries, stats, and live checks."""
    db_file = tmp_path / "test_exec_tools.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    # Test query_jobs tool
    q_res = await execute_tool("query_jobs", {"title_keyword": "Java"}, db_path=db_file)
    assert q_res["status"] == "success"
    assert q_res["count"] == 1
    assert "Databricks" in q_res["jobs"][0]["company"]

    # Test get_tracking_stats tool
    s_res = await execute_tool("get_tracking_stats", {}, db_path=db_file)
    assert s_res["status"] == "success"
    assert s_res["stats"]["total_tracked"] == 3

    # Test check_company_live tool with mock
    async def mock_scan(*args, **kwargs):
        return [sample_jobs[0]]

    with patch("gcc_job_radar.ai_agent.scan_all_companies", side_effect=mock_scan):
        c_res = await execute_tool("check_company_live", {"company_name": "Celonis"}, db_path=db_file)
        assert c_res["status"] == "success"
        assert c_res["count"] == 1

    # Test get_configured_companies tool
    comp_res = await execute_tool("get_configured_companies", {})
    assert comp_res["status"] == "success"
    assert comp_res["count"] > 0
    assert any(c["name"] == "Celonis" for c in comp_res["companies"])



# 5. Fallback Mode Tests (No API Keys Configured)


@pytest.mark.asyncio
async def test_ask_ai_agent_fallback_greeting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify greeting triggers assistant description when no LLM key is set."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    reply = await ask_ai_agent("Hello, what can you do?", chat_id="chat-1")
    assert "GCC Job Radar Assistant" in reply
    assert "/scan" in reply


@pytest.mark.asyncio
async def test_ask_ai_agent_fallback_search(
    tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify natural query finds jobs in database via keyword extraction."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "fallback_search.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    reply = await ask_ai_agent("Are there any python roles in Bangalore?", chat_id="chat-2", db_path=db_file)
    assert "Celonis" in reply
    assert "Associate Python Engineer" in reply
    assert "Apply on ATS" in reply


@pytest.mark.asyncio
async def test_ask_ai_agent_fallback_stats(
    tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify natural query for stats reports totals."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "fallback_stats.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    reply = await ask_ai_agent("How many total jobs are in the database?", chat_id="chat-3", db_path=db_file)
    assert "Total Roles Tracked:</b> 3" in reply


@pytest.mark.asyncio
async def test_ask_ai_agent_fallback_live_check(
    tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify live check intent triggers ATS scan and replies."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "fallback_live.db"
    init_db(db_file)

    async def mock_scan(*args, **kwargs):
        return [sample_jobs[0]]

    with patch("gcc_job_radar.ai_agent.scan_all_companies", side_effect=mock_scan):
        reply = await ask_ai_agent("Check Celonis live now", chat_id="chat-4", db_path=db_file)
        assert "Live Scan Results for Celonis" in reply
        assert "Associate Python Engineer" in reply


@pytest.mark.asyncio
async def test_ask_ai_agent_fallback_list_companies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify natural query asking to list companies returns company directory."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    reply = await ask_ai_agent("Can you list all companies tracked?", chat_id="chat-list")
    assert "Tracked GCCs & Enterprise Tech Hubs" in reply
    assert "GREENHOUSE" in reply
    assert "Celonis" in reply



# 6. Gemini REST API Integration with Tool Calling Mock


@pytest.mark.asyncio
async def test_ask_ai_agent_gemini_tool_calling(
    tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Gemini API flow executes functionCall and returns LLM response."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "gemini_tool.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    call_count = 0

    def gemini_mock(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        data = json.loads(request.content.decode("utf-8"))

        if call_count == 1:
            # First response: Gemini asks to call tool `query_jobs`
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "query_jobs",
                                            "args": {"title_keyword": "Python"},
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                },
            )
        else:
            # Second response: Gemini summarizes tool output
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": "I found 1 Python role: **Associate Python Engineer** at Celonis! [Apply](https://job-boards.greenhouse.io/celonis/jobs/101)"
                                    }
                                ]
                            }
                        }
                    ]
                },
            )

    async with httpx.AsyncClient(transport=httpx.MockTransport(gemini_mock)) as client:
        reply = await ask_ai_agent(
            "Find python roles",
            chat_id="chat-gemini",
            db_path=db_file,
            client=client,
        )
        assert call_count == 2
        assert "<b>Associate Python Engineer</b>" in reply
        assert "href=\"https://job-boards.greenhouse.io/celonis/jobs/101\"" in reply


# 7. OpenAI REST API Integration with Tool Calling Mock


@pytest.mark.asyncio
async def test_ask_ai_agent_openai_tool_calling(
    tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify OpenAI API flow executes tool_calls and returns LLM response."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fake_openai_key")

    db_file = tmp_path / "openai_tool.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    call_count = 0

    def openai_mock(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        data = json.loads(request.content.decode("utf-8"))

        if call_count == 1:
            # First response: OpenAI returns tool_calls
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "call_123",
                                        "type": "function",
                                        "function": {
                                            "name": "query_jobs",
                                            "arguments": json.dumps({"company": "Databricks"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        else:
            # Second response: OpenAI generates final answer
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Found 1 role at **Databricks**: [Graduate Software Engineer](https://job-boards.greenhouse.io/databricks/jobs/102)",
                            }
                        }
                    ]
                },
            )

    async with httpx.AsyncClient(transport=httpx.MockTransport(openai_mock)) as client:
        reply = await ask_ai_agent(
            "Show me jobs at Databricks",
            chat_id="chat-openai",
            db_path=db_file,
            client=client,
        )
        assert call_count == 2
        assert "<b>Databricks</b>" in reply
        assert "href=\"https://job-boards.greenhouse.io/databricks/jobs/102\"" in reply


# 8. Non-Tool Queries & Error Logging Tests


@pytest.mark.asyncio
async def test_ask_ai_agent_gemini_direct_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Verify basic non-tool queries (like general conversation or 2+2) return direct model text."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def gemini_mock(request: httpx.Request) -> httpx.Response:
        assert "gemini-flash-lite-latest" in str(request.url)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "2 + 2 is **4**."}
                            ]
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(gemini_mock)) as client:
        reply = await ask_ai_agent("what is 2+2", chat_id="chat-math", client=client)
        assert "<b>4</b>" in reply

    captured = capsys.readouterr()
    assert "Available LLM providers: Gemini" in captured.out


@pytest.mark.asyncio
async def test_ask_ai_agent_gemini_api_error_logging(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Verify API error (e.g. 404/429) is logged to stderr with exact message before fallback."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def gemini_mock(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            text='{"error": {"code": 404, "message": "models/gemini-1.5-flash is not found"}}',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(gemini_mock)) as client:
        reply = await ask_ai_agent("what is 2+2", chat_id="chat-err", client=client)
        assert "I couldn't find an exact match" in reply

    captured = capsys.readouterr()
    assert "Available LLM providers: Gemini" in captured.out
    assert "[AI Agent Error] Gemini API error (HTTP 404)" in captured.err
    assert "models/gemini-1.5-flash is not found" in captured.err


@pytest.mark.asyncio
async def test_ask_ai_agent_no_key_logging(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Verify ask_ai_agent logs when no keys are detected."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    reply = await ask_ai_agent("hello", chat_id="chat-nokey")
    assert "Hello! I'm your GCC Job Radar Assistant" in reply
    captured = capsys.readouterr()
    assert "Neither GEMINI_API_KEY nor GROQ_API_KEY nor OPENAI_API_KEY detected" in captured.out


# 9. Smart Shifting Tests (Gemini -> Groq)


@pytest.mark.asyncio
async def test_ask_ai_agent_smart_shift_gemini_to_groq(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Verify that when Gemini fails (e.g. HTTP 429 quota error), the agent automatically shifts to Groq."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.setenv("GROQ_API_KEY", "fake_groq_key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def multi_provider_mock(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "generativelanguage.googleapis.com" in url_str:
            # Gemini fails with 429 rate limit error
            return httpx.Response(
                429,
                text='{"error": {"code": 429, "message": "Quota exceeded for Gemini"}}',
            )
        elif "api.groq.com" in url_str:
            # Groq catches request and responds successfully
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Hello from **Groq** via smart shifting!",
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(multi_provider_mock)) as client:
        reply = await ask_ai_agent("what is 2+2", chat_id="chat-shift", client=client)
        assert "<b>Groq</b>" in reply

    captured = capsys.readouterr()
    assert "Gemini, Groq" in captured.out
    assert "Attempting primary provider: Gemini" in captured.out
    assert "Smart shifting to Groq" in captured.out


@pytest.mark.asyncio
async def test_ask_ai_agent_groq_standalone(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Groq works directly when Gemini key is absent."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "fake_groq_key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def groq_mock(request: httpx.Request) -> httpx.Response:
        assert "api.groq.com" in str(request.url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Answer is **4**",
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(groq_mock)) as client:
        reply = await ask_ai_agent("what is 2+2", chat_id="chat-groq-only", client=client)
        assert "<b>4</b>" in reply

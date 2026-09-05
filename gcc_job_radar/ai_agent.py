"""Interactive conversational AI job assistant for GCC Job Radar."""

import html
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any, Optional
from dotenv import load_dotenv
import httpx

# Automatically load environment variables from .env if present
load_dotenv()

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.db import get_stats, query_jobs, record_jobs
from gcc_job_radar.engine import scan_all_companies
from gcc_job_radar.models import JobPosting

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are the GCC Job Radar AI Assistant. You help candidates discover verified "
    "entry-level engineering, software, and tech roles at foreign GCCs and enterprise tech hubs in India. "
    "Use your tools to query the local database, inspect the company directory, or run real-time company checks.\n\n"
    "CRITICAL FORMATTING GUIDELINES FOR TELEGRAM:\n"
    "- NEVER use markdown tables (no '| ... |' format). Telegram cannot render tables and they look broken and unreadable on mobile screens.\n"
    "- When presenting jobs, ALWAYS present each job as a clean, structured card with emojis and markdown links:\n"
    "  🏢 **Company Name**\n"
    "  💼 Job Title\n"
    "  📍 Location • 📅 Posted Date\n"
    "  🔗 [Apply on ATS](apply_url)\n"
    "- If multiple jobs are found, separate each job card with a blank line.\n"
    "- Keep answers concise, crisp, and conversational. Avoid walls of text."
)

# Tool Schemas for Gemini & OpenAI

GEMINI_TOOLS = [
    {
        "function_declarations": [
            {
                "name": "query_jobs",
                "description": "Query the database of verified entry-level GCC tech jobs in India by title keyword, location, or company.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "title_keyword": {"type": "STRING", "description": "Role or skill keyword (e.g. software, python, intern, backend)"},
                        "location": {"type": "STRING", "description": "City or region in India (e.g. Bangalore, Hyderabad, Pune)"},
                        "company": {"type": "STRING", "description": "Company name (e.g. Celonis, Snowflake, Databricks)"},
                        "limit": {"type": "INTEGER", "description": "Max results to return (default 5)"},
                    },
                },
            },
            {
                "name": "check_company_live",
                "description": "Trigger an immediate real-time live scan of a specific GCC company's career portal for entry-level positions.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "company_name": {"type": "STRING", "description": "Company name to scan (e.g. Celonis, Databricks)"},
                    },
                    "required": ["company_name"],
                },
            },
            {
                "name": "get_tracking_stats",
                "description": "Get database statistics (total jobs tracked, top companies, first and last seen timestamps).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {},
                },
            },
            {
                "name": "get_configured_companies",
                "description": "Get the directory of all configured GCC companies and foreign tech hubs tracked by GCC Job Radar.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {},
                },
            },
        ]
    }
]

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_jobs",
            "description": "Query the database of verified entry-level GCC tech jobs in India by title keyword, location, or company.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title_keyword": {"type": "string", "description": "Role or skill keyword (e.g. software, python, intern, backend)"},
                    "location": {"type": "string", "description": "City or region in India (e.g. Bangalore, Hyderabad, Pune)"},
                    "company": {"type": "string", "description": "Company name (e.g. Celonis, Snowflake, Databricks)"},
                    "limit": {"type": "integer", "description": "Max results to return (default 5)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_company_live",
            "description": "Trigger an immediate real-time live scan of a specific GCC company's career portal for entry-level positions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name": {"type": "string", "description": "Company name to scan (e.g. Celonis, Databricks)"},
                },
                "required": ["company_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tracking_stats",
            "description": "Get database statistics (total jobs tracked, top companies, first and last seen timestamps).",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_configured_companies",
            "description": "Get the directory of all configured GCC companies and foreign tech hubs tracked by GCC Job Radar.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


class ChatHistoryManager:
    """Manages short multi-turn conversation histories per chat session."""

    def __init__(self, max_turns: int = 10) -> None:
        self.max_turns = max_turns
        self._histories: dict[str, list[dict[str, str]]] = {}

    def add_turn(self, chat_id: str | int, role: str, content: str) -> None:
        cid = str(chat_id)
        if cid not in self._histories:
            self._histories[cid] = []
        self._histories[cid].append({"role": role, "content": content})
        if len(self._histories[cid]) > self.max_turns * 2:
            self._histories[cid] = self._histories[cid][-self.max_turns * 2 :]

    def get_history(self, chat_id: str | int) -> list[dict[str, str]]:
        return list(self._histories.get(str(chat_id), []))

    def clear(self, chat_id: str | int) -> None:
        self._histories.pop(str(chat_id), None)


_chat_manager = ChatHistoryManager()


def clear_chat_history(chat_id: str | int) -> None:
    """Clear conversation history for a given chat session."""
    _chat_manager.clear(chat_id)


# Tool Implementations


def get_configured_companies() -> list[dict[str, str]]:
    """Return all configured GCC companies with name and ATS provider."""
    return [
        {
            "name": c.name,
            "provider": c.provider.value,
        }
        for c in COMPANIES
    ]


async def execute_tool(
    name: str, args: dict[str, Any], db_path: Optional[Path] = None
) -> dict[str, Any]:
    """Execute a registered tool function by name."""
    if name == "query_jobs":
        title_keyword = args.get("title_keyword")
        location = args.get("location")
        company = args.get("company")
        limit = int(args.get("limit", 5))
        jobs = query_jobs(
            title_keyword=title_keyword,
            location=location,
            company=company,
            limit=limit,
            db_path=db_path,
        )
        return {"status": "success", "count": len(jobs), "jobs": jobs}

    elif name == "check_company_live":
        company_name = args.get("company_name", "").strip()
        query = company_name.lower()
        matched = [
            c for c in COMPANIES if query in c.name.lower() or query in c.board_token.lower()
        ]
        if not matched:
            return {
                "status": "not_found",
                "message": f"Company '{company_name}' is not in the tracked GCC registry.",
                "jobs": [],
            }

        target_company = matched[0]
        jobs = await scan_all_companies(companies=[target_company])
        record_jobs(jobs, db_path)
        job_dicts = [
            {
                "company": j.company,
                "title": j.title,
                "location": j.location,
                "apply_url": str(j.apply_url),
                "published_date": j.published_date or "Active",
            }
            for j in jobs
        ]
        return {
            "status": "success",
            "company": target_company.name,
            "count": len(job_dicts),
            "jobs": job_dicts,
        }

    elif name == "get_tracking_stats":
        stats = get_stats(db_path)
        return {"status": "success", "stats": stats}

    elif name == "get_configured_companies":
        comps = get_configured_companies()
        return {"status": "success", "count": len(comps), "companies": comps}

    return {"status": "error", "message": f"Unknown tool '{name}'"}


# Telegram HTML Formatting Helper


def _clean_cell(val: str) -> str:
    val = val.strip()
    if (val.startswith("**") and val.endswith("**")) or (val.startswith("__") and val.endswith("__")):
        val = val[2:-2].strip()
    return val


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    return bool(re.match(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?$", stripped))


def convert_markdown_tables_to_cards(text: str) -> str:
    """Convert raw markdown tables into mobile-friendly structured cards."""
    lines = text.split("\n")
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "|" in line and i + 1 < len(lines) and _is_table_separator(lines[i + 1]):
            headers = [_clean_cell(c) for c in _split_table_row(line)]
            i += 2  # skip header and separator line
            table_cards = []
            while i < len(lines) and "|" in lines[i] and not _is_table_separator(lines[i]):
                row_cells = _split_table_row(lines[i])
                row_dict = {h.lower(): cell for h, cell in zip(headers, row_cells)}

                comp = next((_clean_cell(v) for k, v in row_dict.items() if any(w in k for w in ["company", "employer", "org", "firm"]) and v), None)
                role = next((_clean_cell(v) for k, v in row_dict.items() if any(w in k for w in ["role", "title", "position", "job", "designation"]) and v), None)
                loc = next((_clean_cell(v) for k, v in row_dict.items() if any(w in k for w in ["location", "city", "place", "office"]) and v), None)
                date = next((_clean_cell(v) for k, v in row_dict.items() if any(w in k for w in ["posted", "date", "published", "added"]) and v), None)
                link = next((v for k, v in row_dict.items() if any(w in k for w in ["link", "apply", "url", "action"]) and v), None)

                if comp or role:
                    card = []
                    if comp:
                        card.append(f"🏢 **{comp}**")
                    if role:
                        card.append(f"💼 {role}")
                    meta = []
                    if loc:
                        meta.append(f"📍 {loc}")
                    if date:
                        meta.append(f"📅 {date}")
                    if meta:
                        card.append(" • ".join(meta))
                    if link:
                        if link.startswith("[") and "](" in link:
                            card.append(f"🔗 {link}")
                        elif link.startswith("http://") or link.startswith("https://"):
                            card.append(f"🔗 [Apply on ATS]({link})")
                        else:
                            card.append(f"🔗 {link}")
                    table_cards.append("\n".join(card))
                else:
                    items = [f"• **{h.title()}**: {v}" for h, v in zip(headers, row_cells) if v]
                    table_cards.append("\n".join(items))
                i += 1
            if table_cards:
                new_lines.append("\n\n".join(table_cards))
            continue
        new_lines.append(line)
        i += 1
    return "\n".join(new_lines)


def markdown_to_telegram_html(text: str) -> str:
    """Convert common markdown patterns to safe Telegram HTML."""
    if not text:
        return ""

    # 1. Convert any raw markdown tables to clean card layout
    text = convert_markdown_tables_to_cards(text)

    # 2. Convert markdown bullet points (* or - at start of line) to •
    text = re.sub(r"(?m)^[\*\-]\s+", "• ", text)

    # Replace markdown code blocks ```code``` -> <pre>code</pre>
    def replace_code_block(match: re.Match) -> str:
        content = match.group(1)
        return f"<pre>{html.escape(content.strip())}</pre>"

    text = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\n?(.*?)```", replace_code_block, text, flags=re.DOTALL)

    # Escape HTML special chars in text outside already replaced <pre>
    parts = re.split(r"(<pre>.*?</pre>)", text, flags=re.DOTALL)
    escaped_parts = []
    for part in parts:
        if part.startswith("<pre>"):
            escaped_parts.append(part)
        else:
            # Escape raw & < >
            part = html.escape(part)
            # Restore markdown links [title](url) -> <a href="url">title</a>
            part = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r'<a href="\2">\1</a>', part)
            # Bold **text** or __text__ -> <b>text</b>
            part = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", part)
            part = re.sub(r"__([^_]+)__", r"<b>\1</b>", part)
            # Inline code `code` -> <code>code</code>
            part = re.sub(r"`([^`]+)`", r"<code>\1</code>", part)
            # Italics *text* or _text_ -> <i>text</i> (excluding word boundaries or whitespace)
            part = re.sub(r"(?<![\*\w])\*([^\*\s](?:[^\*]*?[^\*\s])?)\*(?![\*\w])", r"<i>\1</i>", part)
            part = re.sub(r"(?<![_\w])_([^_\s](?:[^_]*?[^_\s])?)_(?![_\w])", r"<i>\1</i>", part)
            escaped_parts.append(part)

    return "".join(escaped_parts).strip()


def format_jobs_html(jobs: list[dict[str, Any]], title: str) -> str:
    """Format job listings into Telegram HTML."""
    if not jobs:
        return f"ℹ️ <b>{html.escape(title)}</b>\n\nNo matching entry-level roles found."

    msg = f"🚀 <b>{html.escape(title)} ({len(jobs)})</b>\n\n"
    for idx, item in enumerate(jobs, start=1):
        company = item.get("company", "")
        pos_title = item.get("title", "")
        location = item.get("location", "")
        date = item.get("published_date") or "Active"
        url = item.get("apply_url", "")

        msg += (
            f"<b>{idx}. {html.escape(company)}</b>\n"
            f"💼 {html.escape(pos_title)}\n"
            f"📍 {html.escape(location)} • 📅 {html.escape(date)}\n"
            f'🔗 <a href="{url}">Apply on ATS</a>\n\n'
        )
    return msg.strip()


def format_tool_result_summary(name: str, result: dict[str, Any]) -> str:
    """Format tool result as clean readable HTML rather than raw JSON."""
    if "jobs" in result:
        return format_jobs_html(result["jobs"], f"Results for {name}")
    if "companies" in result:
        by_provider: dict[str, list[str]] = {}
        for c in result["companies"]:
            p = c.get("provider", "OTHER").upper()
            by_provider.setdefault(p, []).append(c.get("name", ""))
        text = f"🏢 <b>Configured GCC Companies ({len(result['companies'])}):</b>\n\n"
        for provider, names in sorted(by_provider.items()):
            text += f"<b>{provider} ({len(names)}):</b>\n"
            text += f"{', '.join(sorted(names))}\n\n"
        return text.strip()
    if "stats" in result:
        stats = result["stats"]
        total = stats.get("total_tracked", 0)
        breakdown = stats.get("company_breakdown", {})
        text = f"📊 <b>Database Stats:</b>\n\n• <b>Total Roles:</b> {total}\n"
        if breakdown:
            text += "\n<b>Top Tracked Companies:</b>\n"
            for comp, count in list(breakdown.items())[:6]:
                text += f"• {html.escape(comp)}: {count}\n"
        return text.strip()
    return f"ℹ️ <b>{html.escape(name)}</b>: {html.escape(str(result.get('message', 'Completed')))}"


# Smart Rule-Based Fallback


async def _fallback_response(
    query: str, db_path: Optional[Path] = None
) -> str:
    """Rule-based natural language parsing and intent matching when no LLM key is configured."""
    q = query.lower().strip()

    # 1. Greetings & capabilities
    if any(q.startswith(g) or q == g for g in ["hi", "hello", "hey", "who are you", "what can you do", "help"]):
        return (
            "👋 <b>Hello! I'm your GCC Job Radar Assistant.</b>\n\n"
            "I help you track entry-level tech roles in India across 150+ foreign GCCs and Fortune 500 tech hubs.\n\n"
            "💡 <b>You can ask me:</b>\n"
            "• <i>\"Any Python or backend roles in Bangalore?\"</i>\n"
            "• <i>\"List all tracked companies\"</i>\n"
            "• <i>\"Show me entry-level jobs at Celonis\"</i>\n"
            "• <i>\"Check Databricks live\"</i>\n"
            "• <i>\"How many jobs are currently tracked?\"</i>\n\n"
            "Or use slash commands like <code>/scan</code>, <code>/latest</code>, or <code>/stats</code>."
        )

    # 2. Company directory intent
    is_company_list_query = (
        any(phrase in q for phrase in [
            "list companies", "name all companies", "show gccs", "all companies",
            "company list", "list of companies", "show companies", "tracked companies",
            "list gccs", "supported companies", "which companies", "what companies",
        ])
        or (
            ("companies" in q or "gcc" in q or "gccs" in q)
            and any(w in q for w in ["which", "what", "list", "name", "show", "all", "track", "who"])
        )
    )
    if is_company_list_query:
        by_provider: dict[str, list[str]] = {}
        for c in COMPANIES:
            p_name = c.provider.value.upper()

            if p_name not in by_provider:
                by_provider[p_name] = []
            by_provider[p_name].append(c.name)

        text = f"🏢 <b>Tracked GCCs & Enterprise Tech Hubs ({len(COMPANIES)}):</b>\n\n"
        for provider, names in sorted(by_provider.items()):
            text += f"<b>{provider} ({len(names)}):</b>\n"
            text += f"{', '.join(sorted(names))}\n\n"
        text += "💡 <i>Use <code>/check &lt;name&gt;</code> to scan any company live!</i>"
        return text.strip()

    # 3. Database Stats intent
    if any(term in q for term in ["stats", "statistics", "how many", "count", "metrics", "total jobs"]):
        res = await execute_tool("get_tracking_stats", {}, db_path=db_path)
        stats = res.get("stats", {})
        total = stats.get("total_tracked", 0)
        breakdown = stats.get("company_breakdown", {})
        text = f"📊 <b>Database Stats:</b>\n\n• <b>Total Roles Tracked:</b> {total}\n"
        if breakdown:
            text += "\n<b>Top Tracked Companies:</b>\n"
            for comp, count in list(breakdown.items())[:6]:
                text += f"• {html.escape(comp)}: {count}\n"
        return text.strip()

    # 4. Check live intent
    matched_companies = [
        c
        for c in COMPANIES
        if re.search(r"\b" + re.escape(c.name.lower()) + r"\b", q)
        or re.search(r"\b" + re.escape(c.board_token.lower()) + r"\b", q)
    ]
    is_live_request = any(term in q for term in ["live", "check", "scan", "fresh", "now", "update"])

    if matched_companies and is_live_request:
        company = matched_companies[0]
        res = await execute_tool("check_company_live", {"company_name": company.name}, db_path=db_path)
        jobs = res.get("jobs", [])
        return format_jobs_html(jobs, f"Live Scan Results for {company.name}")

    # 5. Job query intent: match location, title keywords, or company
    locations = [
        "bangalore", "bengaluru", "hyderabad", "pune", "gurgaon", "gurugram",
        "noida", "chennai", "mumbai", "delhi", "remote"
    ]
    matched_location = next((loc for loc in locations if loc in q), None)

    title_keywords = [
        "software", "sde", "engineer", "developer", "backend", "frontend",
        "fullstack", "python", "java", "data", "machine learning", "ai",
        "cloud", "intern", "graduate", "analyst", "qa", "devops", "security"
    ]
    matched_title = next((kw for kw in title_keywords if kw in q), None)
    matched_comp_name = matched_companies[0].name if matched_companies else None

    if matched_location or matched_title or matched_comp_name:
        res = await execute_tool(
            "query_jobs",
            {
                "title_keyword": matched_title,
                "location": matched_location,
                "company": matched_comp_name,
                "limit": 5,
            },
            db_path=db_path,
        )
        jobs = res.get("jobs", [])
        header = "Matching Entry-Level Openings"
        criteria = []
        if matched_comp_name:
            criteria.append(matched_comp_name)
        if matched_title:
            criteria.append(matched_title.title())
        if matched_location:
            criteria.append(matched_location.title())
        if criteria:
            header += f" ({', '.join(criteria)})"

        if jobs:
            return format_jobs_html(jobs, header)
        else:
            return (
                f"ℹ️ <b>{html.escape(header)}</b>\n\n"
                f"No entry-level postings currently found in the local database matching your query.\n"
                f"Try <code>/scan</code> to refresh all 150+ boards or <code>/check {matched_comp_name or 'company'}</code> for a live scan."
            )

    # 6. Generic helpful response
    return (
        "🤖 <i>I couldn't find an exact match for your request.</i>\n\n"
        "Try asking for specific roles, companies, or cities:\n"
        "• <i>\"Find Python jobs in Bangalore\"</i>\n"
        "• <i>\"List all companies\"</i>\n"
        "• <i>\"Check Celonis live\"</i>\n"
        "• <i>\"Show stats\"</i>\n\n"
        "Or use <code>/latest</code> to see recent openings."
    )


# LLM Callers (Gemini & OpenAI)


async def _call_gemini(
    prompt: str,
    history: list[dict[str, str]],
    api_key: str,
    client: httpx.AsyncClient,
    db_path: Optional[Path] = None,
) -> str:
    """Call Google Gemini REST API with multi-turn tool calling and conversational synthesis."""
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    # Build multi-turn contents
    contents: list[dict[str, Any]] = []
    for turn in history:
        role = "user" if turn["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": turn["content"]}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload: dict[str, Any] = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "tools": GEMINI_TOOLS,
    }

    last_tool_name = ""
    last_tool_result: dict[str, Any] = {}

    for _ in range(5):
        try:
            resp = await client.post(url, json=payload, timeout=15.0)
        except Exception as exc:
            err_msg = f"[AI Agent Error] Gemini request failed (connection/timeout): {exc}"
            print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            break

        if resp.status_code != 200:
            err_msg = f"[AI Agent Error] Gemini API error (HTTP {resp.status_code}): {resp.text}"
            print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            break

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            err_msg = f"[AI Agent Error] Gemini API returned no candidates: {data}"
            print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            break

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])

        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not function_calls:
            # Model provided direct text response without tool invocation (e.g. general questions or 2+2)
            text = "".join(p.get("text", "") for p in parts if "text" in p)
            if text:
                return markdown_to_telegram_html(text)
            err_msg = f"[AI Agent Error] Gemini candidate had no text and no functionCall: {parts}"
            print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            break

        # Append model turn with tool call parts
        contents.append({"role": "model", "parts": parts})
        response_parts = []
        for fc in function_calls:
            tool_name = fc.get("name", "")
            tool_args = fc.get("args", {})
            tool_result = await execute_tool(tool_name, tool_args, db_path=db_path)
            last_tool_name = tool_name
            last_tool_result = tool_result
            response_parts.append({
                "functionResponse": {
                    "name": tool_name,
                    "response": tool_result,
                }
            })

        # Append user turn with functionResponse parts and loop back to model
        contents.append({"role": "user", "parts": response_parts})
        payload["contents"] = contents

    # If the LLM loop terminated without producing conversational text, summarize gracefully
    if last_tool_result:
        return format_tool_result_summary(last_tool_name, last_tool_result)
    return None


async def _call_openai_compatible(
    prompt: str,
    history: list[dict[str, str]],
    api_key: str,
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    provider_name: str = "OpenAI",
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Call OpenAI-compatible REST API (OpenAI, Groq, etc.) with multi-turn tool calling."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "tools": OPENAI_TOOLS,
        "tool_choice": "auto",
    }

    last_tool_name = ""
    last_tool_result: dict[str, Any] = {}

    for _ in range(5):
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=30.0)
        except Exception as exc:
            err_msg = f"[AI Agent Error] {provider_name} request failed (connection/timeout): {exc}"
            print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            return None

        if resp.status_code != 200:
            err_msg = f"[AI Agent Error] {provider_name} API error (HTTP {resp.status_code}): {resp.text}"
            print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            return None

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        tool_calls = msg.get("tool_calls", [])

        if not tool_calls:
            text = msg.get("content", "")
            if text:
                return markdown_to_telegram_html(text)
            err_msg = f"[AI Agent Error] {provider_name} response had no content and no tool_calls: {msg}"
            print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            return None

        # Append assistant tool calls turn
        messages.append(msg)
        for tc in tool_calls:
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            try:
                fn_args = json.loads(fn.get("arguments", "{}"))
            except Exception:
                fn_args = {}

            res = await execute_tool(fn_name, fn_args, db_path=db_path)
            last_tool_name = fn_name
            last_tool_result = res
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": json.dumps(res),
            })

        payload["messages"] = messages

    if last_tool_result:
        return format_tool_result_summary(last_tool_name, last_tool_result)
    return None


async def _call_groq(
    prompt: str,
    history: list[dict[str, str]],
    api_key: str,
    client: httpx.AsyncClient,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Call Groq REST API using high-performance open models (e.g. openai/gpt-oss-120b)."""
    base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    return await _call_openai_compatible(
        prompt=prompt,
        history=history,
        api_key=api_key,
        client=client,
        base_url=base_url,
        model=model,
        provider_name="Groq",
        db_path=db_path,
    )


async def _call_openai(
    prompt: str,
    history: list[dict[str, str]],
    api_key: str,
    client: httpx.AsyncClient,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Call OpenAI REST API with multi-turn tool calling."""
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return await _call_openai_compatible(
        prompt=prompt,
        history=history,
        api_key=api_key,
        client=client,
        base_url=base_url,
        model=model,
        provider_name="OpenAI",
        db_path=db_path,
    )


# Public Interface


async def ask_ai_agent(
    prompt: str,
    chat_id: str | int,
    db_path: Optional[Path] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """Ask the conversational AI agent a question, returning formatted HTML reply.

    Uses smart provider shifting:
    1. Primary: Gemini (if GEMINI_API_KEY is configured).
    2. Secondary: Groq (if GROQ_API_KEY is configured and Gemini fails or is unconfigured).
    3. Tertiary: OpenAI (if OPENAI_API_KEY is configured).
    4. Local Fallback: Rule-based deterministic NLP engine.
    """
    history = _chat_manager.get_history(chat_id)

    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    detected = []
    if gemini_key:
        detected.append("Gemini")
    if groq_key:
        detected.append("Groq")
    if openai_key:
        detected.append("OpenAI")

    if detected:
        print(f"[AI Agent] Available LLM providers: {', '.join(detected)} for prompt: '{prompt}'")
        logger.info("Available LLM providers: %s", ", ".join(detected))
    else:
        print(f"[AI Agent] Neither GEMINI_API_KEY nor GROQ_API_KEY nor OPENAI_API_KEY detected (using rule-based fallback) for prompt: '{prompt}'")
        logger.info("No LLM keys detected; using rule-based fallback")

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=45.0)
        own_client = True

    try:
        response: Optional[str] = None

        primary_pref = os.getenv("PRIMARY_LLM_PROVIDER", "gemini").strip().lower()

        if primary_pref == "groq" and groq_key:
            # 1. Primary: Groq (ultra-fast ~2s LPU inference)
            print(f"[AI Agent] Attempting primary provider: Groq...")
            response = await _call_groq(prompt, history, groq_key, client, db_path=db_path)

            # 2. Smart shift to Gemini if Groq failed
            if not response and gemini_key:
                print(f"[AI Agent] [Shift] Smart shifting to Gemini (Groq unavailable or failed)...")
                logger.info("Smart shifting to Gemini")
                response = await _call_gemini(prompt, history, gemini_key, client, db_path=db_path)

            # 3. Tertiary: OpenAI
            if not response and openai_key:
                print(f"[AI Agent] [Shift] Shifting to OpenAI provider...")
                logger.info("Shifting to OpenAI provider")
                response = await _call_openai(prompt, history, openai_key, client, db_path=db_path)
        else:
            # Default primary: Gemini (with fast gemini-3.1-flash-lite and smart shifting)
            # 1. Primary: Gemini
            if gemini_key:
                print(f"[AI Agent] Attempting primary provider: Gemini...")
                response = await _call_gemini(prompt, history, gemini_key, client, db_path=db_path)

            # 2. Smart shift to Groq if Gemini failed or was unconfigured
            if not response and groq_key:
                if gemini_key:
                    print(f"[AI Agent] [Shift] Smart shifting to Groq (Gemini unavailable or failed)...")
                    logger.info("Smart shifting to Groq")
                else:
                    print(f"[AI Agent] Attempting provider: Groq...")
                    logger.info("Attempting provider: Groq")
                response = await _call_groq(prompt, history, groq_key, client, db_path=db_path)

            # 3. Tertiary: OpenAI
            if not response and openai_key:
                print(f"[AI Agent] [Shift] Shifting to OpenAI provider...")
                logger.info("Shifting to OpenAI provider")
                response = await _call_openai(prompt, history, openai_key, client, db_path=db_path)

        # 4. Final: Deterministic NLP fallback
        if not response:
            if detected:
                print(f"[AI Agent] [Notice] All configured LLMs failed; falling back to rule-based NLP engine")
                logger.warning("All LLMs failed; falling back to rule-based engine")
            response = await _fallback_response(prompt, db_path=db_path)

        # Update memory on success
        _chat_manager.add_turn(chat_id, "user", prompt)
        _chat_manager.add_turn(chat_id, "assistant", response)
        return response

    except Exception as exc:
        err_msg = f"[AI Agent Error] Exception during ask_ai_agent: {exc}"
        print(err_msg, file=sys.stderr)
        logger.error(err_msg)
        return await _fallback_response(prompt, db_path=db_path)

    finally:
        if own_client:
            await client.aclose()

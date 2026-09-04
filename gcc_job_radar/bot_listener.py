"""Interactive Telegram bot listener with command handlers and authentication."""

import asyncio
import html
import logging
import os
from pathlib import Path
from typing import Any, Optional
import httpx
from rich.console import Console
from rich.panel import Panel

from gcc_job_radar import __version__
from gcc_job_radar.ai_agent import ask_ai_agent, clear_chat_history
from gcc_job_radar.config import COMPANIES
from gcc_job_radar.db import filter_new_jobs, get_latest_jobs, get_stats, record_jobs
from gcc_job_radar.engine import scan_all_companies
from gcc_job_radar.models import CompanyConfig, JobPosting

logger = logging.getLogger(__name__)
console = Console(highlight=False)

# Debounce & lock flags to prevent duplicate simultaneous or re-delivered /scan executions
_is_scanning: bool = False
_last_scan_timestamp: float = 0.0


async def send_telegram_chat_action(
    bot_token: str, chat_id: str | int, client: httpx.AsyncClient, action: str = "typing"
) -> bool:
    """Send a chat action (e.g. typing) to Telegram."""
    url = f"https://api.telegram.org/bot{bot_token}/sendChatAction"
    payload = {"chat_id": chat_id, "action": action}
    try:
        resp = await client.post(url, json=payload)
        return resp.status_code == 200
    except Exception as exc:
        logger.debug("Error sending chat action: %s", exc)
        return False


async def send_telegram_reply(
    bot_token: str, chat_id: str | int, text: str, client: httpx.AsyncClient
) -> bool:
    """Send an HTML-formatted message to the specified Telegram chat."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = await client.post(url, json=payload)
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("Error sending Telegram reply: %s", exc)
        return False



def format_jobs_html(jobs: list[JobPosting | dict[str, Any]], title: str) -> str:
    """Format a list of jobs into clean Telegram HTML."""
    if not jobs:
        return f"ℹ️ <b>{html.escape(title)}</b>\n\nNo matching entry-level roles found."

    msg = f"🚀 <b>{html.escape(title)} ({len(jobs)})</b>\n\n"
    for idx, item in enumerate(jobs, start=1):
        if isinstance(item, JobPosting):
            company = item.company
            pos_title = item.title
            location = item.location
            ats = item.provider.value.upper()
            date = item.published_date or "Active"
            url = str(item.apply_url)
        else:
            company = item.get("company", "")
            pos_title = item.get("title", "")
            location = item.get("location", "")
            ats = str(item.get("provider", "")).upper()
            date = item.get("published_date") or "Active"
            url = item.get("apply_url", "")

        msg += (
            f"<b>{idx}. {html.escape(company)}</b>\n"
            f"💼 {html.escape(pos_title)}\n"
            f"📍 {html.escape(location)} ({ats}) • 📅 {html.escape(date)}\n"
            f"🔗 <a href=\"{url}\">Apply on ATS</a>\n\n"
        )
    return msg.strip()


async def handle_command(
    command_text: str,
    chat_id: str | int,
    bot_token: str,
    allowed_chat_id: str,
    client: httpx.AsyncClient,
    db_path: Optional[Path] = None,
) -> None:
    """Handle incoming Telegram command if chat_id is authorized."""
    if str(chat_id).strip() != str(allowed_chat_id).strip():
        logger.warning("Unauthorized access attempt from chat_id: %s", chat_id)
        await send_telegram_reply(
            bot_token,
            chat_id,
            "⛔ <b>Access Denied</b>: Your Telegram account is not authorized to control this GCC Job Radar bot.",
            client,
        )
        return

    text = command_text.strip()
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/start", "/help", "/list"):
        help_text = (
            "📋 <b>GCC Radar Commands:</b>\n"
            "• <code>/scan</code> — Scan all 150+ GCCs\n"
            "• <code>/check &lt;name&gt;</code> — Check single company\n"
            "• <code>/latest</code> — Show 5 recent openings\n"
            "• <code>/stats</code> — View database stats\n"
            "• <code>/clear</code> — Clear AI conversation memory\n"
            "• <code>/list</code> — Show this menu\n\n"
            "💬 <i>Or ask any question in plain text to chat with the AI assistant!</i>"
        )
        await send_telegram_reply(bot_token, chat_id, help_text, client)


    elif cmd == "/stats":
        stats = get_stats(db_path)
        total = stats.get("total_tracked", 0)
        first_seen = stats.get("first_recorded") or "N/A"
        last_seen = stats.get("last_active") or "N/A"
        breakdown = stats.get("company_breakdown", {})

        stats_text = (
            "📊 <b>GCC Job Radar - Database Statistics</b>\n\n"
            f"• <b>Total Roles Tracked:</b> {total}\n"
            f"• <b>First Recorded:</b> {first_seen}\n"
            f"• <b>Last Active:</b> {last_seen}\n\n"
        )
        if breakdown:
            stats_text += "<b>Top Tracked Companies:</b>\n"
            for comp, count in list(breakdown.items())[:8]:
                stats_text += f"• {html.escape(comp)}: {count}\n"
        else:
            stats_text += "<i>No postings stored yet.</i>"

        await send_telegram_reply(bot_token, chat_id, stats_text, client)

    elif cmd == "/latest":
        recent_jobs = get_latest_jobs(limit=5, db_path=db_path)
        reply = format_jobs_html(recent_jobs, "Latest Discovered Openings")
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd == "/check":
        if not arg:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⚠️ Please provide a company name, e.g. <code>/check celonis</code> or <code>/check databricks</code>",
                client,
            )
            return

        query = arg.lower()
        matched_companies = [
            c for c in COMPANIES if query in c.name.lower() or query in c.board_token.lower()
        ]
        if not matched_companies:
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"❌ Company matching '<code>{html.escape(arg)}</code>' not found in registry.",
                client,
            )
            return

        await send_telegram_reply(
            bot_token,
            chat_id,
            f"🔍 Scanning <b>{html.escape(matched_companies[0].name)}</b> ATS...",
            client,
        )
        jobs = await scan_all_companies(companies=matched_companies)
        record_jobs(jobs, db_path)
        reply = format_jobs_html(jobs, f"Results for {matched_companies[0].name}")
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd == "/scan":
        global _is_scanning, _last_scan_timestamp
        import time

        now = time.time()
        if _is_scanning:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⏳ <i>A scan is currently already running. Please wait for it to complete.</i>",
                client,
            )
            return

        # Debounce: if a scan finished less than 10 seconds ago (e.g. duplicate webhook/update)
        if now - _last_scan_timestamp < 10:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⚡ <i>A scan was just completed seconds ago. Use <code>/latest</code> to see current findings or try again in a few moments.</i>",
                client,
            )
            return

        _is_scanning = True
        try:
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"⚡ Initiating scan across all <b>{len(COMPANIES)}</b> foreign GCCs & tech centers in India...",
                client,
            )
            jobs = await scan_all_companies(companies=COMPANIES)
            new_jobs, _ = filter_new_jobs(jobs, db_path)
            record_jobs(jobs, db_path)

            if jobs:
                reply = format_jobs_html(jobs, "Verified Active Entry-Level Openings")
            else:
                reply = (
                    "ℹ️ <b>Scan Complete</b>\n\n"
                    "No entry-level tech roles currently open matching strict criteria across all 150+ tracked boards."
                )
            await send_telegram_reply(bot_token, chat_id, reply, client)
        finally:
            _is_scanning = False
            _last_scan_timestamp = time.time()

    elif cmd in ("/clear", "/reset"):
        clear_chat_history(chat_id)
        await send_telegram_reply(
            bot_token,
            chat_id,
            "🧹 <b>Chat history cleared.</b> How can I help you find GCC roles?",
            client,
        )

    else:
        await send_telegram_reply(
            bot_token,
            chat_id,
            "❓ Unknown command. Send <code>/help</code> to see available commands.",
            client,
        )



async def run_bot_listener(
    bot_token: str,
    allowed_chat_id: str,
    db_path: Optional[Path] = None,
    poll_timeout: int = 20,
) -> None:
    """Run long-polling loop to listen for Telegram commands."""
    console.print(
        Panel(
            f"[bold white]Authorized Chat ID:[/bold white] [bold cyan]{allowed_chat_id}[/bold cyan]\n"
            f"[bold white]Target Boards:[/bold white] [green]{len(COMPANIES)} GCCs[/green]\n"
            f"[bold white]Mode:[/bold white] Long-polling via Telegram Bot API\n"
            f"[dim]Press Ctrl+C to stop the bot listener.[/dim]",
            title="[bold cyan]🤖 GCC Job Radar - Telegram Bot Active[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    offset: Optional[int] = None
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"

    async with httpx.AsyncClient(timeout=poll_timeout + 10.0) as client:
        while True:
            try:
                params: dict[str, Any] = {"timeout": poll_timeout}
                if offset is not None:
                    params["offset"] = offset

                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    logger.warning("Telegram getUpdates returned status %s: %s", resp.status_code, resp.text)
                    await asyncio.sleep(3)
                    continue

                data = resp.json()
                updates = data.get("result", [])

                for update in updates:
                    offset = update["update_id"] + 1
                    message = update.get("message") or update.get("edited_message")
                    if not message:
                        continue

                    chat = message.get("chat", {})
                    chat_id = chat.get("id")
                    text = message.get("text") or ""

                    if text.startswith("/"):
                        console.print(f"[cyan]Received command:[/cyan] [bold]{text}[/bold] from chat_id [yellow]{chat_id}[/yellow]")
                        await handle_command(
                            command_text=text,
                            chat_id=chat_id,
                            bot_token=bot_token,
                            allowed_chat_id=allowed_chat_id,
                            client=client,
                            db_path=db_path,
                        )
                    elif text.strip():
                        if str(chat_id).strip() != str(allowed_chat_id).strip():
                            logger.warning("Unauthorized access attempt from chat_id: %s", chat_id)
                            await send_telegram_reply(
                                bot_token,
                                chat_id,
                                "⛔ <b>Access Denied</b>: Your Telegram account is not authorized to control this GCC Job Radar bot.",
                                client,
                            )
                            continue

                        console.print(f"[green]AI Query:[/green] [bold]{text.strip()}[/bold] from chat_id [yellow]{chat_id}[/yellow]")
                        await send_telegram_chat_action(bot_token, chat_id, client, "typing")
                        ai_reply = await ask_ai_agent(text.strip(), chat_id=chat_id, db_path=db_path, client=client)
                        await send_telegram_reply(bot_token, chat_id, ai_reply, client)

            except asyncio.CancelledError:
                break
            except httpx.ConnectTimeout:
                logger.error(
                    "Connection timeout connecting to api.telegram.org. If your local ISP blocks Telegram API, enable WARP/VPN or configure a proxy."
                )
                await asyncio.sleep(5)
            except Exception as exc:
                logger.error("Error in bot polling loop: %s", exc)
                await asyncio.sleep(2)


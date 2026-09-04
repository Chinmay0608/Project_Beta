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
from gcc_job_radar.config import COMPANIES
from gcc_job_radar.db import filter_new_jobs, get_latest_jobs, get_stats, record_jobs
from gcc_job_radar.engine import scan_all_companies
from gcc_job_radar.models import CompanyConfig, JobPosting

logger = logging.getLogger(__name__)
console = Console(highlight=False)


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

    if cmd in ("/start", "/help"):
        help_text = (
            f"⚡ <b>GCC Job Radar Bot (v{__version__})</b>\n\n"
            "Here are the available commands:\n"
            "• <code>/scan</code> - Run live scan across all 70+ configured GCCs & tech centers\n"
            "• <code>/check &lt;company&gt;</code> - Scan a specific company (e.g. <code>/check celonis</code>)\n"
            "• <code>/stats</code> - View historical database tracking statistics\n"
            "• <code>/latest</code> - View the 5 most recently recorded postings\n"
            "• <code>/help</code> - Show this menu"
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
                "No entry-level tech roles currently open matching strict criteria across all 70+ tracked boards."
            )
        await send_telegram_reply(bot_token, chat_id, reply, client)

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

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in bot polling loop: %s", exc)
                await asyncio.sleep(2)

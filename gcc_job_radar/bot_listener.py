"""Interactive Telegram bot listener with command handlers and authentication."""

import asyncio
import html
import logging
from pathlib import Path
import re
from typing import Any, Optional
from dotenv import load_dotenv
import httpx
from rich.console import Console

# Automatically load environment variables from .env if present
load_dotenv()
from rich.panel import Panel

from gcc_job_radar.ai_agent import ask_ai_agent, clear_chat_history
from gcc_job_radar.config import COMPANIES
from gcc_job_radar.db import (
    filter_new_jobs,
    find_jobs_by_selector,
    get_job_by_id,
    get_jobs_by_status,
    get_latest_jobs,
    get_stats,
    mark_job_status,
    record_jobs,
)
from gcc_job_radar.scanner import scan_all_companies
from gcc_job_radar.link_resolver import resolve_effective_apply_url
from gcc_job_radar.models import JobPosting
from gcc_job_radar.notifier import build_job_inline_keyboard

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
    try:
        resp = await client.post(url, json={"chat_id": chat_id, "action": action}, timeout=5.0)
        return resp.status_code == 200
    except Exception as exc:
        logger.debug("Failed to send chat action: %s", exc)
        return False


async def send_telegram_reply(
    bot_token: str,
    chat_id: str | int,
    text: str,
    client: httpx.AsyncClient,
    reply_markup: Optional[dict[str, Any]] = None,
) -> bool:
    """Send an HTML-formatted reply to a Telegram chat with optional InlineKeyboardMarkup."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = await client.post(url, json=payload, timeout=10.0)
        if resp.status_code != 200:
            logger.warning("Failed to send Telegram reply: %s (status %s)", resp.text, resp.status_code)
            return False
        return True
    except Exception as exc:
        logger.warning("Error sending Telegram reply: %s", exc)
        return False


def format_jobs_html(jobs: list[JobPosting | dict[str, Any]], title: str) -> str:
    """Format a list of JobPostings into an HTML message for Telegram."""
    if not jobs:
        return f"ℹ️ <b>{html.escape(title)}</b>\n\nNo matching positions found."

    msg = f"🚀 <b>{html.escape(title)} ({len(jobs)})</b>\n\n"
    for idx, item in enumerate(jobs, start=1):
        if isinstance(item, JobPosting):
            company = item.company
            pos_title = item.title
            location = item.location
            ats = item.provider.value.upper()
            date = item.published_date or "Active"
        else:
            company = item.get("company", "")
            pos_title = item.get("title", "")
            location = item.get("location", "")
            ats = str(item.get("provider", "")).upper()
            date = item.get("published_date") or "Active"

        effective_url, _, label = resolve_effective_apply_url(item)

        msg += (
            f"<b>{idx}. {html.escape(company)}</b>\n"
            f"💼 {html.escape(pos_title)}\n"
            f"📍 {html.escape(location)} ({ats}) • 📅 {html.escape(date)}\n"
            f"🔗 <a href=\"{html.escape(effective_url)}\">{html.escape(label)}</a>\n\n"
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
            "• <code>/applied</code> — View your applied roles\n"
            "• <code>/stats</code> — View database stats\n"
            "• <code>/apply &lt;id/company&gt; [-n note]</code> — Mark job(s) as APPLIED\n"
            "• <code>/dismiss &lt;id(s)/company&gt;</code> — Dismiss job(s)\n"
            "• <code>/restore &lt;id(s)/company&gt;</code> — Restore job(s) to NEW\n"
            "• <code>/clear</code> — Clear AI conversation memory\n"
            "• <code>/list</code> — Show this menu\n\n"
            "💡 <i>Tip: Pass IDs (e.g. <code>/dismiss 1, 2, 4</code>) or company names (e.g. <code>/apply uipath, celonis</code>).</i>\n"
            "💬 <i>Or ask any question in plain text to chat with the AI assistant!</i>"
        )
        await send_telegram_reply(bot_token, chat_id, help_text, client)


    elif cmd == "/stats":
        stats = get_stats(db_path)
        total = stats.get("total_tracked", 0)
        active = stats.get("active_count", 0)
        needs_resolve = stats.get("needs_resolve_count", 0)
        applied = stats.get("applied_count", 0)
        dismissed = stats.get("dismissed_count", 0)
        first_seen = stats.get("first_recorded") or "N/A"
        last_seen = stats.get("last_active") or "N/A"
        breakdown = stats.get("company_breakdown", {})

        stats_text = (
            "📊 <b>GCC Job Radar - Database Statistics</b>\n\n"
            f"• <b>Total Roles Tracked:</b> {total}\n"
            f"• <b>Active (New):</b> {active}\n"
            f"• <b>Needs Resolve:</b> {needs_resolve}\n"
            f"• <b>Applied:</b> {applied}\n"
            f"• <b>Dismissed:</b> {dismissed}\n"
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
        recent_jobs = get_latest_jobs(limit=5, status="NEW", db_path=db_path)
        if not recent_jobs:
            reply = format_jobs_html([], "Latest Discovered Openings")
            await send_telegram_reply(bot_token, chat_id, reply, client)
        elif len(recent_jobs) == 1:
            reply = format_jobs_html(recent_jobs, "Latest Discovered Openings")
            markup = build_job_inline_keyboard(recent_jobs[0])
            await send_telegram_reply(bot_token, chat_id, reply, client, reply_markup=markup)
        else:
            reply = format_jobs_html(recent_jobs, "Latest Discovered Openings")
            keyboard = []
            for idx, rj in enumerate(recent_jobs, start=1):
                jid = rj.get("numeric_id") or rj.get("id")
                eff_url, _, _ = resolve_effective_apply_url(rj)
                comp = (rj.get("company") or "")[:12]
                keyboard.append([
                    {"text": f"Apply #{idx} ({comp})", "url": str(eff_url)},
                    {"text": f"Dismiss #{idx}", "callback_data": f"dismiss:{jid}"},
                    {"text": f"Applied #{idx}", "callback_data": f"applied:{jid}"},
                ])
            await send_telegram_reply(
                bot_token, chat_id, reply, client, reply_markup={"inline_keyboard": keyboard}
            )

    elif cmd in ("/applied", "/applications"):
        applied_jobs = get_jobs_by_status("APPLIED", db_path=db_path)
        if not applied_jobs:
            reply = (
                "ℹ️ <b>No Applied Roles Recorded</b>\n\n"
                "You haven't marked any roles as applied yet.\n"
                "Use <code>/apply &lt;id or company&gt;</code> to track your applications!"
            )
        else:
            reply = format_jobs_html(applied_jobs, f"Your Applied Listings ({len(applied_jobs)})")
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

    elif cmd in ("/dismiss", "/hide"):
        if not arg:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⚠️ <b>Usage:</b> <code>/dismiss &lt;id(s) or company&gt;</code>\n\n"
                "Examples:\n"
                "• <code>/dismiss 1</code>\n"
                "• <code>/dismiss 1, 2, 4</code>\n"
                "• <code>/dismiss Devmani Traders</code>",
                client,
            )
            return

        jobs = find_jobs_by_selector(arg, db_path=db_path)
        if not jobs:
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"❌ No jobs found matching '<code>{html.escape(arg)}</code>'.\nUse <code>/latest</code> to check active job IDs.",
                client,
            )
            return

        dismissed_list = []
        for j in jobs:
            rowid = j.get("numeric_id") or j.get("id")
            mark_job_status(job_id=rowid, status="DISMISSED", db_path=db_path)
            dismissed_list.append(
                f"• <b>#{rowid}. {html.escape(j.get('company', 'Unknown'))}</b> — {html.escape(j.get('title', 'Role'))}"
            )

        reply = (
            f"🗑️ <b>Dismissed {len(dismissed_list)} Job(s):</b>\n\n"
            + "\n".join(dismissed_list)
            + "\n\n<i>These postings will no longer appear in scans or active listings. Use <code>/restore &lt;id&gt;</code> to undo.</i>"
        )
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd == "/apply":
        if not arg:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⚠️ <b>Usage:</b> <code>/apply &lt;id(s) or company&gt; [-n optional note]</code>\n\n"
                "Examples:\n"
                "• <code>/apply 2</code>\n"
                "• <code>/apply BT Group</code>\n"
                "• <code>/apply 2 -n Applied via official ATS</code>",
                client,
            )
            return

        notes = None
        target_selector = arg
        m_note = re.search(r"(?:-n|--notes)\s+(.+)$", arg, flags=re.IGNORECASE)
        if m_note:
            notes = m_note.group(1).strip().strip('"').strip("'")
            target_selector = arg[: m_note.start()].strip()

        jobs = find_jobs_by_selector(target_selector, db_path=db_path)
        if not jobs:
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"❌ No jobs found matching '<code>{html.escape(target_selector)}</code>'.\nUse <code>/latest</code> to check active job IDs.",
                client,
            )
            return

        applied_list = []
        for j in jobs:
            rowid = j.get("numeric_id") or j.get("id")
            mark_job_status(job_id=rowid, status="APPLIED", notes=notes, db_path=db_path)
            effective_url, _, label = resolve_effective_apply_url(j)
            link_html = f' • <a href="{html.escape(str(effective_url))}">Apply Link</a>' if effective_url else ""
            applied_list.append(
                f"• <b>#{rowid}. {html.escape(j.get('company', 'Unknown'))}</b> — {html.escape(j.get('title', 'Role'))}{link_html}"
            )

        notes_msg = f"\n📝 <b>Notes:</b> <i>{html.escape(notes)}</i>" if notes else ""
        reply = (
            f"✅ <b>Marked as APPLIED ({len(applied_list)}):</b>\n\n"
            + "\n".join(applied_list)
            + notes_msg
            + "\n\n<i>Application timestamp recorded in database. Good luck!</i>"
        )
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd in ("/restore", "/undismiss"):
        if not arg:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⚠️ <b>Usage:</b> <code>/restore &lt;id(s) or company&gt;</code>\n\n"
                "Examples:\n"
                "• <code>/restore 1</code>\n"
                "• <code>/restore 1, 2</code>\n"
                "• <code>/restore Devmani Traders</code>",
                client,
            )
            return

        jobs = find_jobs_by_selector(arg, db_path=db_path)
        if not jobs:
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"❌ No jobs found matching '<code>{html.escape(arg)}</code>'.",
                client,
            )
            return

        restored_list = []
        for j in jobs:
            rowid = j.get("numeric_id") or j.get("id")
            mark_job_status(job_id=rowid, status="NEW", db_path=db_path)
            restored_list.append(
                f"• <b>#{rowid}. {html.escape(j.get('company', 'Unknown'))}</b> — {html.escape(j.get('title', 'Role'))}"
            )

        reply = (
            f"🔄 <b>Restored {len(restored_list)} Job(s) to NEW:</b>\n\n"
            + "\n".join(restored_list)
            + "\n\n<i>These postings will now appear in scans and active listings again.</i>"
        )
        await send_telegram_reply(bot_token, chat_id, reply, client)

    else:
        await send_telegram_reply(
            bot_token,
            chat_id,
            "❓ Unknown command. Send <code>/help</code> to see available commands.",
            client,
        )



async def handle_callback_query(
    callback_query: dict[str, Any],
    bot_token: str,
    allowed_chat_id: str,
    client: httpx.AsyncClient,
    db_path: Optional[Path] = None,
) -> bool:
    """Handle incoming Telegram callback queries from interactive inline keyboards."""
    cb_id = callback_query.get("id")
    from_user = callback_query.get("from", {})
    user_id = str(from_user.get("id", "")).strip()
    data = (callback_query.get("data") or "").strip()
    message = callback_query.get("message", {})
    chat = message.get("chat", {})
    chat_id = str(chat.get("id") or user_id).strip()
    message_id = message.get("message_id")

    # 1. Access authorization
    if chat_id != str(allowed_chat_id).strip() and user_id != str(allowed_chat_id).strip():
        logger.warning("Unauthorized callback attempt from user_id: %s, chat_id: %s", user_id, chat_id)
        if cb_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                    json={"callback_query_id": cb_id, "text": "⛔ Access Denied.", "show_alert": True},
                    timeout=5.0,
                )
            except Exception as exc:
                logger.debug("Failed to answer unauthorized callback: %s", exc)
        return False

    # 2. Handle Dismiss: "dismiss:{job_id}"
    if data.startswith("dismiss:"):
        job_id = data.split("dismiss:", 1)[1].strip()
        mark_job_status(job_id=job_id, status="DISMISSED", db_path=db_path)
        target_job = get_job_by_id(job_id, db_path=db_path)

        comp = target_job["company"] if target_job else "Job"
        pos_title = target_job["title"] if target_job else f"#{job_id}"
        loc = target_job.get("location", "India") if target_job else ""

        updated_text = (
            f"❌ <b>[DISMISSED]</b> <s>{html.escape(comp)} — {html.escape(pos_title)}</s>\n"
            f"📍 <s>{html.escape(loc)}</s>\n"
            f"<i>Marked as DISMISSED from active tracker.</i>"
        )

        if message_id and chat_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": updated_text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                        "reply_markup": {"inline_keyboard": []},
                    },
                    timeout=5.0,
                )
            except Exception as exc:
                logger.warning("Failed to edit message text on dismiss: %s", exc)

        if cb_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                    json={
                        "callback_query_id": cb_id,
                        "text": f"Dismissed: {comp} - {pos_title}",
                    },
                    timeout=5.0,
                )
            except Exception as exc:
                logger.warning("Failed to answer callback query on dismiss: %s", exc)

        return True

    # 3. Handle Applied: "applied:{job_id}"
    elif data.startswith("applied:"):
        job_id = data.split("applied:", 1)[1].strip()
        mark_job_status(job_id=job_id, status="APPLIED", db_path=db_path)
        target_job = get_job_by_id(job_id, db_path=db_path)

        comp = target_job["company"] if target_job else "Job"
        pos_title = target_job["title"] if target_job else f"#{job_id}"
        loc = target_job.get("location", "India") if target_job else ""

        updated_text = (
            f"✅ <b>[APPLIED]</b> <b>{html.escape(comp)}</b> — {html.escape(pos_title)}\n"
            f"📍 {html.escape(loc)}\n"
            f"📅 <i>Application recorded in database • Best of luck!</i>"
        )

        if message_id and chat_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": updated_text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                        "reply_markup": {"inline_keyboard": []},
                    },
                    timeout=5.0,
                )
            except Exception as exc:
                logger.warning("Failed to edit message text on applied: %s", exc)

        if cb_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                    json={
                        "callback_query_id": cb_id,
                        "text": f"Marked Applied: {comp}! Good luck!",
                    },
                    timeout=5.0,
                )
            except Exception as exc:
                logger.warning("Failed to answer callback query on applied: %s", exc)

        return True

    return False


async def run_bot_listener(
    bot_token: str,
    allowed_chat_id: str,
    db_path: Optional[Path] = None,
    poll_timeout: int = 20,
) -> None:
    """Run long-polling loop to listen for Telegram commands and callback queries."""
    logger.info("Telegram Bot Active. Authorized Chat ID: %s, Target Boards: %d", allowed_chat_id, len(COMPANIES))
    try:
        console.print(
            Panel(
                f"[bold white]Authorized Chat ID:[/bold white] [bold cyan]{allowed_chat_id}[/bold cyan]\n"
                f"[bold white]Target Boards:[/bold white] [green]{len(COMPANIES)} GCCs[/green]\n"
                f"[bold white]Mode:[/bold white] Long-polling via Telegram Bot API\n"
                f"[dim]Press Ctrl+C to stop the bot listener.[/dim]",
                title="[bold cyan]GCC Job Radar - Telegram Bot Active[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
    except Exception:
        pass

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

                    # Check for callback queries (interactive inline keyboard actions)
                    if "callback_query" in update:
                        cb_query = update["callback_query"]
                        logger.info(
                            "Received callback: %s from user %s",
                            cb_query.get("data"),
                            cb_query.get("from", {}).get("id"),
                        )
                        try:
                            console.print(
                                f"[magenta]Received callback:[/magenta] [bold]{cb_query.get('data')}[/bold] "
                                f"from user [yellow]{cb_query.get('from', {}).get('id')}[/yellow]"
                            )
                        except Exception:
                            pass
                        await handle_callback_query(
                            callback_query=cb_query,
                            bot_token=bot_token,
                            allowed_chat_id=allowed_chat_id,
                            client=client,
                            db_path=db_path,
                        )
                        continue

                    message = update.get("message") or update.get("edited_message")
                    if not message:
                        continue

                    chat = message.get("chat", {})
                    chat_id = chat.get("id")
                    text = message.get("text") or ""

                    if text.startswith("/"):
                        logger.info("Received command: %s from chat_id %s", text, chat_id)
                        try:
                            console.print(f"[cyan]Received command:[/cyan] [bold]{text}[/bold] from chat_id [yellow]{chat_id}[/yellow]")
                        except Exception:
                            pass
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

                        logger.info("AI Query: %s from chat_id %s", text.strip(), chat_id)
                        try:
                            console.print(f"[green]AI Query:[/green] [bold]{text.strip()}[/bold] from chat_id [yellow]{chat_id}[/yellow]")
                        except Exception:
                            pass
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


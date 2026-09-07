"""Webhook notification dispatchers for Discord and Telegram."""

import html
import logging
import os
from typing import Any, Optional
import httpx
from rich.console import Console

from gcc_job_radar.link_resolver import resolve_effective_apply_url
from gcc_job_radar.models import JobPosting

logger = logging.getLogger(__name__)
console = Console(highlight=False)

DISCORD_EMBED_COLOR = 0x00FF88  # Bright neon green
MAX_DISCORD_FIELDS_PER_EMBED = 25
DISCORD_CHUNK_SIZE = 5


async def send_discord_notification(
    webhook_url: str, new_jobs: list[JobPosting], client: httpx.AsyncClient
) -> bool:
    """Send formatted Discord webhook embeds for newly detected job postings."""
    webhook_url = webhook_url.strip() if webhook_url else ""
    if not webhook_url or not new_jobs:
        return False

    success = True
    # Send in chunks of 5 to stay well within Discord message size limits
    for i in range(0, len(new_jobs), DISCORD_CHUNK_SIZE):
        chunk = new_jobs[i : i + DISCORD_CHUNK_SIZE]
        embeds = []

        for job in chunk:
            effective_url, _, label = resolve_effective_apply_url(job)
            embed = {
                "title": f"🚀 {job.company} - {job.title}",
                "url": str(effective_url),
                "color": DISCORD_EMBED_COLOR,
                "fields": [
                    {"name": "🏢 Company", "value": job.company, "inline": True},
                    {"name": "💼 Position", "value": job.title, "inline": True},
                    {"name": "📍 Location", "value": job.location, "inline": True},
                    {"name": "📡 Source", "value": job.provider.value.upper(), "inline": True},
                    {"name": "📅 Date", "value": job.published_date or "Active", "inline": True},
                    {
                        "name": "🔗 Apply Link",
                        "value": f"[{label}]({effective_url})",
                        "inline": False,
                    },
                ],
                "footer": {
                    "text": "GCC Job Radar • India Tech Tracker"
                },
            }
            embeds.append(embed)

        payload = {
            "content": "⚡ **New GCC Entry-Level Opening(s) Detected!**" if i == 0 else "",
            "embeds": embeds,
        }

        try:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code not in (200, 204):
                logger.warning("Discord webhook returned status %s: %s", resp.status_code, resp.text)
                success = False
        except Exception as exc:
            logger.warning("Failed to send Discord webhook: %s", exc)
            success = False

    return success


def build_job_inline_keyboard(job: JobPosting | dict[str, Any]) -> dict[str, Any]:
    """Build Telegram InlineKeyboardMarkup for an individual job posting card.

    Requirements:
    - Button 1 (URL): "Apply" pointing to apply_url.
    - Button 2 (URL, conditional): If status is 'NEEDS_RESOLVE' or direct_search_url is present,
      add "Search Direct ATS" pointing to direct_search_url.
    - Button 3 (Callback): "Dismiss" (callback_data="dismiss:{job_id}").
    - Button 4 (Callback): "Applied" (callback_data="applied:{job_id}").
    """
    if isinstance(job, JobPosting):
        job_id = job.numeric_id if job.numeric_id is not None else job.id
        effective_url, _, _ = resolve_effective_apply_url(job)
        apply_url = str(effective_url) if effective_url else str(job.apply_url)
        direct_search_url = str(job.direct_search_url) if job.direct_search_url else None
        status = getattr(job, "status", "NEW")
    else:
        job_id = job.get("numeric_id") if job.get("numeric_id") is not None else job.get("id", "")
        effective_url, _, _ = resolve_effective_apply_url(job)
        apply_url = str(effective_url) if effective_url else str(job.get("apply_url", ""))
        direct_search_url = str(job.get("direct_search_url")) if job.get("direct_search_url") else None
        status = job.get("status", "NEW")

    # Row 1: URL Navigation Buttons
    row_1: list[dict[str, str]] = [{"text": "Apply", "url": apply_url}]
    if (status == "NEEDS_RESOLVE" or direct_search_url) and direct_search_url:
        row_1.append({"text": "Search Direct ATS", "url": direct_search_url})

    # Row 2: Interactive Callback Buttons
    row_2: list[dict[str, str]] = [
        {"text": "Dismiss", "callback_data": f"dismiss:{job_id}"},
        {"text": "Applied", "callback_data": f"applied:{job_id}"},
    ]

    return {
        "inline_keyboard": [row_1, row_2]
    }


def format_job_card_html(job: JobPosting | dict[str, Any]) -> str:
    """Format an individual job posting card for Telegram alert."""
    if isinstance(job, JobPosting):
        company = job.company
        pos_title = job.title
        location = job.location
        ats = job.provider.value.upper()
        date = job.published_date or "Active"
    else:
        company = job.get("company", "Unknown")
        pos_title = job.get("title", "Role")
        location = job.get("location", "India")
        prov = job.get("provider", "")
        ats = getattr(prov, "value", str(prov)).upper()
        date = job.get("published_date") or "Active"

    effective_url, _, label = resolve_effective_apply_url(job)
    return (
        f"🚀 <b>{html.escape(company)}</b>\n"
        f"💼 {html.escape(pos_title)}\n"
        f"📍 {html.escape(location)} ({ats}) • 📅 {html.escape(date)}\n"
        f"🔗 <a href=\"{html.escape(str(effective_url))}\">{html.escape(label)}</a>"
    )


async def send_telegram_job_card(
    bot_token: str,
    chat_id: str | int,
    job: JobPosting | dict[str, Any],
    client: httpx.AsyncClient,
) -> bool:
    """Send an individual interactive job card with InlineKeyboardMarkup."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": str(chat_id),
        "text": format_job_card_html(job),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": build_job_inline_keyboard(job),
    }
    try:
        resp = await client.post(url, json=payload, timeout=10.0)
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("Failed to send Telegram job card: %s", exc)
        return False


async def send_telegram_notification(
    bot_token: str,
    chat_id: str,
    new_jobs: list[JobPosting],
    client: httpx.AsyncClient,
    send_as_cards: bool = False,
) -> bool:
    """Send formatted Telegram message via Bot API for newly detected postings."""
    bot_token = bot_token.strip() if bot_token else ""
    chat_id = chat_id.strip() if chat_id else ""
    if not bot_token or not chat_id or not new_jobs:
        return False

    if send_as_cards:
        all_ok = True
        for job in new_jobs:
            ok = await send_telegram_job_card(bot_token, chat_id, job, client)
            if not ok:
                all_ok = False
        return all_ok

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    success = True

    # Build HTML formatted text
    header = f"🚀 <b>New GCC Entry-Level Opening(s) Detected ({len(new_jobs)})!</b>\n\n"
    items_text = []

    for idx, job in enumerate(new_jobs, start=1):
        clean_company = html.escape(job.company)
        clean_title = html.escape(job.title)
        clean_location = html.escape(job.location)
        effective_url, _, label = resolve_effective_apply_url(job)

        item = (
            f"<b>{idx}. {clean_company}</b>\n"
            f"💼 {clean_title}\n"
            f"📍 {clean_location} ({job.provider.value.upper()})\n"
            f"🔗 <a href=\"{html.escape(effective_url)}\">{html.escape(label)}</a>\n"
        )
        items_text.append(item)

    # Telegram messages are limited to 4096 characters, chunk if needed
    message_chunks = []
    current_chunk = header

    for item in items_text:
        if len(current_chunk) + len(item) > 3800:
            message_chunks.append(current_chunk)
            current_chunk = item + "\n"
        else:
            current_chunk += item + "\n"

    if current_chunk:
        message_chunks.append(current_chunk)

    # Build interactive inline keyboard
    reply_markup: Optional[dict[str, Any]] = None
    if len(new_jobs) == 1:
        reply_markup = build_job_inline_keyboard(new_jobs[0])
    elif len(new_jobs) <= 8:
        # Multi-job compact inline keyboard
        keyboard_rows = []
        for idx, job in enumerate(new_jobs, start=1):
            job_id = job.numeric_id if job.numeric_id is not None else job.id
            effective_url, _, _ = resolve_effective_apply_url(job)
            apply_url = str(effective_url) if effective_url else str(job.apply_url)
            row = [
                {"text": f"Apply #{idx}", "url": apply_url},
                {"text": f"Dismiss #{idx}", "callback_data": f"dismiss:{job_id}"},
                {"text": f"Applied #{idx}", "callback_data": f"applied:{job_id}"},
            ]
            keyboard_rows.append(row)
        reply_markup = {"inline_keyboard": keyboard_rows}

    for chunk in message_chunks:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk.strip(),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("Telegram Bot API returned status %s: %s", resp.status_code, resp.text)
                success = False
        except Exception as exc:
            logger.warning("Failed to send Telegram notification: %s", exc)
            success = False

    return success


async def dispatch_notifications(
    new_jobs: list[JobPosting],
    discord_webhook: Optional[str] = None,
    telegram_token: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
    db_path: Optional[os.PathLike] = None,
) -> None:
    """Dispatch notifications to configured channels for new postings, preventing duplicates."""
    if not new_jobs:
        return

    from gcc_job_radar.db import filter_unalerted_jobs, record_dispatched_alerts

    # Fallback to environment variables
    discord_url = (discord_webhook or os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
    tg_token = (telegram_token or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    tg_chat = (telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not discord_url and not (tg_token and tg_chat):
        return

    async with httpx.AsyncClient(timeout=10.0) as client:
        if discord_url:
            # Filter out jobs already alerted to Discord
            discord_jobs = filter_unalerted_jobs(new_jobs, "discord", db_path)
            if discord_jobs:
                ok = await send_discord_notification(discord_url, discord_jobs, client)
                if ok:
                    record_dispatched_alerts(discord_jobs, "discord", db_path)
                    console.print(f"[bold green][+][/bold green] Sent Discord alert for {len(discord_jobs)} new posting(s).")
                else:
                    console.print("[bold red][!][/bold red] Failed to send Discord notification.")

        if tg_token and tg_chat:
            # Filter out jobs already alerted to Telegram
            telegram_jobs = filter_unalerted_jobs(new_jobs, "telegram", db_path)
            if telegram_jobs:
                ok = await send_telegram_notification(tg_token, tg_chat, telegram_jobs, client)
                if ok:
                    record_dispatched_alerts(telegram_jobs, "telegram", db_path)
                    console.print(f"[bold green][+][/bold green] Sent Telegram alert for {len(telegram_jobs)} new posting(s).")
                else:
                    console.print("[bold red][!][/bold red] Failed to send Telegram notification.")

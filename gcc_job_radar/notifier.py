"""Webhook notification dispatchers for Discord and Telegram."""

import html
import logging
import os
from typing import Optional
import httpx
from rich.console import Console

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
    if not webhook_url or not new_jobs:
        return False

    success = True
    # Send in chunks of 5 to stay well within Discord message size limits
    for i in range(0, len(new_jobs), DISCORD_CHUNK_SIZE):
        chunk = new_jobs[i : i + DISCORD_CHUNK_SIZE]
        embeds = []

        for job in chunk:
            embed = {
                "title": f"🚀 {job.company} - {job.title}",
                "url": str(job.apply_url),
                "color": DISCORD_EMBED_COLOR,
                "fields": [
                    {"name": "🏢 Company", "value": job.company, "inline": True},
                    {"name": "💼 Position", "value": job.title, "inline": True},
                    {"name": "📍 Location", "value": job.location, "inline": True},
                    {"name": "📡 Source", "value": job.provider.value.upper(), "inline": True},
                    {"name": "📅 Date", "value": job.published_date or "Active", "inline": True},
                    {
                        "name": "🔗 Apply Link",
                        "value": f"[Apply on ATS]({job.apply_url})",
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


async def send_telegram_notification(
    bot_token: str, chat_id: str, new_jobs: list[JobPosting], client: httpx.AsyncClient
) -> bool:
    """Send formatted Telegram message via Bot API for newly detected postings."""
    if not bot_token or not chat_id or not new_jobs:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    success = True

    # Build HTML formatted text
    header = f"🚀 <b>New GCC Entry-Level Opening(s) Detected ({len(new_jobs)})!</b>\n\n"
    items_text = []

    for idx, job in enumerate(new_jobs, start=1):
        clean_company = html.escape(job.company)
        clean_title = html.escape(job.title)
        clean_location = html.escape(job.location)
        apply_url_str = str(job.apply_url)

        item = (
            f"<b>{idx}. {clean_company}</b>\n"
            f"💼 {clean_title}\n"
            f"📍 {clean_location} ({job.provider.value.upper()})\n"
            f"🔗 <a href=\"{apply_url_str}\">Apply on ATS</a>\n"
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

    for chunk in message_chunks:
        payload = {
            "chat_id": chat_id,
            "text": chunk.strip(),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
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
    discord_url = discord_webhook or os.getenv("DISCORD_WEBHOOK_URL")
    tg_token = telegram_token or os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID")

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

import os
import imaplib
import email
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from typing import Optional

# Local imports from the main package
from gcc_job_radar.email_revert_detector import process_email_record, RevertMatch, RevertType
from gcc_job_radar.notifier import send_telegram_notification, record_dispatched_alerts
from gcc_job_radar.db import get_db_path, init_db, mark_job_status


def _parse_email_message(msg_bytes: bytes) -> dict:
    """Extract useful fields from a raw email message.

    Returns a dict with keys: ``subject``, ``sender``, ``date``, ``body``.
    """
    msg = email.message_from_bytes(msg_bytes)
    subject = msg.get("Subject", "")
    sender = msg.get("From", "")
    date = msg.get("Date", "")

    # Prefer plain‑text part
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                charset = part.get_content_charset() or "utf-8"
                body = part.get_payload(decode=True).decode(charset, errors="ignore")
                break
    else:
        charset = msg.get_content_charset() or "utf-8"
        body = msg.get_payload(decode=True).decode(charset, errors="ignore")
    return {"subject": subject, "sender": sender, "date": date, "body": body}


def _store_seen_email(uid: str, db_path: Optional[Path] = None) -> None:
    """Record a processed email UID in the ``seen_emails`` table.

    The table is created lazily by ``init_db`` if it does not exist.
    """
    init_db(db_path)
    target = get_db_path(db_path)
    with sqlite3.connect(target) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_emails (uid) VALUES (?)",
            (uid,),
        )
        conn.commit()


def _has_seen_email(uid: str, db_path: Optional[Path] = None) -> bool:
    """Check whether an email UID has already been processed."""
    init_db(db_path)
    target = get_db_path(db_path)
    with sqlite3.connect(target) as conn:
        cur = conn.execute("SELECT 1 FROM seen_emails WHERE uid = ?", (uid,))
        return cur.fetchone() is not None


def _send_revert_alert(match: RevertMatch) -> None:
    """Send a Telegram alert for important revert types.

    Only ``INTERVIEW`` and ``OA_INVITE`` generate a notification.
    """
    if match.revert_type in {RevertType.INTERVIEW, RevertType.OA_INVITE}:
        message = (
            f"*{match.revert_type.value}* detected from *{match.sender}* for *{match.company}*\n"
            f"Subject: {match.subject}\nSnippet: {match.snippet}"
        )
        send_telegram_notification(message)
        # ``record_dispatched_alerts`` expects a job_id; it may be ``None``.
        record_dispatched_alerts(
            job_id=str(match.matched_job_id) if match.matched_job_id else None,
            platform="telegram",
        )


def _update_job_status(match: RevertMatch, db_path: Optional[Path] = None) -> None:
    """Update the status of the correlated job based on the revert type."""
    if not match.matched_job_id:
        return
    if match.revert_type == RevertType.INTERVIEW:
        status, notes = "INTERVIEWING", "Interview invitation received via email."
    elif match.revert_type == RevertType.OA_INVITE:
        status, notes = "INTERVIEWING", "Online assessment invitation received via email."
    elif match.revert_type == RevertType.REJECTED:
        status, notes = "REJECTED", "Rejection email received."
    else:
        return
    mark_job_status(job_id=match.matched_job_id, status=status, notes=notes, db_path=db_path)


def run_check_reverts(
    days: int = 1,
    dry_run: bool = False,
    notify: bool = True,
    db_path: Optional[Path] = None,
) -> None:
    """Core routine that scans Gmail for recruiter replies and synchronises state.

    Parameters
    ----------
    days:
        How many days back to search for messages.
    dry_run:
        When ``True`` no database writes or Telegram notifications are performed.
    notify:
        Whether to send Telegram alerts for ``INTERVIEW`` / ``OA_INVITE``.
    db_path:
        Optional custom path to the SQLite database.
    """
    user = os.getenv("GMAIL_USER")
    password = os.getenv("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise EnvironmentError("GMAIL_USER and GMAIL_APP_PASSWORD must be set in the environment")

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(user, password)
    imap.select("INBOX")
    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    status, data = imap.search(None, f"(SINCE \"{since}\")")
    if status != "OK":
        imap.logout()
        raise RuntimeError("Failed to search mailbox")

    uids = data[0].split()
    for uid_bytes in uids:
        uid = uid_bytes.decode()
        if _has_seen_email(uid, db_path):
            continue
        fetch_status, msg_data = imap.fetch(uid, "(RFC822)")
        if fetch_status != "OK" or not msg_data:
            continue
        raw_msg = msg_data[0][1]
        email_parts = _parse_email_message(raw_msg)
        match = process_email_record(
            uid=uid,
            subject=email_parts["subject"],
            sender=email_parts["sender"],
            date=email_parts["date"],
            body=email_parts["body"],
            db_path=db_path,
        )
        if not match:
            if not dry_run:
                _store_seen_email(uid, db_path)
            continue
        if not dry_run:
            _update_job_status(match, db_path)
            _store_seen_email(uid, db_path)
            if notify:
                _send_revert_alert(match)
    imap.logout()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Detect recruiter email reverts and sync job status")
    parser.add_argument("--days", type=int, default=1, help="Number of days back to scan")
    parser.add_argument("--dry-run", action="store_true", help="Do not modify DB or send alerts")
    parser.add_argument("--no-notify", dest="notify", action="store_false", help="Suppress Telegram alerts")
    parser.add_argument("--db-path", type=str, default=None, help="Custom SQLite DB path")
    args = parser.parse_args()
    run_check_reverts(
        days=args.days,
        dry_run=args.dry_run,
        notify=args.notify,
        db_path=Path(args.db_path) if args.db_path else None,
    )

import os
import imaplib
import email
import argparse
import re
from pathlib import Path
from typing import List, Tuple, Set
from getpass import getpass

from dotenv import load_dotenv
from rich.table import Table
from rich.console import Console

# Load environment variables from .env if present
load_dotenv()

# Heuristic phrase lists
CONFIRM_PHRASES = [
    "thank you for applying",
    "application received",
    "received your application",
    "application confirmation",
    "applied to",
    "application submitted",
    "thanks for applying",
    "your application at",
    "your application with",
]
NOISE_PHRASES = [
    "job alert",
    "digest",
    "recommended jobs",
    "matches for you",
    "new jobs",
]

def parse_accounts(env_accounts: str | None, arg_accounts: str | None) -> List[Tuple[str, str]]:
    """Return a list of (email, password) tuples.

    Preference order: CLI argument > GMAIL_ACCOUNTS env > individual env vars.
    If any password is missing, prompt the user.
    """
    accounts_str = arg_accounts or env_accounts
    accounts: List[Tuple[str, str]] = []

    if accounts_str:
        # Expected format: "email1:pass1,email2:pass2"
        for pair in accounts_str.split(","):
            if not pair.strip():
                continue
            if ":" in pair:
                email_addr, pwd = pair.split(":", 1)
                accounts.append((email_addr.strip(), pwd.strip()))
            else:
                # No password supplied – will prompt later
                accounts.append((pair.strip(), ""))
    else:
        # Fallback to single account vars
        user = os.getenv("GMAIL_USER")
        pwd = os.getenv("GMAIL_APP_PASSWORD")
        if user:
            accounts.append((user, pwd or ""))
    # Prompt for missing passwords
    final_accounts: List[Tuple[str, str]] = []
    for email_addr, pwd in accounts:
        if not pwd:
            pwd = getpass(f"Enter app password for {email_addr}: ")
        final_accounts.append((email_addr, pwd))
    return final_accounts

def fetch_headers(imap: imaplib.IMAP4_SSL, uids: List[bytes]) -> List[dict]:
    """Fetch SUBJECT, FROM, DATE headers for given UIDs.

    Returns a list of dictionaries with keys 'subject', 'from', 'date'.
    """
    results: List[dict] = []
    # Process in chunks to avoid long IMAP commands
    CHUNK_SIZE = 100
    for i in range(0, len(uids), CHUNK_SIZE):
        chunk = uids[i : i + CHUNK_SIZE]
        uid_set = b",".join(chunk)
        # Fetch only the needed header fields
        typ, data = imap.fetch(uid_set, '(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])')
        if typ != "OK":
            continue
        for response in data:
            if isinstance(response, tuple):
                raw = response[1]
                msg = email.message_from_bytes(raw)
                results.append({
                    "subject": msg.get("Subject", "").strip(),
                    "from": msg.get("From", "").strip(),
                    "date": msg.get("Date", "").strip(),
                })
    return results

def is_confirmation(subject: str) -> bool:
    subj = subject.lower()
    return any(phrase in subj for phrase in CONFIRM_PHRASES) and not any(noise in subj for noise in NOISE_PHRASES)

def extract_company(sender: str) -> str:
    """Extract a normalized company identifier from the From header.

    Strategy: find an email address, take the domain part before the first dot.
    Example: "HR <recruiter@awesomeco.com>" -> "awesomeco".
    """
    # Find the email address inside <> or as plain text
    match = re.search(r"[\w.+-]+@([\w.-]+)", sender)
    if not match:
        return sender.lower()
    domain = match.group(1).lower()
    # Remove common subdomains like mail., recruitment.
    domain = re.sub(r"^(mail|recruit|jobs?)\.", "", domain)
    # Take the first component before a dot as company name
    company = domain.split(".")[0]
    return company

def main():
    parser = argparse.ArgumentParser(
        description="Count unique companies applied to since 01-Aug-2025 across configured Gmail accounts",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--accounts",
        type=str,
        default=None,
        help="Comma‑separated list of email:app_password pairs (e.g., 'a@x.com:pwd,b@x.com:pwd').",
    )
    args = parser.parse_args()

    accounts = parse_accounts(os.getenv("GMAIL_ACCOUNTS"), args.accounts)
    if not accounts:
        print("No Gmail accounts configured. Provide via GMAIL_ACCOUNTS env or --accounts.")
        return

    console = Console()
    table = Table(title="Historical Application Summary")
    table.add_column("Account Email", style="cyan", no_wrap=True)
    table.add_column("Scanned Messages", justify="right")
    table.add_column("Confirmations", justify="right")
    table.add_column("Unique Companies", justify="right")

    grand_total_confirmations = 0
    global_unique_companies: Set[str] = set()

    for email_addr, pwd in accounts:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        try:
            imap.login(email_addr, pwd)
        except imaplib.IMAP4.error as e:
            console.print(f"[red]Failed to login to {email_addr}: {e}[/red]")
            continue
        imap.select("INBOX")
        typ, data = imap.search(None, '(SINCE "01-Aug-2025")')
        if typ != "OK":
            console.print(f"[red]Search failed for {email_addr}[/red]")
            imap.logout()
            continue
        uids = data[0].split()
        total_scanned = len(uids)
        headers = fetch_headers(imap, uids)
        confirmations = 0
        local_companies: Set[str] = set()
        for hdr in headers:
            if is_confirmation(hdr["subject"]):
                confirmations += 1
                comp = extract_company(hdr["from"]).lower()
                local_companies.add(comp)
                global_unique_companies.add(comp)
        table.add_row(email_addr, str(total_scanned), str(confirmations), str(len(local_companies)))
        grand_total_confirmations += confirmations
        imap.logout()

    console.print(table)
    console.print(f"[bold]Grand Total Confirmations:[/bold] {grand_total_confirmations}")
    console.print(f"[bold]Grand Unique Companies Applied To:[/bold] {len(global_unique_companies)}")

if __name__ == "__main__":
    main()

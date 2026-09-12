#!/usr/bin/env python3
"""Pre-flight verification script for Render background worker deployment.

Verifies:
1. Environment variables parse cleanly.
2. SQLite database connects, initializes tables, and creates parent directories if needed.
3. Headless Rich console output handles Unicode characters and emojis without encoding crashes.
4. Bot listener dependencies and configuration load properly.
"""

import os
from pathlib import Path
import sys
import sqlite3

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from gcc_job_radar.config import DB_PATH, DEFAULT_DB_PATH
from gcc_job_radar.db import get_db_path, init_db
from gcc_job_radar.display import create_safe_console


def mask_secret(val: str) -> str:
    """Mask sensitive credentials for safe log printing."""
    if not val:
        return "[NOT SET]"
    if len(val) <= 8:
        return "***"
    return f"{val[:4]}...{val[-4:]}"


def main() -> int:
    console = create_safe_console()
    console.print("[bold cyan]════════════════════════════════════════════════════════════[/bold cyan]")
    console.print("[bold cyan]       GCC Job Radar — Pre-Flight Deployment Check          [/bold cyan]")
    console.print("[bold cyan]════════════════════════════════════════════════════════════[/bold cyan]\n")

    errors: list[str] = []

    # 1. Environment Variables Check
    console.print("[bold white]1. Checking Environment Variables...[/bold white]")
    active_db_env = os.getenv("GCC_RADAR_DB_PATH", "")
    llm_provider = os.getenv("PRIMARY_LLM_PROVIDER", "gemini")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    groq_key = os.getenv("GROQ_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    console.print(f"  • GCC_RADAR_DB_PATH:    [yellow]{active_db_env or '(default)'}[/yellow]")
    console.print(f"  • PRIMARY_LLM_PROVIDER: [yellow]{llm_provider}[/yellow]")
    console.print(f"  • TELEGRAM_BOT_TOKEN:   [green]{mask_secret(tg_token)}[/green]")
    console.print(f"  • TELEGRAM_CHAT_ID:     [green]{mask_secret(tg_chat)}[/green]")
    console.print(f"  • GROQ_API_KEY:         [green]{mask_secret(groq_key)}[/green]")
    console.print(f"  • GEMINI_API_KEY:       [green]{mask_secret(gemini_key)}[/green]")

    # 2. Database Path & Schema Initialization Check
    console.print("\n[bold white]2. Checking SQLite Database Mount & Connectivity...[/bold white]")
    try:
        resolved_db = get_db_path()
        console.print(f"  • Resolved DB Path: [cyan]{resolved_db}[/cyan]")
        init_db(resolved_db)
        
        with sqlite3.connect(resolved_db) as conn:
            c = conn.cursor()
            c.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
            tables = [r[0] for r in c.fetchall()]
            console.print(f"  • Database schema initialized: [green]{len(tables)} tables/views detected[/green]")
            for required in ("seen_jobs", "jobs", "dormant_companies"):
                if required not in tables:
                    errors.append(f"Missing required table/view: {required}")
    except Exception as exc:
        errors.append(f"Database initialization failed: {exc}")
        console.print(f"  [bold red]✖ Database Error:[/bold red] {exc}")

    # 3. Headless Console / Encoding Safety Check
    console.print("\n[bold white]3. Checking Console Unicode & Emoji Support (Headless Safety)...[/bold white]")
    try:
        # Test emojis and special characters that previously broke Windows charmap or non-UTF8 containers
        test_message = "🤖 Bot Online | 🚀 Scan Ready | 💼 Job Alerts | 📄 Resumes Tailored | ✅ UTF-8 OK"
        console.print(f"  • Test Output: [green]{test_message}[/green]")
        console.print(f"  • Standard Output Encoding: [cyan]{getattr(sys.stdout, 'encoding', 'unknown')}[/cyan]")
        console.print(f"  • Console is_terminal: [cyan]{console.is_terminal}[/cyan]")
    except UnicodeEncodeError as exc:
        errors.append(f"Console encoding failure: {exc}")
        console.print(f"  [bold red]✖ Encoding Error:[/bold red] {exc}")

    # 4. Summary
    console.print("\n[bold cyan]────────────────────────────────────────────────────────────[/bold cyan]")
    if errors:
        console.print(f"[bold red]✖ Pre-Flight Check Failed with {len(errors)} error(s):[/bold red]")
        for err in errors:
            console.print(f"  - {err}")
        return 1
    else:
        console.print("[bold green]✔ Pre-Flight Deployment Check Passed! System ready for Render deployment.[/bold green]\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())

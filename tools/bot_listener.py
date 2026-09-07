"""Standalone runner for interactive Telegram bot listener with inline callback handling.

Supports windowless/daemon background execution via pythonw.exe, PID tracking,
and file logging to bot.log.
"""

import argparse
import asyncio
import atexit
import logging
import os
from pathlib import Path
import sys
from typing import Optional
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

# Load environment variables (.env) from project root
load_dotenv(PROJECT_ROOT / ".env")

from gcc_job_radar.bot_listener import run_bot_listener

logger = logging.getLogger("bot_listener")


def _is_pid_running(pid: int) -> bool:
    """Check if a process with the given PID is currently active."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                exit_code = ctypes.c_ulong()
                kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                kernel32.CloseHandle(handle)
                STILL_ACTIVE = 259
                return exit_code.value == STILL_ACTIVE
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _manage_pid_file(pid_path: Path) -> None:
    """Ensure single-instance execution via PID file."""
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text(encoding="utf-8").strip())
            if _is_pid_running(old_pid):
                logger.error(
                    "Bot listener is already running under PID %d. Stop it before starting a new instance.",
                    old_pid,
                )
                raise SystemExit(1)
        except (ValueError, OSError):
            pass  # Stale or corrupted PID file, safe to overwrite

    current_pid = os.getpid()
    pid_path.write_text(str(current_pid), encoding="utf-8")

    def _cleanup():
        try:
            if pid_path.exists():
                saved_pid = pid_path.read_text(encoding="utf-8").strip()
                if saved_pid == str(current_pid):
                    pid_path.unlink(missing_ok=True)
        except Exception:
            pass

    atexit.register(_cleanup)


def _setup_logging(log_file: Optional[Path]) -> None:
    """Configure unified logging to file and standard streams."""
    handlers: list[logging.Handler] = []

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        handlers.append(file_handler)

    if sys.stdout is not None:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        handlers.append(console_handler)

    logging.basicConfig(
        level=logging.INFO,
        handlers=handlers if handlers else None,
        force=True,
    )

    # Ensure standard streams use utf-8 on Windows
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # When running detached under pythonw.exe, redirect stdout & stderr to log file
    if log_file and (sys.stdout is None or sys.stderr is None):
        log_fp = open(log_file, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = log_fp
        if sys.stderr is None:
            sys.stderr = log_fp


def main() -> None:
    """CLI entrypoint for tools/bot_listener.py."""
    parser = argparse.ArgumentParser(
        description="Interactive Telegram Bot Listener for GCC Job Radar with Inline Keyboards."
    )
    parser.add_argument(
        "--token",
        "-t",
        type=str,
        default=None,
        help="Telegram bot token (or set TELEGRAM_BOT_TOKEN env var).",
    )
    parser.add_argument(
        "--chat-id",
        "-c",
        type=str,
        default=None,
        help="Authorized Telegram chat ID (or set TELEGRAM_CHAT_ID env var).",
    )
    parser.add_argument(
        "--db-path",
        "-d",
        type=Path,
        default=None,
        help="Custom path to SQLite database.",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=20,
        help="Long-polling timeout in seconds (default: 20).",
    )
    parser.add_argument(
        "--log-file",
        "-l",
        type=Path,
        default=PROJECT_ROOT / "bot.log",
        help="Path to bot log file (default: bot.log).",
    )
    parser.add_argument(
        "--pid-file",
        type=Path,
        default=PROJECT_ROOT / "bot.pid",
        help="Path to PID tracking file (default: bot.pid).",
    )

    args = parser.parse_args()

    # Configure logging
    _setup_logging(args.log_file)

    from rich.console import Console

    bot_token = args.token or os.getenv("TELEGRAM_BOT_TOKEN")
    allowed_chat_id = args.chat_id or os.getenv("TELEGRAM_CHAT_ID")

    console = Console(highlight=False)

    if not bot_token:
        logger.error("Missing Telegram bot token. Pass `--token` or set `TELEGRAM_BOT_TOKEN` in `.env`.")
        if sys.stdout is not None:
            console.print(
                "[bold red]Error:[/bold red] Missing Telegram bot token. Pass `--token` or set `TELEGRAM_BOT_TOKEN` in `.env`."
            )
        raise SystemExit(1)

    if not allowed_chat_id:
        logger.error("Missing authorized Telegram chat ID. Pass `--chat-id` or set `TELEGRAM_CHAT_ID` in `.env`.")
        if sys.stdout is not None:
            console.print(
                "[bold red]Error:[/bold red] Missing authorized Telegram chat ID. Pass `--chat-id` or set `TELEGRAM_CHAT_ID` in `.env`."
            )
        raise SystemExit(1)

    # Manage PID tracking
    if args.pid_file:
        _manage_pid_file(args.pid_file)

    logger.info("Starting GCC Job Radar Telegram Bot Listener (PID: %d)...", os.getpid())

    try:
        asyncio.run(
            run_bot_listener(
                bot_token=bot_token,
                allowed_chat_id=str(allowed_chat_id),
                db_path=args.db_path,
                poll_timeout=args.poll_timeout,
            )
        )
    except KeyboardInterrupt:
        logger.info("Telegram bot listener stopped via KeyboardInterrupt.")
        if sys.stdout is not None:
            console.print("\n[yellow]Telegram bot listener stopped.[/yellow]")
    except Exception as exc:
        logger.exception("Fatal error in bot listener: %s", exc)
        raise


if __name__ == "__main__":
    main()

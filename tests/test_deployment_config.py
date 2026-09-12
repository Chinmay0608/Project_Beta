import os
from pathlib import Path
import sqlite3
import pytest

from gcc_job_radar.config import DB_PATH, DEFAULT_DB_PATH
from gcc_job_radar.db import get_db_path, init_db, record_jobs
from gcc_job_radar.display import create_safe_console
from gcc_job_radar.models import ATSProvider, JobPosting


def test_config_exports_db_path():
    """Verify config.py exports DB_PATH as a Path object."""
    assert isinstance(DB_PATH, Path)
    assert isinstance(DEFAULT_DB_PATH, Path)


def test_custom_db_path_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verify that GCC_RADAR_DB_PATH environment variable directs database operations."""
    custom_db_file = tmp_path / "render_data" / "custom_jobs.db"
    monkeypatch.setenv("GCC_RADAR_DB_PATH", str(custom_db_file))

    resolved = get_db_path()
    assert resolved == custom_db_file
    assert resolved.parent.exists()

    # Initialize and verify schema was created at the custom path
    init_db()
    assert custom_db_file.exists()

    with sqlite3.connect(custom_db_file) as conn:
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        assert c.fetchone() is not None

    # Record a job to custom DB path
    job = JobPosting(
        id="test-deploy-1",
        company="RenderCo",
        title="Software Engineer",
        location="Remote - India",
        apply_url="https://example.com/apply",
        provider=ATSProvider.GREENHOUSE,
    )
    record_jobs([job])

    with sqlite3.connect(custom_db_file) as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM seen_jobs WHERE company='RenderCo'")
        assert c.fetchone()[0] == 1


def test_db_path_auto_creates_parent_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verify get_db_path automatically creates missing parent directories for mounts like /data."""
    deep_path = tmp_path / "mount" / "persistent" / "data" / "gcc_jobs.db"
    assert not deep_path.parent.exists()

    monkeypatch.setenv("GCC_RADAR_DB_PATH", str(deep_path))
    resolved = get_db_path()

    assert resolved == deep_path
    assert deep_path.parent.exists()


def test_safe_console_headless_initialization():
    """Verify create_safe_console creates a Console that safely prints Unicode and emojis without encoding crashes."""
    console = create_safe_console()
    assert console is not None

    # Verify rendering emojis and special characters does not throw UnicodeEncodeError
    test_str = "\U0001f916 Bot Online | 🚀 Launch | 💼 Job | ✅ Status OK"
    # Should complete without error
    console.print(test_str)

"""Tests for the CLI 'ask' command interfacing with the AI agent."""

from pathlib import Path
from unittest.mock import patch
from typer.testing import CliRunner

from gcc_job_radar.cli import app
from gcc_job_radar.db import init_db

runner = CliRunner()


def test_cli_ask_help() -> None:
    """Verify gcc-job-radar ask --help renders usage."""
    result = runner.invoke(app, ["ask", "--help"])
    assert result.exit_code == 0
    assert "Ask the AI agent about jobs, stats, companies" in result.output
    assert "question" in result.output.lower()


def test_cli_ask_execution(tmp_path: Path) -> None:
    """Verify gcc-job-radar ask command calls ask_ai_agent and outputs response."""
    db_file = tmp_path / "test_ask.db"
    init_db(db_file)

    async def mock_ask(prompt, chat_id="cli", db_path=None, client=None, **kwargs):
        return f"Found 3 Java roles matching your query '{prompt}'."

    with patch("gcc_job_radar.ai_agent.ask_ai_agent", side_effect=mock_ask):
        result = runner.invoke(app, ["ask", "What are the best Java roles?", "--db", str(db_file)])
        assert result.exit_code == 0
        assert "GCC Job Radar • AI Career Agent" in result.output
        assert "Found 3 Java roles matching your query 'What are the best Java roles?'" in result.output

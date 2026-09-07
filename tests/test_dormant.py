"""Tests for dormant company registry wiring, auto-dormant tracking, and CLI reactivate/dormant commands."""

from pathlib import Path
from unittest.mock import patch
from typer.testing import CliRunner

from gcc_job_radar.cli import app
from gcc_job_radar.db import (
    get_dormant_companies_entries,
    get_dormant_company_names,
    init_db,
    reactivate_company,
    record_company_scan_activity,
)
from gcc_job_radar.models import ATSProvider, CompanyConfig

runner = CliRunner()


def test_dormant_db_seeding_and_reactivate(tmp_path: Path) -> None:
    """Verify init_db seeds dormant registry and reactivate_company updates status."""
    db_file = tmp_path / "test_dormant.db"
    init_db(db_file)

    # Backblaze should be in dormant registry by default
    dormant_names = get_dormant_company_names(db_path=db_file)
    assert "backblaze" in dormant_names

    entries = get_dormant_companies_entries(db_path=db_file)
    assert any(e["company_name"].lower() == "backblaze" for e in entries)

    # Reactivate Backblaze
    reactivated = reactivate_company("Backblaze", db_path=db_file)
    assert reactivated is True

    # Now Backblaze should no longer be in dormant names
    active_dormant = get_dormant_company_names(db_path=db_file)
    assert "backblaze" not in active_dormant


def test_auto_dormant_tracking_and_threshold(tmp_path: Path) -> None:
    """Verify consecutive zero scans increment and auto-mark company dormant at threshold."""
    db_file = tmp_path / "test_auto_dormant.db"
    init_db(db_file)

    comp_name = "ZeroMatchCorp"

    # Scan 1 & 2: zero matches
    record_company_scan_activity(comp_name, jobs_found=0, auto_dormant_threshold=3, db_path=db_file)
    record_company_scan_activity(comp_name, jobs_found=0, auto_dormant_threshold=3, db_path=db_file)

    dormant_set = get_dormant_company_names(db_path=db_file)
    assert comp_name.lower() not in dormant_set

    # Scan 3: matches found -> reset to 0
    record_company_scan_activity(comp_name, jobs_found=2, auto_dormant_threshold=3, db_path=db_file)

    # Scan 4, 5, 6: zero matches with threshold = 3 -> transitions to dormant
    record_company_scan_activity(comp_name, jobs_found=0, auto_dormant_threshold=3, db_path=db_file)
    record_company_scan_activity(comp_name, jobs_found=0, auto_dormant_threshold=3, db_path=db_file)
    transitioned = record_company_scan_activity(comp_name, jobs_found=0, auto_dormant_threshold=3, db_path=db_file)
    assert transitioned is True

    # Now ZeroMatchCorp is dormant
    dormant_set_after = get_dormant_company_names(db_path=db_file)
    assert comp_name.lower() in dormant_set_after


def test_cli_dormant_and_reactivate_commands(tmp_path: Path) -> None:
    """Verify CLI dormant listing and reactivate commands."""
    db_file = tmp_path / "cli_dormant.db"
    init_db(db_file)

    # Command `dormant`
    res_list = runner.invoke(app, ["dormant", "--db", str(db_file)])
    assert res_list.exit_code == 0
    assert "Backblaze" in res_list.output
    assert "Dormant / Paused Companies" in res_list.output

    # Command `reactivate`
    res_reactivate = runner.invoke(app, ["reactivate", "Backblaze", "--db", str(db_file)])
    assert res_reactivate.exit_code == 0
    assert "Reactivated company Backblaze" in res_reactivate.output

    # Verify via flag --list-dormant
    res_flag = runner.invoke(app, ["--list-dormant", "--db", str(db_file)])
    assert res_flag.exit_code == 0


def test_scan_excludes_dormant_companies_by_default(tmp_path: Path) -> None:
    """Verify scan excludes dormant companies from target list."""
    db_file = tmp_path / "scan_dormant.db"
    init_db(db_file)

    test_dormant_comp = CompanyConfig(
        name="DormantTech",
        provider=ATSProvider.GREENHOUSE,
        board_token="dormanttech",
    )
    test_active_comp = CompanyConfig(
        name="ActiveTech",
        provider=ATSProvider.GREENHOUSE,
        board_token="activetech",
    )

    scanned_companies: list[CompanyConfig] = []

    async def mock_scan(companies, *args, **kwargs):
        scanned_companies.extend(companies)
        return []

    # Mark DormantTech dormant
    record_company_scan_activity("DormantTech", jobs_found=0, auto_dormant_threshold=1, db_path=db_file)

    with patch("gcc_job_radar.cli.COMPANIES", [test_dormant_comp, test_active_comp]), patch(
        "gcc_job_radar.cli.scan_all_companies", side_effect=mock_scan
    ):
        res = runner.invoke(app, ["scan", "--db", str(db_file)])
        assert res.exit_code == 0
        scanned_names = [c.name for c in scanned_companies]
        assert "ActiveTech" in scanned_names
        assert "DormantTech" not in scanned_names

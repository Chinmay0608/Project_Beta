"""Unit tests for tools/harvest_yc.py automated Y Combinator company harvester."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from gcc_job_radar.models import ATSProvider, CompanyConfig
from tools.harvest_yc import (
    FALLBACK_SOURCE,
    PRIMARY_SOURCE,
    fetch_yc_data,
    harvest_companies,
    is_inactive_entry,
    normalize_company_name,
    parse_args,
    pipe_to_probe,
    save_targets,
)


def test_normalize_company_name_legal_suffixes() -> None:
    """Verify legal suffixes (Inc, LLC, Corp, Ltd, Technologies, Software) are stripped."""
    assert normalize_company_name("Stripe, Inc.") == "Stripe"
    assert normalize_company_name("Retool Inc") == "Retool"
    assert normalize_company_name("DoorDash, LLC.") == "DoorDash"
    assert normalize_company_name("Zapier Corp.") == "Zapier"
    assert normalize_company_name("Monzo Bank Ltd") == "Monzo Bank"
    assert normalize_company_name("Brex Technologies, Inc.") == "Brex"
    assert normalize_company_name("Postman Software Solutions") == "Postman"
    assert normalize_company_name("Docker") == "Docker"


def test_normalize_company_name_punctuation_and_edge_cases() -> None:
    """Verify punctuation artifacts, quotes, and invalid strings are safely handled."""
    assert normalize_company_name("'Scale AI'") == "Scale AI"
    assert normalize_company_name('"Gusto"') == "Gusto"
    assert normalize_company_name("  Segment.io  ") == "Segment.io"
    assert normalize_company_name("0x") == "0x"
    assert normalize_company_name("") == ""
    assert normalize_company_name("   ") == ""
    assert normalize_company_name("---") == ""
    assert normalize_company_name("Inc.") == ""


def test_is_inactive_entry() -> None:
    """Verify detection of dead, closed, or inactive YC company records."""
    assert is_inactive_entry({"status": "Inactive"}) is True
    assert is_inactive_entry({"status": "dead"}) is True
    assert is_inactive_entry({"status": "Closed"}) is True
    assert is_inactive_entry({"status": "defunct"}) is True
    assert is_inactive_entry({"dead": True}) is True
    assert is_inactive_entry({"active": False}) is True

    # Active, acquired, or unspecified entries should be kept
    assert is_inactive_entry({"status": "Active"}) is False
    assert is_inactive_entry({"status": "Acquired"}) is False
    assert is_inactive_entry({"status": "Public"}) is False
    assert is_inactive_entry({"name": "Active Startup"}) is False


def test_fetch_yc_data_primary_success() -> None:
    """Verify successful downloading from primary source URL."""
    sample_data = [{"name": "Retool", "status": "Active"}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == PRIMARY_SOURCE
        return httpx.Response(200, json=sample_data)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    data = fetch_yc_data(client=client, sources=[PRIMARY_SOURCE, FALLBACK_SOURCE])
    assert data == sample_data


def test_fetch_yc_data_fallback_failover() -> None:
    """Verify automatic failover to fallback URL when primary source fails."""
    sample_data = [{"name": "Postman", "status": "Active"}]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PRIMARY_SOURCE:
            return httpx.Response(404, text="Not Found")
        elif str(request.url) == FALLBACK_SOURCE:
            return httpx.Response(200, json=sample_data)
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    data = fetch_yc_data(client=client, sources=[PRIMARY_SOURCE, FALLBACK_SOURCE])
    assert data == sample_data


def test_fetch_yc_data_all_fail() -> None:
    """Verify graceful handling and empty list return when all sources fail."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    data = fetch_yc_data(client=client, sources=[PRIMARY_SOURCE, FALLBACK_SOURCE])
    assert data == []


def test_harvest_companies_deduplication_and_filtering() -> None:
    """Verify deduplication against active registry and internal batch duplicates."""
    existing = {"stripe", "databricks", "airbnb"}
    raw_data = [
        {"name": "Stripe, Inc.", "status": "Active"},  # in existing
        {"name": "Databricks", "status": "Active"},    # in existing
        {"name": "Retool Inc", "status": "Active"},     # new
        {"name": "Retool, LLC", "status": "Active"},    # duplicate in batch
        {"name": "DeadCo", "status": "Inactive"},       # inactive
        {"name": "Zapier Corp", "status": "Active"},   # new
        {"name": "", "status": "Active"},              # blank
    ]

    harvested = harvest_companies(raw_data, existing_names=existing)
    assert harvested == ["Retool", "Zapier"]


def test_harvest_companies_with_limit() -> None:
    """Verify capping output count with limit parameter."""
    raw_data = [
        {"name": f"Startup {i}", "status": "Active"}
        for i in range(1, 20)
    ]
    harvested = harvest_companies(raw_data, existing_names=set(), limit=5)
    assert len(harvested) == 5
    assert harvested[0] == "Startup 1"
    assert harvested[4] == "Startup 5"


def test_save_targets(tmp_path: Path) -> None:
    """Verify saving targets to target text file with one company per line."""
    targets = ["Retool", "Zapier", "Postman", "Scale AI"]
    output_file = tmp_path / "test_targets.txt"
    count = save_targets(targets, output_file)
    assert count == 4
    assert output_file.exists()

    content = output_file.read_text(encoding="utf-8").splitlines()
    assert content == targets


def test_pipe_to_probe(tmp_path: Path) -> None:
    """Verify probe_ats invocation command construction and execution."""
    target_file = tmp_path / "targets.txt"
    target_file.write_text("Retool\nZapier\n", encoding="utf-8")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        ret = pipe_to_probe(target_file, append=True)
        assert ret == 0
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "--file" in cmd
        assert str(target_file) in cmd
        assert "--append" in cmd


def test_pipe_to_probe_missing_file(tmp_path: Path) -> None:
    """Verify missing target file returns error code."""
    non_existent = tmp_path / "does_not_exist.txt"
    ret = pipe_to_probe(non_existent)
    assert ret == 1


def test_parse_args() -> None:
    """Verify CLI argument defaults and custom parameters."""
    defaults = parse_args([])
    assert defaults.output == Path("targets_yc.txt")
    assert defaults.limit is None
    assert defaults.pipe_to_probe is False

    custom = parse_args(["--output", "custom.txt", "--limit", "100", "--pipe-to-probe"])
    assert custom.output == Path("custom.txt")
    assert custom.limit == 100
    assert custom.pipe_to_probe is True

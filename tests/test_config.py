"""Unit tests for company registry configuration and ATS validation."""

import pytest

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.models import ATSProvider, CompanyConfig


def test_company_registry_minimum_count() -> None:
    """Verify registry contains at least 150 target GCCs and tech centers."""
    assert len(COMPANIES) >= 150


def test_company_registry_field_integrity() -> None:
    """Verify all entries have non-empty name, valid ATSProvider, and board_token."""
    for company in COMPANIES:
        assert isinstance(company, CompanyConfig)
        assert isinstance(company.name, str) and company.name.strip(), f"Empty name found: {company}"
        assert isinstance(company.provider, ATSProvider), f"Invalid provider for {company.name}: {company.provider}"
        assert (
            isinstance(company.board_token, str) and company.board_token.strip()
        ), f"Empty board_token for {company.name}"

        # If Workday, verify tenant/site format and cluster
        if company.provider == ATSProvider.WORKDAY:
            parts = company.board_token.split("/")
            assert len(parts) == 2, f"Workday board_token must be 'tenant/site' format: {company.name}"
            assert company.cluster is not None and company.cluster.strip(), f"Workday cluster missing: {company.name}"


def test_company_registry_no_duplicate_names() -> None:
    """Verify no duplicate company names exist in registry (case-insensitive)."""
    names = [c.name.strip().lower() for c in COMPANIES]
    duplicates = [name for name in set(names) if names.count(name) > 1]
    assert not duplicates, f"Duplicate company names detected: {duplicates}"


def test_company_registry_no_duplicate_tokens_per_provider() -> None:
    """Verify no duplicate board tokens exist within the same ATS provider."""
    provider_tokens = [(c.provider, c.board_token.strip().lower()) for c in COMPANIES]
    duplicates = [pt for pt in set(provider_tokens) if provider_tokens.count(pt) > 1]
    assert not duplicates, f"Duplicate provider/token pairs detected: {duplicates}"


def test_required_gcc_companies_present() -> None:
    """Verify all explicitly requested target GCC companies are registered."""
    registered_names = {c.name.lower() for c in COMPANIES}

    required_targets = [
        # Enterprise GCCs
        "honeywell",
        "bosch",
        "philips",
        "abb",
        "maersk",
        "qualcomm",
        "western digital",
        "micron",
        "caterpillar",
        "john deere",
        # FinTech/Trading GCCs
        "razorpay",
        "groww",
        "phonepe",
        "cred",
        "pine labs",
        "tower research",
        "millennium",
        "worldquant",
        "de shaw",
        "falconx",
        "coinbase",
        # Global Cloud/SaaS
        "mongodb",
        "twilio",
        "hashicorp",
        "confluent",
        "datadog",
        "splunk",
        "new relic",
        "okta",
        "postman",
        "browserstack",
        # Fast-Growing Product Hubs
        "glean",
        "rippling",
        "scale ai",
        "weights & biases",
        "modal",
        "perplexity",
        "supabase",
        "hasura",
    ]

    missing = [target for target in required_targets if target not in registered_names]
    assert not missing, f"Missing required companies from registry: {missing}"

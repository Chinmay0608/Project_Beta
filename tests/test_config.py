"""Unit tests for company registry configuration and ATS validation."""

import pytest

from gcc_job_radar.config import COMPANIES, DORMANT_COMPANIES
from gcc_job_radar.models import ATSProvider, CompanyConfig


def test_company_registry_total_count() -> None:
    """Verify registry contains exactly 1492 active target GCCs, banks, and tech centers."""
    assert len(COMPANIES) == 1492


def test_dormant_companies_registry() -> None:
    """Verify dormant companies file holds paused companies like Backblaze with valid configs."""
    assert len(DORMANT_COMPANIES) >= 1
    dormant_names = [c.name.lower() for c in DORMANT_COMPANIES]
    assert "backblaze" in dormant_names

    # Ensure dormant companies are excluded from active scanning list
    active_names = [c.name.lower() for c in COMPANIES]
    assert "backblaze" not in active_names

    for company in DORMANT_COMPANIES:
        assert isinstance(company, CompanyConfig)
        assert isinstance(company.name, str) and company.name.strip()
        assert isinstance(company.provider, ATSProvider)
        assert isinstance(company.board_token, str) and company.board_token.strip()


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


def test_banking_gcc_companies_present() -> None:
    """Verify all 30 requested banking giants and financial capability centers are present."""
    registered_names = {c.name.lower() for c in COMPANIES}

    required_banks = [
        "citi",
        "barclays",
        "hsbc",
        "deutsche bank",
        "standard chartered",
        "societe generale",
        "bnp paribas",
        "ubs",
        "credit suisse",
        "nomura",
        "state street",
        "northern trust",
        "ing",
        "rabobank",
        "santander",
        "bbva",
        "credit agricole",
        "natixis",
        "capital one",
        "discover",
        "synchrony",
        "american express",
        "visa",
        "mastercard",
        "fidelity",
        "vanguard",
        "blackrock",
        "franklin templeton",
        "invesco",
        "western union",
    ]

    missing = [b for b in required_banks if b not in registered_names]
    assert not missing, f"Missing bank GCCs from registry: {missing}"


def test_wfh_remote_companies_present() -> None:
    """Verify Work From Home and distributed tech companies are registered."""
    registered_names = {c.name.lower() for c in COMPANIES}

    required_wfh = [
        "canonical",
        "mozilla",
        "automattic",
        "zapier",
        "wikimedia foundation",
        "netlify",
        "bitwarden",
        "tailscale",
        "airbyte",
        "pinecone",
        "baseten",
        "runpod",
        "prisma",
        "alchemy",
        "uniswap",
        "opensea",
        "infisical",
        "gitbook",
        "mattermost",
        "circleci",
        "ghost",
        "dremio",
        "consensys",
    ]

    missing = [w for w in required_wfh if w not in registered_names]
    assert not missing, f"Missing WFH companies from registry: {missing}"

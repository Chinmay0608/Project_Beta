"""Async aggregation and scanning engine for GCC Job Radar.

Re-exports core scanning routines from gcc_job_radar.scanner for backward compatibility.
"""

from gcc_job_radar.scanner import (
    DEFAULT_DOMAIN_LIMITS,
    DEFAULT_GLOBAL_CONCURRENCY,
    DEFAULT_HOST_LIMIT,
    USER_AGENT,
    HostRateLimiter,
    RetryTransport,
    fetch_single_company,
    get_company_domain,
    scan_all_companies,
)

__all__ = [
    "DEFAULT_DOMAIN_LIMITS",
    "DEFAULT_GLOBAL_CONCURRENCY",
    "DEFAULT_HOST_LIMIT",
    "USER_AGENT",
    "HostRateLimiter",
    "RetryTransport",
    "fetch_single_company",
    "get_company_domain",
    "scan_all_companies",
]

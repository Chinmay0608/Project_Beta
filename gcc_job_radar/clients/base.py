"""Base class for canonical ATS API clients."""

from abc import ABC, abstractmethod
import httpx
from gcc_job_radar.models import CompanyConfig, JobPosting


class BaseATSClient(ABC):
    """Abstract base class for querying canonical ATS job boards."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    @abstractmethod
    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        """Fetch and filter open jobs for a company from the ATS API."""
        raise NotImplementedError

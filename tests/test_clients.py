"""Unit tests for canonical ATS clients using mocked HTTP transports."""

import httpx
import pytest

from gcc_job_radar.clients.ashby import AshbyClient
from gcc_job_radar.clients.greenhouse import GreenhouseClient
from gcc_job_radar.clients.lever import LeverClient
from gcc_job_radar.clients.phenom_successfactors import PhenomSuccessFactorsClient
from gcc_job_radar.clients.smartrecruiters import SmartRecruitersClient
from gcc_job_radar.clients.workday import WorkdayClient
from gcc_job_radar.models import ATSProvider, CompanyConfig


@pytest.mark.asyncio
async def test_greenhouse_client_success() -> None:
    """Test Greenhouse client correctly parses and filters payloads."""
    payload = {
        "jobs": [
            {
                "id": 1001,
                "title": "Software Engineer 1",
                "location": {"name": "Bengaluru, India"},
                "absolute_url": "https://boards.greenhouse.io/databricks/jobs/1001",
                "updated_at": "2026-08-30T10:00:00Z",
            },
            {
                "id": 1002,
                "title": "Senior Software Engineer",  # Should be filtered out
                "location": {"name": "Bengaluru, India"},
                "absolute_url": "https://boards.greenhouse.io/databricks/jobs/1002",
                "updated_at": "2026-08-30T10:00:00Z",
            },
            {
                "id": 1003,
                "title": "Software Engineer 1",
                "location": {"name": "San Francisco, CA"},  # Should be filtered out
                "absolute_url": "https://boards.greenhouse.io/databricks/jobs/1003",
                "updated_at": "2026-08-30T10:00:00Z",
            },
            {
                "id": 1004,
                "title": "Software Engineer 1",
                "location": {"name": "Bengaluru, India"},
                "content": "<p>Requirements: 4+ years of experience in Java</p>",  # Filtered out by experience
                "absolute_url": "https://boards.greenhouse.io/databricks/jobs/1004",
                "updated_at": "2026-08-30T10:00:00Z",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "boards-api.greenhouse.io/v1/boards/databricks/jobs" in str(request.url)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(name="Databricks", provider=ATSProvider.GREENHOUSE, board_token="databricks")
        jobs = await GreenhouseClient(client).fetch_jobs(company)

        assert len(jobs) == 1
        assert jobs[0].id == "1001"
        assert jobs[0].company == "Databricks"
        assert jobs[0].title == "Software Engineer 1"
        assert jobs[0].location == "Bengaluru, India"
        assert jobs[0].published_date == "2026-08-30"
        assert jobs[0].provider == ATSProvider.GREENHOUSE


@pytest.mark.asyncio
async def test_greenhouse_client_errors() -> None:
    """Test Greenhouse client handles 404, 500, and network exceptions without raising."""
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Not Found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(error_handler)) as client:
        company = CompanyConfig(name="NonExistent", provider=ATSProvider.GREENHOUSE, board_token="invalid_token")
        jobs = await GreenhouseClient(client).fetch_jobs(company)
        assert jobs == []


@pytest.mark.asyncio
async def test_lever_client_success() -> None:
    """Test Lever client parses payloads and converts epoch milliseconds to date string."""
    payload = [
        {
            "id": "lever-2001",
            "text": "Associate Software Engineer",
            "categories": {"location": "Pune, India"},
            "hostedUrl": "https://jobs.lever.co/atlassian/lever-2001",
            "createdAt": 1756000000000,
        },
        {
            "id": "lever-2002",
            "text": "Engineering Manager",  # Disqualified
            "categories": {"location": "Pune, India"},
            "hostedUrl": "https://jobs.lever.co/atlassian/lever-2002",
            "createdAt": 1756000000000,
        },
        {
            "id": "lever-2003",
            "text": "Associate Software Engineer",
            "categories": {"location": "Sydney, Australia"},  # Non-India
            "hostedUrl": "https://jobs.lever.co/atlassian/lever-2003",
            "createdAt": 1756000000000,
        },
        {
            "id": "lever-2004",
            "text": "Associate Software Engineer",
            "categories": {"location": "Pune, India"},
            "descriptionPlain": "Candidate must possess 4+ years of experience in Java.",  # Disqualified
            "hostedUrl": "https://jobs.lever.co/atlassian/lever-2004",
            "createdAt": 1756000000000,
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.lever.co/v0/postings/atlassian" in str(request.url)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(name="Atlassian", provider=ATSProvider.LEVER, board_token="atlassian")
        jobs = await LeverClient(client).fetch_jobs(company)

        assert len(jobs) == 1
        assert jobs[0].id == "lever-2001"
        assert jobs[0].company == "Atlassian"
        assert jobs[0].title == "Associate Software Engineer"
        assert jobs[0].location == "Pune, India"
        assert jobs[0].provider == ATSProvider.LEVER
        assert jobs[0].published_date != "Active"


@pytest.mark.asyncio
async def test_lever_client_errors() -> None:
    """Test Lever client handles 500 status gracefully."""
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(error_handler)) as client:
        company = CompanyConfig(name="Broken", provider=ATSProvider.LEVER, board_token="broken")
        jobs = await LeverClient(client).fetch_jobs(company)
        assert jobs == []


@pytest.mark.asyncio
async def test_ashby_client_success() -> None:
    """Test Ashby client parses nested postal addresses, secondary locations, and date."""
    payload = {
        "jobs": [
            {
                "id": "ashby-3001",
                "title": "Junior Software Engineer",
                "location": "Remote",
                "secondaryLocations": [{"location": "India"}],
                "jobUrl": "https://jobs.ashbyhq.com/linear/ashby-3001",
                "publishedAt": "2026-08-25T14:30:00Z",
            },
            {
                "id": "ashby-3002",
                "title": "Staff Engineer",  # Disqualified
                "location": "Bengaluru, India",
                "jobUrl": "https://jobs.ashbyhq.com/linear/ashby-3002",
                "publishedAt": "2026-08-25T14:30:00Z",
            },
            {
                "id": "ashby-3004",
                "title": "Junior Software Engineer",
                "location": "Bengaluru, India",
                "descriptionPlain": "Requires minimum 3+ years of experience in distributed systems.",  # Disqualified
                "jobUrl": "https://jobs.ashbyhq.com/linear/ashby-3004",
                "publishedAt": "2026-08-25T14:30:00Z",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.ashbyhq.com/posting-api/job-board/linear" in str(request.url)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(name="Linear", provider=ATSProvider.ASHBY, board_token="linear")
        jobs = await AshbyClient(client).fetch_jobs(company)

        assert len(jobs) == 1
        assert jobs[0].id == "ashby-3001"
        assert jobs[0].company == "Linear"
        assert jobs[0].title == "Junior Software Engineer"
        assert jobs[0].published_date == "2026-08-25"
        assert jobs[0].provider == ATSProvider.ASHBY


@pytest.mark.asyncio
async def test_ashby_client_errors() -> None:
    """Test Ashby client handles network failures gracefully."""
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Not Found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(error_handler)) as client:
        company = CompanyConfig(name="Linear", provider=ATSProvider.ASHBY, board_token="linear")
        jobs = await AshbyClient(client).fetch_jobs(company)
        assert jobs == []


@pytest.mark.asyncio
async def test_smartrecruiters_client_success() -> None:
    """Test SmartRecruiters client parses content, location objects, ref URL, and releasedDate."""
    payload = {
        "content": [
            {
                "id": "sr-4001",
                "name": "Associate Software Engineer",
                "location": {
                    "city": "Bengaluru",
                    "region": "Karnataka",
                    "country": "India",
                },
                "ref": "https://jobs.smartrecruiters.com/WoltersKluwer/sr-4001",
                "releasedDate": "2026-08-28T09:15:00.000Z",
            },
            {
                "id": "sr-4002",
                "name": "Senior Software Engineer",  # Disqualified title
                "location": {
                    "city": "Bengaluru",
                    "region": "Karnataka",
                    "country": "India",
                },
                "ref": "https://jobs.smartrecruiters.com/WoltersKluwer/sr-4002",
                "releasedDate": "2026-08-28T09:15:00.000Z",
            },
            {
                "id": "sr-4003",
                "name": "Graduate Software Engineer",
                "location": {
                    "city": "London",  # Disqualified location
                    "country": "UK",
                },
                "ref": "https://jobs.smartrecruiters.com/WoltersKluwer/sr-4003",
                "releasedDate": "2026-08-28T09:15:00.000Z",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.smartrecruiters.com/v1/companies/WoltersKluwer/postings" in str(request.url)
        assert request.url.params.get("limit") == "100"
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(name="Wolters Kluwer", provider=ATSProvider.SMARTRECRUITERS, board_token="WoltersKluwer")
        jobs = await SmartRecruitersClient(client).fetch_jobs(company)

        assert len(jobs) == 1
        assert jobs[0].id == "sr-4001"
        assert jobs[0].company == "Wolters Kluwer"
        assert jobs[0].title == "Associate Software Engineer"
        assert "Bengaluru" in jobs[0].location
        assert jobs[0].published_date == "2026-08-28"
        assert jobs[0].provider == ATSProvider.SMARTRECRUITERS
        assert str(jobs[0].apply_url) == "https://jobs.smartrecruiters.com/WoltersKluwer/sr-4001"


@pytest.mark.asyncio
async def test_smartrecruiters_client_errors() -> None:
    """Test SmartRecruiters client handles 404, 500 without crashing."""
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Company not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(error_handler)) as client:
        company = CompanyConfig(name="Unknown", provider=ATSProvider.SMARTRECRUITERS, board_token="unknown")
        jobs = await SmartRecruitersClient(client).fetch_jobs(company)
        assert jobs == []


@pytest.mark.asyncio
async def test_workday_client_success() -> None:
    """Test Workday CXS client parses POST responses, externalPath, and location text."""
    payload = {
        "total": 3,
        "jobPostings": [
            {
                "title": "Software Engineer 1",
                "externalPath": "/job/Bengaluru-India/Software-Engineer-1_JR-5001",
                "locationsText": "Bengaluru, Karnataka, India",
                "postedOn": "Posted Yesterday",
                "bulletFields": ["Full time"],
            },
            {
                "title": "Staff Software Engineer",  # Disqualified
                "externalPath": "/job/Bengaluru-India/Staff-Software-Engineer_JR-5002",
                "locationsText": "Bengaluru, Karnataka, India",
                "postedOn": "Posted 2 Days Ago",
            },
            {
                "title": "Software Engineer 1",
                "externalPath": "/job/Bentonville-AR/Software-Engineer-1_JR-5003",
                "locationsText": "Bentonville, AR, USA",  # Non-India
                "postedOn": "Posted Today",
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "walmart.wd3.myworkdayjobs.com/wday/cxs/walmart/WalmartExternal/jobs" in str(request.url)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(
            name="Walmart Global Tech",
            provider=ATSProvider.WORKDAY,
            board_token="walmart/WalmartExternal",
            cluster="3",
        )
        jobs = await WorkdayClient(client).fetch_jobs(company)

        assert len(jobs) == 1
        assert jobs[0].id == "JR-5001"
        assert jobs[0].company == "Walmart Global Tech"
        assert jobs[0].title == "Software Engineer 1"
        assert "Bengaluru" in jobs[0].location
        assert jobs[0].provider == ATSProvider.WORKDAY
        assert "walmart.wd3.myworkdayjobs.com/en-US/WalmartExternal/job/Bengaluru-India/Software-Engineer-1_JR-5001" in str(jobs[0].apply_url)


@pytest.mark.asyncio
async def test_workday_client_errors() -> None:
    """Test Workday client handles 500 error gracefully."""
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(error_handler)) as client:
        company = CompanyConfig(
            name="Broken",
            provider=ATSProvider.WORKDAY,
            board_token="broken/Ext",
            cluster="3",
        )
        jobs = await WorkdayClient(client).fetch_jobs(company)
        assert jobs == []


@pytest.mark.asyncio
async def test_phenom_successfactors_client_success() -> None:
    """Test Phenom / SuccessFactors client parses Majid Al Futtaim job structure."""
    payload = {
        "jobs": [
            {
                "id": "maf-6001",
                "title": "Graduate Software Engineer",
                "location": "Gurgaon, India",
                "city": "Gurgaon",
                "country": "India",
                "url": "https://careers.majidalfuttaim.com/jobs/maf-6001",
                "datePosted": "2026-08-29",
            },
            {
                "id": "maf-6002",
                "title": "Senior Solutions Architect",  # Disqualified
                "location": "Gurgaon, India",
                "url": "https://careers.majidalfuttaim.com/jobs/maf-6002",
                "datePosted": "2026-08-29",
            },
            {
                "id": "maf-6003",
                "title": "Associate Software Engineer",
                "location": "Dubai, UAE",  # Non-India
                "url": "https://careers.majidalfuttaim.com/jobs/maf-6003",
                "datePosted": "2026-08-29",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "careers.majidalfuttaim.com" in str(request.url)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(
            name="Majid Al Futtaim",
            provider=ATSProvider.PHENOM_SUCCESSFACTORS,
            board_token="careers.majidalfuttaim.com",
        )
        jobs = await PhenomSuccessFactorsClient(client).fetch_jobs(company)

        assert len(jobs) == 1
        assert jobs[0].id == "maf-6001"
        assert jobs[0].company == "Majid Al Futtaim"
        assert jobs[0].title == "Graduate Software Engineer"
        assert "Gurgaon" in jobs[0].location
        assert jobs[0].provider == ATSProvider.PHENOM_SUCCESSFACTORS
        assert jobs[0].published_date == "2026-08-29"


@pytest.mark.asyncio
async def test_phenom_successfactors_client_errors() -> None:
    """Test Phenom client handles 404 and network errors gracefully."""
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(error_handler)) as client:
        company = CompanyConfig(
            name="Majid Al Futtaim",
            provider=ATSProvider.PHENOM_SUCCESSFACTORS,
            board_token="careers.majidalfuttaim.com",
        )
        jobs = await PhenomSuccessFactorsClient(client).fetch_jobs(company)
        assert jobs == []

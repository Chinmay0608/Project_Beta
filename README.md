# gcc-job-radar

> High-performance Python CLI tool that queries canonical ATS APIs (Greenhouse, Lever, Ashby) to aggregate and filter verified entry-level tech roles (SDE-1, Junior Engineer, Associate, Fresher, Tech Intern) in India from non-Indian tech companies and Global Capability Centers (GCCs).

---

## Key Features

- **Direct Canonical ATS Integration**:
  - **Greenhouse**: `https://boards-api.greenhouse.io`
  - **Lever**: `https://api.lever.co/v0/postings`
  - **Ashby**: `https://api.ashbyhq.com/posting-api`
- **Curated Company Registry**: Pre-configured with **31 foreign GCCs & high-growth tech companies** operating in India (Databricks, Stripe, Figma, GitLab, Pinterest, Rubrik, Elastic, Cloudflare, Reddit, Couchbase, DoorDash, Brex, Toast, Samsara, Flexport, PostHog, Deel, Docker, Coinbase, Robinhood, Atlassian, Ripple, Fullscript, Kraken, Palantir, Linear, Ramp, Synthesia, Monzo, Notion, Snowflake).
- **Strict Level & Title Filters**: Specifically surfaces entry-level roles (SDE 1, Junior Engineer, Associate, Fresher, Intern) while strictly disqualifying Senior, Lead, Staff, Principal, Architect, Manager, and numeral levels II through VI.
- **Indian Hub Geolocation**: Filters positions across Bengaluru, Hyderabad, Pune, Gurgaon, Noida, Mumbai, Chennai, Delhi NCR, and India Remote.
- **Persistent SQLite State Tracking**: Caches seen postings to highlight only newly discovered roles with `--new-only`.
- **Instant Webhook Alerts**: Automatic notifications to **Discord** and **Telegram** channels when new listings are detected.
- **Rich Terminal UI**: Animated progress bars, styled tables, and clickable terminal apply URLs.
- **Data Export**: Clean export options to JSON (`--json`) and CSV (`--csv`).

---

## Installation

```bash
# Clone the repository
git clone https://github.com/your-username/gcc-job-radar.git
cd gcc-job-radar

# Install in editable mode
pip install -e .

# Or with development dependencies for testing
pip install -e ".[dev]"
```

---

## Usage

### 1. Basic Scan
Scan all 31 companies across Greenhouse, Lever, and Ashby:
```bash
gcc-job-radar
```

### 2. Filter to a Specific Company
Scan a single company by name or slug:
```bash
gcc-job-radar --company databricks
gcc-job-radar --company atlassian
```

### 3. Track Only New Postings (`--new-only`)
Use SQLite state tracking to display only newly opened roles since your last scan:
```bash
gcc-job-radar --new-only
```

### 4. View Database Tracking Statistics
View historical counts of tracked roles per company:
```bash
gcc-job-radar --stats
```

### 5. Export Findings to JSON or CSV
```bash
gcc-job-radar --json latest_openings.json --csv latest_openings.csv
```

### 6. Instant Discord & Telegram Webhook Alerts
Pass webhook credentials directly via CLI flags or set environment variables:
```bash
# Via CLI flags:
gcc-job-radar --notify-discord "https://discord.com/api/webhooks/xxx/yyy"
gcc-job-radar --notify-telegram-token "BOT_TOKEN" --notify-telegram-chat "CHAT_ID"

# Or via environment variables:
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxx/yyy"
export TELEGRAM_BOT_TOKEN="BOT_TOKEN"
export TELEGRAM_CHAT_ID="CHAT_ID"
gcc-job-radar --new-only
```

### 7. Interactive Telegram Bot (Remote Control)
Start an interactive long-polling Telegram bot that responds to commands directly on your phone:
```bash
gcc-job-radar bot --token "BOT_TOKEN" --chat-id "CHAT_ID"
```
**Supported Commands**:
- `/scan`: Triggers live scan across all 70+ configured GCCs and replies with active openings.
- `/check <company>`: Scans a specific company (e.g. `/check celonis`, `/check databricks`).
- `/stats`: Shows database tracking metrics and company breakdown.
- `/latest`: Shows the 5 most recently discovered postings with direct apply URLs.
- `/help`: Lists available commands.

*Security Note: The bot strictly authenticates incoming messages against `--chat-id` (or `TELEGRAM_CHAT_ID`) and rejects unauthorized users.*

---

## Automating with GitHub Actions (Zero-Server Setup)

A production-ready GitHub Actions workflow is included at [`.github/workflows/job_radar_cron.yml`](.github/workflows/job_radar_cron.yml).

### How It Works:
1. **Scheduled Runs**: Runs automatically every 4 hours (`cron: '0 */4 * * *'`).
2. **Persistent Database Caching**: Uses `actions/cache@v4` to persist `gcc_jobs.db` across runs, ensuring duplicate alerts are never dispatched.
3. **Webhook Notifications**: Alerts your Discord channel or Telegram chat whenever a new fresher/SDE-1 role is detected.
4. **Artifact Retention**: Automatically uploads `latest_openings.json` as a build artifact retained for 7 days.
5. **Manual Triggering**: Can be triggered on-demand via the **Actions** tab in GitHub with an optional company filter.

### Setting Up Secrets in GitHub:
Go to your GitHub repository -> **Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret**:

| Secret Name | Description | Example |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | Discord Channel Webhook URL | `https://discord.com/api/webhooks/...` |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API Token | `123456789:AAH...` |
| `TELEGRAM_CHAT_ID` | Telegram Chat or Channel ID | `-100123456789` or `@channel_name` |

*(Note: Secrets are optional; if omitted, the scanner runs silently and records findings to `latest_openings.json`).*

---

## Testing

Run the comprehensive automated test suite (132 test cases covering filters, ATS clients, database, and CLI):
```bash
pytest -v
```

---

## Project Structure

```
gcc-job-radar/
├── .github/
│   └── workflows/
│       └── job_radar_cron.yml   # 4-hour scheduled GitHub Actions workflow
├── pyproject.toml               # Package metadata, dependencies, entry points
├── README.md                    # Documentation
├── gcc_job_radar/
│   ├── __init__.py              # Package version
│   ├── cli.py                   # Typer CLI runner with rich UI and export options
│   ├── config.py                # 31 curated companies and regex pattern definitions
│   ├── models.py                # Pydantic data models (JobPosting, CompanyConfig)
│   ├── filters.py               # Strict title and Indian location matching logic
│   ├── db.py                    # SQLite persistence layer and state tracking
│   ├── notifier.py              # Discord and Telegram webhook alert dispatchers
│   ├── engine.py                # Async concurrent scanning engine (httpx + semaphore)
│   ├── display.py               # Rich terminal tables, banners, and stats panels
│   └── clients/
│       ├── base.py              # Base abstract ATS client
│       ├── greenhouse.py        # Greenhouse API client
│       ├── lever.py             # Lever API client
│       └── ashby.py             # Ashby API client
└── tests/
    ├── test_filters.py          # Title/location regex positive & negative test suite
    ├── test_clients.py          # Mocked HTTP tests for Greenhouse, Lever, and Ashby
    ├── test_db.py               # SQLite schema, upsert, and isolation tests
    ├── test_notifier.py         # Discord & Telegram payload and error handling tests
    └── test_cli.py              # CliRunner tests for flags, exports, and dispatch
```

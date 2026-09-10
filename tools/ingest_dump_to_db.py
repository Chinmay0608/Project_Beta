"""Ingest job postings from tools/raw_dump.txt, resolve career portals, and record to DB."""

import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path

import sqlite3
from gcc_job_radar.db import record_jobs
from gcc_job_radar.filters import is_tech_role, matches_target_title, requires_experienced_candidate
from gcc_job_radar.link_resolver import build_direct_careers_redirect_url, resolve_company_career_portal
from gcc_job_radar.models import ATSProvider, JobPosting
from gcc_job_radar.relevance import score_job_posting

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATE_PAT = re.compile(
    r"^(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\s+202\d)\s*(Linkedin|General guide|Naukri|Indeed|Glassdoor|Internshala)?\s*(.*)$",
    re.IGNORECASE,
)

SKILL_SUFFIXES = [
    r"Javascript", r"JavaScript", r"TypeScript", r"React\.js", r"React", r"Node\.js", r"Nodejs",
    r"Python", r"Java", r"SQL", r"HTML", r"CSS", r"C\+\+", r"C#", r"Excel", r"SpringBoot", r"Springboot",
    r"AWS", r"Django", r"Flask", r"MongoDB", r"Golang", r"Go", r"Flutter", r"PHP", r"Ruby", r"DSA",
    r"DataStructures", r"Algorithms", r"OOP", r"PowerBI", r"Power BI", r"Tableau",
]
SKILL_PAT = re.compile(r"(?i)(" + "|".join(SKILL_SUFFIXES) + r")$")

TITLE_REGEX = re.compile(
    r"""(?ix)
    (
        (?:Associate|Junior|Jr\.?|Trainee|Graduate|Entry[- ]Level)?\s*
        (?:
            Software\s+Development\s+Engineer(?:\s*[-–—]?\s*(?:1|I\b|II\b|2))? |
            Software\s+Engineer(?:\s*[-–—]?\s*(?:1|I\b|II\b|2))? |
            Software\s+Developer(?:\s*[-–—]?\s*(?:1|I\b|II\b|2))? |
            SW\s+Engineer |
            Full[\s\-]?Stack\s+(?:Software\s+)?(?:Engineer|Developer|Development) |
            (?:Java\s+)?Backend\s+(?:Software\s+)?(?:Engineer|Developer) |
            Frontend\s+(?:Software\s+)?(?:Engineer|Developer) |
            Data\s+(?:Engineer|Scientist|Analayst|Analyst|&\s+Analytics\s+Specialist|Operations\s+Analyst) |
            QA\s+(?:Test\s+)?(?:Engineer|Tester|Analyst)? |
            Quality\s+Assurance\s+(?:Engineer|Tester) |
            DevOps\s+Engineer | Cloud\s+Engineer | Security\s+Engineer |
            Associate\s+Software\s+Analyst | Systems?\s+Engineer | Systems?\s+Analyst |
            Apprentice\s+Tech | Apprentice | Management\s+Trainee |
            Graduate\s+Engineer\s+Trainee | GET |
            Developer(?:\s*[-–—]?\s*(?:1|I\b|II\b|2))? |
            Engineer(?:\s*[-–—]?\s*(?:1|I\b|II\b|2))?
        )
        (?:\s*[-–—]?\s*Intern(?:ship)?)? |
        Intern\s*[-–—]\s*(?:Software|Product\s+Analyst|Engineering|Developer|QA|Data) |
        (?:Software|Full[\s\-]?Stack|Frontend|Backend|QA|Data|Web|Mobile|App|React\s+Native)\s+(?:Developer|Engineer|Development)?\s*[-–—]?\s*Intern(?:ship)?\b
    )
    """,
    re.VERBOSE,
)


def parse_dump_file(filepath: Path = Path("tools/raw_dump.txt")) -> list[dict]:
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    lines = text.splitlines()
    entries = []
    current_entry = None

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        m = DATE_PAT.match(line_clean)
        if m:
            date_str = m.group(1)
            source = m.group(2) or "Direct"
            rest = m.group(3).strip()

            if any(w in rest.lower() for w in ["placement program", "scholarship", "mentorship", "fast track"]):
                continue

            # Strip skill suffix
            m_skill = SKILL_PAT.search(rest)
            if m_skill:
                skill = m_skill.group(1)
                raw_target = rest[:m_skill.start()].strip()
            else:
                skill = ""
                raw_target = rest

            m_title = TITLE_REGEX.search(raw_target)
            if m_title:
                comp = raw_target[:m_title.start()].strip()
                tit = m_title.group(1).strip()

                comp = re.sub(
                    r"(?i)\b(pvt\.?\s*ltd\.?|private\s+limited|inc\.?|llc|llp|technologies\s+pvt\s+ltd)\b",
                    "",
                    comp,
                ).strip(" ,.-/")

                if comp.endswith("Associate"):
                    comp = comp[:-9].strip()
                    if not tit.startswith("Associate"):
                        tit = "Associate " + tit

                tit = re.sub(r"(?i)\bData Analayst\b", "Data Analyst", tit)

                current_entry = {
                    "date": date_str,
                    "source": source,
                    "company": comp,
                    "title": tit,
                    "skills": [skill] if skill else [],
                }
                entries.append(current_entry)
        elif current_entry:
            if len(line_clean) < 40 and not any(w in line_clean.lower() for w in ["scholarship", "placement", "₹"]):
                current_entry["skills"].append(line_clean)

    return entries


def main() -> None:
    print("Parsing tools/raw_dump.txt...")
    raw_entries = parse_dump_file()
    print(f"Extracted {len(raw_entries)} raw job postings.")

    conn = sqlite3.connect("gcc_jobs.db")
    initial_count = conn.execute("SELECT count(*) FROM seen_jobs").fetchone()[0]
    initial_new = conn.execute("SELECT count(*) FROM seen_jobs WHERE status = 'NEW'").fetchone()[0]
    conn.close()
    print(f"Initial DB State: {initial_count} total jobs ({initial_new} NEW)")

    postings: list[JobPosting] = []
    seen_keys: set[tuple[str, str]] = set()

    for entry in raw_entries:
        comp = entry["company"]
        tit = entry["title"]
        date_str = entry["date"]
        skills_str = ", ".join(entry["skills"])

        # Strict quality & entry-level filtering
        is_entry = matches_target_title(tit) or any(
            w in tit.lower() for w in ["intern", "trainee", "apprentice", "associate", "graduate", "sde 1", "sde-1", "engineer 1", "engineer i"]
        )
        if not is_entry:
            continue

        if not is_tech_role(tit):
            continue

        if requires_experienced_candidate(tit):
            continue

        key = (comp.lower().strip(), tit.lower().strip())
        if key in seen_keys:
            continue
        seen_keys.add(key)

        # Resolve careers portal
        portal = resolve_company_career_portal(comp)
        if not portal:
            portal = build_direct_careers_redirect_url(comp, tit)

        # Generate deterministic job ID
        clean_slug = re.sub(r"[^a-zA-Z0-9]+", "_", f"{comp}_{tit}").strip("_").lower()
        hash_suffix = hashlib.md5(f"{comp}_{tit}_{date_str}".encode("utf-8")).hexdigest()[:8]
        job_id = f"dump_{clean_slug[:30]}_{hash_suffix}"

        notes = f"Source: {entry['source']} | Skills: {skills_str}" if skills_str else f"Source: {entry['source']}"

        try:
            posting = JobPosting(
                id=job_id,
                company=comp,
                title=tit,
                location="India",
                apply_url=portal,
                provider=ATSProvider.EMAIL_ALERT,
                published_date=date_str,
                notes=notes,
                is_remote="remote" in comp.lower() or "remote" in tit.lower(),
            )
            postings.append(posting)
        except Exception as exc:
            print(f"[!] Validation error for {comp} - {tit}: {exc}")

    print(f"\nFiltered to {len(postings)} valid entry-level tech roles in India.")

    if postings:
        record_jobs(postings)
        conn = sqlite3.connect("gcc_jobs.db")
        final_count = conn.execute("SELECT count(*) FROM seen_jobs").fetchone()[0]
        final_new = conn.execute("SELECT count(*) FROM seen_jobs WHERE status = 'NEW'").fetchone()[0]
        conn.close()
        print(f"\n[+] Successfully processed and recorded jobs to DB!")
        print(f"Final DB State: {final_count} total jobs ({final_new} NEW)")
        print(f"Net new additions: {final_count - initial_count}")


if __name__ == "__main__":
    main()

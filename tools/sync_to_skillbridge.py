"""Sync verified jobs from gcc_jobs.db directly into SkillBridge."""

import argparse
import json
import logging
from pathlib import Path
import sqlite3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_skillbridge")

DEFAULT_DB = Path(__file__).resolve().parent.parent / "gcc_jobs.db"
DEFAULT_DEST = Path(r"D:\MERN Project\skill-bridge\lib\gcc-jobs.json")


def sync_jobs(db_path: Path = DEFAULT_DB, dest_path: Path = DEFAULT_DEST) -> int:
    """Read active jobs from gcc_jobs.db and export as SkillBridge Job[] JSON."""
    if not db_path.exists():
        logger.error(f"Database not found at {db_path}")
        return 0

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, company, title, location, apply_url, provider, published_date, is_remote, status
        FROM jobs
        WHERE is_active = 1
        ORDER BY published_date DESC, id DESC
        """
    )
    rows = cur.fetchall()
    conn.close()

    skillbridge_jobs = []
    for row in rows:
        jid, company, title, loc, apply_url, provider, pub_date, is_remote, status = row
        t_lower = (title or "").lower()

        # Infer Experience Level
        if any(w in t_lower for w in ["intern", "trainee", "apprentice", "co-op"]):
            level = "Entry Level"
        elif any(w in t_lower for w in ["senior", "lead", "staff", "principal", "manager", "architect"]):
            level = "Senior"
        elif any(w in t_lower for w in ["associate", "junior", "entry", "graduate", "fresher"]):
            level = "Entry Level"
        else:
            level = "Entry Level" if (" 1" in t_lower or "- 1" in t_lower or " sde-1" in t_lower or " i" in t_lower.split()) else "Mid Level"

        # Infer Location Type
        loc_str = loc or "India"
        loc_lower = loc_str.lower()
        if is_remote or "remote" in loc_lower:
            loc_type = "Remote"
        elif "hybrid" in loc_lower:
            loc_type = "Hybrid"
        elif any(hub in loc_lower for hub in ["bangalore", "bengaluru", "hyderabad", "pune", "gurgaon", "noida", "mumbai", "chennai"]):
            loc_type = "Hybrid"
        else:
            loc_type = "On-site"

        # Infer Role Category
        if any(w in t_lower for w in ["frontend", "react", "ui", "web"]):
            role = "Frontend Engineer"
        elif any(w in t_lower for w in ["backend", "java", "python", "golang", "c++"]):
            role = "Backend Engineer"
        elif any(w in t_lower for w in ["data", "analyst", "analytics", "bi", "sql"]):
            role = "Data & Analytics"
        elif any(w in t_lower for w in ["qa", "test", "sdet"]):
            role = "QA & Automation"
        elif any(w in t_lower for w in ["devops", "cloud", "sre", "infrastructure"]):
            role = "Cloud & DevOps"
        elif any(w in t_lower for w in ["ai", "ml", "machine learning", "deep learning"]):
            role = "AI / ML Engineer"
        else:
            role = "Software Engineering"

        # Bracketed GCC compensation estimation
        salary = "?24,00,000 - ?42,00,000 a year" if level == "Senior" else ("?14,00,000 - ?28,00,000 a year" if level == "Mid Level" else "?8,00,000 - ?18,00,000 a year")

        skillbridge_jobs.append({
            "id": jid,
            "title": title,
            "company": company,
            "location": loc_str,
            "locationType": loc_type,
            "source": "External",
            "level": level,
            "salary": salary,
            "role": role,
            "postedAt": pub_date or "Recently",
            "matchScore": 85,
            "recommended": True,
            "description": f"{company} is hiring for {title}. Verified active posting discovered via official {provider.upper()} ATS integration.",
            "highlights": [
                f"Official {provider.upper()} ATS Listing",
                f"Location: {loc_str} ({loc_type})",
                f"Target Level: {level}"
            ],
            "applyUrl": apply_url,
        })

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(json.dumps(skillbridge_jobs, indent=2), encoding="utf-8")
    logger.info(f"Successfully synced {len(skillbridge_jobs)} verified GCC jobs to {dest_path}")
    return len(skillbridge_jobs)


def main():
    parser = argparse.ArgumentParser(description="Sync GCC Job Radar database to SkillBridge.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to gcc_jobs.db")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="Path to SkillBridge gcc-jobs.json")
    args = parser.parse_args()
    count = sync_jobs(args.db, args.dest)
    print(f"[OK] Synced {count} jobs to SkillBridge!")


if __name__ == "__main__":
    main()

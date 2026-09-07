"""Stack-relevance scoring engine for personal job-hunt optimization.

Target Stack:
- Java / Spring Boot 3
- MERN (MongoDB, Express, React, Node.js)
- Apache Kafka
- MySQL & Relational DBs
- JWT (JSON Web Tokens) & Authentication
- Docker & Containerization
- GitHub Actions & CI/CD
"""

import re
from typing import Optional, Sequence

# Tier 1: Core Stack Matches (Highest Priority, +20 to +25 points each)
TIER1_PATTERNS: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"(?i)(?<!javascript\b)\bjava\b(?!script)", re.IGNORECASE), 25, "Java"),
    (re.compile(r"(?i)\bspring(?:\s*boot(?:\s*3)?)?\b", re.IGNORECASE), 25, "Spring Boot"),
    (re.compile(r"(?i)\breact(?:\.js|js)?\b", re.IGNORECASE), 20, "React"),
    (re.compile(r"(?i)\bnode(?:\.js|js)?\b", re.IGNORECASE), 20, "Node.js"),
    (re.compile(r"(?i)\bexpress(?:\.js|js)?\b", re.IGNORECASE), 15, "Express"),
    (re.compile(r"(?i)\bkafka\b", re.IGNORECASE), 20, "Kafka"),
    (re.compile(r"(?i)\bmongo(?:db)?\b", re.IGNORECASE), 20, "MongoDB"),
    (re.compile(r"(?i)\bmysql\b", re.IGNORECASE), 20, "MySQL"),
]

# Tier 2: Essential Architecture & DevOps (High Priority, +10 to +15 points each)
TIER2_PATTERNS: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"(?i)\bdocker\b", re.IGNORECASE), 15, "Docker"),
    (re.compile(r"(?i)\b(?:jwt|json\s+web\s+tokens?)\b", re.IGNORECASE), 15, "JWT"),
    (re.compile(r"(?i)\bgithub\s+actions\b", re.IGNORECASE), 15, "GitHub Actions"),
    (re.compile(r"(?i)\b(?:ci\s*/\s*cd|continuous\s+integration)\b", re.IGNORECASE), 10, "CI/CD"),
    (re.compile(r"(?i)\b(?:rest(?:ful)?(?:\s*apis?)?|microservices?)\b", re.IGNORECASE), 10, "REST/Microservices"),
    (re.compile(r"(?i)\bmern\b", re.IGNORECASE), 25, "MERN"),
]

# Tier 3: Supporting Technologies & Tools (+5 to +10 points each)
TIER3_PATTERNS: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"(?i)\btypescript\b", re.IGNORECASE), 10, "TypeScript"),
    (re.compile(r"(?i)\b(?:sql|postgresql|postgres)\b", re.IGNORECASE), 10, "SQL/PostgreSQL"),
    (re.compile(r"(?i)\bredis\b", re.IGNORECASE), 10, "Redis"),
    (re.compile(r"(?i)\bgit\b", re.IGNORECASE), 5, "Git"),
    (re.compile(r"(?i)\blinux\b", re.IGNORECASE), 5, "Linux"),
]

# Title Affinity Bonus
TITLE_BONUS_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"(?i)(?<!javascript\b)\bjava\b(?!script)"), 20),
    (re.compile(r"(?i)\b(?:backend|back-end)\b"), 15),
    (re.compile(r"(?i)\b(?:full\s*stack|fullstack|mern)\b"), 15),
    (re.compile(r"(?i)\bspring(?:\s*boot)?\b"), 15),
    (re.compile(r"(?i)\b(?:software\s+engineer|sde|mts)\b"), 10),
]

# Non-stack Penalties (roles focused on unrelated ecosystems when target stack is absent)
NON_STACK_DISQUALIFIERS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"(?i)\b(?:ios|swift|android|kotlin|flutter|react\s+native)\b"), 25),
    (re.compile(r"(?i)\b(?:embedded|firmware|hardware|vlsi|verilog)\b"), 30),
    (re.compile(r"(?i)\b(?:salesforce|apex|sap|abap|servicenow)\b"), 30),
    (re.compile(r"(?i)\b(?:ruby|rails|php|laravel|c#|\.net)\b"), 20),
]


def calculate_relevance_score(
    title: str,
    description: str = "",
    skills: Optional[Sequence[str]] = None,
) -> int:
    """Calculate personal stack-relevance score (0 to 100) for a job posting.

    Evaluates title, description, and skill tags against the target stack:
    Java/Spring Boot 3, MERN, Kafka, MongoDB, MySQL, JWT, Docker, GitHub Actions.
    """
    if not title and not description and not skills:
        return 0

    combined_text = f"{title}\n{description}"
    if skills:
        combined_text += f"\n{' '.join(skills)}"

    raw_score = 0
    matched_stack: set[str] = set()

    # 1. Evaluate Tier 1 Core Stack
    for pattern, weight, label in TIER1_PATTERNS:
        if pattern.search(combined_text):
            raw_score += weight
            matched_stack.add(label)

    # 2. Evaluate Tier 2 Architecture & DevOps
    for pattern, weight, label in TIER2_PATTERNS:
        if pattern.search(combined_text):
            raw_score += weight
            matched_stack.add(label)

    # 3. Evaluate Tier 3 Supporting Technologies
    for pattern, weight, label in TIER3_PATTERNS:
        if pattern.search(combined_text):
            raw_score += weight
            matched_stack.add(label)

    # 4. Evaluate Title Affinity Bonus
    for pattern, bonus in TITLE_BONUS_PATTERNS:
        if pattern.search(title):
            raw_score += bonus
            break  # Apply the highest single title bonus

    # 5. Apply Penalties for non-stack roles if no Tier 1 core stack matched
    matched_tier1 = any(pattern.search(combined_text) for pattern, _, _ in TIER1_PATTERNS)
    if not matched_tier1:
        for pattern, penalty in NON_STACK_DISQUALIFIERS:
            if pattern.search(title) or pattern.search(combined_text):
                raw_score -= penalty

    # Normalize to 0 - 100 bounds
    final_score = max(0, min(100, raw_score))
    return final_score


def score_job_posting(job: object) -> int:
    """Compute and attach relevance score to a JobPosting or job dictionary."""
    title = str(getattr(job, "title", "") if not isinstance(job, dict) else job.get("title", ""))
    desc = str(
        getattr(job, "description", "")
        or getattr(job, "content", "")
        or getattr(job, "notes", "")
        if not isinstance(job, dict)
        else (job.get("description") or job.get("content") or job.get("notes") or "")
    )
    score = calculate_relevance_score(title, desc)
    if hasattr(job, "relevance_score"):
        setattr(job, "relevance_score", score)
    return score

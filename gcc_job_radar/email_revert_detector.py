import re
import difflib
import sqlite3
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Tuple, List, Any
from pathlib import Path

from .db import get_applied_jobs, get_db_path, init_db

class RevertType(Enum):
    INTERVIEW = 'INTERVIEW'
    OA_INVITE = 'OA_INVITE'
    REJECTED = 'REJECTED'
    ACKNOWLEDGED = 'ACKNOWLEDGED'
    UNKNOWN = 'UNKNOWN'

@dataclass
class RevertMatch:
    company: str
    revert_type: RevertType
    subject: str
    sender: str
    date: str
    snippet: str
    matched_job_id: Optional[int] = None

_INTERVIEW_PATTERNS = [
    r'interview',
    r'schedule a call',
    r'technical interview',
    r'video interview',
    r'on-site interview',
    r'screening call',
]
_OA_INVITE_PATTERNS = [
    r'online assessment',
    r'coding challenge',
    r'take‑home assignment',
    r'technical exercise',
]
_REJECTED_PATTERNS = [
    r'thank you for your interest',
    r'we have decided to move forward',
    r'not selected',
    r'unfortunately',
    r'rejected',
]
_ACKNOWLEDGED_PATTERNS = [
    r'received your application',
    r'application received',
    r'we have your resume',
    r'thanks for applying',
]

_NOISE_SUBJECTS = [
    'digest',
    'top jobs',
    'openings for you',
    'job alert',
]

def _match_patterns(text: str, patterns: List[str]) -> Optional[str]:
    " Return first matching snippet.
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            start, end = m.start(), m.end()
            return text[max(0, start-50):min(len(text), end+150)].strip()
    return None

def classify_email_content(subject: str, sender: str, body: str) -> Tuple[RevertType, str]:
    Classify email and return snippet.
    sub = subject.lower()
    if any(noise in sub for noise in _NOISE_SUBJECTS):
        return RevertType.UNKNOWN, ''
    for patterns, rtype in [
        (_INTERVIEW_PATTERNS, RevertType.INTERVIEW),
        (_OA_INVITE_PATTERNS, RevertType.OA_INVITE),
        (_REJECTED_PATTERNS, RevertType.REJECTED),
        (_ACKNOWLEDGED_PATTERNS, RevertType.ACKNOWLEDGED),
    ]:
        snippet = _match_patterns(body, patterns) or _match_patterns(subject, patterns)
        if snippet:
            return rtype, snippet
    return RevertType.UNKNOWN, ''

def _extract_company_from_sender(sender: str) -> str:
    try:
        domain = sender.split('@')[-1].lower()
        for pref in ['mail.', 'noreply.', 'no-reply.', 'notification.']:
            if domain.startswith(pref):
                domain = domain[len(pref):]
        return domain.split('.')[0]
    except Exception:
        return ''

def correlate_revert_to_job(conn: sqlite3.Connection, company_hint: str, sender: str) -> Optional[int]:
    applied = get_applied_jobs()
    hint = company_hint.strip().lower()
    best_id = None
    best_score = 0.0
    for job in applied:
        score = difflib.SequenceMatcher(None, job['company'].lower(), hint).ratio()
        if score > best_score:
            best_score = score
            best_id = job['numeric_id']
    if best_score >= 0.7:
        return best_id
    domain = _extract_company_from_sender(sender).lower()
    best_id = None
    best_score = 0.0
    for job in applied:
        score = difflib.SequenceMatcher(None, job['company'].lower(), domain).ratio()
        if score > best_score:
            best_score = score
            best_id = job['numeric_id']
    return best_id if best_score >= 0.7 else None

def process_email_record(uid: str, subject: str, sender: str, date: str, body: str, db_path: Optional[Path] = None) -> Optional[RevertMatch]:
    rtype, snippet = classify_email_content(subject, sender, body)
    if rtype is RevertType.UNKNOWN:
        return None
    hint = subject or sender
    conn = sqlite3.connect(get_db_path(db_path))
    match_id = correlate_revert_to_job(conn, hint, sender)
    conn.close()
    return RevertMatch(company=hint, revert_type=rtype, subject=subject, sender=sender, date=date, snippet=snippet, matched_job_id=match_id)

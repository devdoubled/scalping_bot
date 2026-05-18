from datetime import datetime, timezone, time
from typing import List, Dict


def is_trading_session(utc_now: datetime, sessions: List[Dict]) -> bool:
    """Return True if utc_now falls within any configured trading session."""
    current_time = utc_now.time().replace(second=0, microsecond=0)
    for session in sessions:
        start = _parse_time(session["start_utc"])
        end = _parse_time(session["end_utc"])
        if start <= current_time < end:
            return True
    return False


def get_active_session_name(utc_now: datetime, sessions: List[Dict]) -> str:
    current_time = utc_now.time().replace(second=0, microsecond=0)
    for session in sessions:
        start = _parse_time(session["start_utc"])
        end = _parse_time(session["end_utc"])
        if start <= current_time < end:
            return session["name"]
    return "CLOSED"


def _parse_time(t_str: str) -> time:
    h, m = t_str.split(":")
    return time(int(h), int(m))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

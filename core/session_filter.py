from datetime import datetime, timezone, time, timedelta
from typing import List, Dict, Optional


def is_trading_session(utc_now: datetime, sessions: List[Dict]) -> bool:
    current_time = utc_now.time().replace(second=0, microsecond=0)
    for session in sessions:
        start = _parse_time(session["start_utc"])
        end = _parse_time(session["end_utc"])
        if start <= current_time < end:
            return True
    return False


def get_active_session(utc_now: datetime, sessions: List[Dict]) -> Optional[Dict]:
    """Return the active session dict, or None if outside all sessions."""
    current_time = utc_now.time().replace(second=0, microsecond=0)
    for session in sessions:
        start = _parse_time(session["start_utc"])
        end = _parse_time(session["end_utc"])
        if start <= current_time < end:
            return session
    return None


def get_active_session_name(utc_now: datetime, sessions: List[Dict]) -> str:
    session = get_active_session(utc_now, sessions)
    return session["name"] if session else "CLOSED"


def is_session_ending_soon(utc_now: datetime, session: Optional[Dict], buffer_min: int) -> bool:
    """True if the session ends within buffer_min minutes."""
    if session is None:
        return False
    end = _parse_time(session["end_utc"])
    end_dt = utc_now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    return utc_now >= end_dt - timedelta(minutes=buffer_min)


def _parse_time(t_str: str) -> time:
    h, m = t_str.split(":")
    return time(int(h), int(m))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

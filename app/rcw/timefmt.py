"""Hobart-time helpers for pages and messages."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from . import settings

TZ = ZoneInfo(settings.TZ)


def now() -> datetime:
    return datetime.now(timezone.utc)


def local(ts: datetime | None) -> datetime | None:
    return ts.astimezone(TZ) if ts else None


def today(ts: datetime | None = None) -> date:
    return (ts or now()).astimezone(TZ).date()


def dt(ts: datetime | None) -> str:
    """'Thu 29 Oct 14:02'."""
    return local(ts).strftime("%a %-d %b %H:%M") if ts else "—"


def d(value: date | None) -> str:
    """'29 Oct'."""
    return value.strftime("%-d %b") if value else "****"


def span(seconds: float | None) -> str:
    """'3 d 4 h', '5 h', '40 min'."""
    if seconds is None:
        return "—"
    minutes = int(seconds // 60)
    days, rem = divmod(minutes, 24 * 60)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days} d {hours} h" if hours else f"{days} d"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


def ago(ts: datetime | None) -> str:
    return f"{span((now() - ts).total_seconds())} ago" if ts else "never"

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

import pandas as pd

from .errors import ConfigurationError


UTC = timezone.utc


def parse_utc(value: str | datetime, *, end_of_day: bool = False) -> datetime:
    """Parse an ISO date/time and always return a timezone-aware UTC datetime."""
    date_only = isinstance(value, str) and len(value.strip()) == 10
    try:
        ts = pd.Timestamp(value)
    except Exception as exc:
        raise ConfigurationError(f"Invalid date/time: {value!r}") from exc
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    if end_of_day and date_only:
        ts = ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return ts.to_pydatetime()


def normalize_interval(start: str | datetime, end: str | datetime) -> tuple[datetime, datetime]:
    start_dt = parse_utc(start)
    end_dt = parse_utc(end, end_of_day=True)
    if end_dt < start_dt:
        raise ConfigurationError("The end time must not precede the start time")
    return start_dt, end_dt


def iter_hours(start: datetime, end: datetime) -> Iterator[datetime]:
    current = start.replace(minute=0, second=0, microsecond=0)
    while current < end:
        yield current
        current += timedelta(hours=1)


def iter_days(start: datetime, end: datetime) -> Iterator[datetime]:
    current = start.replace(hour=0, minute=0, second=0, microsecond=0)
    final = end.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= final:
        yield current
        current += timedelta(days=1)


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

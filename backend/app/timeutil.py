"""The athlete's "today" -- computed in ATHLETE_TIMEZONE, not server-local time.

A container's system clock is UTC regardless of where it's actually hosted, so a
plain `date.today()` is wrong for any athlete not in UTC: for timezones behind
UTC it reads a day ahead, for timezones ahead of UTC (e.g. Australia) it reads a
day behind for however many hours into the athlete's day the UTC offset covers.
Every place in this app that means "today" for the athlete -- the Today view,
plan-generation windows, the intervals.icu sync window, the daily job -- should
call `local_today()` here instead of `date.today()` directly."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import ATHLETE_TIMEZONE

_TZ = ZoneInfo(ATHLETE_TIMEZONE)


def local_today() -> date:
    return datetime.now(_TZ).date()

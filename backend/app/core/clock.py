"""Business dates: the desk works in Asia/Jakarta (PIPELINE_TZ) while the containers run in UTC, so "today" taken
from the system clock is the previous day between 00:00 and 07:00 WIB."""
from __future__ import annotations

import re
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.core.config import get_settings


@lru_cache(maxsize=1)
def business_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().pipeline_tz)


def business_today() -> date:
    return datetime.now(business_tz()).date()


_MD = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def valid_month_day(md: str) -> bool:
    """MM-DD that exists in a leap year (02-29 allowed: the seasonal axis is the year 2000); 04-31 is refused."""
    if not _MD.match(md):
        return False
    try:
        date(2000, int(md[:2]), int(md[3:]))
    except ValueError:
        return False
    return True

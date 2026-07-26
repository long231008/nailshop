"""Time helpers anchored to the shop's local timezone.

Everything is stored and compared in UTC. These helpers only decide *where a day
starts*: opening hours, "today" on the dashboard and the daily walk-in counter all
follow the shop's wall clock, which drifts from UTC whenever the UK is on BST.
"""

from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.shared.infrastructure.config.settings import settings


def shop_timezone() -> ZoneInfo:
    return ZoneInfo(settings.SHOP_TIMEZONE)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_in_shop_tz(now: datetime | None = None) -> date_type:
    return (now or now_utc()).astimezone(shop_timezone()).date()


def day_bounds_utc(target_date: date_type) -> tuple[datetime, datetime]:
    """Return the UTC instants bounding one local calendar day."""
    tz = shop_timezone()
    start = datetime.combine(target_date, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def start_of_day_utc(now: datetime | None = None) -> datetime:
    return day_bounds_utc(today_in_shop_tz(now))[0]


def start_of_week_utc(now: datetime | None = None) -> datetime:
    today = today_in_shop_tz(now)
    return day_bounds_utc(today - timedelta(days=today.weekday()))[0]


def start_of_month_utc(now: datetime | None = None) -> datetime:
    return day_bounds_utc(today_in_shop_tz(now).replace(day=1))[0]


def start_of_year_utc(now: datetime | None = None) -> datetime:
    return day_bounds_utc(today_in_shop_tz(now).replace(month=1, day=1))[0]


def opening_window_utc(target_date: date_type) -> tuple[datetime, datetime]:
    """The UTC instants of the shop's opening hours on one local calendar day."""
    tz = shop_timezone()
    open_local = datetime.combine(target_date, time(hour=settings.SHOP_OPEN_HOUR), tzinfo=tz)
    close_local = datetime.combine(target_date, time(hour=settings.SHOP_CLOSE_HOUR), tzinfo=tz)
    return open_local.astimezone(timezone.utc), close_local.astimezone(timezone.utc)


def within_booking_horizon(target_date: date_type, now: datetime | None = None) -> bool:
    today = today_in_shop_tz(now)
    return today <= target_date <= today + timedelta(days=settings.BOOKING_HORIZON_DAYS)

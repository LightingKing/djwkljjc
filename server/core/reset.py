"""Server wall-clock policy; persisted and wire timestamps remain UTC epochs.

ServerClock owns the injectable source and optional test-time offset.  This
policy only decides which local calendar period an already-obtained epoch
belongs to.  Constructing LocalGame directly uses the same Beijing defaults
as starting server.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re

from .clock import ServerClock, parse_refresh_time

DAY_SECONDS = 86400
_EPOCH_DATE = date(1970, 1, 1)


@dataclass(frozen=True)
class ResetPolicy:
    utc_offset_seconds: int = 28800
    daily_refresh: str = "06:00:00"
    signin_refresh: str = "00:00:00"
    week_start: int = 0

    def __post_init__(self) -> None:
        timezone(timedelta(seconds=self.utc_offset_seconds))
        parse_refresh_time(self.daily_refresh)
        parse_refresh_time(self.signin_refresh)
        if not 0 <= self.week_start <= 6:
            raise ValueError("week_start must be Monday=0 through Sunday=6")

    @property
    def timezone(self):
        return timezone(timedelta(seconds=self.utc_offset_seconds))

    def _clock(self, now: int) -> ServerClock:
        return ServerClock(source=lambda: int(now), timezone_=self.timezone)

    def local_datetime(self, now: int) -> datetime:
        return self._clock(now).now_datetime()

    def day_key(self, now: int, *, signin: bool = False) -> str:
        return self._clock(now).daily_key(refresh_at=self.signin_refresh if signin else self.daily_refresh)

    def day_index(self, now: int, *, signin: bool = False) -> int:
        return (date.fromisoformat(self.day_key(now, signin=signin)) - _EPOCH_DATE).days

    def day_start_from_index(self, index: int, *, signin: bool = False) -> int:
        refresh = parse_refresh_time(self.signin_refresh if signin else self.daily_refresh)
        return int(datetime.combine(_EPOCH_DATE + timedelta(days=int(index)), refresh, self.timezone).timestamp())

    def week_key(self, now: int) -> str:
        return self._clock(now).weekly_key(refresh_at=self.daily_refresh, week_start=self.week_start)

    def week_index(self, now: int) -> int:
        return (self.day_index(now) + 3 - self.week_start) // 7

    def period_start(self, now: int, period: str = "day", *, signin: bool = False) -> int:
        if period == "day":
            return self.day_start_from_index(self.day_index(now, signin=signin), signin=signin)
        if period == "week":
            return int(datetime.combine(date.fromisoformat(self.week_key(now)),
                                        parse_refresh_time(self.daily_refresh), self.timezone).timestamp())
        if period == "month":
            local_day = date.fromisoformat(self.day_key(now))
            return int(datetime.combine(local_day.replace(day=1), parse_refresh_time(self.daily_refresh),
                                        self.timezone).timestamp())
        raise ValueError("unknown reset period: " + period)

    def next_boundary(self, now: int, period: str = "day", *, signin: bool = False) -> int:
        clock = self._clock(now)
        if period == "day":
            return int(clock.next_daily_boundary(refresh_at=self.signin_refresh if signin else self.daily_refresh).timestamp())
        if period == "week":
            return int(clock.next_weekly_boundary(refresh_at=self.daily_refresh, week_start=self.week_start).timestamp())
        if period == "month":
            current = self.local_datetime(self.period_start(now, "month"))
            year, month = (current.year + 1, 1) if current.month == 12 else (current.year, current.month + 1)
            return int(current.replace(year=year, month=month).timestamp())
        raise ValueError("unknown reset period: " + period)

    def parse_config_datetime(self, value: str) -> int:
        """Parse a table wall time locally, preserving any explicit ISO offset."""
        text = str(value).strip().replace("Z", "+00:00")
        # Some extracted tables use unpadded month/day components.
        match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?", text)
        if match:
            y, m, d, h, mi, s = (int(v or 0) for v in match.groups())
            return int(datetime(y, m, d, h, mi, s, tzinfo=self.timezone).timestamp())
        current = datetime.fromisoformat(text)
        if current.tzinfo is None:
            current = current.replace(tzinfo=self.timezone)
        return int(current.timestamp())


DEFAULT_RESET_POLICY = ResetPolicy()


def policy_for(game) -> ResetPolicy:
    """Also supports the small game doubles used by isolated module tests."""
    return getattr(game, "reset_policy", DEFAULT_RESET_POLICY)


def period_due(previous, current) -> bool:
    """Only advance a period marker; never clear claims on a clock rollback."""
    if previous is None or previous == "":
        return True
    try:
        return previous < current
    except TypeError:
        return True

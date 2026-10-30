"""Deterministic server clock and reset/recovery primitives.

All gameplay code should obtain the current time from :class:`ServerClock`
instead of calling ``time.time`` directly.  A clock can be shifted in local
admin/test mode while still preserving a real UTC source.  Daily and weekly
period keys are evaluated in a configured timezone and at a configurable
refresh time; this matches the game's 06:00 daily reset without baking that
value into business handlers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import time as _time
from typing import Callable


UTC = timezone.utc


def _coerce_datetime(value: datetime | date | int | float | None, *, tz: timezone = UTC) -> datetime:
    """Return an aware datetime in ``tz`` from common clock inputs."""
    if value is None:
        return datetime.now(tz)
    if isinstance(value, datetime):
        current = value if value.tzinfo is not None else value.replace(tzinfo=tz)
        return current.astimezone(tz)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=tz)
    return datetime.fromtimestamp(float(value), tz=tz)


def parse_refresh_time(value: str | time | None) -> time:
    """Parse a ``HH:MM[:SS]`` reset time, defaulting to midnight."""
    if value is None:
        return time.min
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    raw = str(value).strip()
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"invalid refresh time: {value!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as exc:
        raise ValueError(f"invalid refresh time: {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError(f"invalid refresh time: {value!r}")
    return time(hour, minute, second)


class ServerClock:
    """UTC-backed clock with a mutable local offset.

    ``source`` is injectable so tests and the admin panel can use a fixed
    timeline.  ``offset_seconds`` affects every read and can be changed while
    the process is running; it does not mutate stored timestamps.
    """

    def __init__(
        self,
        *,
        source: Callable[[], float] | None = None,
        offset_seconds: int = 0,
        timezone_: timezone = UTC,
    ) -> None:
        self._source = source or _time.time
        self._offset = int(offset_seconds)
        self.timezone = timezone_

    def now(self) -> int:
        """Return current server epoch seconds as an integer."""
        return int(self._source()) + self._offset

    def now_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.now(), tz=self.timezone)

    def set_offset(self, seconds: int) -> None:
        self._offset = int(seconds)

    def adjust(self, seconds: int) -> int:
        self._offset += int(seconds)
        return self._offset

    @property
    def offset_seconds(self) -> int:
        return self._offset

    def at(self, value: datetime | date | int | float | None = None) -> datetime:
        return _coerce_datetime(self.now() if value is None else value, tz=self.timezone)

    def daily_key(self, value: datetime | date | int | float | None = None, *, refresh_at: str | time | None = None) -> str:
        """Return the date bucket after applying the daily refresh boundary."""
        current = self.at(value)
        refresh = parse_refresh_time(refresh_at)
        if current.timetz().replace(tzinfo=None) < refresh:
            current -= timedelta(days=1)
        return current.date().isoformat()

    def weekly_key(
        self,
        value: datetime | date | int | float | None = None,
        *,
        refresh_at: str | time | None = None,
        week_start: int = 0,
    ) -> str:
        """Return ISO date of the week's configured start boundary.

        ``week_start`` follows Python/ISO weekday numbering (Monday=0,
        Sunday=6).  The result is stable and suitable for persisted reset
        markers.
        """
        if not 0 <= int(week_start) <= 6:
            raise ValueError("week_start must be between 0 and 6")
        current = self.at(value)
        refresh = parse_refresh_time(refresh_at)
        if current.timetz().replace(tzinfo=None) < refresh:
            current -= timedelta(days=1)
        start = current.date() - timedelta(days=(current.weekday() - int(week_start)) % 7)
        return start.isoformat()

    def next_daily_boundary(self, value: datetime | date | int | float | None = None, *, refresh_at: str | time | None = None) -> datetime:
        current = self.at(value)
        refresh = parse_refresh_time(refresh_at)
        candidate = datetime.combine(current.date(), refresh, tzinfo=self.timezone)
        if candidate <= current:
            candidate += timedelta(days=1)
        return candidate

    def next_weekly_boundary(
        self,
        value: datetime | date | int | float | None = None,
        *,
        refresh_at: str | time | None = None,
        week_start: int = 0,
    ) -> datetime:
        if not 0 <= int(week_start) <= 6:
            raise ValueError("week_start must be between 0 and 6")
        current = self.at(value)
        refresh = parse_refresh_time(refresh_at)
        days = (int(week_start) - current.weekday()) % 7
        candidate = datetime.combine(current.date() + timedelta(days=days), refresh, tzinfo=self.timezone)
        if candidate <= current:
            candidate += timedelta(days=7)
        return candidate

    def period_elapsed(
        self,
        previous: datetime | date | int | float,
        current: datetime | date | int | float | None = None,
        *,
        period: str = "daily",
        refresh_at: str | time | None = None,
        week_start: int = 0,
    ) -> int:
        """Count reset boundaries crossed between two timestamps."""
        before = self.at(previous)
        after = self.at(current)
        if after <= before:
            return 0
        if period == "daily":
            before_key = self.daily_key(before, refresh_at=refresh_at)
            after_key = self.daily_key(after, refresh_at=refresh_at)
            return max(0, (date.fromisoformat(after_key) - date.fromisoformat(before_key)).days)
        if period != "weekly":
            raise ValueError("period must be 'daily' or 'weekly'")
        before_key = self.weekly_key(before, refresh_at=refresh_at, week_start=week_start)
        after_key = self.weekly_key(after, refresh_at=refresh_at, week_start=week_start)
        return max(0, (date.fromisoformat(after_key) - date.fromisoformat(before_key)).days // 7)

    def in_window(
        self,
        value: datetime | date | int | float | None,
        *,
        start: datetime | date | int | float,
        end: datetime | date | int | float,
        inclusive_end: bool = False,
    ) -> bool:
        current = self.at(value)
        begin = self.at(start)
        finish = self.at(end)
        return begin <= current <= finish if inclusive_end else begin <= current < finish


@dataclass(frozen=True)
class RecoveryResult:
    balance: int
    recovered: int
    intervals: int
    last_recovery_at: int


def recover_resource(
    *,
    balance: int,
    last_recovery_at: int,
    now: int,
    capacity: int,
    interval_seconds: int,
    amount_per_interval: int = 1,
) -> RecoveryResult:
    """Apply bounded passive recovery without mutating storage.

    Recovery is calculated from elapsed complete intervals.  Future timestamps
    and non-positive intervals are rejected, while an already-full resource
    advances its marker to ``now`` so old elapsed time is not replayed later.
    """
    balance = int(balance)
    last_recovery_at = int(last_recovery_at)
    now = int(now)
    capacity = int(capacity)
    interval_seconds = int(interval_seconds)
    amount_per_interval = int(amount_per_interval)
    if balance < 0 or capacity < 0 or balance > capacity:
        raise ValueError("balance must be between zero and capacity")
    if interval_seconds <= 0 or amount_per_interval <= 0:
        raise ValueError("recovery interval and amount must be positive")
    if now < last_recovery_at:
        raise ValueError("now cannot precede last_recovery_at")
    if balance >= capacity or now == last_recovery_at:
        return RecoveryResult(balance, 0, 0, now if balance >= capacity else last_recovery_at)
    intervals = (now - last_recovery_at) // interval_seconds
    recovered = min(capacity - balance, intervals * amount_per_interval)
    new_balance = balance + recovered
    # Keep unconsumed partial interval time so recovery remains deterministic.
    if new_balance >= capacity:
        marker = now
    else:
        marker = last_recovery_at + (intervals * interval_seconds)
    return RecoveryResult(new_balance, recovered, intervals, marker)


__all__ = [
    "UTC",
    "ServerClock",
    "RecoveryResult",
    "recover_resource",
    "parse_refresh_time",
]



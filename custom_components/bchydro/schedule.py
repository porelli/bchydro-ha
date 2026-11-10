"""When to ask BC Hydro for data, and when it tends to publish it.

BC Hydro publishes usage once a day, normally in the morning (Pacific time), so
polling around the clock mostly fetches nothing new and spends a password login
each time. The daily schedule checks from a configurable time of day, keeps
checking hourly until that day's data shows up, then waits for the next day.

Each day's first sighting of new data is also recorded, so the integration can
tell the user when data for their account usually arrives.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any

# Default time of day to start looking for the day's data (local time)
DEFAULT_CHECK_TIME = time(8, 0)
# How long to wait between checks while the day's data has not arrived yet
# (the default; configurable, and 0 turns retries off)
DEFAULT_RETRY_MINUTES = 60
RETRY_CHOICES_MINUTES = (15, 30, 60, 120, 180, 0)
# An arrival is only timed if the previous check (which did not have the data)
# happened this recently; otherwise all we know is "some time before".
MAX_TIMED_GAP = timedelta(hours=2, minutes=15)
# Data first seen at a check that follows one with yesterday's data only counts
# (untimed) if the checks were part of a normal daily rhythm, not a long outage
MAX_EARLY_GAP = timedelta(hours=30)
# Days of arrival history kept per account
ARRIVAL_HISTORY_DAYS = 30


def newest_actual_reading(hourly: list[dict[str, Any]] | None) -> datetime | None:
    """Return the timestamp of the newest real (not placeholder) hourly reading."""
    newest: datetime | None = None
    for point in hourly or []:
        if point.get("quality") == "INVALID":
            continue  # BC Hydro pads the future with INVALID zero readings
        try:
            stamp = datetime.fromisoformat(str(point.get("date_time")))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            continue
        if newest is None or stamp > newest:
            newest = stamp
    return newest


def has_latest_batch(newest: datetime | None, now: datetime) -> bool:
    """Whether today's publication is in: readings reach at least yesterday.

    ``now`` must be timezone-aware and in the time zone the user thinks in.
    """
    if newest is None:
        return False
    return newest.astimezone(now.tzinfo).date() >= now.date() - timedelta(days=1)


def next_daily_check(
    now: datetime,
    check_time: time,
    have_batch: bool,
    retry: timedelta | None = timedelta(minutes=DEFAULT_RETRY_MINUTES),
) -> datetime:
    """When the daily schedule should check next.

    ``retry`` is how long to wait before looking again while the day's data is
    missing; ``None`` means a single check a day.
    """
    today_check = datetime.combine(now.date(), check_time, tzinfo=now.tzinfo)
    if have_batch:
        # Today's data is in (even if it came before the check time): the next
        # publication is tomorrow's
        return today_check + timedelta(days=1)
    if now < today_check:
        return today_check
    if retry is None:
        # No retries: whatever is missing comes with tomorrow's check
        return today_check + timedelta(days=1)
    return now + retry


def parse_retry(value: Any) -> timedelta | None:
    """Read the configured retry interval (minutes); 0 means no retries."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = DEFAULT_RETRY_MINUTES
    if minutes <= 0:
        return None
    return timedelta(minutes=minutes)


def parse_check_time(value: Any, default: time = DEFAULT_CHECK_TIME) -> time:
    """Read the configured check time ("HH:MM" or "HH:MM:SS", as options store it)."""
    try:
        return time.fromisoformat(str(value))
    except ValueError:
        return default


@dataclass
class Arrival:
    """When a day's data was first seen.

    ``after`` is the previous check, which still lacked it. When that check was
    recent, the data arrived between ``after`` and ``seen``; otherwise only
    "by ``seen``" is known.
    """

    day: date
    seen: datetime
    after: datetime | None

    @property
    def timed(self) -> bool:
        return self.after is not None and self.seen - self.after <= MAX_TIMED_GAP

    @property
    def estimate(self) -> datetime:
        """Best guess of the arrival moment (midpoint of the window)."""
        if self.timed and self.after is not None:
            return self.after + (self.seen - self.after) / 2
        return self.seen

    def as_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "seen": self.seen.isoformat(),
            "after": self.after.isoformat() if self.after else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Arrival | None:
        try:
            return cls(
                day=date.fromisoformat(data["day"]),
                seen=datetime.fromisoformat(data["seen"]),
                after=datetime.fromisoformat(data["after"]) if data.get("after") else None,
            )
        except (KeyError, TypeError, ValueError):
            return None


def _minutes(moment: datetime, tz: Any) -> int:
    local = moment.astimezone(tz)
    return local.hour * 60 + local.minute


def _clock(minutes: float) -> str:
    minutes = int(round(minutes)) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class ArrivalSummary:
    """What the recorded arrivals say about this account."""

    days: int  # days with a recorded arrival
    timed_days: int  # of which the arrival time is known to within ~2 hours
    untimed_days: int  # data was already there when checked; time unknown
    typical: str | None  # "HH:MM", median arrival, None without timed days
    earliest: str | None
    latest: str | None
    # "HH:MM" the data was usually already there by, on the untimed days
    ready_by: str | None = None

    @property
    def state(self) -> str | None:
        """Sensor state: the typical time, or "≤HH:MM" when only that is known."""
        if self.typical is not None:
            return self.typical
        if self.ready_by is not None:
            return f"≤{self.ready_by}"
        return None

    def as_attributes(self) -> dict[str, Any]:
        return {
            "days_observed": self.days,
            "days_timed": self.timed_days,
            "days_not_timed": self.untimed_days,
            "earliest": self.earliest,
            "latest": self.latest,
            "ready_by": self.ready_by,
            "estimate": (
                "timed" if self.typical else "upper_bound" if self.ready_by else None
            ),
        }


def summarize(arrivals: list[Arrival], tz: Any) -> ArrivalSummary:
    """Summarize arrival times as local clock times."""
    timed = [_minutes(a.estimate, tz) for a in arrivals if a.timed]
    # On untimed days the data was there by the check that found it
    seen = [_minutes(a.seen, tz) for a in arrivals if not a.timed]
    return ArrivalSummary(
        days=len(arrivals),
        timed_days=len(timed),
        untimed_days=len(arrivals) - len(timed),
        typical=_clock(median(timed)) if timed else None,
        earliest=_clock(min(timed)) if timed else None,
        latest=_clock(max(timed)) if timed else None,
        ready_by=_clock(median(seen)) if seen else None,
    )


class ArrivalTracker:
    """Track the first sighting of each day's data across checks."""

    def __init__(self) -> None:
        self.arrivals: list[Arrival] = []
        self.last_check: datetime | None = None
        self.last_check_had_batch: bool | None = None
        self.last_newest: datetime | None = None

    def observe(self, now: datetime, newest: datetime | None) -> bool:
        """Record one successful check. Returns True if the history changed."""
        have = has_latest_batch(newest, now)
        changed = False
        if have and not self._recorded_today(now):
            if self.last_check_had_batch is False and self.last_check is not None:
                # The previous check lacked today's data: we saw it arrive
                self.arrivals.append(Arrival(now.date(), now, self.last_check))
                changed = True
            elif (
                self.last_check_had_batch is True
                and self.last_check is not None
                and now - self.last_check <= MAX_EARLY_GAP
                and (self.last_newest is None or newest is None or newest > self.last_newest)
            ):
                # New data, but we last looked yesterday: it came some time before
                self.arrivals.append(Arrival(now.date(), now, None))
                changed = True
        self.last_check = now
        self.last_check_had_batch = have
        if newest is not None:
            self.last_newest = newest
        cutoff = now.date() - timedelta(days=ARRIVAL_HISTORY_DAYS)
        before = len(self.arrivals)
        self.arrivals = [a for a in self.arrivals if a.day > cutoff]
        return changed or len(self.arrivals) != before

    def _recorded_today(self, now: datetime) -> bool:
        return any(a.day == now.date() for a in self.arrivals)

    def as_dict(self) -> dict[str, Any]:
        return {
            "arrivals": [a.as_dict() for a in self.arrivals],
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "last_check_had_batch": self.last_check_had_batch,
            "last_newest": self.last_newest.isoformat() if self.last_newest else None,
        }

    def load(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        self.arrivals = [
            a for a in (Arrival.from_dict(x) for x in data.get("arrivals", [])) if a
        ]
        try:
            self.last_check = (
                datetime.fromisoformat(data["last_check"]) if data.get("last_check") else None
            )
            self.last_newest = (
                datetime.fromisoformat(data["last_newest"]) if data.get("last_newest") else None
            )
        except (TypeError, ValueError):
            self.last_check = self.last_newest = None
        had = data.get("last_check_had_batch")
        self.last_check_had_batch = had if isinstance(had, bool) else None

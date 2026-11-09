"""Tests for the daily schedule and arrival-time tracking."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from custom_components.bchydro.schedule import (
    ArrivalTracker,
    has_latest_batch,
    newest_actual_reading,
    next_daily_check,
    parse_check_time,
    parse_retry,
    summarize,
)

PT = ZoneInfo("America/Vancouver")
EIGHT = time(8, 0)


def at(day: int, hour: int, minute: int = 0, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=PT)


def hourly_until(stamp: str, invalid_after: int = 3) -> list[dict]:
    """BC Hydro-style points: ACTUAL up to ``stamp``, then INVALID padding."""
    end = datetime.fromisoformat(stamp)
    points = [
        {"date_time": (end - timedelta(hours=h)).isoformat(), "quality": "ACTUAL"}
        for h in range(5, -1, -1)
    ]
    points += [
        {"date_time": (end + timedelta(hours=h)).isoformat(), "quality": "INVALID"}
        for h in range(1, invalid_after + 1)
    ]
    return points


# --- reading the data -------------------------------------------------------------


def test_newest_reading_ignores_invalid_padding() -> None:
    newest = newest_actual_reading(hourly_until("2026-11-07T22:00:01-08:00"))
    assert newest == datetime.fromisoformat("2026-11-07T22:00:01-08:00")


def test_newest_reading_of_nothing() -> None:
    assert newest_actual_reading([]) is None
    assert newest_actual_reading(None) is None
    assert newest_actual_reading([{"date_time": "junk", "quality": "ACTUAL"}]) is None


def test_latest_batch_matches_observed_publications() -> None:
    # Seen on the test box: at 10:49 on Nov 8, readings reached Nov 7 22:00
    assert has_latest_batch(datetime.fromisoformat("2026-11-07T22:00:01-08:00"), at(8, 10, 49))
    # ...and at 19:42 on Nov 5 they reached Nov 4 15:00 (a partial day still counts)
    assert has_latest_batch(datetime.fromisoformat("2026-11-04T15:00:01-08:00"), at(5, 19, 42))
    # Before the morning publication, readings stop two days back
    assert not has_latest_batch(datetime.fromisoformat("2026-11-06T22:00:01-08:00"), at(8, 7, 0))
    assert not has_latest_batch(None, at(8, 9, 0))


# --- when to check ----------------------------------------------------------------


def test_waits_for_the_check_time_before_morning() -> None:
    assert next_daily_check(at(8, 2, 30), EIGHT, have_batch=False) == at(8, 8, 0)


def test_retries_hourly_after_the_check_time_until_data_arrives() -> None:
    assert next_daily_check(at(8, 8, 0), EIGHT, have_batch=False) == at(8, 9, 0)
    assert next_daily_check(at(8, 15, 10), EIGHT, have_batch=False) == at(8, 16, 10)


def test_retry_interval_is_configurable() -> None:
    quarter = timedelta(minutes=15)
    assert next_daily_check(at(8, 8, 0), EIGHT, False, retry=quarter) == at(8, 8, 15)
    assert next_daily_check(at(8, 9, 0), EIGHT, False, retry=timedelta(hours=3)) == at(8, 12, 0)


def test_without_retries_a_missed_day_waits_for_tomorrow() -> None:
    assert next_daily_check(at(8, 8, 0), EIGHT, have_batch=False, retry=None) == at(9, 8, 0)
    # Before the check time it still waits for today's check
    assert next_daily_check(at(8, 6, 0), EIGHT, have_batch=False, retry=None) == at(8, 8, 0)


def test_parse_retry() -> None:
    assert parse_retry("30") == timedelta(minutes=30)
    assert parse_retry(120) == timedelta(hours=2)
    assert parse_retry(0) is None
    assert parse_retry("0") is None
    assert parse_retry(None) == timedelta(hours=1)
    assert parse_retry("junk") == timedelta(hours=1)


def test_waits_for_tomorrow_once_data_is_in() -> None:
    assert next_daily_check(at(8, 8, 5), EIGHT, have_batch=True) == at(9, 8, 0)
    # Published unusually early, before the check time: still tomorrow
    assert next_daily_check(at(8, 6, 0), EIGHT, have_batch=True) == at(9, 8, 0)


def test_tomorrow_keeps_the_local_clock_time_across_dst() -> None:
    # Clocks fell back on Nov 1, 2026: 8:00 the next day is still 8:00 local
    assert next_daily_check(at(31, 9, 0, month=10), EIGHT, True) == at(1, 8, 0)
    assert next_daily_check(at(31, 9, 0, month=10), EIGHT, True).hour == 8


def test_parse_check_time() -> None:
    assert parse_check_time("07:30:00", EIGHT) == time(7, 30)
    assert parse_check_time("09:15", EIGHT) == time(9, 15)
    assert parse_check_time(None, EIGHT) == EIGHT
    assert parse_check_time("not a time", EIGHT) == EIGHT


# --- learning when data arrives ---------------------------------------------------


def _day(tracker: ArrivalTracker, day: int, misses: list[int], found: int) -> None:
    """Checks at the given hours on a day: misses first, then the arrival."""
    yesterday = datetime(2026, 11, day - 1, 22, 0, 1, tzinfo=PT)
    two_days = yesterday - timedelta(days=1)
    for hour in misses:
        tracker.observe(at(day, hour), two_days)
    tracker.observe(at(day, found), yesterday)


def test_arrival_is_timed_between_a_miss_and_the_find() -> None:
    tracker = ArrivalTracker()
    _day(tracker, 6, misses=[8], found=9)

    assert len(tracker.arrivals) == 1
    arrival = tracker.arrivals[0]
    assert arrival.timed
    assert arrival.estimate == at(6, 8, 30)


def test_only_the_first_sighting_of_the_day_counts() -> None:
    tracker = ArrivalTracker()
    _day(tracker, 6, misses=[8], found=9)
    tracker.observe(at(6, 10), datetime(2026, 11, 5, 22, 0, 1, tzinfo=PT))

    assert len(tracker.arrivals) == 1


def test_data_already_there_at_first_check_is_counted_but_not_timed() -> None:
    tracker = ArrivalTracker()
    _day(tracker, 6, misses=[8], found=9)
    # Next morning the 8:00 check already has the new day
    tracker.observe(at(7, 8), datetime(2026, 11, 6, 22, 0, 1, tzinfo=PT))

    summary = summarize(tracker.arrivals, PT)
    assert summary.days == 2
    assert summary.timed_days == 1
    assert summary.untimed_days == 1
    assert summary.ready_by == "08:00"
    # A timed arrival exists, so the sensor shows the typical time
    assert summary.state == "08:30"


def test_only_untimed_days_still_say_when_data_was_ready() -> None:
    """Data already there at every 8:00 check: report "by 08:00", not nothing."""
    tracker = ArrivalTracker()
    _day(tracker, 6, misses=[], found=8)  # first check: no history yet
    for day in (7, 8, 9):
        tracker.observe(at(day, 8), datetime(2026, 11, day - 1, 22, 0, 1, tzinfo=PT))

    summary = summarize(tracker.arrivals, PT)
    assert summary.timed_days == 0
    assert summary.untimed_days == 3
    assert summary.typical is None
    assert summary.ready_by == "08:00"
    assert summary.state == "≤08:00"
    assert summary.as_attributes()["estimate"] == "upper_bound"


def test_nothing_observed_has_no_state() -> None:
    summary = summarize([], PT)
    assert summary.state is None
    assert summary.as_attributes()["estimate"] is None


def test_first_check_after_a_long_outage_says_nothing() -> None:
    tracker = ArrivalTracker()
    _day(tracker, 2, misses=[8], found=9)
    # Home Assistant was off for days; the first check finds newer data
    tracker.observe(at(6, 14), datetime(2026, 11, 5, 22, 0, 1, tzinfo=PT))

    assert [a.day.day for a in tracker.arrivals] == [2]


def test_summary_reports_typical_and_range() -> None:
    tracker = ArrivalTracker()
    _day(tracker, 3, misses=[8], found=9)  # ~08:30
    _day(tracker, 4, misses=[8, 9], found=10)  # ~09:30
    _day(tracker, 5, misses=[7], found=8)  # ~07:30

    summary = summarize(tracker.arrivals, PT)
    assert summary.typical == "08:30"
    assert summary.earliest == "07:30"
    assert summary.latest == "09:30"


def test_history_survives_a_restart() -> None:
    tracker = ArrivalTracker()
    _day(tracker, 6, misses=[8], found=9)

    restored = ArrivalTracker()
    restored.load(tracker.as_dict())

    assert restored.arrivals == tracker.arrivals
    assert restored.last_check == tracker.last_check
    assert restored.last_check_had_batch is True


def test_history_is_bounded() -> None:
    tracker = ArrivalTracker()
    for day in range(2, 30):
        _day(tracker, day, misses=[8], found=9)
    # Jump well past the retention window
    tracker.observe(datetime(2026, 12, 31, 9, tzinfo=PT), datetime(2026, 12, 30, 22, tzinfo=PT))

    # Everything older than 30 days is dropped, and the first check after the
    # month-long gap is not counted as an arrival
    assert tracker.arrivals == []


def test_loading_garbage_is_harmless() -> None:
    tracker = ArrivalTracker()
    tracker.load({"arrivals": [{"day": "nope"}], "last_check": "bad"})
    tracker.load(None)
    assert tracker.arrivals == []

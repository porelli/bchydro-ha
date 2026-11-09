"""Tests for rewriting recent statistics from BC Hydro's current data."""
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.bchydro.statistics import StoredHour, _merge_window

UTC = timezone.utc


def utc(day: int, hour: int) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


def bch(label: str, value: float, cost: float = 0.01, quality: str = "ACTUAL") -> dict:
    return {"date_time": label, "value": value, "cost": cost, "quality": quality}


def stored(rows: list[tuple[datetime, float, float]]) -> dict[datetime, StoredHour]:
    return {start: StoredHour(state, total) for start, state, total in rows}


def test_repairs_the_hours_bc_hydro_relabelled_around_its_clock_change() -> None:
    """Production's Nov 1 2026: the first 1 AM missing, later hours an hour late.

    BC Hydro now serves the night correctly, so rewriting the window must put
    every reading back on its hour and keep the running total continuous.
    """
    window = utc(1, 7)
    recorded_energy = stored([
        (utc(1, 7), 0.10, 551.73),
        # 08:00 missing
        (utc(1, 9), 0.09, 551.82),  # actually the 08:00 reading
        (utc(1, 10), 0.08, 551.90),  # actually 09:00
        (utc(1, 11), 0.10, 552.00),  # actually 10:00
    ])
    recorded_cost = {start: StoredHour(0.01, 60 + i * 0.01) for i, start in enumerate(recorded_energy)}
    current = [
        bch("2026-11-01T00:00:01-07:00", 0.10),
        bch("2026-11-01T01:00:01-07:00", 0.09),
        bch("2026-11-01T01:00:01-08:00", 0.08),
        bch("2026-11-01T02:00:01-08:00", 0.10),
        bch("2026-11-01T03:00:01-08:00", 0.09),
    ]

    energy, cost, changed = _merge_window(
        current, window, recorded_energy, recorded_cost, 551.63, 59.99
    )

    assert changed
    # 07:00 was already right; the rewrite starts at the first wrong hour
    assert [(r["start"], r["state"]) for r in energy] == [
        (utc(1, 8), 0.09),
        (utc(1, 9), 0.08),
        (utc(1, 10), 0.10),
        (utc(1, 11), 0.09),
    ]
    assert [round(r["sum"], 2) for r in energy] == [551.82, 551.90, 552.00, 552.09]
    assert len(cost) == len(energy)


def test_up_to_date_window_writes_nothing() -> None:
    window = utc(5, 8)
    recorded_energy = stored([(utc(5, 8), 0.1, 10.1), (utc(5, 9), 0.2, 10.3)])
    recorded_cost = stored([(utc(5, 8), 0.01, 1.01), (utc(5, 9), 0.02, 1.03)])
    current = [bch("2026-11-05T00:00:01-08:00", 0.1, 0.01), bch("2026-11-05T01:00:01-08:00", 0.2, 0.02)]

    _, _, changed = _merge_window(current, window, recorded_energy, recorded_cost, 10.0, 1.0)

    assert not changed


def test_a_revised_reading_updates_that_hour_and_every_later_total() -> None:
    window = utc(5, 8)
    recorded_energy = stored([(utc(5, 8), 0.14, 10.14), (utc(5, 9), 0.2, 10.34)])
    recorded_cost = stored([(utc(5, 8), 0.01, 1.01), (utc(5, 9), 0.02, 1.03)])
    current = [bch("2026-11-05T00:00:01-08:00", 0.34, 0.01), bch("2026-11-05T01:00:01-08:00", 0.2, 0.02)]

    energy, _, changed = _merge_window(current, window, recorded_energy, recorded_cost, 10.0, 1.0)

    assert changed
    assert [round(r["sum"], 2) for r in energy] == [10.34, 10.54]


def test_new_hours_are_appended() -> None:
    window = utc(5, 8)
    recorded_energy = stored([(utc(5, 8), 0.1, 10.1)])
    recorded_cost = stored([(utc(5, 8), 0.01, 1.01)])
    current = [bch("2026-11-05T00:00:01-08:00", 0.1, 0.01), bch("2026-11-05T01:00:01-08:00", 0.5, 0.05)]

    energy, cost, changed = _merge_window(current, window, recorded_energy, recorded_cost, 10.0, 1.0)

    assert changed
    # Only the new hour is written
    assert [round(r["sum"], 2) for r in energy] == [10.6]
    assert [round(r["sum"], 2) for r in cost] == [1.06]


def test_hour_no_longer_reported_keeps_its_recorded_value() -> None:
    """A reading BC Hydro temporarily marks INVALID again must not be erased."""
    window = utc(5, 8)
    recorded_energy = stored([(utc(5, 8), 0.1, 10.1), (utc(5, 9), 0.2, 10.3), (utc(5, 10), 0.3, 10.6)])
    recorded_cost = stored([(utc(5, 8), 0.01, 1.01), (utc(5, 9), 0.02, 1.03), (utc(5, 10), 0.03, 1.06)])
    current = [
        bch("2026-11-05T00:00:01-08:00", 0.1, 0.01),
        bch("2026-11-05T01:00:01-08:00", 0.0, 0.0, quality="INVALID"),
        bch("2026-11-05T02:00:01-08:00", 0.3, 0.03),
    ]

    energy, _, changed = _merge_window(current, window, recorded_energy, recorded_cost, 10.0, 1.0)

    assert not changed
    assert energy == []


def test_hours_before_the_window_are_ignored() -> None:
    window = utc(5, 8)
    current = [bch("2026-11-04T23:00:01-08:00", 9.9), bch("2026-11-05T00:00:01-08:00", 0.1)]

    energy, _, _ = _merge_window(current, window, {}, {}, 10.0, 1.0)

    assert [r["start"] for r in energy] == [utc(5, 8)]
    assert energy[0]["sum"] == pytest.approx(10.1)

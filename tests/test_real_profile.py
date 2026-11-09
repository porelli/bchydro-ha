"""Tests against the real shape of BC Hydro's data (captured Nov 2026).

The mocked profile in conftest.py carries fields BC Hydro never sends
(ratePlanName, isRIB), so the rate plan sensor passed its tests while reading
"unknown" on every real account.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.bchydro.api import BCHydroApiClient
from custom_components.bchydro.coordinator import BCHydroDataUpdateCoordinator

# global-data.html for a residential account, Nov 9 2026 (identifiers changed)
REAL_GLOBAL_DATA = {
    "accountType": None,
    "consumptionGroup": "CONSUMPTION",
    "evpAccount": "000012345678",
    "evpAccountId": "ABCDEF",
    "evpBillingStart": "2026-10-27T00:00:00-07:00",
    "evpBillingEnd": "2026-11-24T23:00:00-08:00",
    "evpConsToDate": 67.0,
    "evpCostToDate": 10.0,
    "evpCurrentDateTime": "2026-11-09T11:24:45-08:00",
    "evpDaysInBillingPeriod": 29,
    "evpEstConsCurPeriod": 195.0,
    "evpEstCostCurPeriod": 30.0,
    "evpProfileId": "someone",
    "evpSlid": "0010000000",
    "ratePlanType": "RIB",
    "rateShowsCosts": True,
    "timezone": "PST",
    "tier2Date": None,
    "tier2Percentage": None,
    "yesterdayDate": "2026-11-07T23:00:00-08:00",
    "yesterdayPercentage": 34,
}


async def test_rate_plan_comes_from_the_real_field() -> None:
    client = BCHydroApiClient("a@example.com", "pw")
    profile = client._parse_account_profile_json(REAL_GLOBAL_DATA)

    assert profile["ratePlanType"] == "RIB"
    assert profile["ratePlanName"] == "Residential tiered rate"
    assert profile["isRIB"] is True


async def test_unknown_rate_plan_shows_its_code() -> None:
    client = BCHydroApiClient("a@example.com", "pw")
    profile = client._parse_account_profile_json({**REAL_GLOBAL_DATA, "ratePlanType": "XYZ"})

    assert profile["ratePlanName"] == "XYZ"
    assert profile["isRIB"] is False


async def test_missing_rate_plan_stays_unknown() -> None:
    client = BCHydroApiClient("a@example.com", "pw")
    data = dict(REAL_GLOBAL_DATA)
    del data["ratePlanType"]
    profile = client._parse_account_profile_json(data)

    assert profile["ratePlanName"] is None
    assert profile["isRIB"] is None


# --- days follow BC's clock --------------------------------------------------------


def _hours(first: str, count: int) -> list[dict]:
    """Hourly ACTUAL points labelled the way BC Hydro does (its -08:00 offset)."""
    from datetime import timedelta

    start = datetime.fromisoformat(first)
    return [
        {
            "date_time": (start + timedelta(hours=h)).isoformat(),
            "value": 1.0,
            "cost": 0.1,
            "quality": "ACTUAL",
        }
        for h in range(count)
    ]


@pytest.fixture
def bc_time_zone():
    """BC stays on UTC-7 all year from November 2026."""
    previous = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(ZoneInfo("America/Vancouver"))
    yield
    dt_util.set_default_time_zone(previous)


@pytest.mark.usefixtures("bc_time_zone")
def test_days_are_split_at_bc_midnight(hass) -> None:
    """BC Hydro labels hours -08:00 while BC is at -07:00; a day starts at 23:00-08:00.

    BC Hydro published Nov 8 (BC time) as 23:00-08:00 Nov 7 .. 22:00-08:00 Nov 8.
    Splitting by the label's date cut that day at 1 AM and left "yesterday" an
    hour short.
    """
    coordinator = BCHydroDataUpdateCoordinator.__new__(BCHydroDataUpdateCoordinator)
    hourly = _hours("2026-11-07T23:00:01-08:00", 24)

    daily = coordinator._calculate_daily_from_hourly(hourly)

    assert [d["date_time"][:10] for d in daily] == ["2026-11-08"]
    assert daily[0]["value"] == pytest.approx(24.0)


@pytest.mark.usefixtures("bc_time_zone")
def test_days_before_the_change_are_unaffected(hass) -> None:
    """In October BC Hydro labelled -07:00 like BC itself."""
    coordinator = BCHydroDataUpdateCoordinator.__new__(BCHydroDataUpdateCoordinator)
    hourly = _hours("2026-10-20T00:00:01-07:00", 48)

    daily = coordinator._calculate_daily_from_hourly(hourly)

    assert [d["date_time"][:10] for d in daily] == ["2026-10-20", "2026-10-21"]
    assert [d["value"] for d in daily] == [pytest.approx(24.0), pytest.approx(24.0)]

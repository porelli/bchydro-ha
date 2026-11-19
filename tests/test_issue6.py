"""Issue #6 and the failed morning checks seen on production (Nov 14-18 2026).

- "Yesterday consumption" showed 1.68 kWh for a home using about 63 kWh a day:
  the last group of hours, a single hour, was taken as a day. Days must follow
  BC time whatever Home Assistant's time zone is, and "yesterday" must be a
  complete day.
- BC Hydro is slow while it publishes (~8:00 BC): requests took 25-90 s and
  some passed the 30 s timeout. A check whose consumption fetch or statistics
  import failed was treated as done, so nothing was retried until the next
  morning and that day was missing from the Energy dashboard.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.bchydro.api import BCHydroConnectionError
from custom_components.bchydro.coordinator import BCHydroDataUpdateCoordinator
from custom_components.bchydro.sensor import get_yesterday_data
from tests.conftest import create_config_entry

BC = ZoneInfo("America/Vancouver")


def _hours(first: str, count: int, value: float = 2.6) -> list[dict]:
    start = datetime.fromisoformat(first)
    return [
        {"date_time": (start + timedelta(hours=h)).isoformat(), "value": value, "cost": 0.3, "quality": "ACTUAL"}
        for h in range(count)
    ]


@pytest.fixture
def other_time_zone():
    """A Home Assistant set to a time zone east of BC."""
    previous = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(ZoneInfo("America/Chicago"))
    yield
    dt_util.set_default_time_zone(previous)


# --- "yesterday" ------------------------------------------------------------------


@pytest.mark.usefixtures("other_time_zone")
def test_days_follow_bc_time_whatever_home_assistants_time_zone(hass) -> None:
    """A full BC day must stay one day when HA runs on Central time."""
    coordinator = BCHydroDataUpdateCoordinator.__new__(BCHydroDataUpdateCoordinator)

    daily = coordinator._calculate_daily_from_hourly(_hours("2026-11-17T00:00:01-07:00", 24))

    assert [d["date_time"][:10] for d in daily] == ["2026-11-17"]
    assert daily[0]["value"] == pytest.approx(24 * 2.6)
    assert daily[0]["hours"] == 24


def test_yesterday_is_the_latest_complete_day() -> None:
    """A trailing group of one hour is not yesterday (it showed 1.68 kWh)."""
    data = {
        "consumption": {
            "daily_consumption": [
                {"date_time": "2026-11-16T00:00:00-07:00", "value": 63.0, "quality": "ACTUAL", "hours": 24},
                {"date_time": "2026-11-17T00:00:00-07:00", "value": 1.68, "quality": "ACTUAL", "hours": 1},
            ]
        }
    }

    assert get_yesterday_data(data)["value"] == 63.0


def test_days_without_an_hour_count_still_count() -> None:
    """Data cached by an older version has no hour count."""
    data = {"consumption": {"daily_consumption": [{"date_time": "x", "value": 5.0, "quality": "ACTUAL"}]}}

    assert get_yesterday_data(data)["value"] == 5.0


# --- failed morning checks --------------------------------------------------------


def _coordinator(hass, mock_config_entry_data, profile):
    entry = create_config_entry(title="BC Hydro (test@example.com)", data=mock_config_entry_data)
    client = AsyncMock()
    client.get_account_profile = AsyncMock(return_value=profile)
    return BCHydroDataUpdateCoordinator(hass, client, entry)


def _local(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)


async def test_failed_statistics_import_is_retried_the_same_morning(
    hass: HomeAssistant, freezer, mock_config_entry_data, mock_account_profile_data
) -> None:
    """The data was in but the Energy dashboard did not get it: check again soon."""
    freezer.move_to(_local("2026-11-18T08:00:00"))
    coordinator = _coordinator(hass, mock_config_entry_data, mock_account_profile_data)
    coordinator.client.get_consumption_data = AsyncMock(
        return_value={"hourly_consumption": _hours("2026-11-17T00:00:01-08:00", 24)}
    )
    with patch(
        "custom_components.bchydro.coordinator.async_import_statistics",
        AsyncMock(side_effect=BCHydroConnectionError("Network error fetching consumption data: ")),
    ), patch(
        "custom_components.bchydro.coordinator.async_cleanup_future_statistics",
        AsyncMock(return_value=0),
    ):
        await coordinator._async_update_data()

    assert coordinator.next_check == _local("2026-11-18T09:00:00")


async def test_failed_consumption_fetch_keeps_the_last_good_days(
    hass: HomeAssistant, freezer, mock_config_entry_data, mock_account_profile_data
) -> None:
    """A slow morning must not blank "yesterday" for an hour."""
    coordinator = _coordinator(hass, mock_config_entry_data, mock_account_profile_data)
    with patch(
        "custom_components.bchydro.coordinator.async_import_statistics", AsyncMock()
    ), patch(
        "custom_components.bchydro.coordinator.async_cleanup_future_statistics",
        AsyncMock(return_value=0),
    ):
        freezer.move_to(_local("2026-11-17T08:00:00"))
        coordinator.client.get_consumption_data = AsyncMock(
            return_value={"hourly_consumption": _hours("2026-11-16T00:00:01-08:00", 24)}
        )
        good = await coordinator._async_update_data()

        freezer.move_to(_local("2026-11-18T08:00:00"))
        coordinator.client.get_consumption_data = AsyncMock(
            side_effect=BCHydroConnectionError("Network error fetching consumption data: ")
        )
        data = await coordinator._async_update_data()

    assert data["consumption"]["daily_consumption"] == good["consumption"]["daily_consumption"]
    assert get_yesterday_data(data) is not None
    # The fetch failed: try again soon instead of trusting old data until tomorrow
    assert coordinator.next_check == _local("2026-11-18T09:00:00")


async def test_consumption_requests_allow_for_slow_mornings() -> None:
    """BC Hydro took up to 90 s while publishing; 30 s was too short."""
    from custom_components.bchydro.api import CONSUMPTION_TIMEOUT, BCHydroApiClient

    session = AsyncMock()
    response = AsyncMock()
    response.status = 200
    response.headers = {"Content-Type": "text/xml"}
    response.url = "https://app.bchydro.com/evportlet/web/consumption-data.html"
    response.text = AsyncMock(return_value="<?xml version='1.0'?><Data/>")
    response.__aenter__.return_value = response
    session.post = lambda *args, **kwargs: (session.calls.append(kwargs), response)[1]
    session.calls = []
    session.cookie_jar = None
    client = BCHydroApiClient("a@example.com", "pw", session=session)
    client._authenticated = True

    await client.get_consumption_data()

    assert session.calls[0]["timeout"] is CONSUMPTION_TIMEOUT
    assert CONSUMPTION_TIMEOUT.total >= 120


async def test_diagnostics_show_what_issue_6_needed(
    hass: HomeAssistant, freezer, mock_config_entry_data, mock_account_profile_data
) -> None:
    """Time zones, the newest days with their hour counts, and BC Hydro's labels."""
    from custom_components.bchydro.diagnostics import async_get_config_entry_diagnostics

    freezer.move_to(_local("2026-11-18T08:00:00"))
    coordinator = _coordinator(hass, mock_config_entry_data, mock_account_profile_data)
    coordinator.client.get_consumption_data = AsyncMock(
        return_value={"hourly_consumption": _hours("2026-11-16T00:00:01-07:00", 48)}
    )
    with patch("custom_components.bchydro.coordinator.async_import_statistics", AsyncMock()), patch(
        "custom_components.bchydro.coordinator.async_cleanup_future_statistics", AsyncMock(return_value=0)
    ):
        coordinator.data = await coordinator._async_update_data()
    coordinator.config_entry.runtime_data = coordinator

    result = await async_get_config_entry_diagnostics(hass, coordinator.config_entry)

    assert result["coordinator"]["home_assistant_time_zone"] == str(hass.config.time_zone)
    assert result["coordinator"]["bc_time_zone"] == "America/Vancouver"
    assert result["coordinator"]["next_check"] == "2026-11-19T08:00:00-08:00"
    assert result["coordinator"]["last_check_incomplete"] is False
    assert [d["hours"] for d in result["data"]["consumption"]["sample_daily_data"]] == [24, 24]
    assert result["data"]["consumption"]["latest_hourly"][-1]["date_time"] == "2026-11-17T23:00:01-07:00"
    assert "state" in result["data"]["arrival"]
    # Still nothing personal in it
    assert result["data"]["profile"]["evpAccountId"] == "**REDACTED**"

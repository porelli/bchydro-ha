"""Tests for the coordinator's daily update schedule."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.bchydro.api import BCHydroConnectionError
from custom_components.bchydro.coordinator import (
    BCHydroDataUpdateCoordinator,
    resolve_update_mode,
)
from tests.conftest import create_config_entry


@pytest.fixture(autouse=True)
def mock_statistics_functions():
    """Statistics helpers need the recorder."""
    with patch(
        "custom_components.bchydro.coordinator.async_import_statistics",
        new_callable=AsyncMock,
    ), patch(
        "custom_components.bchydro.coordinator.async_cleanup_future_statistics",
        new_callable=AsyncMock,
        return_value=0,
    ):
        yield


def _hourly_until(stamp: str) -> list[dict]:
    end = datetime.fromisoformat(stamp)
    points = [
        {"date_time": (end - timedelta(hours=h)).isoformat(), "quality": "ACTUAL",
         "value": 0.1, "cost": 0.01}
        for h in range(3, -1, -1)
    ]
    points.append(
        {"date_time": (end + timedelta(hours=1)).isoformat(), "quality": "INVALID",
         "value": 0, "cost": 0}
    )
    return points


def _coordinator(hass, mock_config_entry_data, profile, options=None, newest=None, error=None):
    entry = create_config_entry(
        title="BC Hydro (test@example.com)", data=mock_config_entry_data, options=options or {}
    )
    client = AsyncMock()
    if error:
        client.get_account_profile = AsyncMock(side_effect=error)
    else:
        client.get_account_profile = AsyncMock(return_value=profile)
    client.get_consumption_data = AsyncMock(
        return_value={"hourly_consumption": _hourly_until(newest)} if newest else {}
    )
    return BCHydroDataUpdateCoordinator(hass, client, entry)


def _local(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)


# --- choosing the schedule --------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, "daily"),
        ({"update_interval": 60}, "daily"),  # saved the default: never chose
        ({"update_interval": 30}, "interval"),  # deliberately polls faster
        ({"update_interval": 360}, "interval"),
        ({"update_mode": "interval", "update_interval": 60}, "interval"),
        ({"update_mode": "daily", "update_interval": 30}, "daily"),
        ({"update_mode": "bogus"}, "daily"),
    ],
)
def test_existing_entries_pick_a_schedule(options, expected) -> None:
    assert resolve_update_mode(options) == expected


# --- following the schedule -------------------------------------------------------


async def test_waits_until_tomorrow_once_data_is_in(
    hass: HomeAssistant, freezer, mock_config_entry_data, mock_account_profile_data
) -> None:
    freezer.move_to(_local("2026-11-08T09:30:00"))
    coordinator = _coordinator(
        hass, mock_config_entry_data, mock_account_profile_data,
        newest="2026-11-07T22:00:01-08:00",
    )

    data = await coordinator._async_update_data()

    assert coordinator.next_check == _local("2026-11-09T08:00:00")
    assert coordinator.update_interval == timedelta(hours=22, minutes=30)
    assert data["arrival"]["next_check"] == coordinator.next_check.isoformat()
    assert data["arrival"]["update_schedule"] == "daily"


async def test_retries_hourly_while_data_is_missing(
    hass: HomeAssistant, freezer, mock_config_entry_data, mock_account_profile_data
) -> None:
    freezer.move_to(_local("2026-11-08T08:00:00"))
    coordinator = _coordinator(
        hass, mock_config_entry_data, mock_account_profile_data,
        newest="2026-11-06T22:00:01-08:00",
    )

    await coordinator._async_update_data()

    assert coordinator.next_check == _local("2026-11-08T09:00:00")
    assert coordinator.update_interval == timedelta(hours=1)


async def test_failure_before_the_check_time_waits_for_it(
    hass: HomeAssistant, freezer, mock_config_entry_data
) -> None:
    freezer.move_to(_local("2026-11-08T05:00:00"))
    coordinator = _coordinator(
        hass, mock_config_entry_data, None, error=BCHydroConnectionError("down")
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator.next_check == _local("2026-11-08T08:00:00")


async def test_custom_check_time(
    hass: HomeAssistant, freezer, mock_config_entry_data, mock_account_profile_data
) -> None:
    freezer.move_to(_local("2026-11-08T05:00:00"))
    coordinator = _coordinator(
        hass, mock_config_entry_data, mock_account_profile_data,
        options={"daily_check_time": "07:15:00"},
        newest="2026-11-06T22:00:01-08:00",
    )

    await coordinator._async_update_data()

    assert coordinator.next_check == _local("2026-11-08T07:15:00")


@pytest.mark.parametrize(
    ("retry", "expected"),
    [
        ("15", "2026-11-08T08:15:00"),
        ("180", "2026-11-08T11:00:00"),
        (0, "2026-11-09T08:00:00"),  # retries off: tomorrow's check
    ],
)
async def test_configured_retry_while_data_is_missing(
    hass: HomeAssistant, freezer, mock_config_entry_data, mock_account_profile_data, retry, expected
) -> None:
    freezer.move_to(_local("2026-11-08T08:00:00"))
    coordinator = _coordinator(
        hass, mock_config_entry_data, mock_account_profile_data,
        options={"daily_retry_minutes": retry},
        newest="2026-11-06T22:00:01-08:00",
    )

    data = await coordinator._async_update_data()

    assert coordinator.next_check == _local(expected)
    assert data["arrival"]["daily_retry_minutes"] == int(retry)


async def test_fixed_interval_is_left_alone(
    hass: HomeAssistant, freezer, mock_config_entry_data, mock_account_profile_data
) -> None:
    freezer.move_to(_local("2026-11-08T09:30:00"))
    coordinator = _coordinator(
        hass, mock_config_entry_data, mock_account_profile_data,
        options={"update_mode": "interval", "update_interval": 45},
        newest="2026-11-07T22:00:01-08:00",
    )

    await coordinator._async_update_data()

    assert coordinator.next_check is None
    assert coordinator.update_interval == timedelta(minutes=45)


# --- learning when data arrives ---------------------------------------------------


async def test_learns_when_data_arrives(
    hass: HomeAssistant, freezer, mock_config_entry_data, mock_account_profile_data
) -> None:
    coordinator = _coordinator(
        hass, mock_config_entry_data, mock_account_profile_data,
        newest="2026-11-06T22:00:01-08:00",
    )
    freezer.move_to(_local("2026-11-08T08:00:00"))
    await coordinator._async_update_data()  # not there yet

    coordinator.client.get_consumption_data = AsyncMock(
        return_value={"hourly_consumption": _hourly_until("2026-11-07T22:00:01-08:00")}
    )
    freezer.move_to(_local("2026-11-08T09:00:00"))
    data = await coordinator._async_update_data()

    assert data["arrival"]["typical"] == "08:30"
    assert data["arrival"]["days_timed"] == 1
    assert coordinator.arrival_placeholders()["arrival_typical"] == "08:30"


async def test_arrival_history_survives_a_restart(
    hass: HomeAssistant, freezer, hass_storage, mock_config_entry_data, mock_account_profile_data
) -> None:
    coordinator = _coordinator(
        hass, mock_config_entry_data, mock_account_profile_data,
        newest="2026-11-06T22:00:01-08:00",
    )
    freezer.move_to(_local("2026-11-08T08:00:00"))
    await coordinator._async_update_data()
    coordinator.client.get_consumption_data = AsyncMock(
        return_value={"hourly_consumption": _hourly_until("2026-11-07T22:00:01-08:00")}
    )
    freezer.move_to(_local("2026-11-08T09:00:00"))
    await coordinator._async_update_data()
    freezer.tick(timedelta(seconds=30))  # let the delayed save run
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    restored = BCHydroDataUpdateCoordinator(
        hass, coordinator.client, coordinator.config_entry
    )
    await restored.async_load_arrivals()

    assert restored.arrival_placeholders()["arrival_typical"] == "08:30"


async def test_unknown_arrival_reads_as_a_dash(
    hass: HomeAssistant, mock_config_entry_data, mock_account_profile_data
) -> None:
    coordinator = _coordinator(hass, mock_config_entry_data, mock_account_profile_data)
    assert coordinator.arrival_placeholders() == {
        "arrival_typical": "—",
        "arrival_earliest": "—",
        "arrival_latest": "—",
        "arrival_days": "0",
        "arrival_untimed_days": "0",
        "arrival_ready_by": "—",
    }


# --- arrival timing under fixed-interval polling ----------------------------------


async def _poll(coordinator, freezer, at: str, newest: str) -> dict:
    coordinator.client.get_consumption_data = AsyncMock(
        return_value={"hourly_consumption": _hourly_until(newest)}
    )
    freezer.move_to(_local(at))
    return await coordinator._async_update_data()


async def test_hourly_polling_times_the_arrival(
    hass: HomeAssistant, freezer, mock_config_entry_data, mock_account_profile_data
) -> None:
    """Fixed hourly polling sees the publication happen: it is timed."""
    coordinator = _coordinator(
        hass, mock_config_entry_data, mock_account_profile_data,
        options={"update_mode": "interval", "update_interval": 60},
    )
    await _poll(coordinator, freezer, "2026-11-08T07:00:00", "2026-11-06T22:00:01-08:00")
    data = await _poll(coordinator, freezer, "2026-11-08T08:00:00", "2026-11-07T22:00:01-08:00")

    assert data["arrival"]["typical"] == "07:30"
    assert data["arrival"]["days_timed"] == 1
    assert data["arrival"]["days_not_timed"] == 0


async def test_slow_polling_cannot_time_the_arrival(
    hass: HomeAssistant, freezer, mock_config_entry_data, mock_account_profile_data
) -> None:
    """Six hours between checks is too wide a window to call a time."""
    coordinator = _coordinator(
        hass, mock_config_entry_data, mock_account_profile_data,
        options={"update_mode": "interval", "update_interval": 360},
    )
    await _poll(coordinator, freezer, "2026-11-08T03:00:00", "2026-11-06T22:00:01-08:00")
    data = await _poll(coordinator, freezer, "2026-11-08T09:00:00", "2026-11-07T22:00:01-08:00")

    assert data["arrival"]["typical"] is None
    assert data["arrival"]["days_timed"] == 0
    assert data["arrival"]["days_not_timed"] == 1


async def test_data_already_there_each_morning_reads_as_ready_by(
    hass: HomeAssistant, freezer, mock_config_entry_data, mock_account_profile_data
) -> None:
    """Published before the 8:00 check: the sensor says "≤08:00", not unknown."""
    coordinator = _coordinator(hass, mock_config_entry_data, mock_account_profile_data)
    await _poll(coordinator, freezer, "2026-11-08T08:00:00", "2026-11-07T22:00:01-08:00")
    data = await _poll(coordinator, freezer, "2026-11-09T08:00:00", "2026-11-08T22:00:01-08:00")

    assert data["arrival"]["state"] == "≤08:00"
    assert data["arrival"]["typical"] is None
    assert data["arrival"]["ready_by"] == "08:00"
    assert data["arrival"]["estimate"] == "upper_bound"
    assert coordinator.arrival_placeholders()["arrival_ready_by"] == "08:00"

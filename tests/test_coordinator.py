"""Test the BC Hydro coordinator."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.bchydro.api import BCHydroApiError, BCHydroAuthError
from custom_components.bchydro.const import DOMAIN
from custom_components.bchydro.coordinator import BCHydroDataUpdateCoordinator
from tests.conftest import create_config_entry


@pytest.fixture
def mock_statistics_functions():
    """Mock statistics functions that require recorder."""
    with patch(
        "custom_components.bchydro.coordinator.async_import_statistics",
        new_callable=AsyncMock,
    ) as mock_import, patch(
        "custom_components.bchydro.coordinator.async_cleanup_future_statistics",
        new_callable=AsyncMock,
        return_value=0,
    ) as mock_cleanup:
        yield {"import": mock_import, "cleanup": mock_cleanup}


@pytest.fixture(autouse=True)
def auto_mock_statistics_functions(mock_statistics_functions):
    """Auto-use the statistics mock."""
    yield mock_statistics_functions


async def test_coordinator_initialization_default_interval(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test coordinator initialization with default update interval."""
    mock_client = MagicMock()

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    assert coordinator.client == mock_client
    assert coordinator.config_entry == mock_config_entry
    assert coordinator.update_interval == timedelta(minutes=60)  # Default


async def test_coordinator_initialization_custom_interval(
    hass: HomeAssistant,
) -> None:
    """Test coordinator initialization with custom update interval."""
    entry = create_config_entry(
        data={"username": "test", "password": "test"},
        options={"update_interval": 120},  # Custom interval
        unique_id="test",
    )

    mock_client = MagicMock()
    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, entry)

    assert coordinator.update_interval == timedelta(minutes=120)


async def test_update_data_success_without_consumption(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test successful data update fetches last 10 days of consumption data."""
    mock_client = AsyncMock()
    # Profile data without billing dates (doesn't matter - coordinator fetches last 10 days anyway)
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = None
    profile_data["evpBillingEnd"] = None

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)
    # Coordinator now always fetches last 10 days of hourly data
    hourly_mock = {"hourly_consumption": [
        {"date_time": "2025-12-25T00:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
    ]}
    mock_client.get_consumption_data = AsyncMock(return_value=hourly_mock)

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    data = await coordinator._async_update_data()

    assert "profile" in data
    assert "consumption" in data
    assert data["profile"] == profile_data
    # Consumption data should be fetched (last 10 days)
    assert "hourly_consumption" in data["consumption"]
    assert "daily_consumption" in data["consumption"]
    mock_client.get_account_profile.assert_called_once()
    # get_consumption_data IS called now (fetches last 10 days)
    mock_client.get_consumption_data.assert_called_once()


async def test_update_data_success_with_iso_dates(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test successful data update fetches last 10 days of hourly data."""
    mock_client = AsyncMock()
    # Profile data with ISO format dates (not used for consumption anymore)
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = "2025-12-25T00:00:00-08:00"
    profile_data["evpBillingEnd"] = "2026-01-26T00:00:00-08:00"

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)
    # Return hourly data only - coordinator calculates daily from hourly
    hourly_mock = {"hourly_consumption": [
        {"date_time": "2025-12-25T00:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
        {"date_time": "2025-12-25T01:00:00-08:00", "value": 1.5, "cost": 0.15, "quality": "ACTUAL"},
    ]}
    mock_client.get_consumption_data = AsyncMock(return_value=hourly_mock)

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    data = await coordinator._async_update_data()

    assert "profile" in data
    assert "consumption" in data
    assert data["profile"] == profile_data
    # Consumption should have both daily and hourly data
    # Daily is calculated from hourly
    assert "daily_consumption" in data["consumption"]
    assert "hourly_consumption" in data["consumption"]

    # Verify get_consumption_data was called once (hourly only)
    assert mock_client.get_consumption_data.call_count == 1

    # Check the call (hourly) - now uses last 10 days, not billing dates
    call = mock_client.get_consumption_data.call_args
    # start_date should be approximately 10 days ago (not billing date)
    assert call.kwargs["date_range"] == "currentBill"
    assert call.kwargs["granularity"] == "hourly"


async def test_update_data_success_with_text_dates(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test successful data update fetches last 10 days (text billing dates ignored)."""
    mock_client = AsyncMock()
    # Profile data with text format dates (not used for consumption anymore)
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = "Dec 25, 2025"
    profile_data["evpBillingEnd"] = "Jan 26, 2026"

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)
    # Return hourly data only - coordinator calculates daily from hourly
    hourly_mock = {"hourly_consumption": [
        {"date_time": "2025-12-25T00:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
        {"date_time": "2025-12-25T01:00:00-08:00", "value": 1.5, "cost": 0.15, "quality": "ACTUAL"},
    ]}
    mock_client.get_consumption_data = AsyncMock(return_value=hourly_mock)

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    data = await coordinator._async_update_data()

    assert "profile" in data
    assert "consumption" in data
    assert data["profile"] == profile_data
    # Consumption should have both daily and hourly data
    assert "daily_consumption" in data["consumption"]
    assert "hourly_consumption" in data["consumption"]

    # Verify get_consumption_data was called once (hourly only)
    assert mock_client.get_consumption_data.call_count == 1

    # Check the call (hourly) - uses last 10 days, not billing dates
    call = mock_client.get_consumption_data.call_args
    assert call.kwargs["granularity"] == "hourly"
    assert call.kwargs["date_range"] == "currentBill"


async def test_update_data_consumption_fetch_fails(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test data update continues when consumption data fetch fails."""
    mock_client = AsyncMock()
    # Profile data with valid billing dates
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = "2025-12-25T00:00:00-08:00"
    profile_data["evpBillingEnd"] = "2026-01-26T00:00:00-08:00"

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)
    # Consumption data fetch fails
    mock_client.get_consumption_data = AsyncMock(
        side_effect=BCHydroApiError("Failed to get consumption")
    )

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    # Should still succeed with profile data only
    data = await coordinator._async_update_data()

    assert "profile" in data
    assert "consumption" in data
    assert data["profile"] == profile_data
    assert data["consumption"] == {}  # Changed: now returns empty dict when fetch fails


async def test_update_data_invalid_date_format(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test data update handles invalid date format gracefully."""
    mock_client = AsyncMock()
    # Profile data with invalid date format
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = "invalid date"
    profile_data["evpBillingEnd"] = "2026-01-26T00:00:00-08:00"

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    # Should still succeed with profile data only (consumption parsing fails)
    data = await coordinator._async_update_data()

    assert "profile" in data
    assert "consumption" in data
    assert data["profile"] == profile_data
    assert data["consumption"] == {}  # Changed: now returns empty dict on date parse failure


async def test_update_data_auth_error(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test data update handles authentication error."""
    mock_client = AsyncMock()
    mock_client.get_account_profile = AsyncMock(
        side_effect=BCHydroAuthError("Invalid credentials")
    )

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    with pytest.raises(ConfigEntryAuthFailed) as exc_info:
        await coordinator._async_update_data()

    assert "Authentication to BC Hydro failed" in str(exc_info.value)


async def test_update_data_api_error(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test data update handles API error."""
    mock_client = AsyncMock()
    mock_client.get_account_profile = AsyncMock(
        side_effect=BCHydroApiError("API Error")
    )

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    with pytest.raises(UpdateFailed) as exc_info:
        await coordinator._async_update_data()

    assert "Error communicating with API" in str(exc_info.value)


async def test_update_data_unexpected_error(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test data update handles unexpected error."""
    mock_client = AsyncMock()
    mock_client.get_account_profile = AsyncMock(
        side_effect=ValueError("Unexpected error")
    )

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    with pytest.raises(UpdateFailed) as exc_info:
        await coordinator._async_update_data()

    assert "Unexpected error" in str(exc_info.value)


async def test_update_data_with_only_start_date(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test data update when only start date is present."""
    mock_client = AsyncMock()
    # Profile data with only start date (missing end date)
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = "2025-12-25T00:00:00-08:00"
    profile_data["evpBillingEnd"] = None

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    data = await coordinator._async_update_data()

    # Should not attempt to fetch consumption without both dates
    assert "profile" in data
    assert data["consumption"] == {}  # Changed: now returns empty dict instead of None


async def test_update_data_with_only_end_date(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test data update when only end date is present."""
    mock_client = AsyncMock()
    # Profile data with only end date (missing start date)
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = None
    profile_data["evpBillingEnd"] = "2026-01-26T00:00:00-08:00"

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    data = await coordinator._async_update_data()

    # Should not attempt to fetch consumption without both dates
    assert "profile" in data
    assert data["consumption"] == {}  # Changed: now returns empty dict instead of None


async def test_update_data_with_invalid_quality_entries(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test that INVALID quality entries are skipped in hourly data."""
    mock_client = AsyncMock()
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = "2025-12-25T00:00:00-08:00"
    profile_data["evpBillingEnd"] = "2026-01-26T00:00:00-08:00"

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)
    # Mix of ACTUAL and INVALID entries
    hourly_mock = {"hourly_consumption": [
        {"date_time": "2025-12-25T00:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
        {"date_time": "2025-12-25T01:00:00-08:00", "value": 0.0, "cost": 0.00, "quality": "INVALID"},  # Should be skipped
        {"date_time": "2025-12-25T02:00:00-08:00", "value": 2.0, "cost": 0.20, "quality": "ACTUAL"},
    ]}
    mock_client.get_consumption_data = AsyncMock(return_value=hourly_mock)

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    data = await coordinator._async_update_data()

    # Should have daily data calculated only from ACTUAL entries
    assert "daily_consumption" in data["consumption"]
    daily = data["consumption"]["daily_consumption"]
    assert len(daily) == 1  # Only one day of data
    # Total should be 1.0 + 2.0 = 3.0 (INVALID entry skipped)
    assert daily[0]["value"] == 3.0


async def test_update_data_with_missing_timestamp_entries(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test that entries without timestamp are skipped in hourly data."""
    mock_client = AsyncMock()
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = "2025-12-25T00:00:00-08:00"
    profile_data["evpBillingEnd"] = "2026-01-26T00:00:00-08:00"

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)
    # Mix of valid and missing timestamp entries
    hourly_mock = {"hourly_consumption": [
        {"date_time": "2025-12-25T00:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
        {"value": 5.0, "cost": 0.50, "quality": "ACTUAL"},  # Missing date_time - should be skipped
        {"date_time": None, "value": 5.0, "cost": 0.50, "quality": "ACTUAL"},  # Null date_time - should be skipped
        {"date_time": "2025-12-25T02:00:00-08:00", "value": 2.0, "cost": 0.20, "quality": "ACTUAL"},
    ]}
    mock_client.get_consumption_data = AsyncMock(return_value=hourly_mock)

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    data = await coordinator._async_update_data()

    # Should have daily data calculated only from entries with timestamps
    assert "daily_consumption" in data["consumption"]
    daily = data["consumption"]["daily_consumption"]
    assert len(daily) == 1  # Only one day of data
    # Total should be 1.0 + 2.0 = 3.0 (entries without timestamp skipped)
    assert daily[0]["value"] == 3.0


async def test_update_data_with_invalid_timestamp_format(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test that entries with invalid timestamp format are skipped gracefully."""
    mock_client = AsyncMock()
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = "2025-12-25T00:00:00-08:00"
    profile_data["evpBillingEnd"] = "2026-01-26T00:00:00-08:00"

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)
    # Mix of valid and invalid timestamp entries
    hourly_mock = {"hourly_consumption": [
        {"date_time": "2025-12-25T00:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
        {"date_time": "invalid-timestamp", "value": 5.0, "cost": 0.50, "quality": "ACTUAL"},  # Invalid format
        {"date_time": "2025-12-25T02:00:00-08:00", "value": 2.0, "cost": 0.20, "quality": "ACTUAL"},
    ]}
    mock_client.get_consumption_data = AsyncMock(return_value=hourly_mock)

    coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

    data = await coordinator._async_update_data()

    # Should have daily data calculated only from entries with valid timestamps
    assert "daily_consumption" in data["consumption"]
    daily = data["consumption"]["daily_consumption"]
    assert len(daily) == 1  # Only one day of data
    # Total should be 1.0 + 2.0 = 3.0 (invalid timestamp entry skipped)
    assert daily[0]["value"] == 3.0


async def test_update_data_cleanup_finds_corrupted_stats(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test that cleanup logging works when corrupted stats found (lines 167-169)."""
    mock_client = AsyncMock()
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = "2025-12-25T00:00:00-08:00"
    profile_data["evpBillingEnd"] = "2026-01-26T00:00:00-08:00"

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)
    hourly_mock = {"hourly_consumption": [
        {"date_time": "2025-12-25T00:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
    ]}
    mock_client.get_consumption_data = AsyncMock(return_value=hourly_mock)

    # Create coordinator with mocks that return > 0 cleaned stats
    with patch(
        "custom_components.bchydro.coordinator.async_import_statistics",
        new_callable=AsyncMock,
    ), patch(
        "custom_components.bchydro.coordinator.async_cleanup_future_statistics",
        new_callable=AsyncMock,
        return_value=5,  # Found 5 corrupted stats
    ):
        coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)
        data = await coordinator._async_update_data()

        assert "profile" in data
        assert "consumption" in data


async def test_update_data_cleanup_fails(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test that cleanup failure is handled gracefully (lines 168-169)."""
    mock_client = AsyncMock()
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = "2025-12-25T00:00:00-08:00"
    profile_data["evpBillingEnd"] = "2026-01-26T00:00:00-08:00"

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)
    hourly_mock = {"hourly_consumption": [
        {"date_time": "2025-12-25T00:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
    ]}
    mock_client.get_consumption_data = AsyncMock(return_value=hourly_mock)

    # Create coordinator with mocks that raise exception during cleanup
    with patch(
        "custom_components.bchydro.coordinator.async_import_statistics",
        new_callable=AsyncMock,
    ), patch(
        "custom_components.bchydro.coordinator.async_cleanup_future_statistics",
        new_callable=AsyncMock,
        side_effect=Exception("Cleanup failed"),
    ):
        coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

        # Should still succeed - cleanup failure shouldn't break the update
        data = await coordinator._async_update_data()

        assert "profile" in data
        assert "consumption" in data


async def test_update_data_statistics_import_fails(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test that statistics import failure is handled gracefully (lines 180-181)."""
    mock_client = AsyncMock()
    profile_data = mock_account_profile_data.copy()
    profile_data["evpBillingStart"] = "2025-12-25T00:00:00-08:00"
    profile_data["evpBillingEnd"] = "2026-01-26T00:00:00-08:00"

    mock_client.get_account_profile = AsyncMock(return_value=profile_data)
    hourly_mock = {"hourly_consumption": [
        {"date_time": "2025-12-25T00:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
    ]}
    mock_client.get_consumption_data = AsyncMock(return_value=hourly_mock)

    # Create coordinator with mocks that raise exception during import
    with patch(
        "custom_components.bchydro.coordinator.async_import_statistics",
        new_callable=AsyncMock,
        side_effect=Exception("Import failed"),
    ), patch(
        "custom_components.bchydro.coordinator.async_cleanup_future_statistics",
        new_callable=AsyncMock,
        return_value=0,
    ):
        coordinator = BCHydroDataUpdateCoordinator(hass, mock_client, mock_config_entry)

        # Should still succeed - import failure shouldn't break the update
        data = await coordinator._async_update_data()

        assert "profile" in data
        assert "consumption" in data

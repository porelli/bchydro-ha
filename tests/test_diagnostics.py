"""Test BC Hydro diagnostics."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from custom_components.bchydro.const import DOMAIN
from custom_components.bchydro.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_with_data(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test diagnostics with coordinator data."""
    # Create a mock coordinator
    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.last_update_success_time = datetime(2025, 1, 1, 12, 0, 0)
    mock_coordinator.update_interval = timedelta(minutes=60)
    mock_coordinator.data = {
        "profile": mock_account_profile_data,
        "consumption": mock_consumption_data,
    }

    # Set the coordinator as runtime_data
    mock_config_entry.runtime_data = mock_coordinator

    # Get diagnostics
    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    # Verify structure
    assert "entry" in diagnostics
    assert "coordinator" in diagnostics
    assert "data" in diagnostics

    # Verify entry info
    assert diagnostics["entry"]["title"] == "BC Hydro (test@example.com)"
    assert diagnostics["entry"]["domain"] == DOMAIN
    assert diagnostics["entry"]["version"] == 1

    # Verify coordinator info
    assert diagnostics["coordinator"]["last_update_success"] is True
    assert diagnostics["coordinator"]["last_update_success_time"] == "2025-01-01T12:00:00"
    assert diagnostics["coordinator"]["update_interval"] == "1:00:00"

    # Verify data structure
    assert "profile" in diagnostics["data"]
    assert "consumption" in diagnostics["data"]
    assert diagnostics["data"]["consumption"]["daily_consumption_count"] == 3
    assert len(diagnostics["data"]["consumption"]["sample_daily_data"]) == 3

    # Verify sensitive data is redacted
    assert diagnostics["data"]["profile"]["evpAccountId"] == "**REDACTED**"
    assert diagnostics["data"]["profile"]["evpAccount"] == "**REDACTED**"
    assert diagnostics["data"]["profile"]["evpProfileId"] == "**REDACTED**"


async def test_diagnostics_without_data(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test diagnostics when coordinator has no data."""
    # Create a mock coordinator with no data
    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = False
    mock_coordinator.last_update_success_time = None
    mock_coordinator.update_interval = timedelta(minutes=60)
    mock_coordinator.data = None

    # Set the coordinator as runtime_data
    mock_config_entry.runtime_data = mock_coordinator

    # Get diagnostics
    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    # Verify structure
    assert "entry" in diagnostics
    assert "coordinator" in diagnostics
    assert "data" in diagnostics

    # Verify coordinator info with None timestamp
    assert diagnostics["coordinator"]["last_update_success"] is False
    assert diagnostics["coordinator"]["last_update_success_time"] is None

    # Verify empty data
    assert diagnostics["data"]["profile"] == {}
    assert diagnostics["data"]["consumption"]["current_date_time"] is None
    assert diagnostics["data"]["consumption"]["daily_consumption_count"] == 0


async def test_diagnostics_with_empty_consumption(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test diagnostics with empty consumption data."""
    # Create a mock coordinator with profile but empty consumption
    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.last_update_success_time = datetime(2025, 1, 1, 12, 0, 0)
    mock_coordinator.update_interval = timedelta(minutes=60)
    mock_coordinator.data = {
        "profile": mock_account_profile_data,
        "consumption": {
            "current_date_time": "2025-12-31T19:53:17-08:00",
            "daily_consumption": [],
        },
    }

    # Set the coordinator as runtime_data
    mock_config_entry.runtime_data = mock_coordinator

    # Get diagnostics
    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    # Verify consumption data
    assert diagnostics["data"]["consumption"]["daily_consumption_count"] == 0
    assert diagnostics["data"]["consumption"]["sample_daily_data"] == []
    assert diagnostics["data"]["consumption"]["current_date_time"] == "2025-12-31T19:53:17-08:00"


async def test_diagnostics_with_all_consumption_fields(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test diagnostics with all consumption fields present."""
    # Create consumption data with all optional fields
    consumption_data = {
        "current_date_time": "2025-12-31T19:53:17-08:00",
        "daily_consumption": [{"value": 5.0, "cost": 1.0}],
        "tier_consumption": {"tier1": 100, "tier2": 50},
        "events": [{"event": "test"}],
        "tps_details": {"detail": "test"},
    }

    # Create a mock coordinator
    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.last_update_success_time = datetime(2025, 1, 1, 12, 0, 0)
    mock_coordinator.update_interval = timedelta(minutes=60)
    mock_coordinator.data = {
        "profile": mock_account_profile_data,
        "consumption": consumption_data,
    }

    # Set the coordinator as runtime_data
    mock_config_entry.runtime_data = mock_coordinator

    # Get diagnostics
    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    # Verify all consumption flags
    assert diagnostics["data"]["consumption"]["has_tier_consumption"] is True
    assert diagnostics["data"]["consumption"]["has_events"] is True
    assert diagnostics["data"]["consumption"]["has_tps_details"] is True
    assert diagnostics["data"]["consumption"]["daily_consumption_count"] == 1


async def test_diagnostics_redacts_sensitive_data(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test that diagnostics properly redacts all sensitive data."""
    # Create data with all sensitive fields
    profile_data = {
        "evpAccountId": "sensitive_account_id",
        "evpAccount": "sensitive_account",
        "evpSlid": "sensitive_slid",
        "evpProfileId": "sensitive_profile",
        "evpCsrId": "sensitive_csr",
        "ratePlanName": "Residential tiered rate",  # Not sensitive
    }

    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.last_update_success_time = datetime(2025, 1, 1, 12, 0, 0)
    mock_coordinator.update_interval = timedelta(minutes=60)
    mock_coordinator.data = {
        "profile": profile_data,
        "consumption": {},
    }

    mock_config_entry.runtime_data = mock_coordinator

    # Get diagnostics
    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    # Verify all sensitive fields are redacted
    assert diagnostics["data"]["profile"]["evpAccountId"] == "**REDACTED**"
    assert diagnostics["data"]["profile"]["evpAccount"] == "**REDACTED**"
    assert diagnostics["data"]["profile"]["evpSlid"] == "**REDACTED**"
    assert diagnostics["data"]["profile"]["evpProfileId"] == "**REDACTED**"
    assert diagnostics["data"]["profile"]["evpCsrId"] == "**REDACTED**"

    # Verify non-sensitive data is not redacted
    assert diagnostics["data"]["profile"]["ratePlanName"] == "Residential tiered rate"

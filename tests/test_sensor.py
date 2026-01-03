"""Test BC Hydro sensor platform."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from custom_components.bchydro.const import DOMAIN
from custom_components.bchydro.sensor import (
    BCHydroSensor,
    _parse_billing_date,
    async_setup_entry,
    get_yesterday_data,
    parse_cost_value,
    parse_energy_value,
)


# Test parse_energy_value function
def test_parse_energy_value_valid():
    """Test parsing valid energy value."""
    assert parse_energy_value("28 kWh") == 28.0
    assert parse_energy_value("152 kWh") == 152.0
    assert parse_energy_value("1,234 kWh") == 1234.0


def test_parse_energy_value_invalid():
    """Test parsing invalid energy value."""
    assert parse_energy_value("invalid") is None
    assert parse_energy_value(None) is None
    assert parse_energy_value("") is None
    assert parse_energy_value("abc kWh") is None


# Test parse_cost_value function
def test_parse_cost_value_valid():
    """Test parsing valid cost value."""
    assert parse_cost_value("$5") == 5.0
    assert parse_cost_value("$26") == 26.0
    assert parse_cost_value("$1,234.56") == 1234.56


def test_parse_cost_value_invalid():
    """Test parsing invalid cost value."""
    assert parse_cost_value("invalid") is None
    assert parse_cost_value(None) is None
    assert parse_cost_value("") is None
    assert parse_cost_value("$abc") is None


# Test _parse_billing_date function
def test_parse_billing_date_iso_format():
    """Test parsing ISO format date string."""
    result = _parse_billing_date("2025-12-25T00:00:00-08:00")
    assert result is not None
    assert result.year == 2025
    assert result.month == 12
    assert result.day == 25


def test_parse_billing_date_human_format():
    """Test parsing human readable date format."""
    result = _parse_billing_date("Dec 25, 2025")
    assert result is not None
    assert result.year == 2025
    assert result.month == 12
    assert result.day == 25


def test_parse_billing_date_none():
    """Test parsing None returns None."""
    assert _parse_billing_date(None) is None


def test_parse_billing_date_invalid():
    """Test parsing invalid date string returns None."""
    assert _parse_billing_date("invalid date") is None
    assert _parse_billing_date("") is None


# Test get_yesterday_data function
def test_get_yesterday_data_with_valid_data():
    """Test getting yesterday's data from consumption data."""
    data = {
        "consumption": {
            "daily_consumption": [
                {
                    "date_time": "2025-12-27T00:00:00-08:00",
                    "value": 5.60,
                    "cost": 0.89,
                    "quality": "ACTUAL",
                },
                {
                    "date_time": "2025-12-28T00:00:00-08:00",
                    "value": 6.50,
                    "cost": 1.05,
                    "quality": "ACTUAL",
                },
            ]
        }
    }

    yesterday = get_yesterday_data(data)
    assert yesterday is not None
    assert yesterday["value"] == 6.50  # Last ACTUAL entry
    assert yesterday["cost"] == 1.05


def test_get_yesterday_data_no_consumption():
    """Test getting yesterday's data when no consumption data."""
    data = {"consumption": {}}
    assert get_yesterday_data(data) is None

    data = {"consumption": {"daily_consumption": []}}
    assert get_yesterday_data(data) is None


def test_get_yesterday_data_no_actual_quality():
    """Test getting yesterday's data when no ACTUAL quality entries."""
    data = {
        "consumption": {
            "daily_consumption": [
                {
                    "date_time": "2025-12-27T00:00:00-08:00",
                    "value": 5.60,
                    "cost": 0.89,
                    "quality": "ESTIMATED",
                },
            ]
        }
    }

    assert get_yesterday_data(data) is None


def test_get_yesterday_data_missing_consumption_key():
    """Test getting yesterday's data when consumption key is missing."""
    data = {}
    assert get_yesterday_data(data) is None


async def test_async_setup_entry(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test sensor platform setup."""
    # Create a mock coordinator
    mock_coordinator = MagicMock()
    mock_coordinator.data = {
        "profile": mock_account_profile_data,
        "consumption": mock_consumption_data,
    }

    # Set coordinator as runtime_data
    mock_config_entry.runtime_data = mock_coordinator

    # Mock async_add_entities
    async_add_entities = MagicMock()

    # Setup the sensor platform
    await async_setup_entry(hass, mock_config_entry, async_add_entities)

    # Verify entities were added
    assert async_add_entities.called
    call_args = async_add_entities.call_args[0]

    # Should create all sensor entities
    entities = list(call_args[0])
    assert len(entities) == 10  # Should create 10 sensors

    # Verify all entities are BCHydroSensor instances
    for entity in entities:
        assert isinstance(entity, BCHydroSensor)


async def test_sensor_native_value_with_data(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test sensor native_value property with data."""
    from custom_components.bchydro.sensor import SENSOR_TYPES

    mock_coordinator = MagicMock()
    mock_coordinator.data = {
        "profile": mock_account_profile_data,
        "consumption": mock_consumption_data,
    }

    mock_config_entry.runtime_data = mock_coordinator

    # Test consumption to date sensor
    description = SENSOR_TYPES[0]  # consumption_to_date
    sensor = BCHydroSensor(mock_coordinator, description, mock_config_entry)

    # Get native value
    value = sensor.native_value
    assert value is not None
    assert value == 28.0  # From mock data "28 kWh"


async def test_sensor_native_value_without_data(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test sensor native_value property without data."""
    from custom_components.bchydro.sensor import SENSOR_TYPES

    mock_coordinator = MagicMock()
    mock_coordinator.data = None

    mock_config_entry.runtime_data = mock_coordinator

    description = SENSOR_TYPES[0]
    sensor = BCHydroSensor(mock_coordinator, description, mock_config_entry)

    # Should return None when no data
    assert sensor.native_value is None


async def test_sensor_extra_state_attributes_with_data(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test sensor extra_state_attributes property with data."""
    from custom_components.bchydro.sensor import SENSOR_TYPES

    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.data = {
        "profile": mock_account_profile_data,
        "consumption": mock_consumption_data,
    }

    mock_config_entry.runtime_data = mock_coordinator

    # Test consumption to date sensor (should include daily_consumption)
    description = SENSOR_TYPES[0]  # consumption_to_date
    sensor = BCHydroSensor(mock_coordinator, description, mock_config_entry)

    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert "account_id" in attrs
    assert "account_number" in attrs
    assert "profile_id" in attrs
    assert "account_type" in attrs
    assert "last_update" in attrs
    assert "billing_start" in attrs
    assert "billing_end" in attrs
    assert "daily_consumption" in attrs

    # Verify daily consumption has max 7 days
    assert len(attrs["daily_consumption"]) <= 7


async def test_sensor_extra_state_attributes_without_data(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test sensor extra_state_attributes property without data."""
    from custom_components.bchydro.sensor import SENSOR_TYPES

    mock_coordinator = MagicMock()
    mock_coordinator.data = None

    mock_config_entry.runtime_data = mock_coordinator

    description = SENSOR_TYPES[0]
    sensor = BCHydroSensor(mock_coordinator, description, mock_config_entry)

    # Should return None when no data
    assert sensor.extra_state_attributes is None


async def test_sensor_extra_state_attributes_cost_to_date(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test sensor extra_state_attributes for cost_to_date sensor."""
    from custom_components.bchydro.sensor import SENSOR_TYPES

    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.data = {
        "profile": mock_account_profile_data,
        "consumption": mock_consumption_data,
    }

    mock_config_entry.runtime_data = mock_coordinator

    # Test cost to date sensor (should also include daily_consumption)
    description = SENSOR_TYPES[1]  # cost_to_date
    sensor = BCHydroSensor(mock_coordinator, description, mock_config_entry)

    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert "daily_consumption" in attrs


async def test_sensor_extra_state_attributes_other_sensors(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test sensor extra_state_attributes for other sensors."""
    from custom_components.bchydro.sensor import SENSOR_TYPES

    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.data = {
        "profile": mock_account_profile_data,
        "consumption": mock_consumption_data,
    }

    mock_config_entry.runtime_data = mock_coordinator

    # Test estimated consumption sensor (should NOT include daily_consumption)
    description = SENSOR_TYPES[2]  # estimated_consumption
    sensor = BCHydroSensor(mock_coordinator, description, mock_config_entry)

    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert "daily_consumption" not in attrs
    assert "account_id" in attrs


async def test_sensor_yesterday_consumption(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test yesterday consumption sensor."""
    from custom_components.bchydro.sensor import SENSOR_TYPES

    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.data = {
        "profile": mock_account_profile_data,
        "consumption": mock_consumption_data,
    }

    mock_config_entry.runtime_data = mock_coordinator

    # Test yesterday consumption sensor
    description = SENSOR_TYPES[4]  # yesterday_consumption
    sensor = BCHydroSensor(mock_coordinator, description, mock_config_entry)

    # Should get the last ACTUAL entry
    value = sensor.native_value
    assert value == 5.60  # Last entry in mock data

    # Attributes should include date and cost
    attrs = sensor.extra_state_attributes
    assert "date" in attrs
    assert "cost" in attrs
    assert attrs["cost"] == 0.89


async def test_sensor_yesterday_consumption_no_data(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test yesterday consumption sensor without data."""
    from custom_components.bchydro.sensor import SENSOR_TYPES

    mock_coordinator = MagicMock()
    mock_coordinator.data = {
        "profile": {},
        "consumption": {"daily_consumption": []},
    }

    mock_config_entry.runtime_data = mock_coordinator

    description = SENSOR_TYPES[4]  # yesterday_consumption
    sensor = BCHydroSensor(mock_coordinator, description, mock_config_entry)

    # Should return None when no yesterday data
    assert sensor.native_value is None

    # Attributes should not include yesterday-specific data
    attrs = sensor.extra_state_attributes
    assert "date" not in attrs or attrs.get("date") is None
    assert "cost" not in attrs or attrs.get("cost") is None


async def test_sensor_device_info(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test sensor device_info."""
    from custom_components.bchydro.sensor import SENSOR_TYPES

    mock_coordinator = MagicMock()
    mock_coordinator.data = {
        "profile": mock_account_profile_data,
        "consumption": mock_consumption_data,
    }

    mock_config_entry.runtime_data = mock_coordinator

    description = SENSOR_TYPES[0]
    sensor = BCHydroSensor(mock_coordinator, description, mock_config_entry)

    # Verify device info
    device_info = sensor.device_info
    assert device_info["identifiers"] == {(DOMAIN, mock_config_entry.entry_id)}
    assert device_info["name"] == "BC Hydro"
    assert device_info["manufacturer"] == "BC Hydro"
    assert device_info["model"] == "Energy Monitor"


async def test_sensor_unique_id(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test sensor unique_id."""
    from custom_components.bchydro.sensor import SENSOR_TYPES

    mock_coordinator = MagicMock()
    mock_coordinator.data = {
        "profile": mock_account_profile_data,
        "consumption": mock_consumption_data,
    }

    mock_config_entry.runtime_data = mock_coordinator

    description = SENSOR_TYPES[0]
    sensor = BCHydroSensor(mock_coordinator, description, mock_config_entry)

    # Verify unique_id format
    assert sensor.unique_id == f"{mock_config_entry.entry_id}_{description.key}"


async def test_sensor_has_entity_name(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test sensor has_entity_name attribute."""
    from custom_components.bchydro.sensor import SENSOR_TYPES

    mock_coordinator = MagicMock()
    mock_coordinator.data = {
        "profile": mock_account_profile_data,
        "consumption": mock_consumption_data,
    }

    mock_config_entry.runtime_data = mock_coordinator

    description = SENSOR_TYPES[0]
    sensor = BCHydroSensor(mock_coordinator, description, mock_config_entry)

    # Verify has_entity_name is True
    assert sensor.has_entity_name is True

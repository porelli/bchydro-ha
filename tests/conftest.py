"""Fixtures for BC Hydro tests."""
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch
import inspect

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from custom_components.bchydro.const import DOMAIN
import custom_components.bchydro.api as api_module

# Import pytest-homeassistant-custom-component fixtures
pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def reset_global_rate_limiting():
    """Reset global rate limiting state before each test."""
    # Save original values
    orig_last_auth = api_module._global_last_auth_attempt
    orig_attempts = api_module._global_auth_attempts_in_window
    orig_window_start = api_module._global_window_start

    # Reset to initial state
    api_module._global_last_auth_attempt = 0.0
    api_module._global_auth_attempts_in_window = 0
    api_module._global_window_start = 0.0

    yield

    # Restore original values (optional, but good practice)
    api_module._global_last_auth_attempt = orig_last_auth
    api_module._global_auth_attempts_in_window = orig_attempts
    api_module._global_window_start = orig_window_start


def create_config_entry(
    domain: str = DOMAIN,
    title: str = "BC Hydro Test",
    data: dict | None = None,
    options: dict | None = None,
    unique_id: str = "test@example.com",
    version: int = 1,
    minor_version: int = 1,
    source: str = "user",
) -> ConfigEntry:
    """Create a ConfigEntry compatible with the current HA version."""
    sig = inspect.signature(ConfigEntry.__init__)
    valid_params = set(sig.parameters.keys())

    kwargs = {
        "version": version,
        "minor_version": minor_version,
        "domain": domain,
        "title": title,
        "data": data or {},
        "source": source,
        "unique_id": unique_id,
        "options": options or {},
    }

    # Add optional params if supported
    if "discovery_keys" in valid_params:
        kwargs["discovery_keys"] = {}
    if "subentries_data" in valid_params:
        kwargs["subentries_data"] = []

    return ConfigEntry(**kwargs)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


@pytest.fixture
def mock_config_entry_data():
    """Return mock config entry data."""
    return {
        CONF_USERNAME: "test@example.com",
        CONF_PASSWORD: "testpassword",
        "cookies": {
            "JSESSIONID": "test_session_id",
            "INGRESSCOOKIE": "test_ingress_cookie",
            "bchydroparam": "test_csrf_token",
        },
    }


@pytest.fixture
def mock_config_entry(mock_config_entry_data):
    """Return a mock config entry."""
    return create_config_entry(
        title="BC Hydro (test@example.com)",
        data=mock_config_entry_data,
        unique_id="test@example.com",
    )


@pytest.fixture
def mock_account_profile_data():
    """Return mock account profile data."""
    return {
        "evpCurrentDateTime": "2025-12-31T19:53:16-08:00",
        "evpSlid": "0001234567",
        "evpAccount": "000012345678",
        "evpAccountId": "ABCDEF0123456789ABCDEF0123456789",
        "evpProfileId": "bchtestuser",
        "evpCsrId": "bchtestuser",
        "evpRole": "user",
        "accountType": "res",
        "evpRateCategory": "BCRSE1101",
        "evpRateGroup": "RES1",
        "ratePlanType": "RIB",
        "ratePlanName": "Residential tiered rate",
        "evRatePlanName": None,
        "isRIB": True,
        "isFlat": False,
        "hasEV": False,
        "enableSMSAlerts": False,
        "nonWan": False,
        "timezone": "PST",
        "evpBillingStart": "2025-12-25T00:00:00-08:00",
        "evpBillingEnd": "2026-01-26T00:00:00-08:00",
        "evpBilledStart": "2025-11-26T00:00:00-08:00",
        "evpBilledEnd": "2025-12-24T00:00:00-08:00",
        "evpDaysInBillingPeriod": 33,
        "yesterdayDate": "2025-12-30T00:00:00-08:00",
        "yesterdayPercentage": 15,
        "evpValidityStart": "2024-04-01T00:00:00-07:00",
        "evpValidityEnd": "9999-12-31T00:00:00-08:00",
        "evpEnablementDate": "2015-03-17T00:00:00-07:00",
        "tier2Date": None,
        "tier2Percentage": None,
        "evpConsToDate": "28 kWh",
        "evpCostToDate": "$5",
        "evpEstConsCurPeriod": "152 kWh",
        "evpEstCostCurPeriod": "$26",
        "evpComLastBillingPeakDemand": None,
        "evpComLastBillingPowerFactor": None,
        "isEnDateInCBP": False,
    }


@pytest.fixture
def mock_consumption_data():
    """Return mock consumption data."""
    return {
        "current_date_time": "2025-12-31T19:53:17-08:00",
        "block_status": "0",
        "non_wan": False,
        "daily_consumption": [
            {
                "date_time": "2025-12-25T00:00:00-08:00",
                "end_time": "2025-12-25T00:00:00-08:00",
                "value": 3.75,
                "cost": 0.67,
                "quality": "ACTUAL",
                "type": "SMI",
                "date_type": "HOLIDAY",
                "date_description": "Christmas Day",
            },
            {
                "date_time": "2025-12-26T00:00:00-08:00",
                "end_time": "2025-12-26T00:00:00-08:00",
                "value": 5.66,
                "cost": 0.90,
                "quality": "ACTUAL",
                "type": "SMI",
                "date_type": "HOLIDAY",
                "date_description": "Boxing Day",
            },
            {
                "date_time": "2025-12-27T00:00:00-08:00",
                "end_time": "2025-12-27T00:00:00-08:00",
                "value": 5.60,
                "cost": 0.89,
                "quality": "ACTUAL",
                "type": "SMI",
                "date_type": None,
                "date_description": None,
            },
        ],
        "hourly_consumption": [],
        "events": [],
    }


@pytest.fixture
def mock_bchydro_api_client():
    """Return a mocked BCHydroApiClient."""
    with patch(
        "custom_components.bchydro.api.BCHydroApiClient", autospec=True
    ) as mock_api:
        client = mock_api.return_value
        client.authenticate = AsyncMock(return_value=True)
        client.authenticate_with_cookies = AsyncMock(return_value=True)
        client.get_account_profile = AsyncMock()
        client.get_consumption_data = AsyncMock()
        client.close = AsyncMock()
        client.get_cookies = MagicMock(return_value={})
        yield client


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock, None, None]:
    """Mock setting up a config entry."""
    with patch(
        "custom_components.bchydro.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup

"""Tests for transient-failure handling.

A transient problem (network blip, timeout, HTTP error, self-imposed rate limit)
must never be reported as an authentication failure: Home Assistant treats
ConfigEntryAuthFailed as terminal - it stops scheduling refreshes and waits for a
human to re-enter the password. Only a genuine credential rejection may escalate.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed

import custom_components.bchydro.api as api_module
from custom_components.bchydro.api import (
    BCHydroApiClient,
    BCHydroApiError,
    BCHydroAuthError,
    BCHydroConnectionError,
)
from custom_components.bchydro.coordinator import BCHydroDataUpdateCoordinator


@pytest.fixture
def mock_session():
    """Return a mocked aiohttp session."""
    return MagicMock(spec=aiohttp.ClientSession)


@pytest.fixture(autouse=True)
def mock_statistics_functions():
    """Mock statistics helpers that need the recorder."""
    with patch(
        "custom_components.bchydro.coordinator.async_import_statistics",
        new_callable=AsyncMock,
    ), patch(
        "custom_components.bchydro.coordinator.async_cleanup_future_statistics",
        new_callable=AsyncMock,
        return_value=0,
    ):
        yield


def _login_page_response(html: str, url: str = "https://app.bchydro.com/sso/UI/Login"):
    """Build a mocked login-page response."""
    response = AsyncMock()
    response.status = 200
    response.url = url
    response.text = AsyncMock(return_value=html)
    response.__aenter__.return_value = response
    return response


# --- api layer: classification -------------------------------------------------


async def test_network_error_is_not_an_auth_error(mock_session) -> None:
    """A dropped connection is retryable, not a credential problem."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    mock_session.post.return_value.__aenter__.side_effect = aiohttp.ClientError("boom")

    with pytest.raises(BCHydroConnectionError):
        await client.authenticate()
    assert not isinstance(BCHydroConnectionError("x"), BCHydroAuthError)


async def test_timeout_is_not_an_auth_error(mock_session) -> None:
    """A request timeout is retryable, not a credential problem."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    mock_session.post.return_value.__aenter__.side_effect = asyncio.TimeoutError()

    with pytest.raises(BCHydroConnectionError):
        await client.authenticate()


async def test_server_error_status_is_not_an_auth_error(mock_session) -> None:
    """BC Hydro returning 503 (maintenance) is retryable."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    response = AsyncMock()
    response.status = 503
    response.url = "https://app.bchydro.com/sso/UI/Login"
    response.__aenter__.return_value = response
    mock_session.post.return_value = response

    with pytest.raises(BCHydroConnectionError) as err:
        await client.authenticate()
    assert "503" in str(err.value)


async def test_rate_limited_is_not_an_auth_error(mock_session) -> None:
    """Our own rate limiter must not look like a credential rejection."""
    import time

    api_module._global_auth_attempts_in_window = api_module._GLOBAL_MAX_ATTEMPTS_PER_WINDOW
    api_module._global_window_start = time.time()

    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    with pytest.raises(BCHydroConnectionError) as err:
        await client.authenticate()
    assert "Too many authentication attempts" in str(err.value)


async def test_page_mentioning_invalid_in_markup_is_not_reported_as_bad_password(
    mock_session,
) -> None:
    """The word "invalid" in page markup is not proof of bad credentials.

    BC Hydro's login page ships client-side validation scripts, so matching the
    bare substring reports "Invalid username or password" for any unexpected page
    (maintenance notice, redirect glitch) and sends the user chasing their
    password instead of retrying.
    """
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    mock_session.post.return_value = _login_page_response(
        "<html><script>function invalid(){}</script>"
        "<p>Service temporarily unavailable, please try again later.</p></html>"
    )

    with pytest.raises(BCHydroApiError) as err:
        await client.authenticate()
    assert "Invalid username or password" not in str(err.value)


async def test_invalid_credentials_still_raises_auth_error(mock_session) -> None:
    """A real credential rejection must still escalate to reauth."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    mock_session.post.return_value = _login_page_response(
        "<html><div class='error'>Invalid username or password.</div></html>"
    )

    with pytest.raises(BCHydroAuthError):
        await client.authenticate()


# --- coordinator: escalation policy -------------------------------------------


def _coordinator(hass: HomeAssistant, entry, side_effect) -> BCHydroDataUpdateCoordinator:
    client = AsyncMock()
    client.get_account_profile = AsyncMock(side_effect=side_effect)
    return BCHydroDataUpdateCoordinator(hass, client, entry)


async def test_connection_error_keeps_polling(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """A transient error must raise UpdateFailed so HA schedules another refresh."""
    coordinator = _coordinator(
        hass, mock_config_entry, BCHydroConnectionError("Network error: boom")
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_single_auth_failure_does_not_stop_the_coordinator(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """One credential rejection is not enough to demand reauthentication."""
    coordinator = _coordinator(
        hass, mock_config_entry, BCHydroAuthError("Invalid username or password")
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_repeated_auth_failures_request_reauth(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Persistent credential rejection must surface as a reauth request."""
    coordinator = _coordinator(
        hass, mock_config_entry, BCHydroAuthError("Invalid username or password")
    )

    for _ in range(coordinator.MAX_CONSECUTIVE_AUTH_FAILURES - 1):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_successful_update_resets_auth_failure_count(
    hass: HomeAssistant, mock_config_entry, mock_account_profile_data
) -> None:
    """A recovery in between must not count towards the reauth threshold."""
    client = AsyncMock()
    client.get_account_profile = AsyncMock(
        side_effect=BCHydroAuthError("Invalid username or password")
    )
    client.get_consumption_data = AsyncMock(return_value={"hourly_consumption": []})
    coordinator = BCHydroDataUpdateCoordinator(hass, client, mock_config_entry)

    for _ in range(coordinator.MAX_CONSECUTIVE_AUTH_FAILURES - 1):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    client.get_account_profile = AsyncMock(return_value=mock_account_profile_data)
    await coordinator._async_update_data()
    assert coordinator.consecutive_auth_failures == 0

    client.get_account_profile = AsyncMock(
        side_effect=BCHydroAuthError("Invalid username or password")
    )
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


# --- setup: retry instead of reauth ------------------------------------------


async def test_setup_retries_on_connection_error(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """A blip while HA is starting must schedule a retry, not demand reauth."""
    from custom_components.bchydro import async_setup_entry

    with patch("custom_components.bchydro.BCHydroApiClient") as mock_api_class:
        client = AsyncMock()
        client.authenticate = AsyncMock(
            side_effect=BCHydroConnectionError("Network error: boom")
        )
        client.close = AsyncMock()
        mock_api_class.return_value = client

        await hass.config_entries.async_add(mock_config_entry)

        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, mock_config_entry)


# --- config flow: transient failures are reported as such ---------------------


async def test_config_flow_reports_connection_error(hass: HomeAssistant) -> None:
    """Setting up while BC Hydro is unreachable must not blame the password."""
    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
    from homeassistant.data_entry_flow import FlowResultType

    from custom_components.bchydro.const import DOMAIN

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    with patch(
        "custom_components.bchydro.config_flow.BCHydroApiClient"
    ) as mock_api_class:
        client = AsyncMock()
        client.authenticate = AsyncMock(
            side_effect=BCHydroConnectionError("Network error: boom")
        )
        client.close = AsyncMock()
        mock_api_class.return_value = client

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "test@example.com", CONF_PASSWORD: "password"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_flow_reports_connection_error(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Reconnecting while BC Hydro is unreachable must not blame the password."""
    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
    from homeassistant.data_entry_flow import FlowResultType

    from custom_components.bchydro.const import DOMAIN

    await hass.config_entries.async_add(mock_config_entry)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": "reauth",
            "entry_id": mock_config_entry.entry_id,
            "unique_id": mock_config_entry.unique_id,
        },
        data=mock_config_entry.data,
    )

    with patch(
        "custom_components.bchydro.config_flow.BCHydroApiClient"
    ) as mock_api_class:
        client = AsyncMock()
        client.authenticate = AsyncMock(
            side_effect=BCHydroConnectionError("Network error: boom")
        )
        client.close = AsyncMock()
        mock_api_class.return_value = client

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "test@example.com", CONF_PASSWORD: "password"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


# --- api layer: data paths, not just the login ---------------------------------


async def test_profile_network_error_is_classified(mock_session) -> None:
    """A dropped connection while reading data is reported as a network error.

    Otherwise it reaches the coordinator unwrapped and is logged hourly as
    "Unexpected error" with a full traceback.
    """
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    client._authenticated = True
    mock_session.get.side_effect = aiohttp.ClientConnectorError(
        MagicMock(), OSError("unreachable")
    )

    with pytest.raises(BCHydroConnectionError) as err:
        await client.get_account_profile()
    assert not isinstance(err.value, BCHydroAuthError)


async def test_consumption_network_error_is_classified(mock_session) -> None:
    """Same for the consumption endpoint."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    client.close = AsyncMock()
    client.authenticate = AsyncMock()
    client._authenticated = True
    mock_session.post.side_effect = aiohttp.ClientConnectorError(
        MagicMock(), OSError("unreachable")
    )

    with pytest.raises(BCHydroConnectionError):
        await client.get_consumption_data()


async def test_account_list_network_error_is_classified(mock_session) -> None:
    """Same for the account list."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    client._authenticated = True
    mock_session.get.side_effect = TimeoutError()

    with pytest.raises(BCHydroConnectionError):
        await client.get_accounts()

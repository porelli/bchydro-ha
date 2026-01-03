"""Test the BC Hydro init."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from custom_components.bchydro import async_setup_entry, async_unload_entry, async_reload_entry
from custom_components.bchydro.api import BCHydroApiError, BCHydroAuthError
from custom_components.bchydro.const import DOMAIN
from tests.conftest import create_config_entry


async def test_setup_entry_success(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test successful setup of a config entry."""
    with patch("custom_components.bchydro.BCHydroApiClient") as mock_api_class:
        # Mock the API client
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(return_value=True)
        mock_client.get_cookies = MagicMock(return_value={"JSESSIONID": "test"})
        mock_client.get_account_profile = AsyncMock(return_value=mock_account_profile_data)
        mock_client.get_consumption_data = AsyncMock(return_value=mock_consumption_data)
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        # Mock coordinator's first refresh
        with patch(
            "custom_components.bchydro.coordinator.BCHydroDataUpdateCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            # Mock platform forward setup
            with patch(
                "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
                new_callable=AsyncMock,
            ):
                # Setup the entry
                await hass.config_entries.async_add(mock_config_entry)
                result = await async_setup_entry(hass, mock_config_entry)
                assert result is True
                await hass.async_block_till_done()

                # Verify coordinator was created and stored
                assert mock_config_entry.runtime_data is not None


async def test_setup_entry_auth_failed(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test setup with authentication failure."""
    with patch("custom_components.bchydro.BCHydroApiClient") as mock_api_class:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(
            side_effect=BCHydroAuthError("Invalid credentials")
        )
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        await hass.config_entries.async_add(mock_config_entry)

        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, mock_config_entry)


async def test_setup_entry_no_cookies(
    hass: HomeAssistant,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test setup with no cookies in config - should authenticate with username/password."""
    entry_data = {
        CONF_USERNAME: "test@example.com",
        CONF_PASSWORD: "testpassword",
        # No cookies
    }

    entry = create_config_entry(
        title="BC Hydro (test@example.com)",
        data=entry_data,
    )

    with patch("custom_components.bchydro.BCHydroApiClient") as mock_api_class:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(return_value=True)
        mock_client.get_cookies = MagicMock(
            return_value={
                "JSESSIONID": "new_session",
                "INGRESSCOOKIE": "new_ingress",
            }
        )
        mock_client.get_account_profile = AsyncMock(return_value=mock_account_profile_data)
        mock_client.get_consumption_data = AsyncMock(return_value=mock_consumption_data)
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        with patch(
            "custom_components.bchydro.coordinator.BCHydroDataUpdateCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            with patch(
                "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
                new_callable=AsyncMock,
            ):
                await hass.config_entries.async_add(entry)
                # Should succeed - authenticates with username/password and stores cookies
                assert await async_setup_entry(hass, entry)

                # Verify authenticate was called
                assert mock_client.authenticate.called

                # Verify cookies were stored
                assert "cookies" in entry.data


async def test_setup_entry_auth_failed_during_refresh(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test setup with auth failure during initial refresh - testing re-raise."""
    with patch("custom_components.bchydro.BCHydroApiClient") as mock_api_class:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(return_value=True)
        mock_client.get_cookies = MagicMock(return_value={"JSESSIONID": "test"})
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        with patch(
            "custom_components.bchydro.coordinator.BCHydroDataUpdateCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
            side_effect=ConfigEntryAuthFailed("Auth failed during refresh"),
        ):
            await hass.config_entries.async_add(mock_config_entry)

            # Should re-raise ConfigEntryAuthFailed as-is (line 88)
            with pytest.raises(ConfigEntryAuthFailed):
                await async_setup_entry(hass, mock_config_entry)


async def test_setup_entry_api_error(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
) -> None:
    """Test setup with API error during initial refresh."""
    with patch("custom_components.bchydro.BCHydroApiClient") as mock_api_class:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(return_value=True)
        mock_client.get_cookies = MagicMock(return_value={"JSESSIONID": "test"})
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        # Mock coordinator refresh to raise API error
        with patch(
            "custom_components.bchydro.coordinator.BCHydroDataUpdateCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
            side_effect=BCHydroApiError("API Error"),
        ):
            await hass.config_entries.async_add(mock_config_entry)

            with pytest.raises(ConfigEntryNotReady):
                await async_setup_entry(hass, mock_config_entry)


async def test_unload_entry(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test successful unload of entry."""
    with patch("custom_components.bchydro.BCHydroApiClient") as mock_api_class:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(return_value=True)
        mock_client.get_cookies = MagicMock(return_value={"JSESSIONID": "test"})
        mock_client.get_account_profile = AsyncMock(return_value=mock_account_profile_data)
        mock_client.get_consumption_data = AsyncMock(return_value=mock_consumption_data)
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        # Mock coordinator's first refresh
        with patch(
            "custom_components.bchydro.coordinator.BCHydroDataUpdateCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            # Mock platform setup/unload
            with patch(
                "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
                new_callable=AsyncMock,
            ) as mock_forward:
                with patch(
                    "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
                    new_callable=AsyncMock,
                    return_value=True,
                ) as mock_unload:
                    # Setup
                    await hass.config_entries.async_add(mock_config_entry)
                    assert await async_setup_entry(hass, mock_config_entry)
                    await hass.async_block_till_done()

                    # Verify platform setup was called
                    assert mock_forward.called

                    # Unload
                    assert await async_unload_entry(hass, mock_config_entry)
                    await hass.async_block_till_done()

                    # Verify platform unload was called
                    assert mock_unload.called

                    # Verify client.close() was called
                    # BC Hydro requires its own session due to complex SSO,
                    # so we close it when unloading
                    assert mock_client.close.called


async def test_reload_entry(
    hass: HomeAssistant,
    mock_config_entry,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test reloading an entry."""
    with patch("custom_components.bchydro.BCHydroApiClient") as mock_api_class:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(return_value=True)
        mock_client.get_cookies = MagicMock(return_value={"JSESSIONID": "test"})
        mock_client.get_account_profile = AsyncMock(return_value=mock_account_profile_data)
        mock_client.get_consumption_data = AsyncMock(return_value=mock_consumption_data)
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        # Mock coordinator's first refresh
        with patch(
            "custom_components.bchydro.coordinator.BCHydroDataUpdateCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            # Mock platform setup/unload
            with patch(
                "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
                new_callable=AsyncMock,
            ):
                with patch(
                    "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
                    new_callable=AsyncMock,
                    return_value=True,
                ):
                    # Setup
                    await hass.config_entries.async_add(mock_config_entry)
                    assert await async_setup_entry(hass, mock_config_entry)
                    await hass.async_block_till_done()

                    # Reload
                    await async_reload_entry(hass, mock_config_entry)
                    await hass.async_block_till_done()

                    # Verify coordinator was created
                    assert mock_config_entry.runtime_data is not None


async def test_setup_entry_unexpected_exception(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test setup with unexpected exception - lines 57-59."""
    with patch("custom_components.bchydro.BCHydroApiClient") as mock_api_class:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(return_value=True)
        mock_client.get_cookies = MagicMock(return_value={"JSESSIONID": "test"})
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        # Mock coordinator refresh to raise a generic exception (not BCHydroApiError or ConfigEntryAuthFailed)
        with patch(
            "custom_components.bchydro.coordinator.BCHydroDataUpdateCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Unexpected error"),
        ):
            await hass.config_entries.async_add(mock_config_entry)

            # Generic exceptions should be wrapped in ConfigEntryNotReady
            with pytest.raises(ConfigEntryNotReady):
                await async_setup_entry(hass, mock_config_entry)


async def test_setup_entry_with_custom_interval(
    hass: HomeAssistant,
    mock_account_profile_data,
    mock_consumption_data,
) -> None:
    """Test setup with custom update interval in options."""
    entry_data = {
        CONF_USERNAME: "test@example.com",
        CONF_PASSWORD: "testpassword",
        "cookies": {
            "JSESSIONID": "test_session",
            "INGRESSCOOKIE": "test_ingress",
        },
    }

    entry = create_config_entry(
        title="BC Hydro (test@example.com)",
        data=entry_data,
        options={"update_interval": 120},  # Custom interval
    )

    with patch("custom_components.bchydro.BCHydroApiClient") as mock_api_class:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(return_value=True)
        mock_client.get_cookies = MagicMock(return_value={"JSESSIONID": "test"})
        mock_client.get_account_profile = AsyncMock(return_value=mock_account_profile_data)
        mock_client.get_consumption_data = AsyncMock(return_value=mock_consumption_data)
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        with patch(
            "custom_components.bchydro.coordinator.BCHydroDataUpdateCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            with patch(
                "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
                new_callable=AsyncMock,
            ):
                await hass.config_entries.async_add(entry)
                assert await async_setup_entry(hass, entry)
                await hass.async_block_till_done()

                # Verify coordinator was created with custom interval
                coordinator = entry.runtime_data
                assert coordinator is not None
                # Coordinator should use the custom interval from options
                assert coordinator.update_interval.total_seconds() == 120 * 60

"""The BC Hydro integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from .api import BCHydroApiClient, BCHydroApiError, BCHydroAuthError
from .const import DOMAIN
from .coordinator import BCHydroDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

type BCHydroConfigEntry = ConfigEntry[BCHydroDataUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: BCHydroConfigEntry) -> bool:
    """Set up BC Hydro from a config entry."""
    username: str = entry.data[CONF_USERNAME]
    password: str = entry.data[CONF_PASSWORD]
    # Set when the login holds several accounts (see the config flow)
    account_id: str | None = entry.data.get("account_id")

    # Note: BC Hydro requires its own session with dedicated cookie jar
    # due to complex SSO authentication with multiple redirects.
    # Using HA's shared session would cause cookie conflicts.
    client = BCHydroApiClient(
        username=username,
        password=password,
        account_id=account_id,
    )

    try:
        _LOGGER.debug("async_setup_entry: authenticating during integration setup")
        await client.authenticate()

        new_cookies = client.get_cookies()
        hass.config_entries.async_update_entry(
            entry,
            # Keep everything else (notably the selected account) intact
            data={**entry.data, "cookies": new_cookies},
        )

        coordinator = BCHydroDataUpdateCoordinator(hass, client, entry)
        await coordinator.async_config_entry_first_refresh()

        if not account_id:
            # Entries created before accounts could be told apart don't record
            # which one they follow. Remember it now, so that adding another
            # account of the same login can leave this one out of the choices.
            followed = (coordinator.data or {}).get("profile", {}).get("evpAccountId")
            if followed:
                hass.config_entries.async_update_entry(
                    entry, data={**entry.data, "account_id": followed}
                )

    except BCHydroAuthError as err:
        _LOGGER.error("Authentication failed for %s: %s", username, err)
        raise ConfigEntryAuthFailed from err
    except ConfigEntryAuthFailed:
        raise
    except BCHydroApiError as err:
        _LOGGER.error("API error during setup for %s: %s", username, err)
        raise ConfigEntryNotReady from err
    except Exception as err:
        _LOGGER.exception("Unexpected error setting up BC Hydro for %s", username)
        raise ConfigEntryNotReady from err

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    _LOGGER.info("Successfully set up BC Hydro integration for %s", username)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BCHydroConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator = entry.runtime_data
        await coordinator.client.close()
        _LOGGER.info("Unloaded BC Hydro integration")

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: BCHydroConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)

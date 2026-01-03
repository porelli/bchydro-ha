"""Config flow for BC Hydro integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResult
from homeassistant.helpers import config_validation as cv

from .api import BCHydroApiClient, BCHydroAuthError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)


async def validate_auth(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    client = BCHydroApiClient(
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
    )

    try:
        # Try to authenticate with username and password
        await client.authenticate()

        # Get cookies for storage
        cookies = client.get_cookies()

        return {
            "title": f"BC Hydro ({data[CONF_USERNAME]})",
            "username": data[CONF_USERNAME],
            "cookies": cookies,
        }
    finally:
        await client.close()


class BCHydroConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BC Hydro."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._username: str | None = None
        self._password: str | None = None
        self._cookies: dict[str, str] | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            try:
                info = await validate_auth(self.hass, user_input)
                await self.async_set_unique_id(self._username)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=info["title"],
                    data={
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                        "cookies": info["cookies"],
                    },
                )
            except BCHydroAuthError as err:
                # Check if error message mentions CAPTCHA
                if "CAPTCHA" in str(err) or "captcha" in str(err).lower():
                    errors["base"] = "captcha_required"
                else:
                    errors["base"] = "invalid_auth"
                _LOGGER.error("Authentication failed: %s", err)
            except AbortFlow:
                raise
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Handle reauthentication flow."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry:
            self._username = self._reauth_entry.data[CONF_USERNAME]
            self._password = self._reauth_entry.data.get(CONF_PASSWORD)

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reauth confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input.get(CONF_USERNAME, self._username)
            self._password = user_input[CONF_PASSWORD]

            try:
                info = await validate_auth(
                    self.hass,
                    {CONF_USERNAME: self._username, CONF_PASSWORD: self._password},
                )

                if self._reauth_entry:
                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry,
                        data={
                            CONF_USERNAME: self._username,
                            CONF_PASSWORD: self._password,
                            "cookies": info["cookies"],
                        },
                    )
                    await self.hass.config_entries.async_reload(
                        self._reauth_entry.entry_id
                    )
                    return self.async_abort(reason="reauth_successful")

            except BCHydroAuthError as err:
                # Check if error message mentions CAPTCHA
                if "CAPTCHA" in str(err) or "captcha" in str(err).lower():
                    errors["base"] = "captcha_required"
                else:
                    errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"

        reauth_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=self._username): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=reauth_schema,
            errors=errors,
            description_placeholders={
                "username": self._username or "",
            },
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return BCHydroOptionsFlowHandler(config_entry)


class BCHydroOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for BC Hydro."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        super().__init__()
        self._config_entry = config_entry

    @property
    def config_entry(self) -> config_entries.ConfigEntry:
        """Return config entry."""
        return self._config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options_schema = vol.Schema(
            {
                vol.Optional(
                    "update_interval",
                    default=self.config_entry.options.get("update_interval", 60),
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=1440)),
                vol.Optional(
                    "historical_days",
                    default=self.config_entry.options.get("historical_days", 7),
                ): vol.In([7, 14, 30, 60, 90]),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            description_placeholders={
                "current_interval": str(
                    self.config_entry.options.get("update_interval", 60)
                ),
                "current_historical_days": str(
                    self.config_entry.options.get("historical_days", 7)
                ),
            },
        )

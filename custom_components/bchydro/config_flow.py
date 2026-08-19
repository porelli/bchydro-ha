"""Config flow for BC Hydro integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import config_validation as cv
from homeassistant.util import slugify

from .api import BCHydroApiClient, BCHydroApiError, BCHydroAuthError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)

HISTORICAL_DAYS_CHOICES = (7, 14, 30, 60, 90)

CONF_ACCOUNT_ID = "account_id"
CONF_ACCOUNT_NAME = "account_name"
#: Distinguishes the statistics of accounts configured alongside each other.
CONF_STATISTIC_SUFFIX = "statistic_suffix"
CONF_ACCOUNT_LABEL = "account_label"


async def validate_auth(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    client = BCHydroApiClient(
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        account_id=data.get(CONF_ACCOUNT_ID),
    )

    try:
        # Try to authenticate with username and password
        await client.authenticate()

        # Get cookies for storage
        cookies = client.get_cookies()

        # A login can hold several accounts (a previous address, a second
        # service, shared access); the user has to tell us which one to follow.
        try:
            accounts = await client.get_accounts()
        except BCHydroApiError as err:
            _LOGGER.debug("Could not list BC Hydro accounts: %s", err)
            accounts = []

        return {
            "title": f"BC Hydro ({data[CONF_USERNAME]})",
            "username": data[CONF_USERNAME],
            "cookies": cookies,
            "accounts": accounts,
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
        self._accounts: list[dict[str, Any]] = []
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            try:
                info = await validate_auth(self.hass, user_input)

                self._cookies = info["cookies"]
                self._accounts = info["accounts"]

                if len(self._accounts) > 1:
                    # Let the user say which account to follow; picking one for
                    # them would silently track the wrong meter. The account step
                    # claims a per-account unique id, so the remaining accounts of
                    # this login can be added afterwards too.
                    return await self.async_step_account()

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
            except BCHydroApiError as err:
                # Transient problem (network, timeout, BC Hydro unavailable) - the
                # credentials may well be fine, so don't blame them.
                _LOGGER.error("Could not reach BC Hydro: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which account to follow, and what to call it.

        Several accounts of the same login can be configured side by side: each
        one gets its own entry, its own device and its own statistics.
        """
        errors: dict[str, str] = {}
        available = self._unconfigured_accounts()

        if not available:
            return self.async_abort(reason="all_accounts_configured")

        if user_input is not None:
            account_id = user_input[CONF_ACCOUNT_ID]
            name = (user_input.get(CONF_ACCOUNT_NAME) or "").strip()
            suffix = self._statistic_suffix(account_id, name)

            if suffix is None:
                errors[CONF_ACCOUNT_NAME] = "name_in_use"
            else:
                label = self._account_label(account_id)
                await self.async_set_unique_id(f"{self._username}:{account_id}")
                self._abort_if_unique_id_configured()

                data = {
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    "cookies": self._cookies,
                    CONF_ACCOUNT_ID: account_id,
                    CONF_ACCOUNT_LABEL: label,
                    CONF_STATISTIC_SUFFIX: suffix,
                }
                if name:
                    data[CONF_ACCOUNT_NAME] = name

                return self.async_create_entry(
                    title=f"BC Hydro ({name or label})", data=data
                )

        default_name = ""
        if len(available) == 1:
            default_name = self._account_number(available[0])

        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCOUNT_ID): vol.In(
                        {
                            str(account["accountId"]): self._account_label(
                                str(account["accountId"])
                            )
                            for account in available
                        }
                    ),
                    vol.Optional(CONF_ACCOUNT_NAME, default=default_name): cv.string,
                }
            ),
            errors=errors,
            description_placeholders={"username": self._username or ""},
        )

    def _unconfigured_accounts(self) -> list[dict[str, Any]]:
        """Return the accounts of this login that are not set up yet."""
        configured = {
            entry.data.get(CONF_ACCOUNT_ID)
            for entry in self._async_current_entries()
            if entry.data.get(CONF_USERNAME) == self._username
        }
        return [
            account
            for account in self._accounts
            if account.get("accountId") and account["accountId"] not in configured
        ]

    def _statistic_suffix(self, account_id: str, name: str) -> str | None:
        """Work out which statistics this account should write to.

        The first BC Hydro account in the system keeps the original statistic ids
        so existing history and Energy dashboard settings keep resolving. Any
        further account gets its own, derived from the name the user gave it.

        Returns:
            The suffix, or None if it would collide with another account's.
        """
        if not self._async_current_entries():
            return ""

        slug = slugify(name) or slugify(self._account_number(account_id)) or "account"
        taken = {
            entry.data.get(CONF_STATISTIC_SUFFIX)
            for entry in self._async_current_entries()
        }
        if slug in taken:
            return None
        return slug

    def _account_number(self, account: dict[str, Any] | str) -> str:
        """Return an account's number without its leading zeros."""
        if isinstance(account, str):
            account = next(
                (a for a in self._accounts if str(a.get("accountId")) == account), {}
            )
        return str(account.get("accountNumber", "")).lstrip("0")

    def _account_label(self, account_id: str) -> str:
        """Describe an account the way the BC Hydro account picker does."""
        for account in self._accounts:
            if str(account.get("accountId")) == account_id:
                description = account.get("accountDesc") or ""
                number = self._account_number(account)
                return f"{number} - {description}".strip(" -") or account_id
        return account_id

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
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
    ) -> ConfigFlowResult:
        """Handle reauth confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input.get(CONF_USERNAME, self._username)
            self._password = user_input[CONF_PASSWORD]

            account_id = (
                self._reauth_entry.data.get(CONF_ACCOUNT_ID)
                if self._reauth_entry
                else None
            )

            try:
                info = await validate_auth(
                    self.hass,
                    {
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                        CONF_ACCOUNT_ID: account_id,
                    },
                )

                if self._reauth_entry:
                    data = {
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                        "cookies": info["cookies"],
                    }
                    if account_id:
                        # Keep following the account the user originally picked
                        data[CONF_ACCOUNT_ID] = account_id
                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry,
                        data=data,
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
            except BCHydroApiError as err:
                # See async_step_user: a transient failure is not a bad password.
                _LOGGER.error("Could not reach BC Hydro during reauth: %s", err)
                errors["base"] = "cannot_connect"
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
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    **user_input,
                    # The choices are offered as strings (see below), store a number.
                    "historical_days": int(user_input["historical_days"]),
                },
            )

        options_schema = vol.Schema(
            {
                vol.Optional(
                    "update_interval",
                    default=self.config_entry.options.get("update_interval", 60),
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=1440)),
                vol.Optional(
                    "historical_days",
                    # Offer the choices as strings: Home Assistant renders a select
                    # of numbers with nothing pre-selected, which reads as "no
                    # history configured" even though a default is in effect.
                    default=str(self.config_entry.options.get("historical_days", 7)),
                ): vol.In([str(days) for days in HISTORICAL_DAYS_CHOICES]),
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

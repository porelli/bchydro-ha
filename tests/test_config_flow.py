"""Test the BC Hydro config flow."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.bchydro.api import BCHydroAuthError, BCHydroLoginBlockedError
from custom_components.bchydro.const import DOMAIN


async def test_form_user_step(hass: HomeAssistant) -> None:
    """Test we get the user form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_form_user_success(hass: HomeAssistant, mock_account_profile_data) -> None:
    """Test successful authentication creates entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Mock the API client and async_setup_entry to prevent real setup
    with patch(
        "custom_components.bchydro.config_flow.BCHydroApiClient"
    ) as mock_api_class, patch(
        "custom_components.bchydro.async_setup_entry", return_value=True
    ):
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(return_value=True)
        mock_client.get_account_profile = AsyncMock(return_value=mock_account_profile_data)
        mock_client.get_cookies = MagicMock(  # Not async!
            return_value={
                "JSESSIONID": "test_session",
                "INGRESSCOOKIE": "test_ingress",
                "bchydroparam": "test_csrf",
            }
        )
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        # Enter credentials
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "test@example.com",
                CONF_PASSWORD: "testpassword",
            },
        )

        assert result2["type"] == FlowResultType.CREATE_ENTRY
        assert result2["title"] == "BC Hydro (test@example.com)"
        assert result2["data"][CONF_USERNAME] == "test@example.com"
        assert result2["data"][CONF_PASSWORD] == "testpassword"
        assert "cookies" in result2["data"]
        assert result2["data"]["cookies"]["JSESSIONID"] == "test_session"


async def test_form_user_invalid_auth(hass: HomeAssistant) -> None:
    """Test invalid credentials shows error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # Mock authentication failure
    with patch(
        "custom_components.bchydro.config_flow.BCHydroApiClient"
    ) as mock_api_class:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(
            side_effect=BCHydroAuthError("Invalid credentials")
        )
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        # Enter invalid credentials
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "test@example.com",
                CONF_PASSWORD: "wrongpassword",
            },
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "user"
    assert result2["errors"] == {"base": "invalid_auth"}


async def test_form_user_captcha_required(hass: HomeAssistant) -> None:
    """Test CAPTCHA required shows specific error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # Mock CAPTCHA requirement
    with patch(
        "custom_components.bchydro.config_flow.BCHydroApiClient"
    ) as mock_api_class:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(
            side_effect=BCHydroLoginBlockedError("BC Hydro is refusing automated logins")
        )
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "test@example.com",
                CONF_PASSWORD: "testpassword",
            },
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "user"
    assert result2["errors"] == {"base": "captcha_required"}


async def test_form_user_unknown_error(hass: HomeAssistant) -> None:
    """Test unknown error shows error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # Mock unknown error
    with patch(
        "custom_components.bchydro.config_flow.BCHydroApiClient"
    ) as mock_api_class:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(side_effect=Exception("Unknown error"))
        mock_client.close = AsyncMock()
        mock_api_class.return_value = mock_client

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "test@example.com",
                CONF_PASSWORD: "testpassword",
            },
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "user"
    assert result2["errors"] == {"base": "unknown"}


async def test_form_user_duplicate_entry(hass: HomeAssistant, mock_config_entry, mock_account_profile_data) -> None:
    """Test we handle duplicate entries."""
    # Mock to avoid setup issues
    with patch("custom_components.bchydro.async_setup_entry", return_value=True):
        # Set the entry state to loaded so it won't try to setup
        mock_config_entry._state = config_entries.ConfigEntryState.LOADED
        # Add existing entry
        await hass.config_entries.async_add(mock_config_entry)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        # Mock the API client
        with patch(
            "custom_components.bchydro.config_flow.BCHydroApiClient"
        ) as mock_api_class:
            mock_client = AsyncMock()
            mock_client.authenticate = AsyncMock(return_value=True)
            mock_client.get_account_profile = AsyncMock(return_value=mock_account_profile_data)
            mock_client.get_cookies = MagicMock(
                return_value={
                    "JSESSIONID": "test_session",
                    "INGRESSCOOKIE": "test_ingress",
                }
            )
            mock_client.close = AsyncMock()
            mock_api_class.return_value = mock_client

            # Try to add duplicate
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_USERNAME: "test@example.com",  # Same as existing
                    CONF_PASSWORD: "testpassword",
                },
            )

        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "already_configured"


async def test_reauth_flow(hass: HomeAssistant, mock_config_entry) -> None:
    """Test reauthentication flow."""
    # Add entry to hass with setup mocked
    with patch("custom_components.bchydro.async_setup_entry", return_value=True):
        await hass.config_entries.async_add(mock_config_entry)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": mock_config_entry.entry_id,
                "unique_id": mock_config_entry.unique_id,
            },
            data=mock_config_entry.data,
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"


async def test_reauth_flow_success(
    hass: HomeAssistant, mock_config_entry, mock_account_profile_data
) -> None:
    """Test successful reauthentication."""
    # Add entry to hass with setup mocked
    with patch("custom_components.bchydro.async_setup_entry", return_value=True):
        await hass.config_entries.async_add(mock_config_entry)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": mock_config_entry.entry_id,
                "unique_id": mock_config_entry.unique_id,
            },
            data=mock_config_entry.data,
        )

        # Mock the API client
        with patch(
            "custom_components.bchydro.config_flow.BCHydroApiClient"
        ) as mock_api_class:
            mock_client = AsyncMock()
            mock_client.authenticate = AsyncMock(return_value=True)
            mock_client.get_account_profile = AsyncMock(return_value=mock_account_profile_data)
            mock_client.get_cookies = MagicMock(
                return_value={
                    "JSESSIONID": "new_session",
                    "INGRESSCOOKIE": "new_ingress",
                    "bchydroparam": "new_csrf",
                }
            )
            mock_client.close = AsyncMock()
            mock_api_class.return_value = mock_client

            # Enter new password
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PASSWORD: "newpassword",
                },
            )

        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "reauth_successful"

        # Verify the entry was updated
        assert mock_config_entry.data[CONF_PASSWORD] == "newpassword"
        assert mock_config_entry.data["cookies"]["JSESSIONID"] == "new_session"


async def test_reauth_flow_invalid_auth(hass: HomeAssistant, mock_config_entry) -> None:
    """Test reauthentication with invalid credentials."""
    # Add entry to hass with setup mocked
    with patch("custom_components.bchydro.async_setup_entry", return_value=True):
        await hass.config_entries.async_add(mock_config_entry)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": mock_config_entry.entry_id,
                "unique_id": mock_config_entry.unique_id,
            },
            data=mock_config_entry.data,
        )

        # Mock authentication failure
        with patch(
            "custom_components.bchydro.config_flow.BCHydroApiClient"
        ) as mock_api_class:
            mock_client = AsyncMock()
            mock_client.authenticate = AsyncMock(
                side_effect=BCHydroAuthError("Invalid credentials")
            )
            mock_client.close = AsyncMock()
            mock_api_class.return_value = mock_client

            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PASSWORD: "wrongpassword",
                },
            )

        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "reauth_confirm"
        assert result2["errors"] == {"base": "invalid_auth"}


async def test_reauth_flow_captcha_required(hass: HomeAssistant, mock_config_entry) -> None:
    """Test reauthentication with CAPTCHA required."""
    # Add entry to hass with setup mocked
    with patch("custom_components.bchydro.async_setup_entry", return_value=True):
        await hass.config_entries.async_add(mock_config_entry)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": mock_config_entry.entry_id,
                "unique_id": mock_config_entry.unique_id,
            },
            data=mock_config_entry.data,
        )

        # Mock CAPTCHA requirement
        with patch(
            "custom_components.bchydro.config_flow.BCHydroApiClient"
        ) as mock_api_class:
            mock_client = AsyncMock()
            mock_client.authenticate = AsyncMock(
                side_effect=BCHydroLoginBlockedError("BC Hydro is refusing automated logins")
            )
            mock_client.close = AsyncMock()
            mock_api_class.return_value = mock_client

            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PASSWORD: "testpassword",
                },
            )

        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "reauth_confirm"
        assert result2["errors"] == {"base": "captcha_required"}


async def test_reauth_flow_unknown_error(hass: HomeAssistant, mock_config_entry) -> None:
    """Test reauthentication with unexpected error."""
    # Add entry to hass with setup mocked
    with patch("custom_components.bchydro.async_setup_entry", return_value=True):
        await hass.config_entries.async_add(mock_config_entry)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": mock_config_entry.entry_id,
                "unique_id": mock_config_entry.unique_id,
            },
            data=mock_config_entry.data,
        )

        # Mock unexpected error
        with patch(
            "custom_components.bchydro.config_flow.BCHydroApiClient"
        ) as mock_api_class:
            mock_client = AsyncMock()
            mock_client.authenticate = AsyncMock(side_effect=Exception("Unexpected error"))
            mock_client.close = AsyncMock()
            mock_api_class.return_value = mock_client

            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PASSWORD: "testpassword",
                },
            )

        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "reauth_confirm"
        assert result2["errors"] == {"base": "unknown"}


def _defaults(result) -> dict:
    return {str(key): key.default() for key in result["data_schema"].schema}


@pytest.mark.usefixtures("skip_setup")
async def test_options_flow_daily_path(hass: HomeAssistant, mock_config_entry) -> None:
    """Step 1 picks the schedule; step 2 only asks what the daily one needs."""
    await hass.config_entries.async_add(mock_config_entry)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"update_interval": 45, "update_mode": "interval"}
    )

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["step_id"] == "init"
    assert set(_defaults(result)) == {"update_mode", "historical_days"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"update_mode": "daily", "historical_days": "7"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "daily"
    assert set(_defaults(result)) == {"daily_check_time", "daily_retry_minutes"}
    assert _defaults(result)["daily_check_time"] == "08:00:00"
    assert _defaults(result)["daily_retry_minutes"] == "60"  # retries on by default
    assert result["description_placeholders"]["arrival_typical"] == "—"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"daily_check_time": "07:30:00", "daily_retry_minutes": "30"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {
        "update_mode": "daily",
        "daily_check_time": "07:30:00",
        "daily_retry_minutes": 30,
        "historical_days": 7,
        # Kept, so switching back to fixed interval restores it
        "update_interval": 45,
    }


@pytest.mark.usefixtures("skip_setup")
async def test_options_flow_interval_path(hass: HomeAssistant, mock_config_entry) -> None:
    """The fixed-interval step asks only for the interval."""
    await hass.config_entries.async_add(mock_config_entry)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"daily_check_time": "07:15:00"}
    )

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"update_mode": "interval", "historical_days": "30"}
    )
    assert result["step_id"] == "interval"
    assert set(_defaults(result)) == {"update_interval"}
    assert _defaults(result)["update_interval"] == 60

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"update_interval": 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {
        "update_mode": "interval",
        "update_interval": 120,
        "historical_days": 30,
        "daily_check_time": "07:15:00",
    }


@pytest.mark.usefixtures("skip_setup")
async def test_options_flow_invalid_interval(hass: HomeAssistant, mock_config_entry) -> None:
    """The interval is still validated on its own step."""
    from homeassistant.data_entry_flow import InvalidData

    await hass.config_entries.async_add(mock_config_entry)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"update_mode": "interval", "historical_days": "7"}
    )

    with pytest.raises(InvalidData) as exc_info:
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"update_interval": 5}  # below the minimum of 30
        )
    assert "update_interval" in str(exc_info.value)


@pytest.mark.usefixtures("skip_setup")
async def test_options_flow_preselects_historical_days(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """The configured history length must come back pre-selected.

    Home Assistant renders a select of plain numbers with no option selected, so
    the form looked like no history was configured at all.
    """
    from homeassistant.helpers import config_validation as cv

    try:  # Home Assistant 2026.10+ serializes forms with probatio
        from probatio import to_field_list as convert
    except ImportError:
        from voluptuous_serialize import convert

    await hass.config_entries.async_add(mock_config_entry)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"update_interval": 120, "historical_days": 30}
    )

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    fields = {
        field["name"]: field
        for field in convert(result["data_schema"], custom_serializer=cv.custom_serializer)
    }
    days = fields["historical_days"]
    option_values = [value for value, _label in days["options"]]

    # The frontend compares the selected value against stringified option values,
    # so numeric choices never light up a radio button.
    assert all(isinstance(value, str) for value in option_values), option_values
    assert days["default"] == "30"
    # 120 minutes was a deliberate choice: the form keeps the fixed interval
    assert fields["update_mode"]["default"] == "interval"

    # Submitting keeps the stored value numeric for the statistics import
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"update_mode": "interval", "historical_days": "60"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"update_interval": 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["historical_days"] == 60

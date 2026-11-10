"""Config flow and lifecycle paths that had no test."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.bchydro.api import BCHydroConnectionError
from custom_components.bchydro.const import DOMAIN
from custom_components.bchydro.coordinator import (
    BCHydroDataUpdateCoordinator,
    arrivals_storage_key,
)
from tests.conftest import create_config_entry


def _client(**overrides) -> AsyncMock:
    client = AsyncMock()
    client.authenticate = AsyncMock(return_value=True)
    client.get_cookies = MagicMock(return_value={"JSESSIONID": "x"})
    client.get_accounts = AsyncMock(return_value=[])
    client.close = AsyncMock()
    for name, value in overrides.items():
        setattr(client, name, value)
    return client


@pytest.mark.usefixtures("skip_setup")
async def test_setup_goes_on_when_the_account_list_fails(hass: HomeAssistant) -> None:
    """Listing accounts is a nicety: a single-account login works without it."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    client = _client(get_accounts=AsyncMock(side_effect=BCHydroConnectionError("down")))
    with patch("custom_components.bchydro.config_flow.BCHydroApiClient", return_value=client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "a@example.com", CONF_PASSWORD: "pw"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_USERNAME] == "a@example.com"


async def test_reauth_keeps_the_chosen_account(hass: HomeAssistant) -> None:
    """Re-entering the password must not switch which account the entry follows."""
    entry = create_config_entry(
        title="BC Hydro (Cabin)",
        data={CONF_USERNAME: "a@example.com", CONF_PASSWORD: "old", "account_id": "BBBB2222"},
        unique_id="a@example.com:BBBB2222",
    )
    with patch("custom_components.bchydro.async_setup_entry", return_value=True):
        await hass.config_entries.async_add(entry)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
                "unique_id": entry.unique_id,
            },
            data=entry.data,
        )
        with patch(
            "custom_components.bchydro.config_flow.BCHydroApiClient", return_value=_client()
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_PASSWORD: "new"}
            )

    assert result["reason"] == "reauth_successful"
    assert entry.data["account_id"] == "BBBB2222"
    assert entry.data[CONF_PASSWORD] == "new"


@pytest.mark.usefixtures("skip_setup")
async def test_options_show_the_arrival_summary_of_a_running_entry(
    hass: HomeAssistant, mock_config_entry
) -> None:
    await hass.config_entries.async_add(mock_config_entry)
    coordinator = BCHydroDataUpdateCoordinator(hass, AsyncMock(), mock_config_entry)
    coordinator.arrival_placeholders = MagicMock(
        return_value={
            "arrival_typical": "07:40",
            "arrival_earliest": "07:10",
            "arrival_latest": "08:20",
            "arrival_days": "12",
            "arrival_untimed_days": "3",
            "arrival_ready_by": "08:00",
        }
    )
    mock_config_entry.runtime_data = coordinator

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"update_mode": "daily", "historical_days": "7"}
    )

    assert result["step_id"] == "daily"
    assert result["description_placeholders"]["arrival_typical"] == "07:40"


async def test_removing_the_entry_deletes_its_arrival_history(
    hass: HomeAssistant, hass_storage, mock_config_entry
) -> None:
    from custom_components.bchydro import async_remove_entry

    key = arrivals_storage_key(mock_config_entry.entry_id)
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {"arrivals": []}}

    await async_remove_entry(hass, mock_config_entry)
    await hass.async_block_till_done()

    assert key not in hass_storage

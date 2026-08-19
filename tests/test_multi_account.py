"""Tests for logins that hold more than one BC Hydro account.

A BC Hydro login can see several accounts (a previous address, a second service,
shared access). Until one of them is activated, the portal answers the data
endpoints with HTML instead of JSON, which surfaced as
"got HTML response after re-authentication" and as a bogus credential error.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.bchydro.api import (
    BCHydroApiClient,
    BCHydroApiError,
)
from custom_components.bchydro.const import (
    ACCOUNT_LIST_URL,
    ACCOUNT_SELECT_URL,
    DOMAIN,
)

ACCOUNTS = [
    {
        "accountNumber": "000012345678",
        "accountDesc": "101 - 123 EXAMPLE ST, VANCOUVER",
        "rateCategory": "RSE",
        "accountId": "AAAA1111",
        "accountType": "ST",
        "selected": False,
        "residential": True,
    },
    {
        "accountNumber": "000087654321",
        "accountDesc": "42 - 456 SAMPLE AVE, VICTORIA",
        "rateCategory": "RSE",
        "accountId": "BBBB2222",
        "accountType": "ST",
        "selected": True,
        "residential": True,
    },
]

PROFILE_JSON = {
    "evpSlid": "0001234567",
    "evpAccount": "000087654321",
    "evpAccountId": "BBBB2222",
    "evpBillingStart": "2026-07-28T00:00:00-07:00",
    "evpBillingEnd": "2026-08-25T00:00:00-07:00",
    "evpConsToDate": "127",
    "evpCostToDate": "$20",
}


def _response(*, status=200, content_type="application/json", json_data=None, text=""):
    """Build a mocked aiohttp response usable as an async context manager."""
    response = AsyncMock()
    response.status = status
    response.url = "https://app.bchydro.com/evportlet/web/global-data.html"
    response.headers = {"Content-Type": content_type}
    response.text = AsyncMock(return_value=text)
    if json_data is None:
        response.json = AsyncMock(side_effect=ValueError("not json"))
    else:
        response.json = AsyncMock(return_value=json_data)
    response.__aenter__.return_value = response
    return response


@pytest.fixture
def mock_session():
    """Return a mocked aiohttp session."""
    return MagicMock(spec=aiohttp.ClientSession)


# --- api layer ---------------------------------------------------------------


async def test_get_accounts_lists_every_account(mock_session) -> None:
    """The portal's account list is exposed to callers."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    mock_session.get.return_value = _response(json_data=ACCOUNTS)

    accounts = await client.get_accounts()

    assert [account["accountId"] for account in accounts] == ["AAAA1111", "BBBB2222"]
    assert mock_session.get.call_args[0][0] == ACCOUNT_LIST_URL


async def test_get_accounts_tolerates_html(mock_session) -> None:
    """An HTML answer yields no accounts rather than an exception."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    mock_session.get.return_value = _response(
        content_type="text/html", text="<html>login</html>"
    )

    assert await client.get_accounts() == []


async def test_select_account_uses_the_account_picker_url(mock_session) -> None:
    """Selecting an account hits the portal's own switch URL."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    mock_session.get.return_value = _response(content_type="text/html", text="<html/>")

    await client.select_account("BBBB2222")

    assert mock_session.get.call_args[0][0] == ACCOUNT_SELECT_URL
    assert mock_session.get.call_args[1]["params"] == {"aid": "BBBB2222"}


async def test_profile_recovers_by_selecting_an_account(mock_session) -> None:
    """An HTML profile response is retried after activating an account."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    client._authenticated = True

    html = _response(content_type="text/html", text="<html>portal</html>")
    accounts = _response(json_data=ACCOUNTS)
    select = _response(content_type="text/html", text="<html/>")
    profile = _response(json_data=PROFILE_JSON)
    mock_session.get.side_effect = [html, accounts, select, profile]

    result = await client.get_account_profile()

    assert result["evpAccountId"] == "BBBB2222"
    # The account flagged as selected is the one activated
    assert mock_session.get.call_args_list[2][1]["params"] == {"aid": "BBBB2222"}
    # ... and no re-authentication was needed
    assert client._authenticated is True


async def test_profile_honours_the_configured_account(mock_session) -> None:
    """The account chosen during setup wins over the portal's default."""
    client = BCHydroApiClient(
        "test@example.com", "password", session=mock_session, account_id="AAAA1111"
    )
    client._authenticated = True

    mock_session.get.side_effect = [
        _response(content_type="text/html", text="<html>portal</html>"),
        _response(json_data=ACCOUNTS),
        _response(content_type="text/html", text="<html/>"),
        _response(json_data=PROFILE_JSON),
    ]

    await client.get_account_profile()

    assert mock_session.get.call_args_list[2][1]["params"] == {"aid": "AAAA1111"}


async def test_unknown_configured_account_is_reported(mock_session) -> None:
    """A stale account id must be reported, not silently swapped."""
    client = BCHydroApiClient(
        "test@example.com", "password", session=mock_session, account_id="GONE9999"
    )
    client._authenticated = True

    mock_session.get.side_effect = [
        _response(content_type="text/html", text="<html>portal</html>"),
        _response(json_data=ACCOUNTS),
    ]

    with pytest.raises(BCHydroApiError, match="not available"):
        await client.get_account_profile()


# --- config flow -------------------------------------------------------------


async def test_config_flow_asks_which_account_to_use(hass: HomeAssistant) -> None:
    """With several accounts the user picks one; it is stored on the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    with patch(
        "custom_components.bchydro.config_flow.BCHydroApiClient"
    ) as mock_api_class:
        client = AsyncMock()
        client.authenticate = AsyncMock(return_value=True)
        client.get_cookies = MagicMock(return_value={"JSESSIONID": "x"})
        client.get_accounts = AsyncMock(return_value=ACCOUNTS)
        client.close = AsyncMock()
        mock_api_class.return_value = client

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "test@example.com", CONF_PASSWORD: "password"},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "account"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"account_id": "AAAA1111"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["account_id"] == "AAAA1111"
    assert "SAMPLE AVE" not in result["title"]
    assert "12345678" in result["title"]


async def test_config_flow_skips_the_question_for_a_single_account(
    hass: HomeAssistant,
) -> None:
    """One account means no extra step and no stored account id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    with patch(
        "custom_components.bchydro.config_flow.BCHydroApiClient"
    ) as mock_api_class:
        client = AsyncMock()
        client.authenticate = AsyncMock(return_value=True)
        client.get_cookies = MagicMock(return_value={"JSESSIONID": "x"})
        client.get_accounts = AsyncMock(return_value=ACCOUNTS[:1])
        client.close = AsyncMock()
        mock_api_class.return_value = client

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "test@example.com", CONF_PASSWORD: "password"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "account_id" not in result["data"]


async def test_setup_passes_the_selected_account_to_the_client(
    hass: HomeAssistant,
) -> None:
    """The stored account must reach the API client and survive a restart."""
    from homeassistant.config_entries import ConfigEntryState

    from tests.conftest import create_config_entry

    entry = create_config_entry(
        data={
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "password",
            "cookies": {},
            "account_id": "AAAA1111",
        }
    )

    with patch("custom_components.bchydro.BCHydroApiClient") as mock_api_class, patch(
        "custom_components.bchydro.coordinator.async_import_statistics",
        new_callable=AsyncMock,
    ), patch(
        "custom_components.bchydro.coordinator.async_cleanup_future_statistics",
        new_callable=AsyncMock,
        return_value=0,
    ), patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        new_callable=AsyncMock,
    ):
        client = AsyncMock()
        client.authenticate = AsyncMock(return_value=True)
        client.get_cookies = MagicMock(return_value={"JSESSIONID": "y"})
        client.get_account_profile = AsyncMock(return_value=PROFILE_JSON)
        client.get_consumption_data = AsyncMock(return_value={"hourly_consumption": []})
        mock_api_class.return_value = client

        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

    assert mock_api_class.call_args[1]["account_id"] == "AAAA1111"
    # Refreshing the cookies must not drop the account selection
    assert entry.data["account_id"] == "AAAA1111"


# --- several accounts of one login, side by side -------------------------------


@pytest.fixture
def mock_setup_client():
    """Patch the client used by both the config flow and the integration setup."""
    client = AsyncMock()
    client.authenticate = AsyncMock(return_value=True)
    client.get_cookies = MagicMock(return_value={"JSESSIONID": "x"})
    client.get_accounts = AsyncMock(return_value=ACCOUNTS)
    client.get_account_profile = AsyncMock(return_value=PROFILE_JSON)
    client.get_consumption_data = AsyncMock(return_value={"hourly_consumption": []})
    client.close = AsyncMock()
    with patch(
        "custom_components.bchydro.config_flow.BCHydroApiClient", return_value=client
    ), patch(
        "custom_components.bchydro.BCHydroApiClient", return_value=client
    ), patch(
        "custom_components.bchydro.coordinator.async_import_statistics",
        new_callable=AsyncMock,
    ), patch(
        "custom_components.bchydro.coordinator.async_cleanup_future_statistics",
        new_callable=AsyncMock,
        return_value=0,
    ):
        yield client


async def _add_account(hass: HomeAssistant, account_id: str, name: str | None = None):
    """Walk the config flow and configure one account."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "test@example.com", CONF_PASSWORD: "password"},
    )
    if result["type"] is not FlowResultType.FORM:
        return result
    payload: dict[str, str] = {"account_id": account_id}
    if name is not None:
        payload["account_name"] = name
    return await hass.config_entries.flow.async_configure(result["flow_id"], payload)


async def test_two_accounts_keep_separate_statistics(
    hass: HomeAssistant, mock_setup_client
) -> None:
    """Two accounts must never write into the same statistic.

    Sharing statistic ids interleaves two cumulative sums, which shows up in the
    Energy dashboard as impossible jumps and negative consumption.
    """
    from custom_components.bchydro.statistics import account_statistics

    first = await _add_account(hass, "AAAA1111", "Home")
    assert first["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    second = await _add_account(hass, "BBBB2222", "Cabin")
    assert second["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 2

    suffixes = {entry.data.get("statistic_suffix") for entry in entries}
    # The first account keeps the original ids, the second gets its own
    assert suffixes == {"", "cabin"}

    ids = [
        account_statistics(entry.data.get("statistic_suffix", "")).energy_id
        for entry in entries
    ]
    assert len(set(ids)) == 2
    assert "bchydro:energy_consumption" in ids
    assert "bchydro:energy_consumption_cabin" in ids


async def test_second_account_gets_its_own_device_name(
    hass: HomeAssistant, mock_setup_client
) -> None:
    """Entities must be attributable to an account, so devices are named."""
    from homeassistant.helpers import device_registry as dr

    await _add_account(hass, "AAAA1111", "Home")
    await hass.async_block_till_done()
    await _add_account(hass, "BBBB2222", "Cabin")
    await hass.async_block_till_done()

    registry = dr.async_get(hass)
    names = {
        device.name
        for entry in hass.config_entries.async_entries(DOMAIN)
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert names == {"BC Hydro Home", "BC Hydro Cabin"}


async def test_configured_accounts_are_not_offered_again(
    hass: HomeAssistant, mock_setup_client
) -> None:
    """The picker only lists accounts that are not set up yet."""
    await _add_account(hass, "AAAA1111", "Home")
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "test@example.com", CONF_PASSWORD: "password"},
    )
    assert result["step_id"] == "account"
    offered = [
        value
        for value, _label in result["data_schema"].schema["account_id"].container.items()
    ]
    assert offered == ["BBBB2222"]


async def test_flow_aborts_when_every_account_is_configured(
    hass: HomeAssistant, mock_setup_client
) -> None:
    """Nothing left to add is an abort, not an empty form."""
    await _add_account(hass, "AAAA1111", "Home")
    await hass.async_block_till_done()
    await _add_account(hass, "BBBB2222", "Cabin")
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "test@example.com", CONF_PASSWORD: "password"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "all_accounts_configured"


async def test_duplicate_name_is_rejected(
    hass: HomeAssistant, mock_setup_client
) -> None:
    """Two accounts named alike would collide on statistic ids."""
    third = dict(ACCOUNTS[0])
    third.update(
        {
            "accountId": "CCCC3333",
            "accountNumber": "000011112222",
            "accountDesc": "7 - 789 THIRD ST, BURNABY",
        }
    )
    mock_setup_client.get_accounts = AsyncMock(return_value=[*ACCOUNTS, third])

    # First account keeps the original statistic ids, the second claims "cabin"
    await _add_account(hass, "AAAA1111", "Home")
    await hass.async_block_till_done()
    await _add_account(hass, "BBBB2222", "Cabin")
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "test@example.com", CONF_PASSWORD: "password"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"account_id": "CCCC3333", "account_name": "cabin"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"account_name": "name_in_use"}

    # A different name is accepted
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"account_id": "CCCC3333", "account_name": "Garage"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["statistic_suffix"] == "garage"


async def test_existing_entry_records_which_account_it_follows(
    hass: HomeAssistant, mock_setup_client
) -> None:
    """An entry from before account selection learns its account on setup.

    Without it, adding a second account would offer the one already in use.
    """
    from homeassistant.config_entries import ConfigEntryState

    from tests.conftest import create_config_entry

    entry = create_config_entry(
        data={
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "password",
            "cookies": {},
        }
    )
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    # PROFILE_JSON reports BBBB2222 as the account being served
    assert entry.data["account_id"] == "BBBB2222"
    # ... and it keeps the original statistic ids
    assert entry.data.get("statistic_suffix", "") == ""

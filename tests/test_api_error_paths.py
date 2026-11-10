"""Error and recovery paths of the BC Hydro client that had no test."""
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.bchydro.api import (
    BCHydroApiClient,
    BCHydroApiError,
    BCHydroConnectionError,
)
from tests.test_multi_account import ACCOUNTS, PROFILE_JSON, _response


@pytest.fixture
def session():
    return MagicMock(spec=aiohttp.ClientSession)


# --- listing and selecting accounts ------------------------------------------------


async def test_account_calls_need_a_session() -> None:
    client = BCHydroApiClient("a@example.com", "pw")

    with pytest.raises(BCHydroApiError, match="Session not initialized"):
        await client.get_accounts()
    with pytest.raises(BCHydroApiError, match="Session not initialized"):
        await client.select_account("AAAA1111")


async def test_account_list_http_error_is_transient(session) -> None:
    session.get.return_value = _response(status=503)
    client = BCHydroApiClient("a@example.com", "pw", session=session)

    with pytest.raises(BCHydroConnectionError, match="503"):
        await client.get_accounts()


async def test_account_list_that_is_not_a_list_reads_as_empty(session) -> None:
    session.get.return_value = _response(json_data={"unexpected": "object"})
    client = BCHydroApiClient("a@example.com", "pw", session=session)

    assert await client.get_accounts() == []


async def test_account_select_http_error_is_transient(session) -> None:
    session.get.return_value = _response(status=500)
    client = BCHydroApiClient("a@example.com", "pw", session=session)

    with pytest.raises(BCHydroConnectionError, match="500"):
        await client.select_account("AAAA1111")


async def test_account_select_network_error_is_transient(session) -> None:
    session.get.side_effect = aiohttp.ClientConnectionError("boom")
    client = BCHydroApiClient("a@example.com", "pw", session=session)

    with pytest.raises(BCHydroConnectionError, match="selecting account"):
        await client.select_account("AAAA1111")


async def test_account_without_an_id_is_not_selected(session) -> None:
    client = BCHydroApiClient("a@example.com", "pw", session=session)
    client.get_accounts = AsyncMock(return_value=[{"accountNumber": "1", "selected": True}])
    client.select_account = AsyncMock()

    assert await client._select_preferred_account() is False
    client.select_account.assert_not_called()


async def test_failed_selection_during_recovery_is_not_fatal(session) -> None:
    client = BCHydroApiClient("a@example.com", "pw", session=session)
    client._select_preferred_account = AsyncMock(side_effect=BCHydroConnectionError("x"))

    assert await client._select_account_for_recovery() is False


# --- logging in --------------------------------------------------------------------


async def test_login_selects_the_configured_account(session) -> None:
    """Every fresh login lands on the default account; the configured one is reselected."""
    login = AsyncMock()
    login.status = 200
    login.url = "https://app.bchydro.com/BCHCustomerPortal/web/accountsOverview.html"
    login.text = AsyncMock(return_value='<input name="bchydroparam" value="tok"/>')
    login.__aenter__.return_value = login
    session.post.return_value = login
    session.cookie_jar = aiohttp.CookieJar()
    client = BCHydroApiClient("a@example.com", "pw", session=session, account_id="AAAA1111")
    client._cookies = {"JSESSIONID": "x"}
    client._select_preferred_account = AsyncMock(return_value=True)

    await client.authenticate()

    client._select_preferred_account.assert_awaited_once()


# --- recovering the account profile ------------------------------------------------


async def test_profile_still_html_after_logging_in_again_is_an_error(session) -> None:
    session.get.return_value = _response(content_type="text/html", text="<html></html>")
    client = BCHydroApiClient("a@example.com", "pw", session=session)
    client._authenticated = True
    client._select_account_for_recovery = AsyncMock(return_value=False)
    client.authenticate = AsyncMock(return_value=True)

    with pytest.raises(BCHydroApiError, match="HTML response after re-authentication"):
        await client.get_account_profile()


async def test_unparseable_profile_recovers_by_selecting_an_account(session) -> None:
    """A login with several accounts answers JSON-shaped requests with junk until one is chosen."""
    session.get.side_effect = [
        _response(content_type="application/json"),  # .json() fails
        _response(json_data=PROFILE_JSON),
    ]
    client = BCHydroApiClient("a@example.com", "pw", session=session)
    client._authenticated = True
    client._accounts = ACCOUNTS
    client._select_account_for_recovery = AsyncMock(return_value=True)

    profile = await client.get_account_profile()

    assert profile["evpAccountId"] == "BBBB2222"
    client._select_account_for_recovery.assert_awaited_once()

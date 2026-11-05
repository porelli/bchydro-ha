"""Tests for reusing one BC Hydro login across requests.

Every password login counts against BC Hydro's reCAPTCHA risk scoring; logging in
for each consumption request (three times per hourly update) eventually got the
integration's logins turned away for hours. The consumption endpoint does answer
a repeat request on its own session with 401, but forgetting just that session
(the /evportlet cookies) is enough - the login itself stays valid.
"""
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from yarl import URL

from custom_components.bchydro.api import (
    BCHydroApiClient,
    BCHydroApiError,
    BCHydroAuthError,
    BCHydroConnectionError,
    BCHydroLoginBlockedError,
)
from custom_components.bchydro.coordinator import BCHydroDataUpdateCoordinator

CONSUMPTION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Data evpCurrentDateTime="2026-11-04T10:00:00-08:00" blockStatus="0" nonWan="false">
    <Series blockStatus="0">
        <Point type="SMI" quality="ACTUAL" dateTime="2026-11-03T00:00:00-08:00"
               endTime="2026-11-03T01:00:00-08:00" value="0.5" cost="0.06"/>
    </Series>
</Data>"""


def _response(status: int = 200, text: str = CONSUMPTION_XML, content_type: str = "text/xml"):
    response = AsyncMock()
    response.status = status
    response.headers = {"Content-Type": content_type}
    response.url = URL("https://app.bchydro.com/evportlet/web/consumption-data.html")
    response.text = AsyncMock(return_value=text)
    response.__aenter__.return_value = response
    return response


def _set_cookie(jar: aiohttp.CookieJar, name: str, path: str) -> None:
    cookie = SimpleCookie()
    cookie[name] = "value"
    cookie[name]["path"] = path
    jar.update_cookies(cookie, URL(f"https://app.bchydro.com{path}"))


async def _signed_in_client(responses) -> BCHydroApiClient:
    session = MagicMock()
    session.cookie_jar = aiohttp.CookieJar()
    session.post.side_effect = responses
    client = BCHydroApiClient("test@example.com", "password", session=session)
    client._authenticated = True
    client._csrf_token = "token"
    client.authenticate = AsyncMock(return_value=True)
    return client


async def test_consumption_requests_reuse_the_login() -> None:
    """Repeated consumption requests must not log in again."""
    client = await _signed_in_client([_response(), _response(), _response()])

    for _ in range(3):
        data = await client.get_consumption_data(granularity="hourly")
        assert len(data["hourly_consumption"]) == 1

    client.authenticate.assert_not_called()


async def test_usage_session_is_forgotten_before_each_request() -> None:
    """The /evportlet session cookies go, the login cookies stay."""
    client = await _signed_in_client([_response()])
    jar = client._provided_session.cookie_jar
    _set_cookie(jar, "JSESSIONID", "/evportlet")
    _set_cookie(jar, "INGRESSCOOKIE", "/evportlet")
    _set_cookie(jar, "JSESSIONID", "/BCHCustomerPortal")
    _set_cookie(jar, "uid", "/")

    await client.get_consumption_data()

    remaining = {(cookie.key, cookie["path"]) for cookie in jar}
    assert remaining == {("JSESSIONID", "/BCHCustomerPortal"), ("uid", "/")}


async def test_logs_in_when_not_signed_in() -> None:
    client = await _signed_in_client([_response()])
    client._authenticated = False

    await client.get_consumption_data()

    client.authenticate.assert_called_once()


def _login_redirect():
    response = _response(text="<html><body>Log in</body></html>", content_type="text/html")
    response.url = URL("https://app.bchydro.com/sso/UI/Login?goto=x")
    return response


@pytest.mark.parametrize(
    "expired",
    [_response(status=401, text=""), _login_redirect()],
    ids=["401", "login-page"],
)
async def test_expired_login_is_renewed_once(expired) -> None:
    """When BC Hydro no longer accepts the login, sign in again and retry."""
    client = await _signed_in_client([expired, _response()])

    data = await client.get_consumption_data()

    assert data["current_date_time"] == "2026-11-04T10:00:00-08:00"
    client.authenticate.assert_called_once()


async def test_rejected_after_fresh_login_raises() -> None:
    """Do not loop on logins if even a fresh one is not accepted."""
    client = await _signed_in_client([_response(status=401, text=""), _response(status=401, text="")])

    with pytest.raises(BCHydroApiError, match="right after logging in"):
        await client.get_consumption_data()

    client.authenticate.assert_called_once()


async def test_error_page_is_transient_and_does_not_log_in() -> None:
    """BC Hydro's error pages are not an expired login; don't burn a login on them."""
    error_page = _response(
        text="<!DOCTYPE html><html><body>Service unavailable</body></html>",
        content_type="text/html;charset=utf-8",
    )
    error_page.url = URL("https://www.bchydro.com/siteinfo/service_unavailable.html")
    client = await _signed_in_client([error_page])

    with pytest.raises(BCHydroConnectionError, match="service_unavailable"):
        await client.get_consumption_data()

    client.authenticate.assert_not_called()


# --- login classification -------------------------------------------------------


def _login_session(status: int, html: str) -> MagicMock:
    response = AsyncMock()
    response.status = status
    response.url = URL("https://app.bchydro.com/sso/UI/Login")
    response.text = AsyncMock(return_value=html)
    response.__aenter__.return_value = response
    session = MagicMock()
    session.post.return_value = response
    session.cookie_jar = aiohttp.CookieJar()
    return session


# Trimmed from the real page: reCAPTCHA is always embedded now, so the word
# "captcha" appears on every login page whether or not anything went wrong.
REAL_LOGIN_PAGE = """<html><head>
<script src="https://www.google.com/recaptcha/api.js?render=6Lel"></script></head><body>
<form id="login-form" action="/sso/UI/Login" method="post">
<input type="hidden" id="captchaEnabled" name="captchaEnabled" value="true">
<input type="hidden" id="g-recaptcha-response" name="g-recaptcha-response" value="">
<input type="hidden" name="bchydroparam" value="abc" />
</form></body></html>"""


async def test_login_rejected_with_401_is_an_auth_error() -> None:
    client = BCHydroApiClient("a@example.com", "wrong", session=_login_session(401, ""))

    with pytest.raises(BCHydroAuthError):
        await client.authenticate()


async def test_login_page_returned_is_a_block_not_a_bad_password() -> None:
    client = BCHydroApiClient("a@example.com", "pw", session=_login_session(200, REAL_LOGIN_PAGE))

    with pytest.raises(BCHydroLoginBlockedError) as excinfo:
        await client.authenticate()

    assert not isinstance(excinfo.value, BCHydroAuthError)


async def test_login_page_with_rejection_message_is_an_auth_error() -> None:
    html = REAL_LOGIN_PAGE.replace(
        "<form", '<div class="error">Invalid email or password.</div><form'
    )
    client = BCHydroApiClient("a@example.com", "pw", session=_login_session(200, html))

    with pytest.raises(BCHydroAuthError):
        await client.authenticate()


async def test_blocked_logins_never_request_reauth(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Hours of bot-blocked logins must not end in a reauthentication request."""
    client = MagicMock(spec=BCHydroApiClient)
    client.get_account_profile = AsyncMock(
        side_effect=BCHydroLoginBlockedError("refusing automated logins")
    )
    coordinator = BCHydroDataUpdateCoordinator(hass, client, mock_config_entry)

    with patch(
        "custom_components.bchydro.coordinator.async_cleanup_future_statistics",
        new_callable=AsyncMock,
        return_value=0,
    ):
        for _ in range(coordinator.MAX_CONSECUTIVE_AUTH_FAILURES * 3):
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()

    assert coordinator.consecutive_auth_failures == 0


# --- keepalive -------------------------------------------------------------------


def _keepalive_client(response=None, error=None) -> BCHydroApiClient:
    session = MagicMock()
    session.cookie_jar = aiohttp.CookieJar()
    if error is not None:
        session.get.side_effect = error
    else:
        session.get.return_value = response
    client = BCHydroApiClient("test@example.com", "password", session=session)
    client._authenticated = True
    return client


async def test_keepalive_reports_live_session() -> None:
    response = _response(text='{"evpAccountId": "1"}', content_type="application/json")
    client = _keepalive_client(response)

    assert await client.keep_alive() is True
    assert client._authenticated is True


async def test_keepalive_marks_expired_session_without_logging_in() -> None:
    client = _keepalive_client(_login_redirect())
    client.authenticate = AsyncMock()

    assert await client.keep_alive() is False

    assert client._authenticated is False
    client.authenticate.assert_not_called()


async def test_keepalive_skips_when_signed_out() -> None:
    client = _keepalive_client(_response())
    client._authenticated = False

    assert await client.keep_alive() is False
    client._provided_session.get.assert_not_called()


async def test_keepalive_network_error_keeps_session() -> None:
    client = _keepalive_client(error=aiohttp.ClientConnectionError("boom"))

    assert await client.keep_alive() is True
    assert client._authenticated is True


async def test_setup_schedules_keepalive(
    hass: HomeAssistant, mock_config_entry, mock_account_profile_data
) -> None:
    """The integration pings BC Hydro between hourly refreshes."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.bchydro.const import KEEPALIVE_INTERVAL_MINUTES

    with patch("custom_components.bchydro.BCHydroApiClient") as mock_api_class, patch(
        "custom_components.bchydro.coordinator.BCHydroDataUpdateCoordinator.async_config_entry_first_refresh",
        new_callable=AsyncMock,
    ), patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        new_callable=AsyncMock,
    ):
        client = AsyncMock()
        client.get_cookies = MagicMock(return_value={})
        client.get_account_profile = AsyncMock(return_value=mock_account_profile_data)
        mock_api_class.return_value = client

        await hass.config_entries.async_add(mock_config_entry)
        await hass.async_block_till_done()
        client.keep_alive.assert_not_called()

        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(minutes=KEEPALIVE_INTERVAL_MINUTES + 1)
        )
        await hass.async_block_till_done()
        client.keep_alive.assert_awaited_once()

        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(minutes=3 * KEEPALIVE_INTERVAL_MINUTES)
        )
        await hass.async_block_till_done()
        client.keep_alive.assert_awaited_once()

"""The other side of conditions that were only ever tested one way."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from bs4 import BeautifulSoup
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.bchydro.api import (
    BCHydroApiClient,
    BCHydroAuthError,
    _login_page_message,
)
from custom_components.bchydro.const import DOMAIN
from custom_components.bchydro.schedule import newest_actual_reading
from custom_components.bchydro.statistics import (
    STATISTIC_ID_COST,
    STATISTIC_ID_ENERGY,
    _get_earliest_statistics_timestamp,
    async_import_statistics,
)

# --- integration lifecycle --------------------------------------------------------


async def test_failed_unload_keeps_the_client_open(hass: HomeAssistant, mock_config_entry) -> None:
    """If Home Assistant cannot unload the sensors, the entry stays up: keep its session."""
    from custom_components.bchydro import async_unload_entry

    coordinator = MagicMock()
    coordinator.client.close = AsyncMock()
    mock_config_entry.runtime_data = coordinator
    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
    ):
        assert await async_unload_entry(hass, mock_config_entry) is False

    coordinator.client.close.assert_not_called()


async def test_reauth_for_a_removed_entry_aborts(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": "gone"},
        data={"username": "a@example.com"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_entry_missing"


# --- reading BC Hydro's pages -----------------------------------------------------


def test_a_repeated_error_message_is_reported_once() -> None:
    page = '<div class="error">Try again</div><p class="error">Try again</p>'
    assert _login_page_message(page) == "Try again"


async def test_empty_token_field_means_no_token() -> None:
    client = BCHydroApiClient("a@example.com", "pw")
    soup = BeautifulSoup('<input name="bchydroparam" value=""/>', "html.parser")

    with pytest.raises(BCHydroAuthError, match="bchydroparam"):
        client._parse_bchydroparam(soup)


def _response(status: int, cookies=()):
    response = AsyncMock()
    response.status = status
    response.cookies = MagicMock()
    response.cookies.values.return_value = list(cookies)
    response.__aenter__.return_value = response
    return response


def _cookie(key: str, value: str):
    cookie = MagicMock()
    cookie.key, cookie.value = key, value
    cookie.get.return_value = "app.bchydro.com"
    cookie.__getitem__.side_effect = lambda k: "app.bchydro.com" if k == "domain" else None
    return cookie


@pytest.mark.parametrize(
    ("global_data", "expected_token"),
    [
        # BC Hydro refuses global-data: keep the token from the saved cookies
        (_response(503), "saved"),
        # A response that sets other cookies but not the token
        (_response(200, [_cookie("JSESSIONID", "j")]), "saved"),
    ],
    ids=["global-data-refused", "no-token-cookie"],
)
async def test_cookie_login_keeps_the_saved_token(global_data, expected_token) -> None:
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get.side_effect = [_response(200), global_data]
    session.cookie_jar = MagicMock()
    session.cookie_jar.__iter__ = lambda self: iter([_cookie("bchydroparam", "saved")])
    client = BCHydroApiClient("a@example.com", "pw", session=session)

    assert await client.authenticate_with_cookies({"bchydroparam": "saved"}) is True
    assert client._csrf_token == expected_token


# --- order of readings ------------------------------------------------------------


def test_newest_reading_does_not_depend_on_order() -> None:
    hourly = [
        {"date_time": "2026-11-07T22:00:01-08:00", "quality": "ACTUAL"},
        {"date_time": "2026-11-07T20:00:01-08:00", "quality": "ACTUAL"},
    ]
    assert newest_actual_reading(hourly) == datetime.fromisoformat("2026-11-07T22:00:01-08:00")


@pytest.fixture
def recorder():
    with patch("custom_components.bchydro.statistics.get_instance") as get_instance:
        instance = MagicMock()
        instance.async_add_executor_job = AsyncMock()
        get_instance.return_value = instance
        yield instance


async def test_earliest_statistic_does_not_depend_on_order(hass: HomeAssistant, recorder) -> None:
    early = datetime(2026, 10, 1, tzinfo=timezone.utc)
    recorder.async_add_executor_job.return_value = {
        STATISTIC_ID_ENERGY: [{"start": early.timestamp()}, {"start": (early + timedelta(hours=5)).timestamp()}]
    }

    assert await _get_earliest_statistics_timestamp(hass) == early


async def test_rewrite_window_inside_a_long_history(hass: HomeAssistant, recorder) -> None:
    """History older than the window: the window starts 30 days before the last hour.

    BC Hydro's readings also arrive newest-first here, which must not matter.
    """
    last = datetime(2026, 11, 8, 6, tzinfo=timezone.utc)
    earliest = last - timedelta(days=90)
    recorder.async_add_executor_job.side_effect = [
        {STATISTIC_ID_ENERGY: [{"start": last.timestamp(), "sum": 100.0}]},
        {STATISTIC_ID_COST: [{"start": last.timestamp(), "sum": 10.0}]},
        {STATISTIC_ID_ENERGY: [{"start": earliest.timestamp(), "sum": 1.0}]},
        {
            STATISTIC_ID_ENERGY: [{"start": last.timestamp(), "state": 1.0, "sum": 100.0}],
            STATISTIC_ID_COST: [{"start": last.timestamp(), "state": 0.1, "sum": 10.0}],
        },
    ]
    client = AsyncMock()
    client.get_consumption_data.return_value = {
        "hourly_consumption": [
            {"date_time": "2026-11-07T23:00:01-08:00", "value": 2.0, "cost": 0.2, "quality": "ACTUAL"},
            {"date_time": "2026-11-07T22:00:01-08:00", "value": 1.0, "cost": 0.1, "quality": "ACTUAL"},
        ]
    }
    with patch("custom_components.bchydro.statistics.async_add_external_statistics") as add:
        await async_import_statistics(hass, client, historical_days=7)

    fetch_start = client.get_consumption_data.call_args.kwargs["start_date"]
    assert fetch_start == last - timedelta(days=31)
    energy_rows = add.call_args_list[0][0][2]
    assert [(r["state"], r["sum"]) for r in energy_rows] == [(2.0, 102.0)]

"""Test the BC Hydro API client."""
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from bs4 import BeautifulSoup

from custom_components.bchydro.api import (
    BCHydroApiClient,
    BCHydroApiError,
    BCHydroAuthError,
    BCHydroConnectionError,
)


@pytest.fixture
def mock_session():
    """Create a mock aiohttp session."""
    session = MagicMock(spec=aiohttp.ClientSession)
    return session


async def test_authenticate_success(mock_session):
    """Test successful authentication."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock successful login response (returns to accountsOverview with bchydroparam)
    mock_login_response = AsyncMock()
    mock_login_response.status = 200
    mock_login_response.url = "https://app.bchydro.com/BCHCustomerPortal/web/accountsOverview.html"
    mock_login_response.text = AsyncMock(
        return_value='<html><input name="bchydroparam" value="test_csrf_token" /></html>'
    )
    mock_login_response.__aenter__.return_value = mock_login_response

    # Mock cookie jar with cookies
    mock_cookie = MagicMock()
    mock_cookie.key = "JSESSIONID"
    mock_cookie.value = "test_session_id"
    mock_cookie_jar = MagicMock()
    mock_cookie_jar.__iter__.return_value = [mock_cookie]
    mock_session.cookie_jar = mock_cookie_jar

    # Set up session mock for login
    mock_session.post.return_value = mock_login_response

    result = await client.authenticate()
    assert result is True
    assert client._authenticated is True
    assert client._csrf_token == "test_csrf_token"
    # Verify post was called for login
    mock_session.post.assert_called_once()
    # Verify cookies were collected
    assert len(client._cookies) > 0


async def test_authenticate_failure_wrong_credentials(mock_session):
    """Test authentication failure with wrong credentials."""
    client = BCHydroApiClient("test@example.com", "wrong_password", session=mock_session)

    # Mock failed login (still on login page)
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.url = "https://app.bchydro.com/sso/UI/Login"
    mock_response.text = AsyncMock(return_value="<html>Login Page</html>")
    mock_response.__aenter__.return_value = mock_response
    mock_session.post.return_value = mock_response

    with pytest.raises(BCHydroAuthError, match="Authentication failed"):
        await client.authenticate()


async def test_authenticate_captcha_required(mock_session):
    """Test authentication when CAPTCHA is required."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock response with CAPTCHA
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.url = "https://app.bchydro.com/sso/UI/Login"
    mock_response.text = AsyncMock(
        return_value="<html>Please complete the reCAPTCHA verification</html>"
    )
    mock_response.__aenter__.return_value = mock_response
    mock_session.post.return_value = mock_response

    with pytest.raises(BCHydroAuthError, match="CAPTCHA"):
        await client.authenticate()


async def test_authenticate_creates_own_session():
    """Test that client creates its own session when none provided."""
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # Mock successful login response
        mock_login_response = AsyncMock()
        mock_login_response.status = 200
        mock_login_response.url = "https://app.bchydro.com/BCHCustomerPortal/web/accountsOverview.html"
        mock_login_response.text = AsyncMock(
            return_value='<input name="bchydroparam" value="test_token"/>'
        )
        mock_login_response.__aenter__.return_value = mock_login_response

        mock_session.post.return_value = mock_login_response
        mock_session.cookie_jar = MagicMock()
        mock_session.cookie_jar.__iter__ = lambda self: iter([
            MagicMock(key="JSESSIONID", value="test_session")
        ])

        # Create client WITHOUT providing a session
        client = BCHydroApiClient("test@example.com", "password")

        await client.authenticate()

        # Verify session was created
        mock_session_class.assert_called_once()
        assert client._authenticated is True


async def test_authenticate_with_cookies_success(mock_session, mock_account_profile_data):
    """Test successful authentication with cookies."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock CSRF cookie with proper domain attribute
    mock_csrf_cookie = MagicMock()
    mock_csrf_cookie.key = "bchydroparam"
    mock_csrf_cookie.value = "test_csrf"
    mock_csrf_cookie.get.return_value = "app.bchydro.com"  # For .get("domain")
    mock_csrf_cookie.__getitem__.side_effect = lambda key: "app.bchydro.com" if key == "domain" else None

    # Mock successful API responses (account profile + global data for CSRF)
    mock_account_response = AsyncMock()
    mock_account_response.status = 200
    mock_account_response.__aenter__.return_value = mock_account_response

    mock_global_response = AsyncMock()
    mock_global_response.status = 200
    mock_global_response.cookies = MagicMock()
    mock_global_response.cookies.values.return_value = [mock_csrf_cookie]
    mock_global_response.__aenter__.return_value = mock_global_response

    # Set up get() to return different responses on successive calls
    mock_session.get.side_effect = [mock_account_response, mock_global_response]

    # Mock cookie jar
    mock_session.cookie_jar = MagicMock()
    mock_session.cookie_jar.__iter__ = lambda self: iter([mock_csrf_cookie])

    cookies = {
        "JSESSIONID": "test_session",
        "INGRESSCOOKIE": "test_ingress",
        "bchydroparam": "test_csrf",
    }

    result = await client.authenticate_with_cookies(cookies)
    assert result is True
    assert client._authenticated is True
    assert client._cookies["bchydroparam"] == "test_csrf"
    assert client._csrf_token == "test_csrf"


async def test_authenticate_with_cookies_invalid(mock_session):
    """Test authentication with invalid cookies."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock 401 response
    mock_response = AsyncMock()
    mock_response.status = 401
    mock_response.__aenter__.return_value = mock_response
    mock_session.get.return_value = mock_response

    cookies = {
        "JSESSIONID": "invalid_session",
    }

    with pytest.raises(BCHydroAuthError):
        await client.authenticate_with_cookies(cookies)


async def test_get_account_profile_success(mock_session, mock_account_profile_data):
    """Test getting account profile data."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    client._authenticated = True
    client._cookies = {"JSESSIONID": "test_session", "INGRESSCOOKIE": "test_ingress"}

    # Mock successful API response
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.json = AsyncMock(return_value=mock_account_profile_data)
    mock_response.__aenter__.return_value = mock_response
    mock_session.get.return_value = mock_response

    result = await client.get_account_profile()
    # The result will be parsed/processed, so check key fields
    assert "evpAccountId" in result
    assert "evpAccount" in result
    assert result["evpAccount"] == "000012345678"
    assert result["evpRateGroup"] == "RES1"
    assert "evpBillingStart" in result
    assert "evpConsToDate" in result
    # Verify get was called with cookies
    assert mock_session.get.called


async def test_get_account_profile_not_authenticated(mock_session):
    """Test getting account profile without authentication."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    client._authenticated = False

    # Mock failed authentication
    with patch.object(client, "authenticate") as mock_auth:
        mock_auth.side_effect = BCHydroAuthError("Authentication failed")

        with pytest.raises(BCHydroAuthError):
            await client.get_account_profile()


async def test_get_account_profile_html_response(mock_session):
    """Test account profile when server returns HTML instead of JSON."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    client._authenticated = True
    client._cookies = {"JSESSIONID": "test_session"}

    # Mock HTML response (indicates session expired - should trigger reauth)
    mock_html_response = AsyncMock()
    mock_html_response.status = 200
    mock_html_response.headers = {"Content-Type": "text/html"}
    mock_html_response.text = AsyncMock(return_value="<html>Login page</html>")
    mock_html_response.__aenter__.return_value = mock_html_response

    # An HTML answer can also mean "no account selected", so the client asks the
    # portal for the account list first. No accounts here -> fall back to reauth.
    mock_accounts_response = AsyncMock()
    mock_accounts_response.status = 200
    mock_accounts_response.headers = {"Content-Type": "application/json"}
    mock_accounts_response.json = AsyncMock(return_value=[])
    mock_accounts_response.__aenter__.return_value = mock_accounts_response

    # Mock successful JSON response after reauth
    mock_json_response = AsyncMock()
    mock_json_response.status = 200
    mock_json_response.headers = {"Content-Type": "application/json"}
    mock_json_response.json = AsyncMock(return_value={"evpAccountId": "123", "nonWan": "false"})
    mock_json_response.__aenter__.return_value = mock_json_response

    mock_session.get.side_effect = [
        mock_html_response,
        mock_accounts_response,
        mock_json_response,
    ]

    # Mock authenticate to succeed
    with patch.object(client, 'authenticate', new_callable=AsyncMock, return_value=True):
        result = await client.get_account_profile()
        assert result is not None


async def test_get_consumption_data_success(mock_session):
    """Test getting consumption data."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock close() and authenticate() since get_consumption_data() calls them
    client.close = AsyncMock()
    client.authenticate = AsyncMock()

    client._authenticated = True
    client._cookies = {"JSESSIONID": "test_session"}
    client._csrf_token = "test_csrf"

    # Mock XML response
    xml_response = """<Data evpCurrentDateTime="2025-12-31T19:53:17-08:00" blockStatus="0" nonWan="false">
    <Series blockStatus="0">
        <Point type="SMI" quality="ACTUAL" dateTime="2025-12-25T00:00:00-08:00"
               endTime="2025-12-25T00:00:00-08:00" value="3.75" cost="0.67"
               evpDateType="HOLIDAY" evpDateDescription="Christmas Day"/>
        <Point type="SMI" quality="ACTUAL" dateTime="2025-12-26T00:00:00-08:00"
               endTime="2025-12-26T00:00:00-08:00" value="5.66" cost="0.90"/>
    </Series>
</Data>"""

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.headers = {"Content-Type": "text/xml"}
    mock_response.text = AsyncMock(return_value=xml_response)
    mock_response.__aenter__.return_value = mock_response
    mock_session.post.return_value = mock_response

    result = await client.get_consumption_data()
    assert "current_date_time" in result
    assert result["current_date_time"] == "2025-12-31T19:53:17-08:00"
    assert "daily_consumption" in result
    assert len(result["daily_consumption"]) == 2
    assert result["daily_consumption"][0]["value"] == 3.75
    assert result["daily_consumption"][0]["date_description"] == "Christmas Day"


async def test_get_consumption_data_api_error(mock_session):
    """Test API error during consumption data fetch."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock close() and authenticate() since get_consumption_data() calls them
    client.close = AsyncMock()
    client.authenticate = AsyncMock()

    client._authenticated = True
    client._cookies = {"JSESSIONID": "test_session"}

    # Mock 500 error
    mock_response = AsyncMock()
    mock_response.status = 500
    mock_response.__aenter__.return_value = mock_response
    mock_session.post.return_value = mock_response

    with pytest.raises(BCHydroApiError, match="Failed to get consumption data"):
        await client.get_consumption_data()


async def test_parse_consumption_xml():
    """Test XML parsing for consumption data."""
    client = BCHydroApiClient("test@example.com", "password")

    xml_data = """<Data evpCurrentDateTime="2025-12-31T19:53:17-08:00" blockStatus="0" nonWan="false">
    <Series blockStatus="0">
        <Point type="SMI" quality="ACTUAL" dateTime="2025-12-25T00:00:00-08:00"
               endTime="2025-12-25T00:00:00-08:00" value="3.75" cost="0.67"
               evpDateType="HOLIDAY" evpDateDescription="Christmas Day"/>
        <Point type="SMI" quality="ACTUAL" dateTime="2025-12-26T00:00:00-08:00"
               endTime="2025-12-26T00:00:00-08:00" value="5.66" cost="0.90"/>
    </Series>
    <Event dateTime="2025-12-25T00:00:00-08:00" type="HOLIDAY" description="Christmas Day"/>
</Data>"""

    result = client._parse_consumption_xml(xml_data)

    assert result["current_date_time"] == "2025-12-31T19:53:17-08:00"
    assert result["block_status"] == "0"
    assert result["non_wan"] is False
    assert len(result["daily_consumption"]) == 2
    assert result["daily_consumption"][0]["value"] == 3.75
    assert result["daily_consumption"][0]["date_description"] == "Christmas Day"
    assert len(result["events"]) == 1


async def test_get_cookies():
    """Test getting stored cookies."""
    client = BCHydroApiClient("test@example.com", "password")
    client._cookies = {"JSESSIONID": "test_session", "INGRESSCOOKIE": "test_ingress"}

    cookies = client.get_cookies()
    assert cookies == {"JSESSIONID": "test_session", "INGRESSCOOKIE": "test_ingress"}
    # Verify it's a copy, not the original
    cookies["new_key"] = "value"
    assert "new_key" not in client._cookies


async def test_close_session(mock_session):
    """Test closing the API client session when session was provided."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    mock_session.close = AsyncMock()

    # Set some state
    client._authenticated = True
    client._cookies = {"JSESSIONID": "test"}
    # Simulate that we have a session created
    client._session = mock_session

    await client.close()
    # Since we provided a session (HA's shared session), close() should NOT close it
    # but SHOULD reset authentication state to allow re-authentication
    mock_session.close.assert_not_called()
    # Auth state should be reset even when session was provided
    assert client._authenticated is False
    assert client._cookies == {}


async def test_close_session_with_cookie_jar():
    """Test closing client with own session (not provided)."""
    client = BCHydroApiClient("test@example.com", "password")
    # Simulate having created own session (not provided via parameter)
    mock_own_session = AsyncMock(spec=aiohttp.ClientSession)
    mock_own_session.close = AsyncMock()
    client._session = mock_own_session
    client._authenticated = True
    client._cookies = {"JSESSIONID": "test"}

    await client.close()
    # Since we created the session (not provided), close() should close it and reset state
    mock_own_session.close.assert_called_once()
    assert client._session is None
    assert client._authenticated is False
    assert client._cookies == {}


async def test_parse_account_profile_xml_complete():
    """Test _parse_account_profile_xml with complete data."""
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<Data evpCurrentDateTime="2025-12-31T19:53:16-08:00" blockStatus="0" nonWan="false" viewDetailedConsumption="true">
    <Rates rateGroup="RES1" rateType="RIB" bpStart="2025-12-25" bpEnd="2026-01-26"
           daysSince="7" estCons="152 kWh" estCost="$26" cons2date="28 kWh" cost2date="$5"/>
    <Series>
        <Point dateTime="2025-12-25" value="3.75" cost="0.67" quality="ACTUAL" type="SMI"/>
    </Series>
</Data>"""

    client = BCHydroApiClient("test", "pass")
    result = client._parse_account_profile_xml(xml_data)

    assert result["evpCurrentDateTime"] == "2025-12-31T19:53:16-08:00"
    assert result["nonWan"] is False
    assert result["evpRateGroup"] == "RES1"
    assert "daily_consumption" in result


async def test_parse_account_profile_xml_minimal():
    """Test _parse_account_profile_xml with minimal data."""
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<Data evpCurrentDateTime="2025-12-31" blockStatus="1" nonWan="true" viewDetailedConsumption="false"/>"""

    client = BCHydroApiClient("test", "pass")
    result = client._parse_account_profile_xml(xml_data)

    assert result["blockStatus"] == "1"
    assert result["nonWan"] is True
    assert result["viewDetailedConsumption"] is False


async def test_parse_account_profile_xml_parse_error():
    """Test _parse_account_profile_xml with invalid XML."""
    client = BCHydroApiClient("test", "pass")
    
    with pytest.raises(BCHydroApiError, match="XML parse error"):
        client._parse_account_profile_xml("invalid xml")


async def test_parse_consumption_xml_complete():
    """Test _parse_consumption_xml with all element types."""
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<Data evpCurrentDateTime="2025-12-31" blockStatus="0" nonWan="false">
    <Series>
        <Point dateTime="2025-12-25" value="3.75" cost="0.67" quality="ACTUAL" type="SMI"/>
    </Series>
    <Consumption type="RIB">
        <Point dateTime="2025-12-25" value="100" cost="15" quality="ACTUAL"/>
    </Consumption>
    <Event dateTime="2025-12-25" type="HOLIDAY" description="Christmas"/>
    <TPSDetails showBanner="true" challengeType="test" daysLeft="5" endDate="2026-01-01"/>
</Data>"""

    client = BCHydroApiClient("test", "pass")
    result = client._parse_consumption_xml(xml_data)

    assert "daily_consumption" in result
    assert "tier_consumption" in result
    assert "events" in result
    assert "tps_details" in result
    assert result["tps_details"]["show_banner"] is True


async def test_parse_consumption_xml_hourly():
    """Test _parse_consumption_xml with hourly granularity."""
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<Data currentDateTime="2025-12-31">
    <Series name="HourlyConsumption">
        <Point dateTime="2025-12-25T01:00" value="0.5"/>
    </Series>
</Data>"""

    client = BCHydroApiClient("test", "pass")
    result = client._parse_consumption_xml(xml_data, granularity="hourly")

    assert "hourly_consumption" in result


async def test_parse_bchydroparam_with_span():
    """Test _parse_bchydroparam when bchydroparam is in span element."""
    html = '<html><span id="bchydroparam">test_token_from_span</span></html>'
    soup = BeautifulSoup(html, "html.parser")
    
    client = BCHydroApiClient("test", "pass")
    result = client._parse_bchydroparam(soup)
    
    assert result == "test_token_from_span"


async def test_parse_bchydroparam_not_found():
    """Test _parse_bchydroparam when bchydroparam is not found."""
    html = '<html><body>No bchydroparam here</body></html>'
    soup = BeautifulSoup(html, "html.parser")
    
    client = BCHydroApiClient("test", "pass")
    
    with pytest.raises(BCHydroAuthError, match="Unable to find bchydroparam"):
        client._parse_bchydroparam(soup)


async def test_parse_account_profile_xml_no_rates():
    """Test parsing account profile XML without Rates element."""
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<Data evpCurrentDateTime="2025-12-31T19:53:16-08:00" blockStatus="0" nonWan="true" viewDetailedConsumption="false">
</Data>"""

    client = BCHydroApiClient("test@example.com", "password")
    result = client._parse_account_profile_xml(xml_data)

    assert result["evpCurrentDateTime"] == "2025-12-31T19:53:16-08:00"
    assert result["nonWan"] is True
    assert result["viewDetailedConsumption"] is False
    # Rates-specific fields should not be present
    assert result.get("evpRateGroup") is None


async def test_parse_account_profile_xml_no_series():
    """Test parsing account profile XML without Series element."""
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<Data evpCurrentDateTime="2025-12-31T19:53:16-08:00" blockStatus="0" nonWan="false" viewDetailedConsumption="true">
    <Rates rateGroup="RES1" rateType="RIB"/>
</Data>"""

    client = BCHydroApiClient("test@example.com", "password")
    result = client._parse_account_profile_xml(xml_data)

    assert "daily_consumption" not in result


async def test_parse_account_profile_xml_invalid():
    """Test parsing invalid account profile XML."""
    xml_data = "invalid xml"

    client = BCHydroApiClient("test@example.com", "password")

    with pytest.raises(BCHydroApiError) as exc_info:
        client._parse_account_profile_xml(xml_data)

    assert "XML parse error" in str(exc_info.value)


async def test_parse_consumption_xml_with_tier_consumption():
    """Test parsing consumption XML with tier consumption data."""
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<Data currentDateTime="2025-12-31T19:53:17-08:00" blockStatus="0" nonWan="false">
    <Consumption type="TierConsumption">
        <Point dateTime="2025-12-25" value="100" cost="10.5" quality="ACTUAL"/>
    </Consumption>
</Data>"""

    client = BCHydroApiClient("test@example.com", "password")
    result = client._parse_consumption_xml(xml_data)

    assert "tier_consumption" in result
    # tier_consumption is keyed by the tier type
    assert "TierConsumption" in result["tier_consumption"]
    assert len(result["tier_consumption"]["TierConsumption"]) == 1
    assert result["tier_consumption"]["TierConsumption"][0]["value"] == 100.0


async def test_parse_consumption_xml_with_events():
    """Test parsing consumption XML with events."""
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<Data currentDateTime="2025-12-31T19:53:17-08:00" blockStatus="0">
    <Event dateTime="2025-12-25" description="Holiday" type="SPECIAL"/>
</Data>"""

    client = BCHydroApiClient("test@example.com", "password")
    result = client._parse_consumption_xml(xml_data)

    assert "events" in result
    assert len(result["events"]) == 1
    assert result["events"][0]["description"] == "Holiday"


async def test_parse_consumption_xml_with_tps_details():
    """Test parsing consumption XML with TPS details."""
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<Data currentDateTime="2025-12-31T19:53:17-08:00" blockStatus="0">
    <TPSDetails showBanner="true" challengeType="test" daysLeft="5" endDate="2026-01-31"/>
</Data>"""

    client = BCHydroApiClient("test@example.com", "password")
    result = client._parse_consumption_xml(xml_data)

    assert "tps_details" in result
    assert result["tps_details"]["show_banner"] is True
    assert result["tps_details"]["challenge_type"] == "test"
    assert result["tps_details"]["days_left"] == "5"


async def test_parse_consumption_xml_invalid():
    """Test parsing invalid consumption XML."""
    xml_data = "invalid xml"

    client = BCHydroApiClient("test@example.com", "password")

    with pytest.raises(BCHydroApiError) as exc_info:
        client._parse_consumption_xml(xml_data)

    assert "XML parse error" in str(exc_info.value)


async def test_parse_consumption_xml_with_missing_cost():
    """Test parsing consumption XML with points that have no cost."""
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<Data currentDateTime="2025-12-31T19:53:17-08:00" blockStatus="0">
    <Series name="DailyConsumption">
        <Point dateTime="2025-12-25T00:00:00-08:00" value="3.75" quality="ACTUAL"/>
    </Series>
</Data>"""

    client = BCHydroApiClient("test@example.com", "password")
    result = client._parse_consumption_xml(xml_data)

    assert len(result["daily_consumption"]) == 1
    # When cost is missing, it defaults to None in the point dict
    assert "cost" in result["daily_consumption"][0]


async def test_get_account_profile_json_parse_error(mock_session):
    """Test get_account_profile with JSON parse error."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock close() and authenticate() since get_account_profile() calls them first
    client.close = AsyncMock()
    client.authenticate = AsyncMock()

    client._authenticated = True
    client._cookies = {"JSESSIONID": "test"}

    # Mock response with invalid JSON
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.headers = {"Content-Type": "application/json"}
    mock_resp.json = AsyncMock(side_effect=ValueError("Invalid JSON"))
    mock_resp.text = AsyncMock(return_value="invalid json")

    mock_session.get.return_value.__aenter__.return_value = mock_resp

    with pytest.raises(BCHydroApiError) as exc_info:
        await client.get_account_profile()

    assert "parse" in str(exc_info.value).lower()


async def test_parse_account_profile_json_with_date_calculation():
    """Test _parse_account_profile_json with date calculations."""
    json_data = {
        "evpSlid": "123",
        "evpAccount": "456",
        "evpBillingStart": "Dec 25, 2025",
        "evpBillingEnd": "Jan 26, 2026",
        "evpConsToDate": "28 kWh",
        "blockStatus": "0",
        "nonWan": "false",
        "viewDetailedConsumption": "true",
    }

    client = BCHydroApiClient("test@example.com", "password")
    result = client._parse_account_profile_json(json_data)

    assert result["evpSlid"] == "123"
    assert result["evpAccount"] == "456"
    assert result["evpBillingStart"] == "Dec 25, 2025"
    assert result["evpBillingEnd"] == "Jan 26, 2026"
    # Date calculation should work and set days in billing period
    assert result["evpDaysInBillingPeriod"] == 32


async def test_parse_account_profile_json_invalid_dates():
    """Test _parse_account_profile_json with invalid date formats."""
    json_data = {
        "evpBillingStart": "invalid date",
        "evpBillingEnd": "also invalid",
        "nonWan": "true",
    }

    client = BCHydroApiClient("test@example.com", "password")
    result = client._parse_account_profile_json(json_data)

    # Should not crash, just keep default of 0 days
    assert result["evpDaysInBillingPeriod"] == 0


async def test_parse_account_profile_json_error():
    """Test _parse_account_profile_json with exception."""
    # Pass None to trigger an error
    client = BCHydroApiClient("test@example.com", "password")

    with pytest.raises(BCHydroApiError) as exc_info:
        client._parse_account_profile_json(None)

    assert "JSON parse error" in str(exc_info.value)


async def test_authenticate_non_200_status(mock_session):
    """Test authenticate with non-200 status code.

    A bad HTTP status is transient (maintenance, WAF, proxy), so it must be
    retryable rather than a credential failure.
    """
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    #Mock response with 500 status
    mock_resp = AsyncMock()
    mock_resp.status = 500

    mock_session.post.return_value.__aenter__.return_value = mock_resp

    with pytest.raises(BCHydroConnectionError) as exc_info:
        await client.authenticate()

    assert "status 500" in str(exc_info.value)
    assert not isinstance(exc_info.value, BCHydroAuthError)


async def test_authenticate_bchydroparam_extraction_fails(mock_session):
    """Test authenticate when bchydroparam extraction fails but continues."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock successful login but HTML without bchydroparam
    mock_login_resp = AsyncMock()
    mock_login_resp.status = 200
    mock_login_resp.url = "https://app.bchydro.com/accounts/accountsOverview.html"
    mock_login_resp.text = AsyncMock(return_value="<html><body>No bchydroparam</body></html>")

    # Mock evportlet response
    mock_evp_resp = AsyncMock()
    mock_evp_resp.status = 200

    mock_session.post.return_value.__aenter__.return_value = mock_login_resp
    mock_session.get.return_value.__aenter__.return_value = mock_evp_resp

    # Mock cookie_jar with cookies
    mock_session.cookie_jar = MagicMock()
    mock_session.cookie_jar.__iter__ = lambda self: iter([
        MagicMock(key="JSESSIONID", value="test_session"),
    ])

    result = await client.authenticate()
    assert result is True


async def test_authenticate_evportlet_fails(mock_session):
    """Test authenticate succeeds (evportlet session no longer established during auth)."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock successful login
    mock_login_resp = AsyncMock()
    mock_login_resp.status = 200
    mock_login_resp.url = "https://app.bchydro.com/accounts/accountsOverview.html"
    mock_login_resp.text = AsyncMock(return_value='<input name="bchydroparam" value="token"/>')

    mock_session.post.return_value.__aenter__.return_value = mock_login_resp

    # Mock cookie_jar with cookies
    mock_session.cookie_jar = MagicMock()
    mock_session.cookie_jar.__iter__ = lambda self: iter([
        MagicMock(key="JSESSIONID", value="test_session"),
    ])

    result = await client.authenticate()
    assert result is True


async def test_authenticate_network_error(mock_session):
    """Test authenticate with network error."""
    import aiohttp

    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock network error
    mock_session.post.return_value.__aenter__.side_effect = aiohttp.ClientError("Network error")

    with pytest.raises(BCHydroApiError) as exc_info:
        await client.authenticate()

    assert "Connection error" in str(exc_info.value) or "Network error" in str(exc_info.value)


async def test_authenticate_with_cookies_non_200_non_401(mock_session):
    """Test authenticate_with_cookies with non-200/401 status."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    client._cookies = {"JSESSIONID": "test_session", "bchydroparam": "test_token"}

    # Mock response with 500 status
    mock_resp = AsyncMock()
    mock_resp.status = 500

    mock_session.get.return_value.__aenter__.return_value = mock_resp

    cookies = {"JSESSIONID": "test_session", "bchydroparam": "test_token"}

    with pytest.raises(BCHydroAuthError) as exc_info:
        await client.authenticate_with_cookies(cookies)

    assert "500" in str(exc_info.value)


async def test_authenticate_with_cookies_network_error(mock_session):
    """Test authenticate_with_cookies with network error."""
    import aiohttp

    client = BCHydroApiClient("test@example.com", "password", session=mock_session)
    cookies = {"JSESSIONID": "test_session", "bchydroparam": "test_token"}

    # Mock network error
    mock_session.get.return_value.__aenter__.side_effect = aiohttp.ClientError("Network error")

    # Network errors propagate as ClientError
    with pytest.raises(aiohttp.ClientError):
        await client.authenticate_with_cookies(cookies)


async def test_get_account_profile_non_200_status(mock_session):
    """Test get_account_profile with non-200 status."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock close() and authenticate() since get_account_profile() calls them first
    client.close = AsyncMock()
    client.authenticate = AsyncMock()

    client._authenticated = True
    client._cookies = {"JSESSIONID": "test"}

    # Mock response with 500 status
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="Error")

    mock_session.get.return_value.__aenter__.return_value = mock_resp

    with pytest.raises(BCHydroApiError) as exc_info:
        await client.get_account_profile()

    assert "500" in str(exc_info.value)


async def test_get_consumption_data_non_200_status(mock_session):
    """Test get_consumption_data with non-200 status."""
    from datetime import datetime, timezone

    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock close() and authenticate() since get_consumption_data() calls them first
    client.close = AsyncMock()
    client.authenticate = AsyncMock()

    client._authenticated = True
    client._cookies = {"JSESSIONID": "test", "bchydroparam": "token"}

    # Mock response with 500 status
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="Error")

    mock_session.post.return_value.__aenter__.return_value = mock_resp

    start_date = datetime(2025, 12, 25, tzinfo=timezone.utc)
    end_date = datetime(2026, 1, 26, tzinfo=timezone.utc)

    with pytest.raises(BCHydroApiError) as exc_info:
        await client.get_consumption_data(start_date, end_date)

    assert "500" in str(exc_info.value)


async def test_authenticate_too_many_total_attempts(mock_session):
    """Test authenticate raises error after too many attempts in global window."""
    import custom_components.bchydro.api as api_module

    # Save original global state
    orig_attempts = api_module._global_auth_attempts_in_window
    orig_window_start = api_module._global_window_start

    try:
        # Set global attempts over the limit (5 per 60-second window)
        api_module._global_auth_attempts_in_window = 6
        api_module._global_window_start = __import__('time').time()  # Recent window

        client = BCHydroApiClient("test@example.com", "password", session=mock_session)

        # Local throttling is self-inflicted and retryable - it must not be
        # mistaken for BC Hydro rejecting the credentials.
        with pytest.raises(BCHydroConnectionError) as exc_info:
            await client.authenticate()

        assert "Too many authentication attempts" in str(exc_info.value)
        assert not isinstance(exc_info.value, BCHydroAuthError)
    finally:
        # Restore global state
        api_module._global_auth_attempts_in_window = orig_attempts
        api_module._global_window_start = orig_window_start


async def test_authenticate_throttling():
    """Test authenticate throttles rapid auth attempts using global rate limiting."""
    import time
    from unittest.mock import patch, AsyncMock, MagicMock
    import custom_components.bchydro.api as api_module

    # Save original global state
    orig_last_auth = api_module._global_last_auth_attempt
    orig_attempts = api_module._global_auth_attempts_in_window
    orig_window_start = api_module._global_window_start

    try:
        # Reset global state for this test
        api_module._global_auth_attempts_in_window = 0
        api_module._global_window_start = time.time()
        # Set last auth attempt to 0.5 seconds ago (less than 2 second minimum)
        api_module._global_last_auth_attempt = time.time() - 0.5

        mock_session = MagicMock()
        client = BCHydroApiClient("test@example.com", "password", session=mock_session)

        # Mock successful auth
        mock_login_resp = AsyncMock()
        mock_login_resp.status = 200
        mock_login_resp.url = "https://app.bchydro.com/accounts/accountsOverview.html"
        mock_login_resp.text = AsyncMock(return_value='<input name="bchydroparam" value="token"/>')
        mock_session.post.return_value.__aenter__.return_value = mock_login_resp
        mock_session.cookie_jar = MagicMock()
        mock_session.cookie_jar.__iter__ = lambda self: iter([MagicMock(key="JSESSIONID", value="test")])

        # Mock asyncio.sleep to capture wait time
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await client.authenticate()
            # Should have slept for approximately 1.5 seconds (2 - 0.5)
            mock_sleep.assert_called_once()
            wait_time = mock_sleep.call_args[0][0]
            # Wait time should be between 1 and 2 seconds
            assert 1 <= wait_time <= 2
    finally:
        # Restore global state
        api_module._global_last_auth_attempt = orig_last_auth
        api_module._global_auth_attempts_in_window = orig_attempts
        api_module._global_window_start = orig_window_start


"""Tests to achieve 100% coverage for api.py."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from custom_components.bchydro.api import BCHydroApiClient, BCHydroAuthError, BCHydroApiError


@pytest.fixture
def mock_session():
    """Create a mock aiohttp session."""
    return AsyncMock()


async def test_authenticate_unexpected_error(mock_session):
    """Test authenticate with unexpected exception - covers lines 191-193."""
    client = BCHydroApiClient("test@example.com", "password", session=mock_session)

    # Mock post to raise an unexpected exception
    mock_session.post.side_effect = RuntimeError("Unexpected error")

    with pytest.raises(BCHydroApiError) as exc_info:
        await client.authenticate()

    # An unexpected failure is wrapped in a retryable error, not an auth error:
    # it is no evidence that the credentials are wrong.
    assert "Authentication error" in str(exc_info.value)
    assert not isinstance(exc_info.value, BCHydroAuthError)


async def test_get_csrf_token_from_response_cookies():
    """Test _get_csrf_token extracting token from response cookies - covers lines 271-277."""
    client = BCHydroApiClient("test@example.com", "password")
    client._cookies = {}  # No bchydroparam cookie

    mock_session = AsyncMock()

    # Mock response with bchydroparam in cookies
    mock_resp = AsyncMock()
    mock_resp.status = 200

    # Create a mock cookie
    mock_cookie = MagicMock()
    mock_cookie.key = "bchydroparam"
    mock_cookie.value = "token_from_endpoint"

    # Mock cookies.values() to return a list
    mock_cookies_obj = MagicMock()
    mock_cookies_obj.values.return_value = [mock_cookie]

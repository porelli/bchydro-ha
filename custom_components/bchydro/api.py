"""API client for BC Hydro."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

import aiohttp
from aiohttp import ClientSession, ClientTimeout
from bs4 import BeautifulSoup

from .const import (
    ACCOUNT_LIST_URL,
    ACCOUNT_PROFILE_URL,
    ACCOUNT_SELECT_URL,
    CONSUMPTION_DATA_URL,
    GLOBAL_DATA_URL,
    LOGIN_GOTO_URL,
    LOGIN_URL,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = ClientTimeout(total=30)

# Global rate limiting state (persists across all API client instances)
# This prevents runaway auth attempts during error loops/reloads
_global_last_auth_attempt: float = 0.0
_global_auth_attempts_in_window: int = 0
_global_window_start: float = 0.0
_GLOBAL_MIN_AUTH_INTERVAL = 2.0  # Minimum seconds between auth attempts
_GLOBAL_MAX_ATTEMPTS_PER_WINDOW = 6  # Max attempts per 60-second window
_GLOBAL_WINDOW_SECONDS = 60.0


# Phrases that positively identify a rejected login. Matching a bare "invalid"
# against the whole page is not safe: BC Hydro's login page ships client-side
# validation scripts, so any unexpected page (maintenance notice, redirect glitch)
# would be misread as wrong credentials and permanently break the integration.
CREDENTIAL_REJECTED_MARKERS = (
    "invalid username or password",
    "invalid email or password",
    "incorrect username or password",
    "incorrect email or password",
    "username or password is incorrect",
    "email or password is incorrect",
    "invalid login credentials",
    "invalid user name or password",
    "authentication failed",
    "wrong password",
    "password is incorrect",
    "account has been locked",
    "account is locked",
)


class BCHydroApiError(Exception):
    """Exception raised for BC Hydro API errors."""


class BCHydroAuthError(BCHydroApiError):
    """Exception raised when BC Hydro rejects the credentials.

    Raise this only when the failure is attributable to the account itself.
    Home Assistant treats it as terminal: it stops polling and waits for the user
    to re-enter their password. Transient problems must use
    BCHydroConnectionError instead.
    """


class BCHydroConnectionError(BCHydroApiError):
    """Exception raised for transient problems reaching BC Hydro.

    Network errors, timeouts, HTTP failures and local rate limiting all belong
    here: they say nothing about the credentials and must be retried.
    """


class BCHydroApiClient:
    """BC Hydro API client."""

    def __init__(
        self,
        username: str,
        password: str,
        session: ClientSession | None = None,
        account_id: str | None = None,
    ) -> None:
        """Initialize the API client."""
        self._username = username
        self._password = password
        self._account_id = account_id
        self._provided_session = session
        self._session: ClientSession | None = None
        self._cookie_jar = aiohttp.CookieJar()
        self._cookies: dict[str, str] = {}
        self._csrf_token: str | None = None
        self._authenticated = False
        self._client_id = str(uuid.uuid4())[:8]
        # Instance-level tracking (for debugging only; global rate limiting handles the actual limits)
        self._last_auth_attempt = 0.0
        self._auth_attempt_count = 0

    def _deduplicate_cookies(self) -> None:
        """Check for duplicate cookies (path-specific cookies are normal)."""
        if not self._session or not self._session.cookie_jar:
            return

        seen = set()
        has_duplicates = False
        for cookie in self._session.cookie_jar:
            if cookie.key in seen:
                has_duplicates = True
                break
            seen.add(cookie.key)

        if has_duplicates:
            cookie_counts: dict[str, int] = {}
            for cookie in self._session.cookie_jar:
                cookie_counts[cookie.key] = cookie_counts.get(cookie.key, 0) + 1
            duplicates = {k: v for k, v in cookie_counts.items() if v > 1}
            _LOGGER.debug("Path-specific cookies detected: %s", duplicates)

    def _parse_bchydroparam(self, soup: BeautifulSoup) -> str:
        """Extract bchydroparam (CSRF token) from page HTML."""
        span_element = soup.find(id="bchydroparam")
        if span_element and hasattr(span_element, "text"):
            return str(span_element.text)
        input_element = soup.find("input", {"name": "bchydroparam"})
        if input_element:
            value = input_element.get("value")
            if value:
                return str(value)
        raise BCHydroAuthError("Unable to find bchydroparam; likely failed to login")

    async def authenticate(self, recaptcha_token: str | None = None) -> bool:
        """Authenticate with BC Hydro using username and password.

        Returns:
            True if authentication was successful.

        Raises:
            BCHydroAuthError: If authentication fails.
        """
        global _global_last_auth_attempt, _global_auth_attempts_in_window, _global_window_start

        current_time = time.time()

        # Global rate limiting (across all instances)
        # Reset window if it's been more than 60 seconds
        if current_time - _global_window_start > _GLOBAL_WINDOW_SECONDS:
            _global_window_start = current_time
            _global_auth_attempts_in_window = 0

        # Check global rate limit
        if _global_auth_attempts_in_window >= _GLOBAL_MAX_ATTEMPTS_PER_WINDOW:
            wait_time = _GLOBAL_WINDOW_SECONDS - (current_time - _global_window_start)
            if wait_time > 0:
                _LOGGER.warning(
                    "Global rate limit reached (%d attempts in %.0fs). "
                    "Waiting %.1fs before next attempt.",
                    _global_auth_attempts_in_window,
                    _GLOBAL_WINDOW_SECONDS,
                    wait_time,
                )
                raise BCHydroConnectionError(
                    f"Too many authentication attempts. Please wait {int(wait_time)} seconds."
                )

        # Enforce minimum interval between attempts (global)
        time_since_last = current_time - _global_last_auth_attempt
        if time_since_last < _GLOBAL_MIN_AUTH_INTERVAL:
            wait_time = _GLOBAL_MIN_AUTH_INTERVAL - time_since_last
            _LOGGER.debug("Throttling auth attempt, sleeping %.1fs", wait_time)
            await asyncio.sleep(wait_time)

        # Update global state
        _global_last_auth_attempt = time.time()
        _global_auth_attempts_in_window += 1

        # Instance-level tracking (for logging/debugging only)
        self._last_auth_attempt = time.time()
        self._auth_attempt_count += 1

        _LOGGER.debug("Authenticating with BC Hydro (attempt %d)", self._auth_attempt_count)

        try:
            login_headers = {
                "User-Agent": "https://github.com/porelli/bchydro-ha",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }

            login_data = {
                "realm": "bch-ps",
                "email": self._username,
                "password": self._password,
                "gotoUrl": LOGIN_GOTO_URL,
            }

            if self._provided_session:
                session = self._provided_session
            else:
                if not self._session:
                    self._session = aiohttp.ClientSession(
                        cookie_jar=self._cookie_jar,
                        timeout=DEFAULT_TIMEOUT,
                    )
                session = self._session

            async with session.post(
                LOGIN_URL,
                data=login_data,
                headers=login_headers,
                allow_redirects=True,
            ) as response:
                if response.status != 200:
                    # A bad status says nothing about the credentials (5xx during
                    # maintenance, 403 from a WAF, ...) - keep it retryable.
                    raise BCHydroConnectionError(
                        f"BC Hydro login returned status {response.status}"
                    )

                page_html = await response.text()
                response_url_str = str(response.url)

                if "UI/Login" in response_url_str or (
                    "login.html" in response_url_str and "bchydroparam" not in page_html
                ):
                    # Check for specific error indicators in the page
                    page_lower = page_html.lower()
                    has_captcha = "captcha" in page_lower or "recaptcha" in page_lower
                    has_invalid_creds = any(
                        marker in page_lower for marker in CREDENTIAL_REJECTED_MARKERS
                    )

                    if has_invalid_creds:
                        # Clear indication of wrong credentials
                        raise BCHydroAuthError(
                            "Invalid username or password. Please check your credentials."
                        )
                    elif has_captcha:
                        # CAPTCHA shown but no clear credential error - could be either
                        raise BCHydroAuthError(
                            "BC Hydro login page showed CAPTCHA. This may indicate wrong "
                            "credentials or too many login attempts. Please verify your "
                            "password and try again, or login manually at https://app.bchydro.com"
                        )
                    else:
                        raise BCHydroAuthError(
                            "Authentication failed. Please check your credentials."
                        )

                try:
                    soup = BeautifulSoup(page_html, "html.parser")
                    self._csrf_token = self._parse_bchydroparam(soup)
                except BCHydroAuthError as e:
                    _LOGGER.warning("Could not extract bchydroparam: %s", e)

            for cookie in session.cookie_jar:
                self._cookies[cookie.key] = cookie.value

            if self._csrf_token:
                from http.cookies import SimpleCookie
                from yarl import URL
                simple_cookie = SimpleCookie()
                simple_cookie["bchydroparam"] = self._csrf_token
                self._cookie_jar.update_cookies(simple_cookie, URL("https://app.bchydro.com"))
                self._cookies["bchydroparam"] = self._csrf_token

            if not self._cookies:
                raise BCHydroAuthError("Authentication failed. Please check your credentials.")

            self._authenticated = True
            self._auth_attempt_count = 0
            _LOGGER.info("Successfully authenticated with BC Hydro")

            if self._account_id:
                # Every fresh session starts on the login's default account, so
                # re-select the configured one before any data is fetched.
                await self._select_preferred_account()

            return True

        except aiohttp.ClientError as err:
            raise BCHydroConnectionError(f"Network error: {err}") from err
        except (TimeoutError, OSError) as err:
            raise BCHydroConnectionError(f"Network error: timeout ({err})") from err
        except BCHydroApiError:
            raise
        except Exception as err:
            # Unexpected failure (parsing, library change, ...). It is not evidence
            # that the credentials are wrong, so keep it retryable.
            raise BCHydroApiError(f"Authentication error: {err}") from err

    async def authenticate_with_cookies(self, cookies: dict[str, str]) -> bool:
        """Authenticate using existing cookies.

        Returns:
            True if authentication was successful.

        Raises:
            BCHydroAuthError: If authentication fails.
        """
        self._cookies = cookies.copy()

        from http.cookies import SimpleCookie
        from yarl import URL

        self._cookie_jar = aiohttp.CookieJar()
        base_url = URL("https://app.bchydro.com")
        simple_cookie = SimpleCookie()
        for key, value in cookies.items():
            simple_cookie[key] = value
        self._cookie_jar.update_cookies(simple_cookie, base_url)

        api_headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
            "User-Agent": "https://github.com/porelli/bchydro-ha",
        }

        if self._provided_session:
            session = self._provided_session
        else:
            if self._session:
                await self._session.close()
            self._session = aiohttp.ClientSession(
                cookie_jar=self._cookie_jar,
                timeout=DEFAULT_TIMEOUT,
            )
            session = self._session

        async with session.get(ACCOUNT_PROFILE_URL, headers=api_headers) as response:
            if response.status == 401:
                raise BCHydroAuthError("Invalid or expired cookies")
            if response.status != 200:
                raise BCHydroAuthError(f"Authentication verification failed: {response.status}")

        async with session.get(GLOBAL_DATA_URL, headers=api_headers) as response:
            if response.status == 200:
                for cookie in response.cookies.values():
                    if cookie.key == "bchydroparam":
                        self._csrf_token = cookie.value
                        self._cookies["bchydroparam"] = cookie.value
                        break

        for cookie in session.cookie_jar:
            self._cookies[cookie.key] = cookie.value

        if self._provided_session:
            new_jar = aiohttp.CookieJar()
            for cookie in session.cookie_jar:
                cookie_url = URL.build(
                    scheme="https",
                    host=cookie["domain"].lstrip(".") if cookie.get("domain") else "app.bchydro.com"
                )
                new_jar.update_cookies({cookie.key: cookie}, cookie_url)
            self._cookie_jar = new_jar

        if not self._csrf_token and "bchydroparam" in self._cookies:
            self._csrf_token = self._cookies["bchydroparam"]

        self._authenticated = True
        _LOGGER.info("Successfully authenticated with provided cookies")
        return True

    def get_cookies(self) -> dict[str, str]:
        """Get current cookies for storage."""
        return self._cookies.copy()

    async def get_accounts(self) -> list[dict[str, Any]]:
        """List the accounts this login has access to.

        Returns:
            One dict per account with at least ``accountId``, ``accountNumber``,
            ``accountDesc`` and ``selected``. Empty if the portal did not answer
            with the expected JSON.
        """
        session = self._provided_session if self._provided_session else self._session
        if not session:
            raise BCHydroApiError("Session not initialized - please authenticate first")

        headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "*/*"}

        try:
            async with session.get(ACCOUNT_LIST_URL, headers=headers) as response:
                if response.status != 200:
                    raise BCHydroConnectionError(
                        f"Failed to list accounts: {response.status}"
                    )
                try:
                    accounts = await response.json(content_type=None)
                except Exception as err:  # noqa: BLE001 - portal may answer with HTML
                    _LOGGER.debug("Could not parse the account list: %s", err)
                    return []
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise BCHydroConnectionError(f"Network error listing accounts: {err}") from err

        if not isinstance(accounts, list):
            return []
        return [account for account in accounts if isinstance(account, dict)]

    async def select_account(self, account_id: str) -> None:
        """Make account_id the active account for this session."""
        session = self._provided_session if self._provided_session else self._session
        if not session:
            raise BCHydroApiError("Session not initialized - please authenticate first")

        headers = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}

        try:
            async with session.get(
                ACCOUNT_SELECT_URL, params={"aid": account_id}, headers=headers
            ) as response:
                if response.status != 200:
                    raise BCHydroConnectionError(
                        f"Failed to select account: {response.status}"
                    )
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise BCHydroConnectionError(
                f"Network error selecting account: {err}"
            ) from err
        _LOGGER.debug("Selected BC Hydro account %s", account_id)

    async def _select_preferred_account(self) -> bool:
        """Activate the configured account, or the only sensible candidate.

        A login with more than one account (a previous address, a second service,
        shared access) starts out with no account selected, and the data endpoints
        then answer with HTML instead of JSON.

        Returns:
            True if an account was selected.
        """
        accounts = await self.get_accounts()
        if not accounts:
            return False

        if len(accounts) > 1:
            _LOGGER.debug(
                "BC Hydro login has %d accounts: %s",
                len(accounts),
                ", ".join(
                    f"{account.get('accountId')} ({account.get('accountDesc')})"
                    for account in accounts
                ),
            )

        chosen: dict[str, Any] | None = None
        if self._account_id:
            chosen = next(
                (a for a in accounts if a.get("accountId") == self._account_id), None
            )
            if chosen is None:
                raise BCHydroApiError(
                    f"Configured account {self._account_id} is not available for "
                    f"this login. Please reconfigure the integration."
                )
        if chosen is None:
            chosen = next((a for a in accounts if a.get("selected")), accounts[0])
            if len(accounts) > 1:
                _LOGGER.info(
                    "No BC Hydro account configured, following account %s. "
                    "Re-add the integration to follow a different one.",
                    chosen.get("accountNumber"),
                )

        account_id = chosen.get("accountId")
        if not account_id:
            return False

        await self.select_account(str(account_id))
        return True

    async def get_account_profile(self) -> dict[str, Any]:
        """Get account profile data from global-data endpoint."""
        if not self._authenticated:
            _LOGGER.debug("get_account_profile: authenticating (not yet authenticated)")
            await self.authenticate()

        try:
            return await self._fetch_account_profile(allow_retry=True)
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise BCHydroConnectionError(
                f"Network error fetching account profile: {err}"
            ) from err

    async def _fetch_account_profile(
        self, allow_retry: bool = True, allow_account_select: bool = True
    ) -> dict[str, Any]:
        """Fetch account profile, recovering from a missing account selection.

        Args:
            allow_retry: Re-authenticate once if the session looks expired.
            allow_account_select: Try activating an account once. A login with
                several accounts has none selected, and this endpoint then answers
                with the portal HTML instead of JSON.
        """
        session = self._provided_session if self._provided_session else self._session
        if not session:
            raise BCHydroApiError("Session not initialized - please authenticate first")

        api_headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
        }

        async with session.get(GLOBAL_DATA_URL, headers=api_headers) as response:
            if response.status != 200:
                raise BCHydroApiError(f"Failed to get account profile: {response.status}")

            content_type = response.headers.get("Content-Type", "")

            # Check if we got redirected to login page (HTML instead of JSON)
            # This happens when the session has expired on BC Hydro's side, or
            # when no account is selected yet (logins with several accounts)
            if "text/html" in content_type or "login" in str(response.url).lower():
                if allow_account_select and await self._select_account_for_recovery():
                    return await self._fetch_account_profile(
                        allow_retry=allow_retry, allow_account_select=False
                    )
                if allow_retry:
                    _LOGGER.debug(
                        "Session expired (got HTML login page), re-authenticating..."
                    )
                    # Force full re-authentication
                    await self.close()
                    await self.authenticate()
                    return await self._fetch_account_profile(allow_retry=False)
                else:
                    raise BCHydroApiError(
                        f"Failed to parse account profile: {response.status}, "
                        f"got HTML response after re-authentication"
                    )

            try:
                json_data = await response.json()
                return self._parse_account_profile_json(json_data)
            except Exception as err:
                # If JSON parsing fails, might be HTML login page
                if allow_account_select and await self._select_account_for_recovery():
                    return await self._fetch_account_profile(
                        allow_retry=allow_retry, allow_account_select=False
                    )
                if allow_retry:
                    _LOGGER.debug(
                        "JSON parse failed (possible session expiration), re-authenticating: %s",
                        err,
                    )
                    await self.close()
                    await self.authenticate()
                    return await self._fetch_account_profile(allow_retry=False)
                raise BCHydroApiError(f"Failed to parse account profile: {err}") from err

    async def _select_account_for_recovery(self) -> bool:
        """Try to activate an account after the portal answered with HTML."""
        try:
            return await self._select_preferred_account()
        except BCHydroConnectionError as err:
            _LOGGER.debug("Could not select a BC Hydro account: %s", err)
            return False

    async def get_consumption_data(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        date_range: str = "currentBill",
        granularity: str = "daily",
    ) -> dict[str, Any]:
        """Get consumption data."""
        # BC Hydro consumption endpoint requires a fresh session for each request
        _LOGGER.debug("get_consumption_data: re-authenticating (BC Hydro requires fresh session)")
        await self.close()
        await self.authenticate()

        session = self._provided_session if self._provided_session else self._session
        if not session:
            raise BCHydroApiError("Session not initialized - please authenticate first")

        post_data = {
            "DateRange": date_range,
            "Granularity": granularity,
            "IncludeMeters": "",
            "Overlays": "none",
            "UserClick": "",
            "IsMobile": "false",
        }

        if start_date:
            tz_str = start_date.strftime("%z")
            tz_formatted = f"{tz_str[:-2]}:{tz_str[-2:]}" if tz_str else ""
            post_data["StartDateTime"] = start_date.strftime("%Y-%m-%dT%H:%M:%S") + tz_formatted
        if end_date:
            tz_str = end_date.strftime("%z")
            tz_formatted = f"{tz_str[:-2]}:{tz_str[-2:]}" if tz_str else ""
            post_data["EndDateTime"] = end_date.strftime("%Y-%m-%dT%H:%M:%S") + tz_formatted

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "*/*",
        }

        if self._csrf_token:
            headers["X-CSRF-Token"] = self._csrf_token
            headers["bchydroparam"] = self._csrf_token

        try:
            async with session.post(
                CONSUMPTION_DATA_URL,
                data=post_data,
                headers=headers,
            ) as response:
                if response.status != 200:
                    raise BCHydroApiError(
                        f"Failed to get consumption data: {response.status}"
                    )
                xml_text = await response.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise BCHydroConnectionError(
                f"Network error fetching consumption data: {err}"
            ) from err

        self._deduplicate_cookies()
        return self._parse_consumption_xml(xml_text, granularity)

    def _parse_consumption_xml(self, xml_text: str, granularity: str = "daily") -> dict[str, Any]:
        """Parse consumption XML response."""
        try:
            root = ET.fromstring(xml_text)

            data = {
                "current_date_time": root.get("evpCurrentDateTime"),
                "block_status": root.get("blockStatus"),
                "non_wan": root.get("nonWan") == "true",
            }

            series = root.find("Series")
            if series is not None:
                consumption_data = []
                for point in series.findall("Point"):
                    cost_val = point.get("cost")
                    consumption_data.append({
                        "date_time": point.get("dateTime"),
                        "end_time": point.get("endTime"),
                        "value": float(point.get("value", 0)),
                        "cost": float(cost_val) if cost_val else 0.0,
                        "quality": point.get("quality"),
                        "type": point.get("type"),
                        "date_type": point.get("evpDateType"),
                        "date_description": point.get("evpDateDescription"),
                    })
                data[f"{granularity}_consumption"] = consumption_data

            tier_data = {}
            for consumption_elem in root.findall("Consumption"):
                tier_type = consumption_elem.get("type")
                tier_points = []
                for point in consumption_elem.findall("Point"):
                    tier_points.append({
                        "date_time": point.get("dateTime"),
                        "value": float(point.get("value", 0)),
                        "quality": point.get("quality"),
                    })
                tier_data[tier_type] = tier_points
            if tier_data:
                data["tier_consumption"] = tier_data

            events = []
            for event in root.findall("Event"):
                events.append({
                    "date_time": event.get("dateTime"),
                    "type": event.get("type"),
                    "description": event.get("description"),
                })
            if events:
                data["events"] = events

            tps_details = root.find("TPSDetails")
            if tps_details is not None:
                data["tps_details"] = {
                    "show_banner": tps_details.get("showBanner") == "true",
                    "challenge_type": tps_details.get("challengeType"),
                    "days_left": tps_details.get("daysLeft"),
                    "end_date": tps_details.get("endDate"),
                }

            return data

        except ET.ParseError as err:
            raise BCHydroApiError(f"XML parse error: {err}") from err

    def _parse_account_profile_json(self, json_data: dict[str, Any]) -> dict[str, Any]:
        """Parse account profile JSON response."""
        try:
            data = {
                "evpSlid": json_data.get("evpSlid"),
                "evpAccount": json_data.get("evpAccount"),
                "evpAccountId": json_data.get("evpAccountId"),
                "evpProfileId": json_data.get("evpProfileId"),
                "evpRateGroup": json_data.get("evpRateGroup"),
                "evpRateCategory": json_data.get("evpRateCategory"),
                "ratePlanType": json_data.get("evpRateGroup"),
                "ratePlanName": json_data.get("evpRateGroup"),
                "evpBillingStart": json_data.get("evpBillingStart"),
                "evpBillingEnd": json_data.get("evpBillingEnd"),
                "evpCurrentDateTime": json_data.get("evpCurrentDateTime"),
                "evpConsToDate": json_data.get("evpConsToDate"),
                "evpCostToDate": json_data.get("evpCostToDate"),
                "yesterdayPercentage": json_data.get("yesterdayPercentage"),
                "evpEstConsCurPeriod": json_data.get("evpEstConsCurPeriod"),
                "evpEstCostCurPeriod": json_data.get("evpEstCostCurPeriod"),
                "evpDaysInBillingPeriod": 0,
                "blockStatus": json_data.get("blockStatus"),
                "nonWan": json_data.get("nonWan") == "true",
                "viewDetailedConsumption": json_data.get("viewDetailedConsumption") == "true",
            }

            if data["evpBillingStart"] and data["evpBillingEnd"]:
                try:
                    try:
                        start = datetime.fromisoformat(data["evpBillingStart"])
                        end = datetime.fromisoformat(data["evpBillingEnd"])
                    except (ValueError, TypeError):
                        start = datetime.strptime(data["evpBillingStart"], "%b %d, %Y")
                        end = datetime.strptime(data["evpBillingEnd"], "%b %d, %Y")
                    data["evpDaysInBillingPeriod"] = (end - start).days
                except Exception:
                    pass

            return data

        except Exception as err:
            raise BCHydroApiError(f"JSON parse error: {err}") from err

    def _parse_account_profile_xml(self, xml_text: str) -> dict[str, Any]:
        """Parse account profile XML response."""
        try:
            root = ET.fromstring(xml_text)

            data = {
                "evpCurrentDateTime": root.get("evpCurrentDateTime"),
                "blockStatus": root.get("blockStatus"),
                "nonWan": root.get("nonWan") == "true",
                "viewDetailedConsumption": root.get("viewDetailedConsumption") == "true",
            }

            rates = root.find("Rates")
            if rates is not None:
                data.update({
                    "evpRateGroup": rates.get("rateGroup"),
                    "ratePlanType": rates.get("rateType"),
                    "evpBillingStart": rates.get("bpStart"),
                    "evpBillingEnd": rates.get("bpEnd"),
                    "evpDaysInBillingPeriod": int(rates.get("daysSince", 0)),
                    "evpEstConsCurPeriod": rates.get("estCons"),
                    "evpEstCostCurPeriod": rates.get("estCost"),
                    "evpConsToDate": rates.get("cons2date"),
                    "evpCostToDate": rates.get("cost2date"),
                    "evpComLastBillingPeakDemand": rates.get("comLastCons"),
                })

            series = root.find("Series")
            if series is not None:
                daily_consumption = []
                for point in series.findall("Point"):
                    daily_consumption.append({
                        "date_time": point.get("dateTime"),
                        "end_time": point.get("endTime"),
                        "value": float(point.get("value", 0)),
                        "cost": float(point.get("cost", 0)) if point.get("cost") else None,
                        "quality": point.get("quality"),
                        "type": point.get("type"),
                        "date_type": point.get("evpDateType"),
                        "date_description": point.get("evpDateDescription"),
                    })
                data["daily_consumption"] = daily_consumption

            return data

        except ET.ParseError as err:
            raise BCHydroApiError(f"XML parse error: {err}") from err

    async def close(self) -> None:
        """Close the API client session.

        If using a provided session, the session is NOT closed (managed externally),
        but auth state is still reset to allow re-authentication.
        """
        if self._session and not self._provided_session:
            await self._session.close()
            self._session = None
            self._cookie_jar = aiohttp.CookieJar()
        # Always reset auth state to allow re-authentication
        self._authenticated = False
        self._cookies = {}

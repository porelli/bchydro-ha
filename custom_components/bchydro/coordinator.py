"""DataUpdateCoordinator for BC Hydro."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import (
    TimestampDataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import BCHydroApiClient, BCHydroApiError, BCHydroAuthError
from .const import DOMAIN, UPDATE_INTERVAL_MINUTES
from .statistics import (
    account_statistics,
    async_import_statistics,
    async_cleanup_future_statistics,
)

_LOGGER = logging.getLogger(__name__)

# Passed as a placeholder: hassfest does not allow URLs inside translations
ISSUE_TRACKER_URL = "https://github.com/porelli/bchydro-ha/issues"


class BCHydroDataUpdateCoordinator(TimestampDataUpdateCoordinator):
    """Class to manage fetching BC Hydro data.

    Timestamped so that diagnostics can report when data last arrived.
    """

    config_entry: ConfigEntry

    # Home Assistant stops scheduling refreshes as soon as ConfigEntryAuthFailed is
    # raised, and only a manual reauthentication brings the integration back. BC
    # Hydro's login flow fails for plenty of reasons that look like an auth problem
    # (maintenance pages, SSO hiccups), so require several consecutive failures
    # before demanding the user's attention. Logins turned away by BC Hydro's bot
    # protection are not counted at all (BCHydroLoginBlockedError is transient).
    MAX_CONSECUTIVE_AUTH_FAILURES = 3

    # Failures that are not about the password only mark the sensors unavailable
    # and write to the log, which is easy to miss. Once data has been missing
    # this long, also raise an issue under Settings > System > Repairs.
    REPAIR_ISSUE_AFTER = timedelta(hours=3)

    def __init__(
        self,
        hass: HomeAssistant,
        client: BCHydroApiClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        update_interval_minutes = entry.options.get(
            "update_interval", UPDATE_INTERVAL_MINUTES
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=update_interval_minutes),
            config_entry=entry,
        )
        self.client = client
        self._historical_fetched = False  # Track if we've fetched historical data
        self.consecutive_auth_failures = 0
        self.failing_since: datetime | None = None
        # Each configured account keeps its statistics separate; the first one
        # configured keeps the original ids (see statistics.account_statistics).
        self.statistics = account_statistics(
            entry.data.get("statistic_suffix", ""),
            entry.data.get("account_label", ""),
        )

    def _calculate_daily_from_hourly(self, hourly_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Calculate daily consumption from hourly data.

        Args:
            hourly_data: List of hourly consumption records

        Returns:
            List of daily consumption records with the same format as BC Hydro's daily API
        """
        daily_totals: dict[str, dict[str, float | datetime | None]] = {}

        for hour in hourly_data:
            try:
                # Skip INVALID quality entries (future dates with 0 values)
                if hour.get("quality") == "INVALID":
                    continue

                # Parse the timestamp to get the date
                timestamp_str = hour.get("date_time")
                if not timestamp_str:
                    continue

                timestamp = datetime.fromisoformat(timestamp_str)
                date_key = timestamp.date().isoformat()

                # Sum up consumption and cost for this date
                consumption = hour.get("value", 0) or 0
                cost = hour.get("cost", 0) or 0

                if date_key not in daily_totals:
                    daily_totals[date_key] = {"consumption": 0.0, "cost": 0.0, "date": None}

                prev_consumption = daily_totals[date_key]["consumption"]
                prev_cost = daily_totals[date_key]["cost"]
                daily_totals[date_key]["consumption"] = (float(prev_consumption) if prev_consumption else 0.0) + float(consumption)
                daily_totals[date_key]["cost"] = (float(prev_cost) if prev_cost else 0.0) + float(cost)
                daily_totals[date_key]["date"] = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)

            except (ValueError, TypeError) as err:
                _LOGGER.debug("Failed to parse hourly data for daily calculation: %s", err)
                continue

        # Convert to list format matching BC Hydro's daily API
        daily_consumption: list[dict[str, Any]] = []
        for date_key in sorted(daily_totals.keys()):
            data = daily_totals[date_key]
            date_val = data["date"]
            date_str = date_val.isoformat() if isinstance(date_val, datetime) else date_key
            daily_consumption.append({
                "date_time": date_str,
                "end_time": date_str,
                "value": data["consumption"],
                "cost": data["cost"],
                "quality": "ACTUAL",  # Calculated from actual hourly data
                "type": "daily",
            })

        _LOGGER.debug("Calculated %d daily records from %d hourly records",
                     len(daily_consumption), len(hourly_data))

        return daily_consumption

    @property
    def repair_issue_id(self) -> str:
        """Id of the repair issue reporting that this entry stopped updating."""
        return f"update_failing_{self.config_entry.entry_id}"

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from BC Hydro, reporting outages that last too long."""
        try:
            data = await self._fetch_data()
        except UpdateFailed as err:
            self._record_failure(err)
            raise
        self._record_success()
        return data

    def _record_failure(self, err: Exception) -> None:
        """Raise (or refresh) the repair issue once data is missing for too long."""
        now = dt_util.utcnow()
        if self.failing_since is None:
            self.failing_since = now
        failing_for = now - self.failing_since
        if failing_for < self.REPAIR_ISSUE_AFTER:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.repair_issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="update_failing",
            translation_placeholders={
                "title": self.config_entry.title,
                "hours": str(int(failing_for.total_seconds() // 3600)),
                "error": str(err)[:300],
                "issue_tracker": ISSUE_TRACKER_URL,
            },
        )

    def _record_success(self) -> None:
        """Clear the outage state and its repair issue."""
        if self.failing_since is not None:
            _LOGGER.info(
                "BC Hydro is updating again after %s without data",
                dt_util.utcnow() - self.failing_since,
            )
            self.failing_since = None
        # Also clears an issue left behind by an earlier coordinator (a reload
        # starts with no failure state); a no-op when there is none.
        ir.async_delete_issue(self.hass, DOMAIN, self.repair_issue_id)

    async def _fetch_data(self) -> dict[str, Any]:
        """Fetch data from BC Hydro."""
        try:
            # Get account profile data
            profile_data = await self.client.get_account_profile()

            # Get consumption data for daily breakdown (only last 10 days for efficiency)
            # Profile data already has consumption/cost to date - we just need recent daily data
            consumption_data = None
            try:
                # Fetch only last 10 days of hourly data for daily attributes
                # (we show 7 days in attributes, 10 gives buffer)
                now = datetime.now(tz=timezone.utc)
                pacific_tz = timezone(timedelta(hours=-8))
                end_date = now.astimezone(pacific_tz)
                start_date = end_date - timedelta(days=10)

                hourly_data = await self.client.get_consumption_data(
                    start_date=start_date,
                    end_date=end_date,
                    date_range="currentBill",
                    granularity="hourly",
                )

                # Process hourly data for sensors
                consumption_data = {}
                if hourly_data and "hourly_consumption" in hourly_data:
                    consumption_data["hourly_consumption"] = hourly_data["hourly_consumption"]

                    # Calculate daily consumption from hourly data
                    daily_consumption = self._calculate_daily_from_hourly(
                        hourly_data["hourly_consumption"]
                    )
                    consumption_data["daily_consumption"] = daily_consumption

            except Exception as consumption_err:
                _LOGGER.warning("Failed to get consumption data: %s", consumption_err)
                    # Continue with just profile data

            _LOGGER.debug("Successfully fetched BC Hydro data")

            # Clean up any corrupted future statistics on first run
            if not self._historical_fetched:
                self._historical_fetched = True
                try:
                    cleaned = await async_cleanup_future_statistics(
                        self.hass, self.statistics
                    )
                    if cleaned > 0:
                        _LOGGER.info("Cleaned up %d corrupted future statistics", cleaned)
                except Exception as cleanup_err:
                    _LOGGER.warning("Failed to cleanup future statistics: %s", cleanup_err)

            # Import statistics for the Energy Dashboard
            # Statistics makes its own optimized API call (only fetches since last_timestamp)
            historical_days = self.config_entry.options.get("historical_days", 7)
            _LOGGER.debug(
                "Triggering statistics import (historical_days=%d, first_run=%s)",
                historical_days,
                not self._historical_fetched,
            )
            try:
                await async_import_statistics(
                    self.hass,
                    self.client,
                    historical_days,
                    self.statistics,
                )
                _LOGGER.debug("Statistics import completed successfully")
            except Exception as stats_err:
                _LOGGER.warning("Failed to import statistics: %s", stats_err)

            self.consecutive_auth_failures = 0

            # Combine the data
            return {
                "profile": profile_data,
                "consumption": consumption_data if consumption_data else {},
            }

        except BCHydroAuthError as err:
            self.consecutive_auth_failures += 1

            if self.consecutive_auth_failures < self.MAX_CONSECUTIVE_AUTH_FAILURES:
                _LOGGER.warning(
                    "BC Hydro login failed (%d/%d consecutive attempts), will retry: %s",
                    self.consecutive_auth_failures,
                    self.MAX_CONSECUTIVE_AUTH_FAILURES,
                    err,
                )
                raise UpdateFailed(f"BC Hydro login failed: {err}") from err

            _LOGGER.warning(
                "BC Hydro login failed %d times in a row - "
                "triggering reauthentication: %s",
                self.consecutive_auth_failures,
                err,
            )
            raise ConfigEntryAuthFailed(
                "Authentication to BC Hydro failed. Please reauthenticate."
            ) from err
        except BCHydroApiError as err:
            _LOGGER.error("API error communicating with BC Hydro: %s", err)
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error fetching BC Hydro data")
            raise UpdateFailed(f"Unexpected error: {err}") from err

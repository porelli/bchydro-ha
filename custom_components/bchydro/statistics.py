"""Statistics recorder for BC Hydro hourly data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STATISTIC_ID_ENERGY = f"{DOMAIN}:energy_consumption"
STATISTIC_ID_COST = f"{DOMAIN}:energy_cost"


async def async_cleanup_future_statistics(hass: HomeAssistant) -> int:
    """Check for statistics with future timestamps (corrupted data).

    Returns the number of corrupted records found.
    If corrupted records are found, user needs to clear them via Developer Tools.
    """
    now = datetime.now(tz=timezone.utc)
    recorder = get_instance(hass)

    # Get all statistics for our IDs
    existing_stats = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        now - timedelta(days=365),  # Look back a year
        now + timedelta(days=365),  # Look forward a year to find future entries
        {STATISTIC_ID_ENERGY, STATISTIC_ID_COST},
        "hour",
        None,
        {"sum"},
    )

    # Find future timestamps
    future_count = 0
    for stat_id in [STATISTIC_ID_ENERGY, STATISTIC_ID_COST]:
        if stat_id in existing_stats:
            for stat in existing_stats[stat_id]:
                stat_time = datetime.fromtimestamp(stat["start"], tz=timezone.utc)
                if stat_time > now:
                    future_count += 1

    if future_count > 0:
        _LOGGER.error(
            "Found %d statistics records with future timestamps blocking imports. "
            "Please clear bchydro statistics via Developer Tools > Statistics > "
            "search 'bchydro' > click each statistic > 'Clear statistics' button.",
            future_count,
        )

    return future_count


def _build_statistics_metadata() -> tuple[StatisticMetaData, StatisticMetaData]:
    """Build metadata for energy and cost statistics."""
    energy_meta: StatisticMetaData = {
        "has_mean": False,
        "has_sum": True,
        "mean_type": StatisticMeanType.NONE,
        "name": "BC Hydro Energy Consumption",
        "source": DOMAIN,
        "statistic_id": STATISTIC_ID_ENERGY,
        "unit_class": "energy",
        "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
    }

    cost_meta: StatisticMetaData = {
        "has_mean": False,
        "has_sum": True,
        "mean_type": StatisticMeanType.NONE,
        "name": "BC Hydro Energy Cost",
        "source": DOMAIN,
        "statistic_id": STATISTIC_ID_COST,
        "unit_class": None,
        "unit_of_measurement": "CAD",
    }

    return energy_meta, cost_meta


async def _get_last_statistics_state(
    hass: HomeAssistant,
) -> tuple[float, float, datetime | None]:
    """Get the last recorded statistics state.

    Returns:
        Tuple of (last_energy_sum, last_cost_sum, last_timestamp)
    """
    last_stats_energy = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, STATISTIC_ID_ENERGY, True, {"sum"}
    )
    last_stats_cost = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, STATISTIC_ID_COST, True, {"sum"}
    )

    cumulative_sum_energy = 0.0
    cumulative_sum_cost = 0.0
    last_timestamp = None

    if STATISTIC_ID_ENERGY in last_stats_energy and last_stats_energy[STATISTIC_ID_ENERGY]:
        last_stat = last_stats_energy[STATISTIC_ID_ENERGY][0]
        cumulative_sum_energy = last_stat.get("sum", 0.0)
        last_timestamp = datetime.fromtimestamp(last_stat["start"], tz=timezone.utc)
        _LOGGER.debug(
            "Found existing energy statistics: sum=%.2f kWh, last=%s",
            cumulative_sum_energy,
            last_timestamp.isoformat(),
        )

    if STATISTIC_ID_COST in last_stats_cost and last_stats_cost[STATISTIC_ID_COST]:
        last_stat = last_stats_cost[STATISTIC_ID_COST][0]
        cumulative_sum_cost = last_stat.get("sum", 0.0)
        _LOGGER.debug("Found existing cost statistics: sum=$%.2f", cumulative_sum_cost)

    return cumulative_sum_energy, cumulative_sum_cost, last_timestamp


async def _get_earliest_statistics_timestamp(
    hass: HomeAssistant,
) -> datetime | None:
    """Get the earliest recorded statistics timestamp.

    Returns:
        The earliest timestamp or None if no statistics exist.
    """
    recorder = get_instance(hass)
    now = datetime.now(tz=timezone.utc)

    # Get statistics for the past year to find the earliest
    existing_stats = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        now - timedelta(days=365),
        now,
        {STATISTIC_ID_ENERGY},
        "hour",
        None,
        {"sum"},
    )

    if STATISTIC_ID_ENERGY not in existing_stats or not existing_stats[STATISTIC_ID_ENERGY]:
        return None

    # Find the earliest timestamp
    earliest_timestamp = None
    for stat in existing_stats[STATISTIC_ID_ENERGY]:
        stat_time = datetime.fromtimestamp(stat["start"], tz=timezone.utc)
        if earliest_timestamp is None or stat_time < earliest_timestamp:
            earliest_timestamp = stat_time

    return earliest_timestamp


async def _clear_statistics(hass: HomeAssistant) -> None:
    """Clear all BC Hydro statistics to allow fresh import.

    This is used when the user requests more historical data than currently exists.
    """
    import asyncio

    recorder = get_instance(hass)
    clear_done = asyncio.Event()

    def on_clear_done() -> None:
        # Callback runs on recorder thread, need to safely set event on main loop
        hass.loop.call_soon_threadsafe(clear_done.set)

    _LOGGER.info("Clearing existing BC Hydro statistics for fresh import")
    recorder.async_clear_statistics(
        [STATISTIC_ID_ENERGY, STATISTIC_ID_COST],
        on_done=on_clear_done,
    )

    # Wait for clear to complete (with timeout)
    try:
        await asyncio.wait_for(clear_done.wait(), timeout=30.0)
        _LOGGER.info("Successfully cleared existing statistics")
    except asyncio.TimeoutError:
        _LOGGER.warning("Timeout waiting for statistics clear to complete")


def _convert_hourly_data_to_statistics(
    hourly_data: list[dict[str, Any]],
    cumulative_sum_energy: float,
    cumulative_sum_cost: float,
    last_timestamp: datetime | None,
) -> tuple[list[StatisticData], list[StatisticData], float, float]:
    """Convert BC Hydro hourly data to HA statistics format.

    Args:
        hourly_data: Raw hourly data from BC Hydro API
        cumulative_sum_energy: Starting cumulative energy sum
        cumulative_sum_cost: Starting cumulative cost sum
        last_timestamp: Skip entries at or before this timestamp

    Returns:
        Tuple of (energy_stats, cost_stats, final_energy_sum, final_cost_sum)
    """
    statistics_energy = []
    statistics_cost = []
    skipped_count = 0
    invalid_count = 0

    # Log data quality transition to debug freshness issue
    if hourly_data:
        # Find last ACTUAL entry and first INVALID entry
        last_actual = None
        first_invalid = None
        for e in hourly_data:
            quality = e.get("quality")
            if quality == "ACTUAL":
                last_actual = e
            elif quality == "INVALID" and first_invalid is None:
                first_invalid = e

        if last_actual:
            _LOGGER.debug(
                "Last ACTUAL data from API: %s value=%s cost=%s",
                last_actual.get("date_time"),
                last_actual.get("value"),
                last_actual.get("cost"),
            )
        if first_invalid:
            _LOGGER.debug(
                "First INVALID data from API: %s",
                first_invalid.get("date_time"),
            )

    for entry in hourly_data:
        try:
            # Skip INVALID quality entries - BC Hydro marks data as INVALID until
            # it's been validated/processed (typically 1.5-2 days after the actual
            # reading). These entries have value=0.0 and cannot be used. The website
            # may show provisional data that the API doesn't expose as ACTUAL.
            if entry.get("quality") == "INVALID":
                invalid_count += 1
                continue

            timestamp_str = entry.get("date_time")
            if not timestamp_str:
                continue

            # Parse ISO format timestamp
            timestamp = datetime.fromisoformat(timestamp_str)

            # Normalize to top of the hour (HA requirement for statistics)
            timestamp = timestamp.replace(minute=0, second=0, microsecond=0)

            # Skip if we've already recorded this timestamp
            if last_timestamp and timestamp <= last_timestamp:
                skipped_count += 1
                continue

            consumption = entry.get("value", 0) or 0
            cost = entry.get("cost", 0) or 0
            try:
                cost = float(cost)
            except (ValueError, TypeError):
                cost = 0.0

            cumulative_sum_energy += consumption
            cumulative_sum_cost += cost

            statistics_energy.append(
                StatisticData(start=timestamp, sum=cumulative_sum_energy, state=consumption)
            )
            statistics_cost.append(
                StatisticData(start=timestamp, sum=cumulative_sum_cost, state=cost)
            )

        except (ValueError, TypeError) as err:
            _LOGGER.warning("Failed to parse timestamp %s: %s", entry.get("date_time"), err)
            continue

    if invalid_count > 0:
        _LOGGER.debug(
            "Skipped %d INVALID quality records (BC Hydro has ~1.5-2 day data lag)",
            invalid_count,
        )
    if skipped_count > 0:
        _LOGGER.debug("Skipped %d records (already recorded)", skipped_count)

    return statistics_energy, statistics_cost, cumulative_sum_energy, cumulative_sum_cost


async def async_import_statistics(
    hass: HomeAssistant,
    api_client: Any,
    historical_days: int,
) -> None:
    """Import statistics for the Energy Dashboard.

    This function fetches only the data needed and imports it into HA's statistics.
    It's optimized to minimize API calls and data transfer:
    - If existing stats: only fetches since last recorded timestamp
    - If no stats: fetches full historical_days for initial import
    - If user wants more history: clears and re-fetches

    Args:
        hass: Home Assistant instance
        api_client: BC Hydro API client
        historical_days: Number of days of history to maintain
    """
    try:
        # BC Hydro operates on Pacific time, so we need to use that for date calculations
        pacific_tz = timezone(timedelta(hours=-8))
        now = datetime.now(tz=pacific_tz)
        full_history_start = now - timedelta(days=historical_days)

        _LOGGER.debug(
            "Statistics import started: now=%s, historical_days=%d",
            now.isoformat(),
            historical_days,
        )

        # Check existing statistics to determine what we need
        cumulative_energy, cumulative_cost, last_timestamp = await _get_last_statistics_state(hass)
        earliest_existing = await _get_earliest_statistics_timestamp(hass)

        _LOGGER.debug(
            "Existing stats state: last_timestamp=%s, earliest=%s, energy_sum=%.2f, cost_sum=%.2f",
            last_timestamp.isoformat() if last_timestamp else "None",
            earliest_existing.isoformat() if earliest_existing else "None",
            cumulative_energy,
            cumulative_cost,
        )

        # Determine optimal fetch range
        if earliest_existing and full_history_start < earliest_existing:
            # User wants more history than we have - clear and re-fetch
            _LOGGER.info(
                "User requested %d days of history, but existing stats only go back to %s. "
                "Will clear and re-fetch full history.",
                historical_days,
                earliest_existing.date().isoformat(),
            )
            await _clear_statistics(hass)
            fetch_start = full_history_start
            last_timestamp = None
            cumulative_energy, cumulative_cost = 0.0, 0.0
            _LOGGER.debug("Fetch mode: FULL HISTORY (cleared existing)")
        elif last_timestamp:
            # Have existing stats - only fetch since last recorded (with buffer)
            # Convert to Pacific time for proper date boundary handling
            # BC Hydro API uses Pacific dates, so we need to go back at least 1 day
            # to catch any new data that became ACTUAL since last import
            last_timestamp_pacific = last_timestamp.astimezone(pacific_tz)
            # Start from the beginning of the day before the last timestamp
            # This ensures we catch new data as it becomes ACTUAL
            fetch_start = (last_timestamp_pacific - timedelta(days=2)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            _LOGGER.debug(
                "Fetch mode: INCREMENTAL from %s (last_timestamp_pacific=%s) to %s",
                fetch_start.isoformat(),
                last_timestamp_pacific.isoformat(),
                now.isoformat(),
            )
        else:
            # No existing stats - initial import
            fetch_start = full_history_start
            _LOGGER.info(
                "Fetch mode: INITIAL IMPORT (%d days of history)",
                historical_days,
            )

        # Fetch only the data we need
        hourly_data = await api_client.get_consumption_data(
            start_date=fetch_start,
            end_date=now,
            date_range="currentBill",
            granularity="hourly",
        )

        if not hourly_data or "hourly_consumption" not in hourly_data:
            _LOGGER.debug("No hourly consumption data returned")
            return

        consumption_list = hourly_data["hourly_consumption"]
        if not consumption_list:
            _LOGGER.debug("Empty hourly consumption list")
            return

        # Analyze data freshness
        actual_records = [e for e in consumption_list if e.get("quality") == "ACTUAL"]
        invalid_records = [e for e in consumption_list if e.get("quality") == "INVALID"]

        # Find the latest ACTUAL record
        latest_actual_ts = None
        if actual_records:
            for record in actual_records:
                ts_str = record.get("date_time")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if latest_actual_ts is None or ts > latest_actual_ts:
                            latest_actual_ts = ts
                    except ValueError:
                        pass

        first_ts = consumption_list[0].get("date_time", "unknown")
        last_ts = consumption_list[-1].get("date_time", "unknown")
        _LOGGER.debug(
            "API returned %d records (%d ACTUAL, %d INVALID): %s to %s",
            len(consumption_list),
            len(actual_records),
            len(invalid_records),
            first_ts,
            last_ts,
        )
        _LOGGER.debug(
            "Latest ACTUAL data: %s, Last recorded: %s",
            latest_actual_ts.isoformat() if latest_actual_ts else "None",
            last_timestamp.isoformat() if last_timestamp else "None",
        )

        # Check if there's any new data to import
        if last_timestamp and latest_actual_ts and latest_actual_ts <= last_timestamp:
            _LOGGER.debug(
                "No new data available: latest ACTUAL (%s) <= last recorded (%s). "
                "BC Hydro has ~1.5-2 day data lag.",
                latest_actual_ts.isoformat(),
                last_timestamp.isoformat(),
            )
            return

        # Convert hourly data to statistics format
        stats_energy, stats_cost, final_energy, final_cost = _convert_hourly_data_to_statistics(
            consumption_list,
            cumulative_energy,
            cumulative_cost,
            last_timestamp,
        )

        # Import statistics
        if stats_energy or stats_cost:
            metadata_energy, metadata_cost = _build_statistics_metadata()

            if stats_energy:
                _LOGGER.info(
                    "Importing %d energy statistics (cumulative: %.2f kWh)",
                    len(stats_energy),
                    final_energy,
                )
                async_add_external_statistics(hass, metadata_energy, stats_energy)

            if stats_cost:
                _LOGGER.info(
                    "Importing %d cost statistics (cumulative: $%.2f)",
                    len(stats_cost),
                    final_cost,
                )
                async_add_external_statistics(hass, metadata_cost, stats_cost)
        else:
            _LOGGER.debug(
                "No new statistics to import: conversion returned empty (all records already recorded or filtered)"
            )

    except Exception as err:
        _LOGGER.error("Failed to import statistics: %s", err, exc_info=True)
        raise

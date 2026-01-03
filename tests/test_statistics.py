"""Tests for statistics.py."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bchydro.statistics import (
    async_import_statistics,
    async_cleanup_future_statistics,
    _build_statistics_metadata,
    _get_last_statistics_state,
    _convert_hourly_data_to_statistics,
    STATISTIC_ID_ENERGY,
    STATISTIC_ID_COST,
)


@pytest.fixture
def mock_recorder():
    """Create a mock recorder instance."""
    with patch("custom_components.bchydro.statistics.get_instance") as mock_get_instance:
        mock_instance = MagicMock()
        mock_instance.async_add_executor_job = AsyncMock()
        mock_get_instance.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_add_statistics():
    """Mock async_add_external_statistics."""
    with patch("custom_components.bchydro.statistics.async_add_external_statistics") as mock:
        yield mock


# Tests for _build_statistics_metadata
def test_build_statistics_metadata() -> None:
    """Test building statistics metadata."""
    energy_meta, cost_meta = _build_statistics_metadata()

    # StatisticMetaData is a TypedDict, so access via dict keys
    assert energy_meta["statistic_id"] == STATISTIC_ID_ENERGY
    assert energy_meta["has_sum"] is True
    assert energy_meta["has_mean"] is False
    assert energy_meta["name"] == "BC Hydro Energy Consumption"

    assert cost_meta["statistic_id"] == STATISTIC_ID_COST
    assert cost_meta["has_sum"] is True
    assert cost_meta["has_mean"] is False
    assert cost_meta["name"] == "BC Hydro Energy Cost"


# Tests for _get_last_statistics_state
async def test_get_last_statistics_state_no_data(
    hass: HomeAssistant,
    mock_recorder,
) -> None:
    """Test getting last statistics state when no data exists."""
    mock_recorder.async_add_executor_job.return_value = {}

    energy_sum, cost_sum, last_ts = await _get_last_statistics_state(hass)

    assert energy_sum == 0.0
    assert cost_sum == 0.0
    assert last_ts is None


async def test_get_last_statistics_state_with_data(
    hass: HomeAssistant,
    mock_recorder,
) -> None:
    """Test getting last statistics state with existing data."""
    last_timestamp = datetime(2025, 12, 25, 10, 0, 0, tzinfo=timezone.utc)
    mock_recorder.async_add_executor_job.side_effect = [
        {STATISTIC_ID_ENERGY: [{"start": last_timestamp.timestamp(), "sum": 100.0}]},
        {STATISTIC_ID_COST: [{"start": last_timestamp.timestamp(), "sum": 10.0}]},
    ]

    energy_sum, cost_sum, last_ts = await _get_last_statistics_state(hass)

    assert energy_sum == 100.0
    assert cost_sum == 10.0
    assert last_ts == last_timestamp


# Tests for _convert_hourly_data_to_statistics
def test_convert_hourly_data_empty() -> None:
    """Test converting empty hourly data."""
    stats_e, stats_c, sum_e, sum_c = _convert_hourly_data_to_statistics([], 0.0, 0.0, None)

    assert stats_e == []
    assert stats_c == []
    assert sum_e == 0.0
    assert sum_c == 0.0


def test_convert_hourly_data_success() -> None:
    """Test converting hourly data successfully."""
    hourly_data = [
        {"date_time": "2025-12-25T10:00:00-08:00", "value": 1.5, "cost": 0.15, "quality": "ACTUAL"},
        {"date_time": "2025-12-25T11:00:00-08:00", "value": 2.0, "cost": 0.20, "quality": "ACTUAL"},
    ]

    stats_e, stats_c, sum_e, sum_c = _convert_hourly_data_to_statistics(hourly_data, 0.0, 0.0, None)

    assert len(stats_e) == 2
    assert len(stats_c) == 2
    assert sum_e == 3.5
    assert sum_c == 0.35


def test_convert_hourly_data_continues_from_existing() -> None:
    """Test that conversion continues cumulative sums from existing values."""
    hourly_data = [
        {"date_time": "2025-12-25T10:00:00-08:00", "value": 1.5, "cost": 0.15},
    ]

    stats_e, stats_c, sum_e, sum_c = _convert_hourly_data_to_statistics(
        hourly_data, 100.0, 10.0, None
    )

    assert len(stats_e) == 1
    assert sum_e == 101.5
    assert sum_c == 10.15


def test_convert_hourly_data_skips_old_timestamps() -> None:
    """Test that old timestamps are skipped."""
    last_ts = datetime(2025, 12, 25, 18, 0, 0, tzinfo=timezone.utc)  # 10:00 PST = 18:00 UTC
    hourly_data = [
        {"date_time": "2025-12-25T09:00:00-08:00", "value": 1.0, "cost": 0.10},  # Before last_ts
        {"date_time": "2025-12-25T10:00:00-08:00", "value": 1.5, "cost": 0.15},  # Equal to last_ts
        {"date_time": "2025-12-25T11:00:00-08:00", "value": 2.0, "cost": 0.20},  # After last_ts
    ]

    stats_e, stats_c, sum_e, sum_c = _convert_hourly_data_to_statistics(
        hourly_data, 100.0, 10.0, last_ts
    )

    # Only the 11:00 entry should be included
    assert len(stats_e) == 1
    assert sum_e == 102.0


def test_convert_hourly_data_skips_invalid_quality() -> None:
    """Test that INVALID quality entries are skipped."""
    hourly_data = [
        {"date_time": "2025-12-25T10:00:00-08:00", "value": 1.5, "cost": 0.15, "quality": "ACTUAL"},
        {"date_time": "2025-12-25T11:00:00-08:00", "value": 0.0, "cost": 0.0, "quality": "INVALID"},
        {"date_time": "2025-12-25T12:00:00-08:00", "value": 2.0, "cost": 0.20, "quality": "ACTUAL"},
    ]

    stats_e, stats_c, sum_e, sum_c = _convert_hourly_data_to_statistics(hourly_data, 0.0, 0.0, None)

    assert len(stats_e) == 2
    assert sum_e == 3.5


def test_convert_hourly_data_handles_none_values() -> None:
    """Test handling of None values."""
    hourly_data = [
        {"date_time": "2025-12-25T10:00:00-08:00", "value": None, "cost": None},
    ]

    stats_e, stats_c, sum_e, sum_c = _convert_hourly_data_to_statistics(hourly_data, 0.0, 0.0, None)

    assert len(stats_e) == 1
    assert sum_e == 0.0
    assert sum_c == 0.0


def test_convert_hourly_data_handles_invalid_cost() -> None:
    """Test handling of invalid cost values."""
    hourly_data = [
        {"date_time": "2025-12-25T10:00:00-08:00", "value": 1.5, "cost": "invalid"},
    ]

    stats_e, stats_c, sum_e, sum_c = _convert_hourly_data_to_statistics(hourly_data, 0.0, 0.0, None)

    assert len(stats_e) == 1
    assert sum_e == 1.5
    assert sum_c == 0.0


def test_convert_hourly_data_skips_missing_timestamp() -> None:
    """Test that entries without timestamp are skipped."""
    hourly_data = [
        {"value": 1.5, "cost": 0.15},  # Missing date_time
        {"date_time": "2025-12-25T10:00:00-08:00", "value": 2.0, "cost": 0.20},
    ]

    stats_e, stats_c, sum_e, sum_c = _convert_hourly_data_to_statistics(hourly_data, 0.0, 0.0, None)

    assert len(stats_e) == 1
    assert sum_e == 2.0


def test_convert_hourly_data_skips_invalid_timestamp() -> None:
    """Test that invalid timestamps are skipped."""
    hourly_data = [
        {"date_time": "invalid-timestamp", "value": 1.5, "cost": 0.15},
    ]

    stats_e, stats_c, sum_e, sum_c = _convert_hourly_data_to_statistics(hourly_data, 0.0, 0.0, None)

    assert len(stats_e) == 0


# Tests for async_import_statistics
async def test_import_statistics_success(
    hass: HomeAssistant,
    mock_recorder,
    mock_add_statistics,
) -> None:
    """Test importing statistics successfully."""
    mock_recorder.async_add_executor_job.return_value = {}

    mock_api_client = AsyncMock()
    mock_api_client.get_consumption_data.return_value = {
        "hourly_consumption": [
            {"date_time": "2025-12-25T10:00:00-08:00", "value": 1.5, "cost": 0.15, "quality": "ACTUAL"},
            {"date_time": "2025-12-25T11:00:00-08:00", "value": 2.0, "cost": 0.20, "quality": "ACTUAL"},
        ]
    }

    await async_import_statistics(hass, mock_api_client, historical_days=7)

    # Should add statistics for both energy and cost
    assert mock_add_statistics.call_count == 2


async def test_import_statistics_no_hourly_data(
    hass: HomeAssistant,
    mock_recorder,
    mock_add_statistics,
) -> None:
    """Test import when API returns no hourly data."""
    mock_recorder.async_add_executor_job.return_value = {}

    mock_api_client = AsyncMock()
    mock_api_client.get_consumption_data.return_value = {}  # No hourly_consumption key

    await async_import_statistics(hass, mock_api_client, historical_days=7)

    mock_add_statistics.assert_not_called()


async def test_import_statistics_empty_consumption_list(
    hass: HomeAssistant,
    mock_recorder,
    mock_add_statistics,
) -> None:
    """Test import with empty consumption list."""
    mock_recorder.async_add_executor_job.return_value = {}

    mock_api_client = AsyncMock()
    mock_api_client.get_consumption_data.return_value = {"hourly_consumption": []}

    await async_import_statistics(hass, mock_api_client, historical_days=7)

    mock_add_statistics.assert_not_called()


async def test_import_statistics_continues_from_existing(
    hass: HomeAssistant,
    mock_recorder,
    mock_add_statistics,
) -> None:
    """Test that import continues from existing statistics."""
    last_timestamp = datetime(2025, 12, 25, 18, 0, 0, tzinfo=timezone.utc)
    # Mock returns: 1) get_last_statistics (energy), 2) get_last_statistics (cost), 3) statistics_during_period (earliest)
    mock_recorder.async_add_executor_job.side_effect = [
        # _get_last_statistics_state - energy
        {STATISTIC_ID_ENERGY: [{"start": last_timestamp.timestamp(), "sum": 100.0}]},
        # _get_last_statistics_state - cost
        {STATISTIC_ID_COST: [{"start": last_timestamp.timestamp(), "sum": 10.0}]},
        # _get_earliest_statistics_timestamp
        {STATISTIC_ID_ENERGY: [{"start": last_timestamp.timestamp(), "sum": 100.0}]},
    ]

    mock_api_client = AsyncMock()
    mock_api_client.get_consumption_data.return_value = {
        "hourly_consumption": [
            {"date_time": "2025-12-25T10:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},  # Old, skipped
            {"date_time": "2025-12-25T11:00:00-08:00", "value": 2.0, "cost": 0.20, "quality": "ACTUAL"},  # New
        ]
    }

    await async_import_statistics(hass, mock_api_client, historical_days=7)

    # Should still call add_statistics
    assert mock_add_statistics.call_count == 2


async def test_import_statistics_api_error_on_backfill(
    hass: HomeAssistant,
    mock_recorder,
    mock_add_statistics,
) -> None:
    """Test handling of API errors when fetching additional history."""
    # Existing stats only go back to Dec 25, user wants 30 days
    earliest_existing = datetime(2025, 12, 25, 0, 0, 0, tzinfo=timezone.utc)
    last_timestamp = datetime(2025, 12, 26, 0, 0, 0, tzinfo=timezone.utc)

    mock_recorder.async_add_executor_job.side_effect = [
        {STATISTIC_ID_ENERGY: [{"start": last_timestamp.timestamp(), "sum": 1.0}]},
        {STATISTIC_ID_COST: [{"start": last_timestamp.timestamp(), "sum": 0.1}]},
        {STATISTIC_ID_ENERGY: [{"start": earliest_existing.timestamp(), "sum": 1.0}]},
    ]

    def mock_clear_statistics(statistic_ids, on_done=None):
        if on_done:
            on_done()

    mock_recorder.async_clear_statistics = mock_clear_statistics

    mock_api_client = AsyncMock()
    mock_api_client.get_consumption_data.side_effect = Exception("API Error")

    # Should raise the exception (not swallow it) when trying to fetch data
    with pytest.raises(Exception, match="API Error"):
        await async_import_statistics(hass, mock_api_client, historical_days=30)


async def test_import_statistics_all_data_already_imported(
    hass: HomeAssistant,
    mock_recorder,
    mock_add_statistics,
) -> None:
    """Test when all data is already imported (nothing new to add)."""
    # Last timestamp is after all the data we'll receive
    # earliest_timestamp should be BEFORE the API data so we don't trigger backfill
    earliest_timestamp = datetime(2025, 12, 25, 8, 0, 0, tzinfo=timezone.utc)  # Before API data
    last_timestamp = datetime(2025, 12, 25, 20, 0, 0, tzinfo=timezone.utc)  # After API data
    # Mock returns: 1) get_last_statistics (energy), 2) get_last_statistics (cost), 3) statistics_during_period (earliest)
    mock_recorder.async_add_executor_job.side_effect = [
        # _get_last_statistics_state - energy
        {STATISTIC_ID_ENERGY: [{"start": last_timestamp.timestamp(), "sum": 100.0}]},
        # _get_last_statistics_state - cost
        {STATISTIC_ID_COST: [{"start": last_timestamp.timestamp(), "sum": 10.0}]},
        # _get_earliest_statistics_timestamp
        {STATISTIC_ID_ENERGY: [{"start": earliest_timestamp.timestamp(), "sum": 1.0}]},
    ]

    mock_api_client = AsyncMock()
    mock_api_client.get_consumption_data.return_value = {
        "hourly_consumption": [
            {"date_time": "2025-12-25T10:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},  # Before last_ts
            {"date_time": "2025-12-25T11:00:00-08:00", "value": 2.0, "cost": 0.20, "quality": "ACTUAL"},  # Before last_ts
        ]
    }

    await async_import_statistics(hass, mock_api_client, historical_days=7)

    # Should not add any statistics (all data is old)
    mock_add_statistics.assert_not_called()


async def test_import_statistics_backfill_clears_existing(
    hass: HomeAssistant,
    mock_recorder,
    mock_add_statistics,
) -> None:
    """Test that statistics are cleared when user requests more history than exists."""
    # Existing statistics start at Dec 25, but user wants 30 days (back to ~Dec 9)
    earliest_existing = datetime(2025, 12, 25, 0, 0, 0, tzinfo=timezone.utc)
    last_timestamp = datetime(2025, 12, 26, 0, 0, 0, tzinfo=timezone.utc)

    # Track if clear was called
    clear_called = False

    def mock_clear_statistics(statistic_ids, on_done=None):
        nonlocal clear_called
        clear_called = True
        if on_done:
            on_done()

    mock_recorder.async_clear_statistics = mock_clear_statistics

    # Mock returns:
    # 1) get_last_statistics (energy) - returns Dec 26
    # 2) get_last_statistics (cost) - returns Dec 26
    # 3) statistics_during_period (earliest) - returns Dec 25
    mock_recorder.async_add_executor_job.side_effect = [
        # _get_last_statistics_state - energy
        {STATISTIC_ID_ENERGY: [{"start": last_timestamp.timestamp(), "sum": 1.0}]},
        # _get_last_statistics_state - cost
        {STATISTIC_ID_COST: [{"start": last_timestamp.timestamp(), "sum": 0.1}]},
        # _get_earliest_statistics_timestamp - existing starts Dec 25
        {STATISTIC_ID_ENERGY: [{"start": earliest_existing.timestamp(), "sum": 1.0}]},
    ]

    mock_api_client = AsyncMock()
    mock_api_client.get_consumption_data.return_value = {
        "hourly_consumption": [
            {"date_time": "2025-12-20T10:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
            {"date_time": "2025-12-20T11:00:00-08:00", "value": 2.0, "cost": 0.20, "quality": "ACTUAL"},
        ],
    }

    await async_import_statistics(hass, mock_api_client, historical_days=30)

    # Clear should have been called because user wants 30 days but stats only go back to Dec 25
    assert clear_called, "Statistics should have been cleared for backfill"
    # Should add new statistics after clearing
    assert mock_add_statistics.call_count == 2


# Tests for async_cleanup_future_statistics
async def test_cleanup_future_statistics_none_found(
    hass: HomeAssistant,
    mock_recorder,
) -> None:
    """Test cleanup when no future statistics exist."""
    now = datetime.now(tz=timezone.utc)
    mock_recorder.async_add_executor_job.return_value = {
        STATISTIC_ID_ENERGY: [
            {"start": (now - timedelta(hours=1)).timestamp()},
            {"start": (now - timedelta(hours=2)).timestamp()},
        ],
        STATISTIC_ID_COST: [
            {"start": (now - timedelta(hours=1)).timestamp()},
        ],
    }

    result = await async_cleanup_future_statistics(hass)

    assert result == 0


async def test_cleanup_future_statistics_found(
    hass: HomeAssistant,
    mock_recorder,
) -> None:
    """Test cleanup when future statistics are found."""
    now = datetime.now(tz=timezone.utc)
    mock_recorder.async_add_executor_job.return_value = {
        STATISTIC_ID_ENERGY: [
            {"start": (now - timedelta(hours=1)).timestamp()},
            {"start": (now + timedelta(days=5)).timestamp()},  # Future
            {"start": (now + timedelta(days=10)).timestamp()},  # Future
        ],
        STATISTIC_ID_COST: [
            {"start": (now + timedelta(days=5)).timestamp()},  # Future
        ],
    }

    result = await async_cleanup_future_statistics(hass)

    assert result == 3


async def test_cleanup_future_statistics_empty(
    hass: HomeAssistant,
    mock_recorder,
) -> None:
    """Test cleanup when no statistics exist at all."""
    mock_recorder.async_add_executor_job.return_value = {}

    result = await async_cleanup_future_statistics(hass)

    assert result == 0


async def test_import_statistics_clear_timeout(
    hass: HomeAssistant,
    mock_recorder,
    mock_add_statistics,
) -> None:
    """Test handling of timeout when clearing statistics."""
    import asyncio

    # Existing statistics start at Dec 25, user wants 30 days (back to ~Dec 9)
    earliest_existing = datetime(2025, 12, 25, 0, 0, 0, tzinfo=timezone.utc)
    last_timestamp = datetime(2025, 12, 26, 0, 0, 0, tzinfo=timezone.utc)

    def mock_clear_statistics(statistic_ids, on_done=None):
        # Don't call on_done - simulates a timeout scenario
        pass

    mock_recorder.async_clear_statistics = mock_clear_statistics

    # Mock returns:
    # 1) get_last_statistics (energy) - returns Dec 26
    # 2) get_last_statistics (cost) - returns Dec 26
    # 3) statistics_during_period (earliest) - returns Dec 25
    mock_recorder.async_add_executor_job.side_effect = [
        # _get_last_statistics_state - energy
        {STATISTIC_ID_ENERGY: [{"start": last_timestamp.timestamp(), "sum": 1.0}]},
        # _get_last_statistics_state - cost
        {STATISTIC_ID_COST: [{"start": last_timestamp.timestamp(), "sum": 0.1}]},
        # _get_earliest_statistics_timestamp
        {STATISTIC_ID_ENERGY: [{"start": earliest_existing.timestamp(), "sum": 1.0}]},
    ]

    mock_api_client = AsyncMock()
    mock_api_client.get_consumption_data.return_value = {
        "hourly_consumption": [
            {"date_time": "2025-12-20T10:00:00-08:00", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
        ],
    }

    # Patch asyncio.wait_for to raise TimeoutError
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        # Should still succeed despite timeout - just logs warning
        await async_import_statistics(hass, mock_api_client, historical_days=30)

    # Statistics should still be added even if clear timed out
    assert mock_add_statistics.call_count == 2


async def test_import_statistics_with_invalid_quality_entries(
    hass: HomeAssistant,
    mock_recorder,
    mock_add_statistics,
) -> None:
    """Test that INVALID quality entries are skipped."""
    mock_recorder.async_add_executor_job.return_value = {}

    mock_api_client = AsyncMock()
    mock_api_client.get_consumption_data.return_value = {
        "hourly_consumption": [
            # INVALID entries should be skipped
            {"date_time": "2025-12-20T10:00:00-08:00", "value": 0.0, "cost": 0.0, "quality": "INVALID"},
            {"date_time": "2025-12-25T10:00:00-08:00", "value": 1.5, "cost": 0.15, "quality": "ACTUAL"},
        ]
    }

    await async_import_statistics(hass, mock_api_client, historical_days=7)

    # Should add statistics only from ACTUAL entry
    assert mock_add_statistics.call_count == 2


async def test_import_statistics_with_invalid_date_parsing(
    hass: HomeAssistant,
    mock_recorder,
    mock_add_statistics,
) -> None:
    """Test that entries with unparseable dates are skipped."""
    mock_recorder.async_add_executor_job.return_value = {}

    mock_api_client = AsyncMock()
    mock_api_client.get_consumption_data.return_value = {
        "hourly_consumption": [
            # Entry with invalid date format should be skipped
            {"date_time": "not-a-valid-date", "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
            {"date_time": "2025-12-25T10:00:00-08:00", "value": 1.5, "cost": 0.15, "quality": "ACTUAL"},
        ]
    }

    await async_import_statistics(hass, mock_api_client, historical_days=7)

    # Should add statistics only from valid entry
    assert mock_add_statistics.call_count == 2


async def test_import_statistics_with_none_date(
    hass: HomeAssistant,
    mock_recorder,
    mock_add_statistics,
) -> None:
    """Test that entries with None date are skipped."""
    mock_recorder.async_add_executor_job.return_value = {}

    mock_api_client = AsyncMock()
    mock_api_client.get_consumption_data.return_value = {
        "hourly_consumption": [
            # Entry with None date_time should be skipped
            {"date_time": None, "value": 1.0, "cost": 0.10, "quality": "ACTUAL"},
            {"value": 1.0, "cost": 0.10, "quality": "ACTUAL"},  # Missing date_time entirely
            {"date_time": "2025-12-25T10:00:00-08:00", "value": 1.5, "cost": 0.15, "quality": "ACTUAL"},
        ]
    }

    await async_import_statistics(hass, mock_api_client, historical_days=7)

    # Should add statistics only from valid entry
    assert mock_add_statistics.call_count == 2

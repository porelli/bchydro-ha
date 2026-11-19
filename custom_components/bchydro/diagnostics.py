"""Diagnostics support for BC Hydro."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import BC_TIME_ZONE, DOMAIN

TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    "cookies",
    "haprd1",
    "uid",
    "JSESSIONID",
    "bchydroparam",
    "evpAccountId",
    "evpAccount",
    "evpSlid",
    "evpProfileId",
    "evpCsrId",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data if coordinator.data else {}
    consumption = data.get("consumption", {})
    next_check = getattr(coordinator, "next_check", None)
    failing_since = getattr(coordinator, "failing_since", None)

    diagnostics_data: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "domain": entry.domain,
            "version": entry.version,
            "entry_id": entry.entry_id,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_update_success_time": (
                coordinator.last_update_success_time.isoformat()
                if coordinator.last_update_success_time
                else None
            ),
            "update_interval": str(coordinator.update_interval),
            # Which days "yesterday" covers depends on these (issue #6)
            "home_assistant_time_zone": str(hass.config.time_zone),
            "bc_time_zone": BC_TIME_ZONE,
            "update_mode": str(getattr(coordinator, "update_mode", None)),
            "next_check": next_check.isoformat() if isinstance(next_check, datetime) else None,
            "last_check_incomplete": bool(getattr(coordinator, "_check_incomplete", False)),
            "failing_since": (
                failing_since.isoformat() if isinstance(failing_since, datetime) else None
            ),
        },
        "data": {
            "profile": data.get("profile", {}),
            "consumption": {
                "current_date_time": data.get("consumption", {}).get(
                    "current_date_time"
                ),
                "daily_consumption_count": len(
                    data.get("consumption", {}).get("daily_consumption", [])
                ),
                "has_tier_consumption": "tier_consumption"
                in data.get("consumption", {}),
                "has_events": "events" in data.get("consumption", {}),
                "has_tps_details": "tps_details" in data.get("consumption", {}),
                # The newest days, with how many hours each one holds
                "sample_daily_data": consumption.get("daily_consumption", [])[-3:],
                # BC Hydro's newest hourly labels: their offset and quality show
                # how its days line up with BC time
                "latest_hourly": [
                    {
                        "date_time": point.get("date_time"),
                        "quality": point.get("quality"),
                        "value": point.get("value"),
                    }
                    for point in consumption.get("hourly_consumption", [])[-6:]
                ],
            },
            "arrival": data.get("arrival", {}),
        },
    }

    return async_redact_data(diagnostics_data, TO_REDACT)

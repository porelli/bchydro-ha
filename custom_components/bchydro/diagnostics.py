"""Diagnostics support for BC Hydro."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN

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
                # Include sample of daily data (redacted)
                "sample_daily_data": data.get("consumption", {})
                .get("daily_consumption", [])[:3],
            },
        },
    }

    return async_redact_data(diagnostics_data, TO_REDACT)

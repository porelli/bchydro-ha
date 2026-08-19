"""Support for BC Hydro sensors."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import EntityCategory
from homeassistant.const import (
    CURRENCY_DOLLAR,
    UnitOfEnergy,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_ACCOUNT_ID,
    ATTR_ACCOUNT_NUMBER,
    ATTR_ACCOUNT_TYPE,
    ATTR_BILLING_END,
    ATTR_BILLING_START,
    ATTR_DAILY_CONSUMPTION,
    ATTR_DAYS_IN_BILLING_PERIOD,
    ATTR_LAST_UPDATE,
    ATTR_PROFILE_ID,
    ATTR_RATE_CATEGORY,
    ATTR_RATE_GROUP,
    DOMAIN,
    PARALLEL_UPDATES,
    SENSOR_BILLING_PERCENTAGE,
    SENSOR_BILLING_PERIOD_START,
    SENSOR_CONSUMPTION_TO_DATE,
    SENSOR_COST_TO_DATE,
    SENSOR_DAYS_IN_BILLING_PERIOD,
    SENSOR_ESTIMATED_CONSUMPTION,
    SENSOR_ESTIMATED_COST,
    SENSOR_RATE_PLAN,
    SENSOR_YESTERDAY_CONSUMPTION,
    SENSOR_YESTERDAY_COST,
)
from .coordinator import BCHydroDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class BCHydroSensorEntityDescriptionMixin:
    """Mixin for required keys."""

    value_fn: Callable[[dict[str, Any]], StateType]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


@dataclass(kw_only=True)
class BCHydroSensorEntityDescription(
    SensorEntityDescription, BCHydroSensorEntityDescriptionMixin
):
    """Describes BC Hydro sensor entity."""


def parse_energy_value(value_str: str) -> float | None:
    """Parse energy value from string like '28 kWh'."""
    try:
        return float(value_str.replace(" kWh", "").replace(",", ""))
    except (ValueError, AttributeError):
        return None


def parse_cost_value(value_str: str) -> float | None:
    """Parse cost value from string like '$5' or '$5.23'."""
    try:
        return float(value_str.replace("$", "").replace(",", ""))
    except (ValueError, AttributeError):
        return None


def get_yesterday_data(data: dict[str, Any]) -> dict[str, Any] | None:
    """Get yesterday's consumption data."""
    consumption = data.get("consumption", {})
    daily_data = consumption.get("daily_consumption", [])

    if not daily_data:
        return None

    # Find yesterday's data (last ACTUAL quality entry)
    for entry in reversed(daily_data):
        if entry.get("quality") == "ACTUAL":
            return entry

    return None


def _parse_billing_date(date_str: str | None) -> datetime | None:
    """Parse billing date string to datetime.

    Handles formats:
    - ISO format: "2025-12-25T00:00:00-08:00"
    - Human format: "Dec 25, 2025"
    """
    if not date_str:
        return None

    try:
        # Try ISO format first
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        pass

    try:
        # Try "Dec 25, 2025" format
        from datetime import timezone, timedelta
        dt = datetime.strptime(date_str, "%b %d, %Y")
        # Add Pacific timezone (UTC-8)
        pacific_tz = timezone(timedelta(hours=-8))
        return dt.replace(tzinfo=pacific_tz)
    except (ValueError, TypeError):
        return None


SENSOR_TYPES: tuple[BCHydroSensorEntityDescription, ...] = (
    BCHydroSensorEntityDescription(
        key=SENSOR_CONSUMPTION_TO_DATE,
        translation_key="consumption_to_date",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:lightning-bolt",
        value_fn=lambda data: parse_energy_value(
            data.get("profile", {}).get("evpConsToDate", "0 kWh")
        ),
        attr_fn=lambda data: {
            ATTR_BILLING_START: data.get("profile", {}).get("evpBillingStart"),
            ATTR_BILLING_END: data.get("profile", {}).get("evpBillingEnd"),
        },
    ),
    BCHydroSensorEntityDescription(
        key=SENSOR_COST_TO_DATE,
        translation_key="cost_to_date",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:currency-usd",
        value_fn=lambda data: parse_cost_value(
            data.get("profile", {}).get("evpCostToDate", "$0")
        ),
        attr_fn=lambda data: {
            ATTR_BILLING_START: data.get("profile", {}).get("evpBillingStart"),
            ATTR_BILLING_END: data.get("profile", {}).get("evpBillingEnd"),
        },
    ),
    BCHydroSensorEntityDescription(
        key=SENSOR_ESTIMATED_CONSUMPTION,
        translation_key="estimated_consumption",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        icon="mdi:lightning-bolt-outline",
        value_fn=lambda data: parse_energy_value(
            data.get("profile", {}).get("evpEstConsCurPeriod", "0 kWh")
        ),
        attr_fn=lambda data: {
            ATTR_BILLING_START: data.get("profile", {}).get("evpBillingStart"),
            ATTR_BILLING_END: data.get("profile", {}).get("evpBillingEnd"),
        },
    ),
    BCHydroSensorEntityDescription(
        key=SENSOR_ESTIMATED_COST,
        translation_key="estimated_cost",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:currency-usd",
        value_fn=lambda data: parse_cost_value(
            data.get("profile", {}).get("evpEstCostCurPeriod", "$0")
        ),
        attr_fn=lambda data: {
            ATTR_BILLING_START: data.get("profile", {}).get("evpBillingStart"),
            ATTR_BILLING_END: data.get("profile", {}).get("evpBillingEnd"),
        },
    ),
    BCHydroSensorEntityDescription(
        key=SENSOR_YESTERDAY_CONSUMPTION,
        translation_key="yesterday_consumption",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:calendar-today",
        value_fn=lambda data: (
            yesterday.get("value")
            if (yesterday := get_yesterday_data(data))
            else None
        ),
        attr_fn=lambda data: (
            {
                "date": yesterday.get("date_time"),
                "cost": yesterday.get("cost"),
                "date_description": yesterday.get("date_description"),
            }
            if (yesterday := get_yesterday_data(data))
            else {}
        ),
    ),
    BCHydroSensorEntityDescription(
        key=SENSOR_YESTERDAY_COST,
        translation_key="yesterday_cost",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:calendar-today",
        value_fn=lambda data: (
            yesterday.get("cost")
            if (yesterday := get_yesterday_data(data))
            else None
        ),
        attr_fn=lambda data: (
            {
                "date": yesterday.get("date_time"),
                "consumption": yesterday.get("value"),
                "date_description": yesterday.get("date_description"),
            }
            if (yesterday := get_yesterday_data(data))
            else {}
        ),
    ),
    BCHydroSensorEntityDescription(
        key=SENSOR_RATE_PLAN,
        translation_key="rate_plan",
        icon="mdi:file-document-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("profile", {}).get("ratePlanName"),
        attr_fn=lambda data: {
            ATTR_RATE_CATEGORY: data.get("profile", {}).get("evpRateCategory"),
            ATTR_RATE_GROUP: data.get("profile", {}).get("evpRateGroup"),
            "rate_plan_type": data.get("profile", {}).get("ratePlanType"),
            "is_rib": data.get("profile", {}).get("isRIB"),
        },
    ),
    BCHydroSensorEntityDescription(
        key=SENSOR_BILLING_PERIOD_START,
        translation_key="billing_period_start",
        icon="mdi:calendar-range",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _parse_billing_date(
            data.get("profile", {}).get("evpBillingStart")
        ),
        attr_fn=lambda data: {
            ATTR_BILLING_START: data.get("profile", {}).get("evpBillingStart"),
            ATTR_BILLING_END: data.get("profile", {}).get("evpBillingEnd"),
            ATTR_DAYS_IN_BILLING_PERIOD: data.get("profile", {}).get(
                "evpDaysInBillingPeriod"
            ),
        },
    ),
    BCHydroSensorEntityDescription(
        key=SENSOR_DAYS_IN_BILLING_PERIOD,
        translation_key="days_in_billing_period",
        icon="mdi:calendar",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="days",
        value_fn=lambda data: data.get("profile", {}).get("evpDaysInBillingPeriod"),
    ),
    BCHydroSensorEntityDescription(
        key=SENSOR_BILLING_PERCENTAGE,
        translation_key="billing_percentage",
        icon="mdi:percent",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="%",
        value_fn=lambda data: data.get("profile", {}).get("yesterdayPercentage"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BC Hydro sensor based on a config entry."""
    coordinator: BCHydroDataUpdateCoordinator = entry.runtime_data

    async_add_entities(
        BCHydroSensor(coordinator, description, entry) for description in SENSOR_TYPES
    )


class BCHydroSensor(CoordinatorEntity[BCHydroDataUpdateCoordinator], SensorEntity):
    """Representation of a BC Hydro sensor."""

    entity_description: BCHydroSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BCHydroDataUpdateCoordinator,
        description: BCHydroSensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        # Accounts configured side by side need distinguishable device names: the
        # entity ids are derived from them. Entries without a name (a single
        # account, or one set up before names existed) keep plain "BC Hydro" so
        # their entity ids never change.
        account_name = entry.data.get("account_name")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"BC Hydro {account_name}" if account_name else "BC Hydro",
            "manufacturer": "BC Hydro",
            "model": "Energy Monitor",
        }

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the state attributes."""
        if self.coordinator.data is None:
            return None

        # Get attributes from the description's attr_fn
        attrs = {}
        if self.entity_description.attr_fn:
            attrs = self.entity_description.attr_fn(self.coordinator.data)

        # Add common attributes
        profile = self.coordinator.data.get("profile", {})
        attrs.update(
            {
                ATTR_ACCOUNT_ID: profile.get("evpAccountId"),
                ATTR_ACCOUNT_NUMBER: profile.get("evpAccount"),
                ATTR_PROFILE_ID: profile.get("evpProfileId"),
                ATTR_ACCOUNT_TYPE: profile.get("accountType"),
                ATTR_LAST_UPDATE: self.coordinator.last_update_success,
            }
        )

        # Add daily consumption data for relevant sensors
        if self.entity_description.key in [
            SENSOR_CONSUMPTION_TO_DATE,
            SENSOR_COST_TO_DATE,
        ]:
            consumption = self.coordinator.data.get("consumption", {})
            daily_data = consumption.get("daily_consumption", [])
            if daily_data:
                # Only include actual data (not invalid/estimated)
                actual_data = [
                    {
                        "date": d.get("date_time"),
                        "consumption": d.get("value"),
                        "cost": d.get("cost"),
                    }
                    for d in daily_data
                    if d.get("quality") == "ACTUAL"
                ]
                attrs[ATTR_DAILY_CONSUMPTION] = actual_data[:7]  # Last 7 days

            # Note: Hourly data is now stored in statistics database
            # and accessible via the Energy Dashboard (bchydro:energy_consumption)
            # No need to store it in attributes anymore

        return attrs

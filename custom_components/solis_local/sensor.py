"""Sensor platform: one SensorEntity per description, fed by the coordinator."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolisCoordinator
from .models import InverterSnapshot

SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="current_power_w",
        name="Current power",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
    ),
    SensorEntityDescription(
        key="yield_today_kwh",
        name="Yield today",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power",
    ),
    SensorEntityDescription(
        key="total_yield_kwh",
        name="Total yield",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
    ),
    SensorEntityDescription(
        key="inverter_temperature_c",
        name="Inverter temperature",
        native_unit_of_measurement="\u00b0C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
    ),
    SensorEntityDescription(
        key="inverter_model", name="Inverter model", icon="mdi:information-outline"
    ),
    SensorEntityDescription(
        key="firmware_version", name="Firmware version", icon="mdi:information-outline"
    ),
    SensorEntityDescription(
        key="serial_no", name="Serial number", icon="mdi:identifier"
    ),
    SensorEntityDescription(
        key="last_updated",
        name="Last updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
    ),
    SensorEntityDescription(
        key="data_age_s",
        name="Data age",
        native_unit_of_measurement="s",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timelapse",
    ),
)


class SolisSensor(CoordinatorEntity[SolisCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SolisCoordinator, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        device_id = coordinator.device_id
        self._attr_unique_id = f"{DOMAIN}_{device_id}_{description.key}"
        snapshot = coordinator.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=f"Solis Inverter {device_id}",
            manufacturer="Solis",
            model=snapshot.inverter_model if snapshot else None,
            sw_version=snapshot.firmware_version if snapshot else None,
        )

    @property
    def last_reset(self) -> datetime | None:
        """When the accumulating total was last re-zeroed (midnight reset).

        ``yield_today_kwh`` resets at local midnight (TOTAL_INCREASING "new
        meter cycle"); every other total (e.g. total yield) never resets, so
        ``last_reset`` stays ``None`` per the HA sensor contract.
        """
        if self.entity_description.key != "yield_today_kwh":
            return None
        snapshot: InverterSnapshot | None = self.coordinator.data
        return snapshot.last_reset if snapshot else None

    @property
    def native_value(self):
        snapshot: InverterSnapshot | None = self.coordinator.data
        if snapshot is None:
            return None
        value = getattr(snapshot, self.entity_description.key)
        if (
            self.entity_description.device_class == SensorDeviceClass.TIMESTAMP
            and value is not None
        ):
            return value.isoformat()
        return value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolisCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [SolisSensor(coordinator, description) for description in SENSOR_TYPES],
        update_before_add=True,
    )
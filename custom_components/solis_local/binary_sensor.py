"""Binary sensor platform: inverter online + alerts flag."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolisCoordinator

BINARY_SENSOR_TYPES: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="inverter_online",
        name="Inverter online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:power-plug",
    ),
    BinarySensorEntityDescription(
        key="alerts",
        name="Alerts",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:alert",
    ),
)


class SolisBinarySensor(CoordinatorEntity[SolisCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolisCoordinator,
        description: BinarySensorEntityDescription,
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
    def is_on(self) -> bool | None:
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        if self.entity_description.key == "inverter_online":
            return snapshot.inverter_online
        return snapshot.alerts


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolisCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SolisBinarySensor(coordinator, description)
            for description in BINARY_SENSOR_TYPES
        ],
        update_before_add=True,
    )
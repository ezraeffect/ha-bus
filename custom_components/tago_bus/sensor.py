"""Summary sensors: number of buses in service per direction."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TagoBusConfigEntry, TagoBusCoordinator
from .models import Direction


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TagoBusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [BusCountSensor(coordinator, None)]
    for meta in coordinator.routes.values():
        entities.extend(BusCountSensor(coordinator, d) for d in meta.directions)
    async_add_entities(entities)


class BusCountSensor(CoordinatorEntity[TagoBusCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:bus-multiple"
    _attr_native_unit_of_measurement = "대"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: TagoBusCoordinator, direction: Direction | None) -> None:
        super().__init__(coordinator)
        self._direction = direction
        entry = coordinator.config_entry
        if direction is None:
            self._attr_name = "운행 대수"
            self._attr_unique_id = f"{entry.entry_id}_count"
        else:
            self._attr_name = f"운행 대수 ({direction.first_stop} → {direction.last_stop})"
            self._attr_unique_id = f"{entry.entry_id}_count_{direction.index}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="국토교통부 TAGO",
            model=f"{coordinator.route_no}번 버스",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _vehicles(self):
        vehicles = (self.coordinator.data or {}).values()
        if self._direction is None:
            return list(vehicles)
        return [v for v in vehicles if v.direction.index == self._direction.index]

    @property
    def native_value(self) -> int:
        return len(self._vehicles())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "buses": [
                {
                    "vehicle_no": v.vehicle_no,
                    "direction": v.direction.label,
                    "current_stop": v.stop_name,
                    "next_stop": v.next_stop,
                    "latitude": v.latitude,
                    "longitude": v.longitude,
                }
                for v in sorted(self._vehicles(), key=lambda v: (v.direction.index, v.stop_order or 0))
            ]
        }

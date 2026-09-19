"""One geo_location entity per running bus; created and removed as buses come and go."""

from __future__ import annotations

from typing import Any

from datetime import datetime, timedelta

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util, slugify
from homeassistant.util.location import distance

from .const import DOMAIN, MAP_ENTITY_BUSES, SOURCE
from .coordinator import TagoBusConfigEntry, TagoBusCoordinator
from .marker import marker_url
from .models import BusVehicle, stops_until

# A bus can drop out of the API for a poll or two while still running. Removing
# its entity right away and recreating it moments later makes Home Assistant
# reject the entity id as still in use, so let it linger first.
REMOVAL_GRACE = timedelta(minutes=3)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TagoBusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: dict[str, BusLocationEvent] = {}
    missing_since: dict[str, datetime] = {}
    removing: set[str] = set()

    async def _remove(key: str, entity: BusLocationEvent) -> None:
        try:
            await entity.async_remove(force_remove=True)
        finally:
            removing.discard(key)

    @callback
    def _sync() -> None:
        if not coordinator.last_update_success or coordinator.data is None:
            return
        vehicles = coordinator.data.vehicles
        now = dt_util.utcnow()

        new: list[BusLocationEvent] = []
        for key, vehicle in vehicles.items():
            missing_since.pop(key, None)
            if entity := entities.get(key):
                entity.update_vehicle(vehicle)
            elif key not in removing:
                # Still being removed: pick the bus up on the next refresh.
                entities[key] = entity = BusLocationEvent(hass, coordinator, vehicle)
                new.append(entity)
        if new:
            async_add_entities(new)

        for key in [k for k in entities if k not in vehicles]:
            first_missing = missing_since.setdefault(key, now)
            if now - first_missing < REMOVAL_GRACE:
                continue
            entity = entities.pop(key)
            missing_since.pop(key, None)
            if entity.hass is not None:
                removing.add(key)
                hass.async_create_task(_remove(key, entity))

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class BusLocationEvent(GeolocationEvent):
    """A bus currently in service.

    Intentionally has no unique_id: buses are transient, so they should not
    pile up in the entity registry.
    """

    _attr_should_poll = False
    _attr_icon = "mdi:bus"
    _attr_source = SOURCE
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(
        self, hass: HomeAssistant, coordinator: TagoBusCoordinator, vehicle: BusVehicle
    ) -> None:
        self._home = (hass.config.latitude, hass.config.longitude)
        self._coordinator = coordinator
        digits = "".join(ch for ch in vehicle.vehicle_no if ch.isdigit())
        self.entity_id = f"geo_location.{DOMAIN}_{slugify(vehicle.route_no)}_{digits or slugify(vehicle.vehicle_no)}"
        self._apply(vehicle)

    def _apply(self, vehicle: BusVehicle) -> None:
        self._vehicle = vehicle
        self._attr_name = f"{vehicle.route_no}번 {vehicle.vehicle_no} ({vehicle.direction.label})"
        # Without standard coordinates HA's own map skips the bus; the tago_bus
        # card reads bus_latitude/bus_longitude instead.
        on_ha_map = MAP_ENTITY_BUSES in self._coordinator.map_entities
        self._attr_latitude = vehicle.latitude if on_ha_map else None
        self._attr_longitude = vehicle.longitude if on_ha_map else None
        meters = distance(self._home[0], self._home[1], vehicle.latitude, vehicle.longitude)
        self._attr_distance = round(meters / 1000, 2) if meters is not None else None
        self._attr_entity_picture = marker_url(vehicle.route_no, vehicle.direction.index)

    @callback
    def update_vehicle(self, vehicle: BusVehicle) -> None:
        self._apply(vehicle)
        if self.hass is not None:
            self.async_write_ha_state()

    def _favorite_stop(self) -> dict[str, Any] | None:
        v = self._vehicle
        stop = self._coordinator.favorites.get(v.route_id)
        if stop is None or (n := stops_until(v.stop_order, stop.station.order)) is None:
            return None
        return {"stop": stop.station.name, "direction": stop.direction.label, "stops_remaining": n}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        v = self._vehicle
        return {
            "vehicle_no": v.vehicle_no,
            "bus_latitude": v.latitude,
            "bus_longitude": v.longitude,
            "route_no": v.route_no,
            "route_id": v.route_id,
            "direction": v.direction.label,
            "direction_index": v.direction.index,
            "direction_first_stop": v.direction.first_stop,
            "direction_last_stop": v.direction.last_stop,
            "updown_code": v.direction.updown,
            "current_stop": v.stop_name,
            "current_stop_id": v.stop_id,
            "stop_order": v.stop_order,
            "next_stop": v.next_stop,
            "favorite_stop": self._favorite_stop(),
            "config_entry": self._coordinator.config_entry.title,
            "config_entry_id": self._coordinator.config_entry.entry_id,
        }

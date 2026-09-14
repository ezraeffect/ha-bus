"""Bus count sensors and favorite-stop arrival sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MAP_ENTITY_FAVORITE_STOPS
from .coordinator import TagoBusConfigEntry, TagoBusCoordinator
from .models import Arrival, BusVehicle, Direction, RouteMeta, WatchedStop


def arrival_unique_id(entry_id: str, route_id: str) -> str:
    return f"{entry_id}_favorite_arrival_{route_id}"


def stops_unique_id(entry_id: str, route_id: str) -> str:
    return f"{entry_id}_favorite_stops_{route_id}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TagoBusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [BusCountSensor(coordinator, None)]
    for meta in coordinator.routes.values():
        entities.extend(BusCountSensor(coordinator, d) for d in meta.directions)
    # One pair per route variant, so changing the favorite stop keeps entity ids.
    for meta in coordinator.routes.values():
        entities.append(ArrivalMinutesSensor(coordinator, meta))
        entities.append(StopsRemainingSensor(coordinator, meta))
    async_add_entities(entities)


class TagoBusEntity(CoordinatorEntity[TagoBusCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: TagoBusCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="국토교통부 TAGO",
            model=f"{coordinator.route_no}번 버스",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _vehicles(self) -> list[BusVehicle]:
        data = self.coordinator.data
        return list(data.vehicles.values()) if data else []


class BusCountSensor(TagoBusEntity):
    _attr_icon = "mdi:bus-multiple"
    _attr_native_unit_of_measurement = "대"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: TagoBusCoordinator, direction: Direction | None) -> None:
        super().__init__(coordinator)
        self._direction = direction
        entry_id = coordinator.config_entry.entry_id
        if direction is None:
            self._attr_name = "운행 대수"
            self._attr_unique_id = f"{entry_id}_count"
        else:
            self._attr_name = f"운행 대수 ({direction.first_stop} → {direction.last_stop})"
            self._attr_unique_id = f"{entry_id}_count_{direction.index}"

    def _selected(self) -> list[BusVehicle]:
        if self._direction is None:
            return self._vehicles
        return [v for v in self._vehicles if v.direction.index == self._direction.index]

    @property
    def native_value(self) -> int:
        return len(self._selected())

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
                for v in sorted(self._selected(), key=lambda v: (v.direction.index, v.stop_order or 0))
            ]
        }


class FavoriteStopSensor(TagoBusEntity):
    def __init__(self, coordinator: TagoBusCoordinator, meta: RouteMeta) -> None:
        super().__init__(coordinator)
        self._route_id = meta.info.route_id
        self._suffix = (
            f" ({meta.info.start_stop} → {meta.info.end_stop})"
            if len(coordinator.routes) > 1
            else ""
        )

    @property
    def _stop(self) -> WatchedStop | None:
        return self.coordinator.favorites.get(self._route_id)

    @property
    def _arrivals(self) -> list[Arrival] | None:
        """None when there is no favorite or the arrival API could not be queried."""
        data, stop = self.coordinator.data, self._stop
        if not data or not stop:
            return None
        return data.arrivals.get(stop.key)

    @property
    def _approaching(self) -> list[tuple[int, BusVehicle]]:
        data, stop = self.coordinator.data, self._stop
        return data.approaching(stop) if data and stop else []

    def _base_attributes(self) -> dict[str, Any]:
        stop = self._stop
        if stop is None:
            return {"route_id": self._route_id, "favorite_stop": None}
        station = stop.station
        # Standard latitude/longitude would put the sensor on HA's own map.
        prefix = "" if MAP_ENTITY_FAVORITE_STOPS in self.coordinator.map_entities else "stop_"
        return {
            "route_id": self._route_id,
            "favorite_stop": station.name,
            "stop_id": station.node_id,
            "stop_order": station.order,
            "direction": stop.direction.label,
            f"{prefix}latitude": station.latitude,
            f"{prefix}longitude": station.longitude,
            "approaching_buses": [
                {"vehicle_no": v.vehicle_no, "stops_remaining": n, "current_stop": v.stop_name}
                for n, v in self._approaching
            ],
            "arrival_api_available": self._arrivals is not None,
        }


class ArrivalMinutesSensor(FavoriteStopSensor):
    """Minutes until the next bus at the favorite stop, from the arrival API."""

    _attr_icon = "mdi:bus-clock"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: TagoBusCoordinator, meta: RouteMeta) -> None:
        super().__init__(coordinator, meta)
        self._attr_name = f"즐겨찾기 도착 예정{self._suffix}"
        self._attr_unique_id = arrival_unique_id(coordinator.config_entry.entry_id, self._route_id)

    @property
    def native_value(self) -> int | None:
        arrivals = self._arrivals
        return arrivals[0].minutes if arrivals else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        arrivals = self._arrivals or []
        attrs = self._base_attributes()
        attrs["arrivals"] = [
            {
                "minutes": a.minutes,
                "seconds": a.seconds,
                "stops_remaining": a.stops_remaining,
                "vehicle_type": a.vehicle_type,
            }
            for a in arrivals
        ]
        if arrivals:
            attrs["seconds"] = arrivals[0].seconds
            attrs["stops_remaining"] = arrivals[0].stops_remaining
            attrs["vehicle_type"] = arrivals[0].vehicle_type
        if len(arrivals) > 1:
            attrs["next_minutes"] = arrivals[1].minutes
            attrs["next_stops_remaining"] = arrivals[1].stops_remaining
        return attrs


class StopsRemainingSensor(FavoriteStopSensor):
    """Stops until the next bus: arrival API if available, else bus positions."""

    _attr_icon = "mdi:bus-stop"
    _attr_native_unit_of_measurement = "정거장"

    def __init__(self, coordinator: TagoBusCoordinator, meta: RouteMeta) -> None:
        super().__init__(coordinator, meta)
        self._attr_name = f"즐겨찾기 남은 정거장{self._suffix}"
        self._attr_unique_id = stops_unique_id(coordinator.config_entry.entry_id, self._route_id)

    @property
    def native_value(self) -> int | None:
        arrivals = self._arrivals
        if arrivals and arrivals[0].stops_remaining is not None:
            return arrivals[0].stops_remaining
        approaching = self._approaching
        return approaching[0][0] if approaching else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = self._base_attributes()
        arrivals = self._arrivals
        if self._stop is not None:
            attrs["source"] = (
                "arrival_api"
                if arrivals and arrivals[0].stops_remaining is not None
                else "bus_location"
            )
        return attrs

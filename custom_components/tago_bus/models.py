"""Data models and pure parsing helpers (no Home Assistant imports)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def extract_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the item list from a TAGO JSON response.

    TAGO returns `items: ""` when empty and a bare object when there is a
    single item, so normalize everything to a list.
    """
    body = (data.get("response") or {}).get("body") or {}
    items = body.get("items")
    if not isinstance(items, dict):
        return []
    item = items.get("item")
    if item is None:
        return []
    return item if isinstance(item, list) else [item]


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class RouteInfo:
    """A route variant returned by the route-number search."""

    route_id: str
    route_no: str
    route_type: str
    start_stop: str
    end_stop: str
    first_time: str
    last_time: str

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> RouteInfo:
        return cls(
            route_id=str(item.get("routeid", "")),
            route_no=str(item.get("routeno", "")),
            route_type=str(item.get("routetp", "")),
            start_stop=str(item.get("startnodenm", "")),
            end_stop=str(item.get("endnodenm", "")),
            first_time=str(item.get("startvehicletime", "")),
            last_time=str(item.get("endvehicletime", "")),
        )

    @property
    def label(self) -> str:
        return f"{self.route_no}번 {self.start_stop} → {self.end_stop} ({self.route_id})"


@dataclass(slots=True)
class Station:
    node_id: str
    name: str
    order: int
    updown: str | None


@dataclass(slots=True)
class Direction:
    """A run of consecutive stations sharing the same up/down code."""

    index: int  # global index across all selected routes, used for colors
    updown: str | None
    first_stop: str
    last_stop: str

    @property
    def label(self) -> str:
        return f"{self.last_stop} 방면"


@dataclass(slots=True)
class RouteMeta:
    info: RouteInfo
    stations: dict[int, Station] = field(default_factory=dict)
    # station order -> Direction
    directions_by_order: dict[int, Direction] = field(default_factory=dict)
    directions: list[Direction] = field(default_factory=list)


def build_route_meta(
    info: RouteInfo, station_items: list[dict[str, Any]], first_index: int
) -> RouteMeta:
    """Build station lookup and direction groups for one route.

    Some cities publish one route id per direction, others one id with an
    `updowncd` per station. Grouping consecutive stations by `updowncd`
    handles both: a split route simply ends up with a single group.
    """
    meta = RouteMeta(info=info)
    for item in station_items:
        order = _to_int(item.get("nodeord"))
        if order is None:
            continue
        updown = item.get("updowncd")
        meta.stations[order] = Station(
            node_id=str(item.get("nodeid", "")),
            name=str(item.get("nodenm", "")),
            order=order,
            updown=None if updown in (None, "") else str(updown),
        )

    current: Direction | None = None
    for order in sorted(meta.stations):
        station = meta.stations[order]
        if current is None or station.updown != current.updown:
            current = Direction(
                index=first_index + len(meta.directions),
                updown=station.updown,
                first_stop=station.name,
                last_stop=station.name,
            )
            meta.directions.append(current)
        current.last_stop = station.name
        meta.directions_by_order[order] = current

    if not meta.directions:
        meta.directions.append(
            Direction(first_index, None, info.start_stop, info.end_stop)
        )
    return meta


@dataclass(slots=True)
class BusVehicle:
    key: str
    vehicle_no: str
    route_id: str
    route_no: str
    latitude: float
    longitude: float
    stop_id: str
    stop_name: str
    stop_order: int | None
    next_stop: str | None
    direction: Direction


def parse_vehicle(item: dict[str, Any], meta: RouteMeta) -> BusVehicle | None:
    """Convert a bus location item; returns None if it has no coordinates."""
    lat = _to_float(item.get("gpslati"))
    lon = _to_float(item.get("gpslong"))
    vehicle_no = str(item.get("vehicleno", "")).strip()
    if lat is None or lon is None or not vehicle_no or (lat == 0 and lon == 0):
        return None

    order = _to_int(item.get("nodeord"))
    direction = meta.directions_by_order.get(order) if order is not None else None
    if direction is None:
        direction = meta.directions[0]

    next_stop = None
    if order is not None and (nxt := meta.stations.get(order + 1)):
        next_stop = nxt.name

    route_id = meta.info.route_id
    return BusVehicle(
        key=f"{route_id}_{vehicle_no}",
        vehicle_no=vehicle_no,
        route_id=route_id,
        route_no=str(item.get("routenm") or meta.info.route_no),
        latitude=lat,
        longitude=lon,
        stop_id=str(item.get("nodeid", "")),
        stop_name=str(item.get("nodenm", "")),
        stop_order=order,
        next_stop=next_stop,
        direction=direction,
    )

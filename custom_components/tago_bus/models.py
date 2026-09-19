"""Data models and pure parsing helpers (no Home Assistant imports)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ERROR_AUTH = "auth"
ERROR_QUOTA = "quota"
ERROR_BUSY = "busy"
ERROR_OTHER = "other"

# data.go.kr result codes and the English strings its gateway returns.
_AUTH_CODES = ("30", "31", "32")
_AUTH_TEXTS = (
    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
    "SERVICE ACCESS DENIED ERROR",
    "UNREGISTERED",
    "DEADLINE_HAS_EXPIRED_ERROR",
)
_QUOTA_CODES = ("22",)
_QUOTA_TEXTS = ("LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",)
# "가용한 세션이 존재하지 않습니다" means every session for this key is busy.
_BUSY_TEXTS = ("가용한 세션", "SERVICE IS NOT AVAILABLE", "일시적", "점검")


def classify_error(code: str, message: str) -> str:
    """Sort an API error into auth / quota / temporary / other."""
    code = (code or "").strip()
    upper = (message or "").upper()
    if code in _AUTH_CODES or any(t in upper for t in _AUTH_TEXTS):
        return ERROR_AUTH
    if code in _QUOTA_CODES or any(t in upper for t in _QUOTA_TEXTS):
        return ERROR_QUOTA
    if any(t.upper() in upper for t in _BUSY_TEXTS):
        return ERROR_BUSY
    return ERROR_OTHER


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
    latitude: float | None = None
    longitude: float | None = None


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
            latitude=_to_float(item.get("gpslati")),
            longitude=_to_float(item.get("gpslong")),
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
class WatchedStop:
    """The favorite stop of one route variant (at most one per route id)."""

    key: str  # "<route_id>:<order>"
    route_id: str
    station: Station
    direction: Direction

    @property
    def label(self) -> str:
        return f"{self.station.name} ({self.direction.label})"


def stop_key(route_id: str, order: int) -> str:
    return f"{route_id}:{order}"


def parse_stop_key(key: str) -> tuple[str, int] | None:
    route_id, _, order_text = key.rpartition(":")
    order = _to_int(order_text)
    if not route_id or order is None:
        return None
    return route_id, order


def resolve_favorites(
    favorites: dict[str, int], routes: dict[str, RouteMeta]
) -> tuple[dict[str, WatchedStop], dict[str, int]]:
    """Map {route_id: station order} to stations; returns (resolved, unknown)."""
    resolved: dict[str, WatchedStop] = {}
    unknown: dict[str, int] = {}
    for route_id, order in favorites.items():
        meta = routes.get(route_id)
        station = meta.stations.get(order) if meta else None
        if meta is None or station is None:
            unknown[route_id] = order
            continue
        resolved[route_id] = WatchedStop(
            key=stop_key(route_id, order),
            route_id=route_id,
            station=station,
            direction=meta.directions_by_order.get(order, meta.directions[0]),
        )
    return resolved, unknown


Point = tuple[float, float]  # (lat, lon)


def direction_segments(meta: RouteMeta) -> list[tuple[int, list[Point]]]:
    """Stations split into per-direction polylines.

    Each segment also includes the first station of the next direction, so the
    drawn line stays continuous where the direction changes.
    """
    stations = [
        s
        for s in (meta.stations[o] for o in sorted(meta.stations))
        if s.latitude is not None and s.longitude is not None
    ]
    segments: list[tuple[int, list[Point]]] = []
    start = 0
    for i in range(1, len(stations) + 1):
        index = meta.directions_by_order[stations[start].order].index
        if i == len(stations) or meta.directions_by_order[stations[i].order].index != index:
            chunk = stations[start : min(i + 1, len(stations))]
            if len(chunk) > 1:
                segments.append((index, [(s.latitude, s.longitude) for s in chunk]))
            start = i
    return segments


def chunk_points(points: list[Point], size: int) -> list[list[Point]]:
    """Split waypoints into overlapping chunks (last point repeats as next first)."""
    if len(points) < 2:
        return []
    step = max(size - 1, 1)
    return [points[i : i + size] for i in range(0, len(points) - 1, step)]


@dataclass(slots=True)
class Arrival:
    stops_remaining: int | None
    seconds: int | None
    vehicle_type: str

    @property
    def minutes(self) -> int | None:
        return None if self.seconds is None else self.seconds // 60


def parse_arrivals(items: list[dict[str, Any]], route_id: str) -> list[Arrival]:
    """Arrivals of one route at a stop, soonest first."""
    arrivals = [
        Arrival(
            stops_remaining=_to_int(item.get("arrprevstationcnt")),
            seconds=_to_int(item.get("arrtime")),
            vehicle_type=str(item.get("vehicletp", "")),
        )
        for item in items
        if str(item.get("routeid", "")) == route_id
    ]
    arrivals.sort(key=lambda a: (a.seconds is None, a.seconds or 0))
    return arrivals


def stops_until(bus_order: int | None, stop_order: int) -> int | None:
    """Stops left before a bus at `bus_order` reaches `stop_order`.

    Only counts forward along the same route variant; a bus that has already
    passed the stop returns None.
    """
    if bus_order is None or bus_order > stop_order:
        return None
    return stop_order - bus_order


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


@dataclass(slots=True)
class TagoBusData:
    vehicles: dict[str, BusVehicle] = field(default_factory=dict)
    # watched stop key -> arrivals; missing key means arrival info unavailable
    arrivals: dict[str, list[Arrival]] = field(default_factory=dict)

    def approaching(self, stop: WatchedStop) -> list[tuple[int, BusVehicle]]:
        """Buses heading to a watched stop by local position, nearest first."""
        found = [
            (stops, v)
            for v in self.vehicles.values()
            if v.route_id == stop.route_id
            and (stops := stops_until(v.stop_order, stop.station.order)) is not None
        ]
        found.sort(key=lambda pair: pair[0])
        return found

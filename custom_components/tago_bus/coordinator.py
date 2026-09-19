"""Polling coordinator for bus locations and watched-stop arrivals."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TagoApi, TagoAuthError, TagoBusyError, TagoError, TagoQuotaError
from .const import (
    CONF_CITY_CODE,
    CONF_FAVORITES,
    CONF_MAP_ENTITIES,
    CONF_ROAD_GEOMETRY,
    CONF_ROUTE_IDS,
    CONF_ROUTE_NO,
    CONF_SCAN_INTERVAL,
    DEFAULT_MAP_ENTITIES,
    DEFAULT_ROAD_GEOMETRY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    IDLE_SCAN_INTERVAL,
    LOGGER,
    STATION_REFRESH_HOURS,
)
from .geometry import GeometryProvider, Segment
from .models import (
    Arrival,
    BusVehicle,
    RouteInfo,
    RouteMeta,
    TagoBusData,
    WatchedStop,
    build_route_meta,
    parse_arrivals,
    parse_vehicle,
    resolve_favorites,
)

type TagoBusConfigEntry = ConfigEntry[TagoBusCoordinator]

# The arrival API can be unavailable for hours; say so once, not every poll.
ARRIVAL_ERROR_LOG_INTERVAL = timedelta(hours=1)


class TagoBusCoordinator(DataUpdateCoordinator[TagoBusData]):
    """Fetches positions of every bus on the configured route variants."""

    config_entry: TagoBusConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TagoBusConfigEntry,
        api: TagoApi,
        geometry: GeometryProvider,
    ) -> None:
        self.scan_interval: int = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self.road_geometry: bool = entry.options.get(CONF_ROAD_GEOMETRY, DEFAULT_ROAD_GEOMETRY)
        self.map_entities: frozenset[str] = frozenset(
            entry.options.get(CONF_MAP_ENTITIES, DEFAULT_MAP_ENTITIES)
        )
        self._interval = timedelta(seconds=self.scan_interval)
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=self._interval,
        )
        self.api = api
        self.city_code: str = entry.data[CONF_CITY_CODE]
        self.route_no: str = entry.data[CONF_ROUTE_NO]
        self.route_ids: list[str] = entry.data[CONF_ROUTE_IDS]
        self.routes: dict[str, RouteMeta] = {}
        self.favorites: dict[str, WatchedStop] = {}  # route_id -> stop
        self.segments: dict[str, list[Segment]] = {}  # route_id -> drawn lines
        self.geometry_ready = False
        self.arrival_error: str | None = None
        self._arrival_error_logged: datetime | None = None
        self._geometry = geometry
        self._stations_loaded: datetime | None = None

    @property
    def watched_stops(self) -> list[WatchedStop]:
        return list(self.favorites.values())

    def apply_favorites(self) -> None:
        """Re-read favorites from options without reloading the entry."""
        self.favorites, unknown = resolve_favorites(
            {k: int(v) for k, v in self.config_entry.options.get(CONF_FAVORITES, {}).items()},
            self.routes,
        )
        if unknown:
            LOGGER.warning("Favorite stops no longer on the route, set them again: %s", unknown)

    async def _load_geometry(self) -> None:
        segments: dict[str, list[Segment]] = {}
        for route_id, meta in self.routes.items():
            segments[route_id] = await self._geometry.async_segments(meta, self.road_geometry)
            # Publish progressively so straight lines are replaced as soon as possible.
            self.segments = {**self.segments, **segments}
        self.geometry_ready = True

    async def _async_setup(self) -> None:
        await self._load_routes()

    async def _load_routes(self) -> None:
        try:
            infos = {
                r.route_id: r
                for r in await self.api.search_routes(self.city_code, self.route_no)
            }
            station_lists = await asyncio.gather(
                *(self.api.get_stations(self.city_code, rid) for rid in self.route_ids)
            )
        except TagoError as err:
            raise UpdateFailed(f"노선 정보를 불러오지 못했습니다: {err}") from err

        routes: dict[str, RouteMeta] = {}
        next_index = 0
        for rid, stations in zip(self.route_ids, station_lists, strict=True):
            info = infos.get(rid) or RouteInfo(rid, self.route_no, "", "", "", "", "")
            meta = build_route_meta(info, stations, next_index)
            next_index += len(meta.directions)
            routes[rid] = meta
        self.routes = routes
        self._stations_loaded = dt_util.utcnow()
        self.apply_favorites()

        self.geometry_ready = False
        self.config_entry.async_create_background_task(
            self.hass, self._load_geometry(), f"{DOMAIN} route geometry"
        )

    async def _async_update_data(self) -> TagoBusData:
        if self._stations_loaded is None or dt_util.utcnow() - self._stations_loaded > timedelta(
            hours=STATION_REFRESH_HOURS
        ):
            try:
                await self._load_routes()
            except UpdateFailed as err:
                if not self.routes:
                    raise
                LOGGER.warning("Keeping cached station list: %s", err)

        # The API client allows one request at a time (session limit), so there
        # is nothing to gain from running these concurrently.
        locations = await self._fetch_locations()
        arrivals = await self._fetch_arrivals()
        # Back off while nothing is running (e.g. overnight).
        self.update_interval = (
            self._interval if locations else timedelta(seconds=IDLE_SCAN_INTERVAL)
        )
        return TagoBusData(vehicles=locations, arrivals=arrivals)

    async def _fetch_locations(self) -> dict[str, BusVehicle]:
        try:
            results = [
                await self.api.get_bus_locations(self.city_code, rid)
                for rid in self.route_ids
            ]
        except TagoAuthError as err:
            raise UpdateFailed(
                f"API 키 인증 실패 (발급 직후라면 1~2시간 뒤 다시 시도): {err}"
            ) from err
        except TagoQuotaError as err:
            self.update_interval = timedelta(seconds=IDLE_SCAN_INTERVAL)
            raise UpdateFailed(f"일일 호출 한도를 초과했습니다: {err}") from err
        except TagoBusyError as err:
            raise UpdateFailed(
                f"공공데이터포털이 잠시 응답하지 않습니다 (동시 접속 제한). 다음 주기에 다시 시도합니다: {err}"
            ) from err
        except TagoError as err:
            raise UpdateFailed(str(err)) from err

        vehicles: dict[str, BusVehicle] = {}
        for rid, items in zip(self.route_ids, results, strict=True):
            meta = self.routes[rid]
            for item in items:
                if vehicle := parse_vehicle(item, meta):
                    vehicles[vehicle.key] = vehicle
        return vehicles

    async def _fetch_arrivals(self) -> dict[str, list[Arrival]]:
        """Arrival API is optional: failures only drop minute estimates."""
        if not self.watched_stops:
            return {}

        node_ids = list(dict.fromkeys(s.station.node_id for s in self.watched_stops))
        by_node: dict[str, list[dict] | TagoError] = {}
        for node_id in node_ids:
            try:
                by_node[node_id] = await self.api.get_arrivals(self.city_code, node_id)
            except TagoError as err:
                by_node[node_id] = err

        arrivals: dict[str, list[Arrival]] = {}
        error: TagoError | None = None
        for stop in self.watched_stops:
            result = by_node[stop.station.node_id]
            if isinstance(result, TagoError):
                error = result
                continue
            arrivals[stop.key] = parse_arrivals(result, stop.route_id)

        self._log_arrival_error(error)
        return arrivals

    def _log_arrival_error(self, error: TagoError | None) -> None:
        """Warn on the first failure, then at most once an hour."""
        if error is None:
            if self.arrival_error:
                LOGGER.info("도착정보 API 조회가 복구되었습니다")
            self.arrival_error = None
            self._arrival_error_logged = None
            return

        self.arrival_error = str(error)
        now = dt_util.utcnow()
        if (
            self._arrival_error_logged is not None
            and now - self._arrival_error_logged < ARRIVAL_ERROR_LOG_INTERVAL
        ):
            return
        self._arrival_error_logged = now
        if isinstance(error, TagoBusyError):
            LOGGER.warning(
                "도착정보 API가 일시적으로 응답하지 않습니다 (동시 접속 제한). "
                "남은 정거장은 버스 위치로 계산합니다: %s",
                error,
            )
        else:
            LOGGER.warning(
                "도착정보 API 조회 실패 (버스도착정보 API 활용신청 여부를 확인하세요). "
                "남은 정거장은 버스 위치로 계산합니다: %s",
                error,
            )

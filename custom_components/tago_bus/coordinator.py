"""Polling coordinator for bus locations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TagoApi, TagoAuthError, TagoError, TagoQuotaError
from .const import (
    CONF_CITY_CODE,
    CONF_ROUTE_IDS,
    CONF_ROUTE_NO,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    IDLE_SCAN_INTERVAL,
    LOGGER,
    STATION_REFRESH_HOURS,
)
from .models import BusVehicle, RouteInfo, RouteMeta, build_route_meta, parse_vehicle

type TagoBusConfigEntry = ConfigEntry[TagoBusCoordinator]


class TagoBusCoordinator(DataUpdateCoordinator[dict[str, BusVehicle]]):
    """Fetches positions of every bus on the configured route variants."""

    config_entry: TagoBusConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: TagoBusConfigEntry, api: TagoApi
    ) -> None:
        self._interval = timedelta(
            seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
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
        self._stations_loaded: datetime | None = None

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

    async def _async_update_data(self) -> dict[str, BusVehicle]:
        if self._stations_loaded is None or dt_util.utcnow() - self._stations_loaded > timedelta(
            hours=STATION_REFRESH_HOURS
        ):
            try:
                await self._load_routes()
            except UpdateFailed as err:
                if not self.routes:
                    raise
                LOGGER.warning("Keeping cached station list: %s", err)

        try:
            results = await asyncio.gather(
                *(
                    self.api.get_bus_locations(self.city_code, rid)
                    for rid in self.route_ids
                )
            )
        except TagoAuthError as err:
            raise UpdateFailed(
                f"API 키 인증 실패 (발급 직후라면 1~2시간 뒤 다시 시도): {err}"
            ) from err
        except TagoQuotaError as err:
            self.update_interval = timedelta(seconds=IDLE_SCAN_INTERVAL)
            raise UpdateFailed(f"일일 호출 한도를 초과했습니다: {err}") from err
        except TagoError as err:
            raise UpdateFailed(str(err)) from err

        vehicles: dict[str, BusVehicle] = {}
        for rid, items in zip(self.route_ids, results, strict=True):
            meta = self.routes[rid]
            for item in items:
                if vehicle := parse_vehicle(item, meta):
                    vehicles[vehicle.key] = vehicle

        # Back off while nothing is running (e.g. overnight).
        self.update_interval = (
            self._interval if vehicles else timedelta(seconds=IDLE_SCAN_INTERVAL)
        )
        return vehicles

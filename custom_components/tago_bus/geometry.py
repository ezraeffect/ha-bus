"""Road-following route lines via OSRM, cached on disk.

TAGO only publishes stop coordinates, so connecting them directly cuts
across blocks. OSRM snaps the stop sequence to the road network. Each route
shape is fetched once and kept in `.storage`; if routing fails or looks
implausible, that part falls back to straight lines.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from dataclasses import dataclass
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, LOGGER, OSRM_ROUTE_URL, VERSION
from .models import Point, RouteMeta, chunk_points, direction_segments

STORAGE_VERSION = 1
CHUNK_SIZE = 25
REQUEST_GAP = 1.1  # the demo server allows about one request per second
# A road path much longer than hopping stop to stop means OSRM took a detour
# (usually a wrongly snapped stop), so keep the straight line there.
MAX_DETOUR_RATIO = 2.5


@dataclass(slots=True)
class Segment:
    direction_index: int
    coordinates: list[Point]
    road: bool


def _haversine(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12_742_000 * math.asin(math.sqrt(h))


def _path_length(points: list[Point]) -> float:
    return sum(_haversine(p, q) for p, q in zip(points, points[1:]))


def _cache_key(points: list[Point]) -> str:
    raw = ";".join(f"{lat:.5f},{lon:.5f}" for lat, lon in points)
    return hashlib.sha1(raw.encode()).hexdigest()


class GeometryProvider:
    def __init__(self, hass: HomeAssistant) -> None:
        self._session = async_get_clientsession(hass)
        self._store: Store[dict[str, list[list[float]]]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.geometry"
        )
        self._cache: dict[str, list[list[float]]] | None = None
        self._lock = asyncio.Lock()

    async def async_segments(self, meta: RouteMeta, use_roads: bool) -> list[Segment]:
        straight = [Segment(i, pts, False) for i, pts in direction_segments(meta)]
        if not use_roads:
            return straight

        async with self._lock:
            if self._cache is None:
                self._cache = await self._store.async_load() or {}
            result: list[Segment] = []
            dirty = False
            for segment in straight:
                key = _cache_key(segment.coordinates)
                cached = self._cache.get(key)
                if cached is None:
                    coords = await self._fetch(segment.coordinates)
                    if coords is None:
                        result.append(segment)
                        continue
                    cached = [[lat, lon] for lat, lon in coords]
                    self._cache[key] = cached
                    dirty = True
                result.append(
                    Segment(segment.direction_index, [(p[0], p[1]) for p in cached], True)
                )
            if dirty:
                await self._store.async_save(self._cache)
            return result

    async def _fetch(self, points: list[Point]) -> list[Point] | None:
        """Road path through all points; None if every chunk failed."""
        path: list[Point] = []
        any_road = False
        for n, chunk in enumerate(chunk_points(points, CHUNK_SIZE)):
            if n:
                await asyncio.sleep(REQUEST_GAP)
            road = await self._route(chunk)
            if road and _path_length(road) <= _path_length(chunk) * MAX_DETOUR_RATIO:
                any_road = True
                # Anchor the ends to the actual stops so segments join cleanly.
                piece = [chunk[0], *road, chunk[-1]]
            else:
                piece = chunk
            path.extend(piece if not path else piece[1:])
        return path if any_road else None

    async def _route(self, points: list[Point]) -> list[Point] | None:
        coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
        try:
            async with self._session.get(
                f"{OSRM_ROUTE_URL}{coords}",
                params={"overview": "full", "geometries": "geojson"},
                headers={"User-Agent": f"HomeAssistant-{DOMAIN}/{VERSION}"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    LOGGER.debug("OSRM HTTP %s", resp.status)
                    return None
                data: dict[str, Any] = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            LOGGER.debug("OSRM request failed: %s", err)
            return None
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        line = data["routes"][0]["geometry"]["coordinates"]
        return [(round(lat, 6), round(lon, 6)) for lon, lat in line]

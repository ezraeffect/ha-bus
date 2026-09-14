"""Async client for the Ministry of Land (TAGO) bus open APIs on data.go.kr."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

import aiohttp

from .models import RouteInfo, extract_items

BASE_URL = "https://apis.data.go.kr/1613000"
ROUTE_SERVICE = "BusRouteInfoInqireService"
LOCATION_SERVICE = "BusLcInfoInqireService"
ARRIVAL_SERVICE = "ArvlInfoInqireService"

AUTH_ERRORS = (
    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
    "SERVICE ACCESS DENIED ERROR",
    "UNREGISTERED",
    "DEADLINE_HAS_EXPIRED_ERROR",
)
QUOTA_ERRORS = ("LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",)


class TagoError(Exception):
    """Generic API error."""


class TagoAuthError(TagoError):
    """Service key is invalid or not yet activated."""


class TagoQuotaError(TagoError):
    """Daily request quota exceeded."""


class TagoConnectionError(TagoError):
    """Network problem."""


def normalize_service_key(key: str) -> str:
    """Accept both the 'Encoding' and 'Decoding' keys from data.go.kr."""
    key = key.strip()
    return unquote(key) if "%" in key else key


class TagoApi:
    def __init__(self, session: aiohttp.ClientSession, service_key: str) -> None:
        self._session = session
        self._key = normalize_service_key(service_key)

    async def _request(
        self, service: str, operation: str, **params: Any
    ) -> list[dict[str, Any]]:
        query = {
            "serviceKey": self._key,
            "_type": "json",
            "pageNo": 1,
            "numOfRows": 1000,
            **params,
        }
        try:
            async with self._session.get(
                f"{BASE_URL}/{service}/{operation}",
                params=query,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                text = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TagoConnectionError(str(err)) from err

        if status in (401, 403):
            raise TagoAuthError(f"HTTP {status}: {text[:200]}")

        # Gateway errors are XML even when JSON is requested.
        if text.lstrip().startswith("<") or status >= 400:
            raise _error_from_text(text, status)

        try:
            data = json.loads(text)
        except ValueError as err:
            raise _error_from_text(text, status) from err

        header = (data.get("response") or {}).get("header") or {}
        code = str(header.get("resultCode", "00"))
        if code not in ("00", "0"):
            msg = str(header.get("resultMsg", ""))
            if code in ("30", "31", "32") or any(e in msg for e in AUTH_ERRORS):
                raise TagoAuthError(msg)
            if code == "22" or any(e in msg for e in QUOTA_ERRORS):
                raise TagoQuotaError(msg)
            raise TagoError(f"{code}: {msg}")
        return extract_items(data)

    async def get_cities(self) -> list[dict[str, str]]:
        items = await self._request(ROUTE_SERVICE, "getCtyCodeList")
        return [
            {"code": str(i.get("citycode")), "name": str(i.get("cityname"))}
            for i in items
        ]

    async def search_routes(self, city_code: str, route_no: str) -> list[RouteInfo]:
        """Find routes whose number matches exactly (the API does prefix matching)."""
        items = await self._request(
            ROUTE_SERVICE, "getRouteNoList", cityCode=city_code, routeNo=route_no
        )
        routes = [RouteInfo.from_item(i) for i in items]
        return [r for r in routes if r.route_no == route_no.strip()]

    async def get_stations(self, city_code: str, route_id: str) -> list[dict[str, Any]]:
        return await self._request(
            ROUTE_SERVICE,
            "getRouteAcctoThrghSttnList",
            cityCode=city_code,
            routeId=route_id,
        )

    async def get_bus_locations(
        self, city_code: str, route_id: str
    ) -> list[dict[str, Any]]:
        return await self._request(
            LOCATION_SERVICE,
            "getRouteAcctoBusLcList",
            cityCode=city_code,
            routeId=route_id,
        )

    async def get_arrivals(self, city_code: str, node_id: str) -> list[dict[str, Any]]:
        """Arrival predictions for every route at a stop."""
        return await self._request(
            ARRIVAL_SERVICE,
            "getSttnAcctoArvlPrearngeInfoList",
            cityCode=city_code,
            nodeId=node_id,
        )


def _error_from_text(text: str, status: int) -> TagoError:
    match = re.search(r"<returnAuthMsg>([^<]+)</returnAuthMsg>", text) or re.search(
        r"<(?:errMsg|resultMsg)>([^<]+)</", text
    )
    msg = match.group(1) if match else f"HTTP {status}: {text[:200]}"
    if any(e in text for e in AUTH_ERRORS):
        return TagoAuthError(msg)
    if any(e in text for e in QUOTA_ERRORS):
        return TagoQuotaError(msg)
    return TagoError(msg)

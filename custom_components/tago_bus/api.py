"""Async client for the Ministry of Land (TAGO) bus open APIs on data.go.kr."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import unquote

import aiohttp

from .const import LOGGER
from .models import ERROR_AUTH, ERROR_BUSY, ERROR_QUOTA, RouteInfo, classify_error, extract_items

BASE_URL = "https://apis.data.go.kr/1613000"
ROUTE_SERVICE = "BusRouteInfoInqireService"
LOCATION_SERVICE = "BusLcInfoInqireService"
ARRIVAL_SERVICE = "ArvlInfoInqireService"

# data.go.kr counts open connections per service key and rejects requests past
# its session limit, so keep at most one request in flight and let the server
# close the connection instead of keeping it pooled.
MAX_CONCURRENT_REQUESTS = 1
RETRY_DELAYS = (1.0, 3.0)


class TagoError(Exception):
    """Generic API error."""


class TagoAuthError(TagoError):
    """Service key is invalid or not yet activated."""


class TagoQuotaError(TagoError):
    """Daily request quota exceeded."""


class TagoBusyError(TagoError):
    """Temporary server-side limit (session limit, maintenance); retryable."""


class TagoConnectionError(TagoError):
    """Network problem."""


ERROR_CLASSES = {
    ERROR_AUTH: TagoAuthError,
    ERROR_QUOTA: TagoQuotaError,
    ERROR_BUSY: TagoBusyError,
}


def normalize_service_key(key: str) -> str:
    """Accept both the 'Encoding' and 'Decoding' keys from data.go.kr."""
    key = key.strip()
    return unquote(key) if "%" in key else key


class TagoApi:
    def __init__(self, session: aiohttp.ClientSession, service_key: str) -> None:
        self._session = session
        self._key = normalize_service_key(service_key)
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def _request(
        self, service: str, operation: str, **params: Any
    ) -> list[dict[str, Any]]:
        """One API call, retried while the server reports a temporary limit."""
        for attempt, delay in enumerate((*RETRY_DELAYS, None)):
            try:
                async with self._semaphore:
                    return await self._request_once(service, operation, **params)
            except (TagoBusyError, TagoConnectionError) as err:
                if delay is None:
                    raise
                LOGGER.debug(
                    "%s failed (attempt %s), retrying in %ss: %s",
                    operation,
                    attempt + 1,
                    delay,
                    err,
                )
                await asyncio.sleep(delay)
        raise TagoError(f"{operation}: 재시도에 실패했습니다")  # unreachable

    async def _request_once(
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
                headers={"Connection": "close"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                text = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TagoConnectionError(f"{operation}: {err or type(err).__name__}") from err

        if status in (401, 403):
            raise TagoAuthError(f"{operation}: HTTP {status}")
        if status in (429, 500, 502, 503, 504):
            raise TagoBusyError(f"{operation}: HTTP {status}")

        # Gateway errors are XML even when JSON is requested.
        if text.lstrip().startswith("<") or status >= 400:
            raise _error_from_text(operation, text, status)

        try:
            data = json.loads(text)
        except ValueError as err:
            raise _error_from_text(operation, text, status) from err

        header = (data.get("response") or {}).get("header") or {}
        code = str(header.get("resultCode", "00"))
        if code not in ("00", "0"):
            message = str(header.get("resultMsg", "")).strip()
            kind = classify_error(code, message)
            error = ERROR_CLASSES.get(kind, TagoError)
            raise error(f"{operation}: [{code}] {message or '메시지 없음'}")
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


def _error_from_text(operation: str, text: str, status: int) -> TagoError:
    match = re.search(r"<returnAuthMsg>([^<]+)</returnAuthMsg>", text) or re.search(
        r"<(?:errMsg|resultMsg)>([^<]+)</", text
    )
    code_match = re.search(r"<(?:returnReasonCode|resultCode)>([^<]+)</", text)
    message = (match.group(1) if match else text.strip()[:200]) or f"HTTP {status}"
    code = code_match.group(1).strip() if code_match else ""
    error = ERROR_CLASSES.get(classify_error(code, f"{code} {message} {text}"), TagoError)
    return error(f"{operation}: {message}")

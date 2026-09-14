"""Serves small SVG map markers (route number on a direction-colored circle)."""

from __future__ import annotations

import re
from html import escape
from urllib.parse import urlencode

from aiohttp import web

from homeassistant.components.http import HomeAssistantView

from .const import DIRECTION_COLORS, MARKER_URL

_HEX = re.compile(r"^[0-9a-fA-F]{6}$")


def marker_url(label: str, direction_index: int) -> str:
    color = DIRECTION_COLORS[direction_index % len(DIRECTION_COLORS)]
    return f"{MARKER_URL}?{urlencode({'label': label, 'color': color})}"


class BusMarkerView(HomeAssistantView):
    """Unauthenticated on purpose: the map loads it as a CSS background image,
    which cannot carry an auth header. It only renders the given text."""

    url = MARKER_URL
    name = "api:tago_bus:marker"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        label = escape(request.query.get("label", "")[:5])
        color = request.query.get("color", DIRECTION_COLORS[0])
        if not _HEX.match(color):
            color = DIRECTION_COLORS[0]
        font_size = 44 if len(label) <= 2 else 34 if len(label) <= 3 else 26
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            f'<circle cx="50" cy="50" r="50" fill="#{color}"/>'
            '<text x="50" y="52" text-anchor="middle" dominant-baseline="central" '
            'font-family="Arial,sans-serif" font-weight="700" '
            f'font-size="{font_size}" fill="#fff">{label}</text></svg>'
        )
        return web.Response(
            text=svg,
            content_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

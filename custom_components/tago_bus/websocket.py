"""WebSocket API for the map card: route geometry and favorite stops."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CONF_FAVORITES, DIRECTION_COLORS, DOMAIN
from .coordinator import TagoBusCoordinator
from .models import direction_segments
from .sensor import arrival_unique_id, stops_unique_id


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_routes)
    websocket_api.async_register_command(hass, ws_set_favorite)


def _loaded_entries(hass: HomeAssistant, entry_id: str | None) -> list[ConfigEntry]:
    return [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED and (not entry_id or e.entry_id == entry_id)
    ]


@websocket_api.websocket_command(
    {vol.Required("type"): "tago_bus/routes", vol.Optional("entry_id"): str}
)
@callback
def ws_routes(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    registry = er.async_get(hass)
    connection.send_result(
        msg["id"],
        [_serialize(registry, e) for e in _loaded_entries(hass, msg.get("entry_id"))],
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "tago_bus/set_favorite",
        vol.Required("entry_id"): str,
        vol.Required("route_id"): str,
        # null clears the favorite for this route
        vol.Required("order"): vol.Any(None, vol.Coerce(int)),
    }
)
@websocket_api.async_response
async def ws_set_favorite(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    entries = _loaded_entries(hass, msg["entry_id"])
    if not entries:
        connection.send_error(msg["id"], "not_found", "노선 설정을 찾을 수 없습니다")
        return
    entry = entries[0]
    coordinator: TagoBusCoordinator = entry.runtime_data
    route_id, order = msg["route_id"], msg["order"]
    meta = coordinator.routes.get(route_id)
    if meta is None or (order is not None and order not in meta.stations):
        connection.send_error(msg["id"], "not_found", "정류장을 찾을 수 없습니다")
        return

    favorites = dict(entry.options.get(CONF_FAVORITES, {}))
    if order is None:
        favorites.pop(route_id, None)
    else:
        favorites[route_id] = order
    # The options listener sees only favorites changed and skips the reload.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_FAVORITES: favorites}
    )
    coordinator.apply_favorites()
    coordinator.async_update_listeners()
    await coordinator.async_request_refresh()
    connection.send_result(msg["id"], _serialize(er.async_get(hass), entry))


def _color(index: int) -> str:
    return f"#{DIRECTION_COLORS[index % len(DIRECTION_COLORS)]}"


def _serialize(registry: er.EntityRegistry, entry: ConfigEntry) -> dict[str, Any]:
    coordinator: TagoBusCoordinator = entry.runtime_data
    routes = []
    for route_id, meta in coordinator.routes.items():
        segments = coordinator.segments.get(route_id)
        if segments is None:
            lines = [
                {"direction_index": i, "coordinates": pts, "road": False}
                for i, pts in direction_segments(meta)
            ]
        else:
            lines = [
                {"direction_index": s.direction_index, "coordinates": s.coordinates, "road": s.road}
                for s in segments
            ]
        favorite = coordinator.favorites.get(route_id)
        routes.append(
            {
                "route_id": route_id,
                "route_no": meta.info.route_no,
                "label": meta.info.label,
                "start_stop": meta.info.start_stop,
                "end_stop": meta.info.end_stop,
                "directions": [
                    {
                        "index": d.index,
                        "label": d.label,
                        "first_stop": d.first_stop,
                        "last_stop": d.last_stop,
                        "color": _color(d.index),
                    }
                    for d in meta.directions
                ],
                "stations": [
                    {
                        "order": s.order,
                        "name": s.name,
                        "node_id": s.node_id,
                        "latitude": s.latitude,
                        "longitude": s.longitude,
                        "direction_index": meta.directions_by_order[s.order].index,
                    }
                    for s in sorted(meta.stations.values(), key=lambda s: s.order)
                    if s.latitude is not None and s.longitude is not None
                ],
                "lines": lines,
                "favorite_order": favorite.station.order if favorite else None,
                "arrival_entity_id": registry.async_get_entity_id(
                    "sensor", DOMAIN, arrival_unique_id(entry.entry_id, route_id)
                ),
                "stops_entity_id": registry.async_get_entity_id(
                    "sensor", DOMAIN, stops_unique_id(entry.entry_id, route_id)
                ),
            }
        )
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "route_no": coordinator.route_no,
        "geometry_ready": coordinator.geometry_ready,
        "routes": routes,
    }

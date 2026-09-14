"""TAGO bus location integration: real-time city bus positions on the map."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.hass_dict import HassKey

from . import websocket
from .api import TagoApi
from .const import (
    CARD_URL,
    CONF_MAP_ENTITIES,
    CONF_ROAD_GEOMETRY,
    CONF_SCAN_INTERVAL,
    DEFAULT_MAP_ENTITIES,
    DEFAULT_ROAD_GEOMETRY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    VERSION,
)
from .coordinator import TagoBusConfigEntry, TagoBusCoordinator
from .geometry import GeometryProvider
from .marker import BusMarkerView

PLATFORMS = [Platform.GEO_LOCATION, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARD_PATH = Path(__file__).parent / "frontend" / "tago-bus-map-card.js"

GEOMETRY_KEY: HassKey[GeometryProvider] = HassKey(f"{DOMAIN}_geometry")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data[GEOMETRY_KEY] = GeometryProvider(hass)
    hass.http.register_view(BusMarkerView())
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), True)]
    )
    # Version query busts the browser cache after an update.
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    websocket.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TagoBusConfigEntry) -> bool:
    api = TagoApi(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    coordinator = TagoBusCoordinator(hass, entry, api, hass.data[GEOMETRY_KEY])
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TagoBusConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: TagoBusConfigEntry) -> None:
    coordinator = entry.runtime_data
    options = entry.options
    if (
        options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL) != coordinator.scan_interval
        or options.get(CONF_ROAD_GEOMETRY, DEFAULT_ROAD_GEOMETRY) != coordinator.road_geometry
        or frozenset(options.get(CONF_MAP_ENTITIES, DEFAULT_MAP_ENTITIES)) != coordinator.map_entities
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        return
    # Only favorites changed: apply live so bus entities don't flicker.
    coordinator.apply_favorites()
    coordinator.async_update_listeners()
    await coordinator.async_request_refresh()

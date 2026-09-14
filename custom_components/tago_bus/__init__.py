"""TAGO bus location integration: real-time city bus positions on the map."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import TagoApi
from .const import DOMAIN
from .coordinator import TagoBusConfigEntry, TagoBusCoordinator
from .marker import BusMarkerView

PLATFORMS = [Platform.GEO_LOCATION, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.http.register_view(BusMarkerView())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TagoBusConfigEntry) -> bool:
    api = TagoApi(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    coordinator = TagoBusCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TagoBusConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: TagoBusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

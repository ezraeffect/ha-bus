"""Constants for the TAGO bus location integration."""

from __future__ import annotations

import logging

DOMAIN = "tago_bus"
LOGGER = logging.getLogger(__package__)

# Source name used by geo_location entities (map card: geo_location_sources).
SOURCE = DOMAIN

CONF_CITY_CODE = "city_code"
CONF_CITY_NAME = "city_name"
CONF_ROUTE_NO = "route_no"
CONF_ROUTE_IDS = "route_ids"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_CITY_CODE = "34010"  # 충청남도 천안시
DEFAULT_SCAN_INTERVAL = 30  # seconds
MIN_SCAN_INTERVAL = 15
MAX_SCAN_INTERVAL = 600

# Poll less often while no bus is running (night time), to save API quota.
IDLE_SCAN_INTERVAL = 300  # seconds

# Station lists rarely change; refresh them once a day.
STATION_REFRESH_HOURS = 24

MARKER_URL = "/api/tago_bus/marker"

# Marker colors per direction (blue, orange, green, purple).
DIRECTION_COLORS = ("1e88e5", "f4511e", "43a047", "8e24aa")

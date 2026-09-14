"""Config flow: API key → city → route number → route variants."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .api import TagoApi, TagoAuthError, TagoConnectionError, TagoError, TagoQuotaError
from .const import (
    CONF_CITY_CODE,
    CONF_CITY_NAME,
    CONF_ROUTE_IDS,
    CONF_ROUTE_NO,
    CONF_SCAN_INTERVAL,
    DEFAULT_CITY_CODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .models import RouteInfo


def _error_key(err: TagoError) -> str:
    if isinstance(err, TagoAuthError):
        return "invalid_auth"
    if isinstance(err, TagoQuotaError):
        return "quota_exceeded"
    if isinstance(err, TagoConnectionError):
        return "cannot_connect"
    return "unknown"


class TagoBusConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str = ""
        self._api: TagoApi | None = None
        self._cities: dict[str, str] = {}
        self._city_code: str = DEFAULT_CITY_CODE
        self._route_no: str = ""
        self._routes: list[RouteInfo] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._api_key = user_input[CONF_API_KEY].strip()
            self._api = TagoApi(async_get_clientsession(self.hass), self._api_key)
            try:
                cities = await self._api.get_cities()
            except TagoError as err:
                LOGGER.debug("City list request failed: %s", err)
                errors["base"] = _error_key(err)
            else:
                self._cities = {c["code"]: c["name"] for c in cities}
                return await self.async_step_city()

        # Reuse the key from an existing entry so adding more routes is quick.
        default_key = next(
            (e.data[CONF_API_KEY] for e in self._async_current_entries()), ""
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_API_KEY, default=default_key): TextSelector()}
            ),
            errors=errors,
        )

    async def async_step_city(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._city_code = user_input[CONF_CITY_CODE]
            self._route_no = user_input[CONF_ROUTE_NO].strip()
            return await self.async_step_route()

        options = [
            SelectOptionDict(value=code, label=f"{name} ({code})")
            for code, name in sorted(self._cities.items(), key=lambda c: c[1])
        ]
        default_city = DEFAULT_CITY_CODE if DEFAULT_CITY_CODE in self._cities else vol.UNDEFINED
        return self.async_show_form(
            step_id="city",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CITY_CODE, default=default_city): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    ),
                    vol.Required(CONF_ROUTE_NO): TextSelector(),
                }
            ),
        )

    async def async_step_route(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._api is not None

        if user_input is None:
            try:
                self._routes = await self._api.search_routes(
                    self._city_code, self._route_no
                )
            except TagoError as err:
                LOGGER.debug("Route search failed: %s", err)
                return self.async_abort(reason=_error_key(err))
            if not self._routes:
                return self.async_abort(
                    reason="route_not_found",
                    description_placeholders={"route_no": self._route_no},
                )
        else:
            route_ids: list[str] = user_input[CONF_ROUTE_IDS]
            if not route_ids:
                errors["base"] = "no_route_selected"
            else:
                await self.async_set_unique_id(f"{self._city_code}_{self._route_no}")
                self._abort_if_unique_id_configured()
                city_name = self._cities.get(self._city_code, self._city_code)
                return self.async_create_entry(
                    title=f"{city_name} {self._route_no}번",
                    data={
                        CONF_API_KEY: self._api_key,
                        CONF_CITY_CODE: self._city_code,
                        CONF_CITY_NAME: city_name,
                        CONF_ROUTE_NO: self._route_no,
                        CONF_ROUTE_IDS: route_ids,
                    },
                )

        options = [SelectOptionDict(value=r.route_id, label=r.label) for r in self._routes]
        return self.async_show_form(
            step_id="route",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ROUTE_IDS, default=[r.route_id for r in self._routes]
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "route_no": self._route_no,
                "count": str(len(self._routes)),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TagoBusOptionsFlow()


class TagoBusOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=5,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )

"""Config flow + options flow for the Solis Inverter Local integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api.local import probe_datalogger
from .const import (
    CONF_DATALOGGER_IP,
    CONF_DATALOGGER_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_REFRESH_MODE,
    CONF_REFRESH_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_REFRESH_MODE,
    DEFAULT_REFRESH_TIMEOUT,
    DOMAIN,
    REFRESH_MODES,
)


def _range(minimum: int, maximum: int):
    return vol.All(vol.Coerce(int), vol.Range(min=minimum, max=maximum))


class SolisLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the datalogger connection."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_DATALOGGER_IP])
            self._abort_if_unique_id_configured()

            result = await probe_datalogger(
                user_input[CONF_DATALOGGER_IP],
                user_input[CONF_DATALOGGER_PASSWORD],
            )
            if result == "ok":
                return self.async_create_entry(
                    title=user_input[CONF_DATALOGGER_IP], data=user_input
                )
            errors["base"] = "invalid_auth" if result == "invalid_auth" else "cannot_connect"

        data_schema = vol.Schema(
            {
                vol.Required(CONF_DATALOGGER_IP): str,
                vol.Required(CONF_DATALOGGER_PASSWORD): str,
                vol.Optional(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): _range(
                    60, 3600
                ),
                vol.Optional(CONF_REFRESH_TIMEOUT, default=DEFAULT_REFRESH_TIMEOUT): _range(
                    1, 330
                ),
                vol.Optional(CONF_REFRESH_MODE, default=DEFAULT_REFRESH_MODE): vol.In(
                    REFRESH_MODES
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SolisLocalOptionsFlow(config_entry)


class SolisLocalOptionsFlow(OptionsFlow):
    """Options flow: tune the poll cadence without re-entering the password."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # The IP may have changed; validate the (possibly new) address with
            # a live probe before saving. A blank password field keeps the
            # currently active one (merged data+options, as in setup).
            current = {**self._entry.data, **self._entry.options}
            stored_password = current.get(CONF_DATALOGGER_PASSWORD, "")
            password = user_input.get(CONF_DATALOGGER_PASSWORD) or stored_password

            result = await probe_datalogger(user_input[CONF_DATALOGGER_IP], password)
            if result == "ok":
                options = {
                    CONF_DATALOGGER_IP: user_input[CONF_DATALOGGER_IP],
                    CONF_POLL_INTERVAL: user_input[CONF_POLL_INTERVAL],
                    CONF_REFRESH_TIMEOUT: user_input[CONF_REFRESH_TIMEOUT],
                    CONF_REFRESH_MODE: user_input[CONF_REFRESH_MODE],
                }
                # Persist a new password only when one was typed; blank keeps
                # the existing one (stored in entry.data) via the data+options
                # merge in async_setup_entry.
                if user_input.get(CONF_DATALOGGER_PASSWORD):
                    options[CONF_DATALOGGER_PASSWORD] = user_input[
                        CONF_DATALOGGER_PASSWORD
                    ]
                return self.async_create_entry(title="", data=options)
            errors["base"] = "invalid_auth" if result == "invalid_auth" else "cannot_connect"

        data = {**self._entry.data, **self._entry.options}
        options_schema = vol.Schema(
            {
                vol.Required(
                    CONF_DATALOGGER_IP, default=data.get(CONF_DATALOGGER_IP)
                ): str,
                # Never pre-fill the stored password; blank means "keep current".
                vol.Optional(CONF_DATALOGGER_PASSWORD, default=""): str,
                vol.Optional(
                    CONF_POLL_INTERVAL, default=data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
                ): _range(60, 3600),
                vol.Optional(
                    CONF_REFRESH_TIMEOUT, default=data.get(CONF_REFRESH_TIMEOUT, DEFAULT_REFRESH_TIMEOUT)
                ): _range(1, 330),
                vol.Optional(
                    CONF_REFRESH_MODE, default=data.get(CONF_REFRESH_MODE, DEFAULT_REFRESH_MODE)
                ): vol.In(REFRESH_MODES),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=options_schema, errors=errors
        )
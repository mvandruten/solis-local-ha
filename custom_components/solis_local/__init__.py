"""Solis Inverter Local -- Home Assistant integration.

Fully local: polls the datalogger's /inverter.cgi page over the LAN
(watch / reboot / none refresh modes). No SolisCloud account, no API keys,
works with no internet. Set up via the UI config flow.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CONF_DATALOGGER_IP,
    CONF_DATALOGGER_PASSWORD,
    CONF_POLL_INTERVAL,
    DOMAIN,
    SERVICE_FORCE_REFRESH,
)
from .coordinator import SolisCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ("sensor", "binary_sensor")


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change.

    Without this, options edits (including a changed datalogger IP) would only
    take effect on the next HA restart. Reloading rebuilds the coordinator with
    the merged data+options config.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solis Inverter Local from a config entry."""
    config = {**entry.data, **entry.options}
    coordinator = SolisCoordinator(
        hass,
        config[CONF_DATALOGGER_IP],
        config[CONF_DATALOGGER_PASSWORD],
        config,
    )
    await coordinator.start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Rebuild the coordinator whenever options are saved (new IP, cadence, ...)
    # so the change applies without a restart.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # First refresh in the background: in watch mode a populated read can take
    # a full stick cycle (~5 min) and must never block HA startup. Entities
    # appear immediately and fill in when the first data lands.
    hass.async_create_task(coordinator.async_refresh())

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_FORCE_REFRESH):
        async def _force_refresh(call: ServiceCall) -> None:
            """Re-read the local status page now (all configured dataloggers)."""
            for coord in hass.data.get(DOMAIN, {}).values():
                await coord.async_refresh()

        hass.services.async_register(
            DOMAIN, SERVICE_FORCE_REFRESH, _force_refresh, schema=vol.Schema({})
        )

    _LOGGER.debug(
        "solis_local set up for %s (poll interval %ss)",
        config[CONF_DATALOGGER_IP],
        config.get(CONF_POLL_INTERVAL),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
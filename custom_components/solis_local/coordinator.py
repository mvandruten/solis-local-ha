"""DataUpdateCoordinator driving one fully-local poll cycle: read the cgi.

The datalogger serves /inverter.cgi from a RAM buffer that only holds
populated data for ~1 second around each read; the rest of the time it
returns an all-zero placeholder (that is its normal idle state). The stick
repeats its inverter read roughly every 5 minutes on its own:

* ``watch`` (default): no kick -- we wait for the stick's own read loop and
  catch the populated window (deadline auto-extends to one full cycle).
* ``reboot``: GET /restart.cgi makes the stick re-read the inverter on boot
  (~25 s) -- a local kick that costs a reboot.
* ``none``: single best-effort read, give up fast.

The stick is powered by the inverter: at night it is unreachable. After a
few failed reads we stop, carry the last-known values (or, once local
midnight has passed, a day-reset snapshot with the daily counters zeroed),
and flip ``inverter_online`` off -- the local probe IS the source of truth
here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.local import read_inverter_cgi, restart_datalogger
from .const import (
    CONF_POLL_INTERVAL,
    CONF_REFRESH_MODE,
    CONF_REFRESH_TIMEOUT,
    DEFAULT_REFRESH_MODE,
    DOMAIN,
)
from .models import InverterSnapshot, is_populated, reset_for_new_day
from .parser import parse_inverter_cgi

_LOGGER = logging.getLogger(__name__)

RETRY_STEP_S = 0.5
# A reboot kick takes the stick down for ~15-25 s; give the read loop room.
REBOOT_WAIT_S = 90.0
# The stick runs its own ~5-min read loop and serves the populated buffer only
# ~1 s per cycle; watch mode waits one full cycle (plus margin) for it.
WATCH_WAIT_S = 330.0
# Kick-less local modes give up after this many consecutive unreachable reads
# (the stick is powered by the inverter: unreachable = inverter asleep).
MAX_DOWN_ATTEMPTS = 3


class SolisCoordinator(DataUpdateCoordinator[InverterSnapshot | None]):
    """Poll the datalogger on a fixed interval; ``data`` is the latest snapshot."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        datalogger_password: str,
        config: Mapping[str, Any],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=config[CONF_POLL_INTERVAL]),
        )
        self._host = host
        self._datalogger_password = datalogger_password
        self._config = config
        self._lan_session: aiohttp.ClientSession | None = None

    @property
    def device_id(self) -> str:
        if self.data is not None and self.data.serial_no:
            return self.data.serial_no
        return "unknown"

    async def start(self) -> None:
        self._lan_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=8)
        )

    async def close(self) -> None:
        if self._lan_session is not None:
            await self._lan_session.close()

    async def _read_populated(
        self, refresh_timeout_s: float, give_up_if_down: bool = False
    ) -> InverterSnapshot | None:
        """Read the cgi, retrying while it serves the empty placeholder.

        ``give_up_if_down`` (kick-less local modes): when the stick is
        persistently unreachable (night -- it is powered by the inverter),
        stop after a few attempts instead of burning the whole window.
        """
        assert self._lan_session is not None
        deadline = time.monotonic() + refresh_timeout_s
        last: InverterSnapshot | None = None
        saw_server = False
        down_attempts = 0
        while True:
            try:
                raw = await read_inverter_cgi(
                    self._lan_session, self._host, self._datalogger_password
                )
                saw_server = True
                down_attempts = 0
                snapshot = parse_inverter_cgi(raw, read_at=datetime.now().astimezone())
                if is_populated(snapshot):
                    return snapshot
                last = snapshot
            except aiohttp.ClientError:
                down_attempts += 1
                if (
                    give_up_if_down
                    and not saw_server
                    and down_attempts >= MAX_DOWN_ATTEMPTS
                ):
                    return None
            if time.monotonic() >= deadline:
                return last
            await asyncio.sleep(RETRY_STEP_S)

    async def _async_update_data(self) -> InverterSnapshot | None:
        now = datetime.now().astimezone()
        previous = self.data
        mode = str(self._config.get(CONF_REFRESH_MODE, DEFAULT_REFRESH_MODE)).lower()

        stale = False
        timeout_s = float(self._config[CONF_REFRESH_TIMEOUT])
        give_up_if_down = mode in ("none", "watch")
        if mode == "reboot":
            # Fully local kick: reboot the stick, it re-reads the inverter on
            # boot and repopulates the cgi (~25 s). No cloud call at all.
            if not await restart_datalogger(
                self._lan_session, self._host, self._datalogger_password
            ):
                stale = True
            timeout_s = max(timeout_s, REBOOT_WAIT_S)
        else:
            # Kick-less ("none", "watch"): wait for the stick's own read loop
            # to repopulate the buffer (one full cycle as the outer bound).
            timeout_s = max(timeout_s, WATCH_WAIT_S)

        snapshot = await self._read_populated(
            timeout_s, give_up_if_down=give_up_if_down
        )
        if snapshot is None:
            if previous is not None:
                # First poll after local midnight with no fresh read: the day
                # counters restart at zero instead of carrying yesterday's
                # values forward until the morning read. TOTAL_INCREASING
                # sensors (yield today) document a daily reset as the start of
                # a new meter cycle, so HA's statistics handle this cleanly.
                reset = reset_for_new_day(previous, now)
                if reset is not None:
                    return reset
                return self._aged(previous, now, stale=True)
            raise UpdateFailed("cgi unreachable and no previous data available")

        # Fully local mode, no cloud status: the probe is the truth.
        online = is_populated(snapshot)

        age = 0.0
        if stale and previous is not None and previous.last_updated is not None:
            age = max(0.0, (now - previous.last_updated).total_seconds())
        return InverterSnapshot(
            serial_no=snapshot.serial_no,
            firmware_version=snapshot.firmware_version,
            inverter_model=snapshot.inverter_model,
            inverter_temperature_c=snapshot.inverter_temperature_c,
            current_power_w=snapshot.current_power_w,
            yield_today_kwh=snapshot.yield_today_kwh,
            total_yield_kwh=snapshot.total_yield_kwh,
            alerts=snapshot.alerts,
            inverter_online=online,
            last_updated=snapshot.last_updated,
            raw=snapshot.raw,
            stale=stale or not is_populated(snapshot),
            data_age_s=age,
        )

    def _aged(
        self, previous: InverterSnapshot, now: datetime, stale: bool
    ) -> InverterSnapshot:
        """Carry the last-known values forward when the probe is unreachable."""
        age = 0.0
        if previous.last_updated is not None:
            age = max(0.0, (now - previous.last_updated).total_seconds())
        return InverterSnapshot(
            serial_no=previous.serial_no,
            firmware_version=previous.firmware_version,
            inverter_model=previous.inverter_model,
            inverter_temperature_c=previous.inverter_temperature_c,
            current_power_w=previous.current_power_w,
            yield_today_kwh=previous.yield_today_kwh,
            total_yield_kwh=previous.total_yield_kwh,
            alerts=previous.alerts,
            inverter_online=False,
            last_updated=previous.last_updated,
            raw=previous.raw,
            stale=True,
            data_age_s=age,
        )
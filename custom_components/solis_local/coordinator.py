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

``inverter_online`` reflects reachability -- the cgi answered at all,
placeholder body or not. ``stale`` means the snapshot is not a fresh
readout (aged carry-forward / midnight reset), never "incomplete read".
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
from .models import (
    InverterSnapshot,
    finalize_readout,
    is_placeholder,
    merge_readout_fields,
    reset_for_new_day,
)
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


def _readout_complete(snapshot: InverterSnapshot) -> bool:
    """Nothing left that a later read in the window could backfill.

    ``total_yield_kwh`` is excluded: the raw field is the literal ``u``
    (genuinely unknown on this device), so no poll cadence will ever fill it.
    """
    return (
        snapshot.serial_no != ""
        and snapshot.firmware_version not in ("", "0")
        and snapshot.inverter_model not in ("", "0")
        and snapshot.inverter_temperature_c not in (None, 0.0)
        and snapshot.current_power_w not in (None, 0)
        and snapshot.yield_today_kwh not in (None, 0.0)
    )


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
    ) -> tuple[InverterSnapshot | None, bool]:
        """Collect one populated window; return ``(snapshot, reachable)``.

        The stick serves the populated buffer for only ~1.6 s per ~5-min
        cycle and fills it field-by-field (identity -> power -> temp ->
        serial). We start collecting the moment ANY real field appears and
        keep unioning 0.5 s reads until the stick returns to its all-zero
        placeholder -- the device's own "window closed" signal (no fixed
        settle count). ``reachable`` is True whenever the cgi answered at
        all this cycle: that is what ``inverter_online`` means, placeholder
        body or not. The placeholder read that closes the window is never
        merged. A deadline mid-window serves whatever was collected.
        """
        assert self._lan_session is not None
        deadline = time.monotonic() + refresh_timeout_s
        best: InverterSnapshot | None = None
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
                if is_placeholder(snapshot):
                    if best is not None:
                        # Populated window closed: serve what we collected.
                        return finalize_readout(best), True
                    # Never saw a window yet: keep waiting for one.
                else:
                    # Window open: collect + union; later reads backfill the
                    # earlier zeros (see merge_readout_fields).
                    best = (
                        snapshot
                        if best is None
                        else merge_readout_fields(best, snapshot)
                    )
                    if _readout_complete(best):
                        return finalize_readout(best), True
            except aiohttp.ClientError:
                if best is not None:
                    # Mid-window blip: keep what we have.
                    return finalize_readout(best), True
                down_attempts += 1
                if (
                    give_up_if_down
                    and not saw_server
                    and down_attempts >= MAX_DOWN_ATTEMPTS
                ):
                    return None, False
            if time.monotonic() >= deadline:
                return (
                    (finalize_readout(best) if best is not None else None),
                    saw_server,
                )
            await asyncio.sleep(RETRY_STEP_S)

    async def _async_update_data(self) -> InverterSnapshot | None:
        now = datetime.now().astimezone()
        previous = self.data
        mode = str(self._config.get(CONF_REFRESH_MODE, DEFAULT_REFRESH_MODE)).lower()

        timeout_s = float(self._config[CONF_REFRESH_TIMEOUT])
        give_up_if_down = mode in ("none", "watch")
        if mode == "reboot":
            # Fully local kick: reboot the stick, it re-reads the inverter on
            # boot and repopulates the cgi (~25 s). No cloud call at all.
            if not await restart_datalogger(
                self._lan_session, self._host, self._datalogger_password
            ):
                _LOGGER.warning("datalogger restart failed; waiting for its own read loop")
            timeout_s = max(timeout_s, REBOOT_WAIT_S)
        else:
            # Kick-less ("none", "watch"): wait for the stick's own read loop
            # to repopulate the buffer (one full cycle as the outer bound).
            timeout_s = max(timeout_s, WATCH_WAIT_S)

        snapshot, reachable = await self._read_populated(
            timeout_s, give_up_if_down=give_up_if_down
        )
        if snapshot is None:
            if previous is not None:
                # First poll after local midnight with no fresh read: the day
                # counters restart at zero (TOTAL_INCREASING "new meter
                # cycle"). Otherwise carry the last-known values forward.
                reset = reset_for_new_day(previous, now)
                if reset is not None:
                    return reset
                return self._aged(previous, now, online=reachable)
            raise UpdateFailed("cgi unreachable and no previous data available")

        # A collected window union is by definition freshly read this cycle
        # (and the probe answered, so the stick is reachable).
        return InverterSnapshot(
            serial_no=snapshot.serial_no,
            firmware_version=snapshot.firmware_version,
            inverter_model=snapshot.inverter_model,
            inverter_temperature_c=snapshot.inverter_temperature_c,
            current_power_w=snapshot.current_power_w,
            yield_today_kwh=snapshot.yield_today_kwh,
            total_yield_kwh=snapshot.total_yield_kwh,
            alerts=snapshot.alerts,
            inverter_online=True,
            last_updated=snapshot.last_updated,
            raw=snapshot.raw,
            stale=False,
        )

    def _aged(
        self, previous: InverterSnapshot, now: datetime, online: bool
    ) -> InverterSnapshot:
        """Carry the last-known values forward when no fresh read arrived.

        ``online`` is reachability, not data freshness: the stick may have
        answered HTTP while serving its idle placeholder (missed window) --
        the inverter is still online, but the values are not fresh.
        """
        return InverterSnapshot(
            serial_no=previous.serial_no,
            firmware_version=previous.firmware_version,
            inverter_model=previous.inverter_model,
            inverter_temperature_c=previous.inverter_temperature_c,
            current_power_w=previous.current_power_w,
            yield_today_kwh=previous.yield_today_kwh,
            total_yield_kwh=previous.total_yield_kwh,
            alerts=previous.alerts,
            inverter_online=online,
            last_updated=previous.last_updated,
            raw=previous.raw,
            stale=True,
        )
"""Async local read of the datalogger's /inverter.cgi page.

Pure aiohttp -- no Home Assistant imports, so this module can be smoke-tested
against a live datalogger without HA.
"""

from __future__ import annotations

import base64
import logging

import aiohttp

from ..const import CGI_PATH, RESTART_PATH

_LOGGER = logging.getLogger(__name__)


def _basic_auth_header(username: str, password: str) -> str:
    """Basic auth header built manually -- BasicAuth is deprecated in aiohttp 3.14+."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _headers(password: str) -> dict[str, str]:
    return {
        "Authorization": _basic_auth_header("admin", password),
        "Accept": "*/*",
        "User-Agent": "HomeAssistant/solis_local",
    }


async def probe_datalogger(host: str, password: str, timeout_s: float = 5.0) -> str:
    """Probe reachability + auth for the config flow.

    Returns ``ok`` | ``invalid_auth`` | ``cannot_connect``. Note: a 200 here
    does NOT guarantee fresh data -- the stick serves an all-zero placeholder
    most of the time; this only proves the page is reachable with the given
    password.
    """
    url = f"http://{host}{CGI_PATH}"
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(url, headers=_headers(password)) as resp,
        ):
            if resp.status == 200:
                body = await resp.text()
                if body.strip("\x00 \t\r\n"):
                    return "ok"
                return "cannot_connect"
            if resp.status in (401, 403):
                return "invalid_auth"
            return "cannot_connect"
    except aiohttp.ClientError as err:
        _LOGGER.debug("Datalogger probe failed: %s", err)
        return "cannot_connect"


async def restart_datalogger(
    session: aiohttp.ClientSession,
    host: str,
    password: str,
    timeout_s: float = 5.0,
) -> bool:
    """Reboot the datalogger via /restart.cgi (fully local refresh kick).

    On boot the stick re-reads the inverter over RS485 and repopulates
    /inverter.cgi (~25 s). Returns True when the restart request was accepted.
    """
    url = f"http://{host}{RESTART_PATH}"
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with session.get(url, headers=_headers(password), timeout=timeout) as resp:
            return resp.status == 200
    except aiohttp.ClientError as err:
        _LOGGER.warning("Datalogger restart failed: %s", err)
        return False


async def read_inverter_cgi(
    session: aiohttp.ClientSession,
    host: str,
    password: str,
    timeout_s: float = 5.0,
) -> str:
    """Fetch the raw /inverter.cgi body. Raises aiohttp errors on failure."""
    url = f"http://{host}{CGI_PATH}"
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with session.get(url, headers=_headers(password), timeout=timeout) as resp:
        resp.raise_for_status()
        return await resp.text()
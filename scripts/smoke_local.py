"""Live smoke test for the local-only component, without Home Assistant.

Loads the exact code shipped in custom_components/solis_local (api/local.py,
parser.py, models.py) and talks to the real datalogger through it. The
HA-importing __init__.py / coordinator are never executed.

Usage:
    uv run python scripts/smoke_local.py [--host IP] [--password PW] [--timeout S]
Or via the environment: SOLIS_DATALOGGER_IP / SOLIS_DATALOGGER_PASSWORD.

Exit codes: 0 = populated readout received, 2 = reachable but only the
all-zero placeholder within the timeout, 1 = probe/auth failure.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
CC = ROOT / "custom_components"

DEFAULT_TIMEOUT_S = 120.0
MAX_DOWN_ATTEMPTS = 3


def _register_ns(name: str, path: Path) -> None:
    if name not in sys.modules:
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module


def load_pure_module(rel_path: str):
    """Load a module from inside the package without executing __init__.py."""
    parts = rel_path.split("/")
    file_path = CC.joinpath(*parts)
    leaf = "custom_components." + ".".join(parts[:-1]) + "." + Path(parts[-1]).stem
    for i in range(1, len(parts)):
        _register_ns("custom_components." + ".".join(parts[:i]), CC.joinpath(*parts[:i]))
    spec = importlib.util.spec_from_file_location(leaf, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[leaf] = module
    spec.loader.exec_module(module)
    return module


async def main() -> int:
    argp = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argp.add_argument("--host", help="datalogger IP (or SOLIS_DATALOGGER_IP)")
    argp.add_argument("--password", help="datalogger password (or SOLIS_DATALOGGER_PASSWORD)")
    argp.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                      help="watch timeout in seconds (default 120; 330 = one full stick cycle)")
    args = argp.parse_args()

    host = args.host or os.environ.get("SOLIS_DATALOGGER_IP")
    password = args.password or os.environ.get("SOLIS_DATALOGGER_PASSWORD")
    if not host or not password:
        print("error: need --host/--password or SOLIS_DATALOGGER_IP/SOLIS_DATALOGGER_PASSWORD")
        return 1

    api = load_pure_module("solis_local/api/local.py")
    parser_mod = load_pure_module("solis_local/parser.py")
    models = load_pure_module("solis_local/models.py")

    probe = await api.probe_datalogger(host, password)
    print(f"probe: {probe}")
    if probe != "ok":
        return 1

    loop = asyncio.get_running_loop()
    deadline = loop.time() + args.timeout
    down_attempts = 0
    last = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
        while True:
            elapsed = args.timeout - max(0.0, deadline - loop.time())
            try:
                raw = await api.read_inverter_cgi(session, host, password)
                if last is None:
                    print("raw head:", repr(raw.replace("\x00", "").strip()[:120]))
                down_attempts = 0
                snap = parser_mod.parse_inverter_cgi(
                    raw, read_at=datetime.now(UTC).astimezone()
                )
                print(
                    f"[{elapsed:6.1f}s] fw={snap.firmware_version!r} model={snap.inverter_model!r} "
                    f"power={snap.current_power_w}W temp={snap.inverter_temperature_c}C "
                    f"today={snap.yield_today_kwh}kWh total={snap.total_yield_kwh}kWh "
                    f"alerts={snap.alerts} serial={snap.serial_no}"
                )
                if models.is_populated(snap):
                    # Serial is written last (~0.5 s after the rest); settle to
                    # prefer the serial-bearing readout like the coordinator.
                    best = snap
                    for _ in range(3):
                        if loop.time() >= deadline:
                            break
                        await asyncio.sleep(0.5)
                        try:
                            settle_raw = await api.read_inverter_cgi(session, host, password)
                        except aiohttp.ClientError:
                            break
                        settle_snap = parser_mod.parse_inverter_cgi(
                            settle_raw, read_at=datetime.now(UTC).astimezone()
                        )
                        if not models.is_populated(settle_snap):
                            break
                        best = models.prefer_serial_bearing_readout(best, settle_snap)
                        if best.serial_no:
                            break
                    print(json.dumps(models.snapshot_to_dict(best), indent=2, ensure_ascii=False))
                    return 0
                last = snap
            except aiohttp.ClientError as err:
                down_attempts += 1
                if down_attempts >= MAX_DOWN_ATTEMPTS:
                    print(f"error: datalogger unreachable after {MAX_DOWN_ATTEMPTS} attempts ({err})")
                    return 1
            if loop.time() >= deadline:
                break
            await asyncio.sleep(0.5)

    print("no populated payload within the timeout (datalogger idle / cloud-synced buffer stale)")
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
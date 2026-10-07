"""Burst probe: measure the populated window at high resolution.

The datalogger serves /inverter.cgi from a RAM buffer it fills field-by-field
and only keeps populated for a moment each ~5-min cycle. The integration's
operational assumptions -- window length ("~1.6 s"), fill order (identity ->
power -> temp -> serial), and that total yield stays the literal ``u`` --
were all derived from 0.5 s sampling. This probe resamples an open window at
the fastest cadence the LAN allows (~50-150 ms per HTTP round-trip) so we can
answer:

1. How long does the populated window actually live (per cycle)?
2. Does any field (esp. total yield, field 6) fill AFTER our 0.5 s polls
   normally land -- i.e. was "genuinely absent" a sampling artifact?
3. What is the true field-by-field fill order at sub-0.5 s resolution?

Usage:
    uv run python scripts/burst_probe.py [--host IP] [--password PW] \\
        [--windows 3] [--timeout 330]
Or via the environment: SOLIS_DATALOGGER_IP / SOLIS_DATALOGGER_PASSWORD.

Writes one JSONL file (one line per raw read, monotonic timestamps) and a
per-window summary to stdout. Exit codes: 0 = at least one window captured,
2 = no populated window within the timeout, 1 = probe/auth failure.
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

DEFAULT_TIMEOUT_S = 330.0
# Detection cadence while waiting for a window to open: fine enough to catch
# the ~1-2 s window early, cheap for the ~5 min wait in between.
DETECT_STEP_S = 0.3
# Burst cadence once the window is open: LAN HTTP round-trips take ~10-50 ms,
# so 0.05 s pacing gives ~20 samples/cycle -- sub-0.5 s fill events become
# visible. The stick's serve loop is unaffected (still one request at a time).
BURST_STEP_S = 0.05


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


def _raw_fields(raw: str) -> list[str]:
    """Split the raw cgi body into its ';'-separated fields (NULs stripped)."""
    return raw.replace("\x00", "").strip().split(";")


def _is_placeholder(fields: list[str]) -> bool:
    """True when the body is the idle all-zero buffer (identity zeroed)."""
    if len(fields) < 3:
        return True  # truncated row: treat as empty, never as a window
    return fields[1] in ("", "0") and fields[2] in ("", "0")


def _sample(
    raw: str,
    t: float,
    parser_mod,
) -> tuple[dict, list[str]]:
    """One annotated sample: raw fields + parsed snapshot."""
    fields = _raw_fields(raw)
    snap = parser_mod.parse_inverter_cgi(
        raw, read_at=datetime.now(UTC).astimezone()
    )
    return {
        "t_mono": round(t, 4),
        "elapsed_s": None,  # filled by the caller relative to window start
        "serial": fields[0] if len(fields) > 0 else "",
        "firmware": fields[1] if len(fields) > 1 else "",
        "model": fields[2] if len(fields) > 2 else "",
        "temp_raw": fields[3] if len(fields) > 3 else "",
        "power_raw": fields[4] if len(fields) > 4 else "",
        "yield_today_raw": fields[5] if len(fields) > 5 else "",
        "total_yield_raw": fields[6] if len(fields) > 6 else "",
        "alerts_raw": fields[7] if len(fields) > 7 else "",
        "temp_c": snap.inverter_temperature_c,
        "power_w": snap.current_power_w,
        "yield_today_kwh": snap.yield_today_kwh,
        "total_yield_kwh": snap.total_yield_kwh,
        "placeholder": _is_placeholder(fields),
    }, fields


async def _read_once(api, session: aiohttp.ClientSession, host: str, password: str) -> str:
    return await api.read_inverter_cgi(session, host, password)


async def main() -> int:
    argp = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argp.add_argument("--host", help="datalogger IP (or SOLIS_DATALOGGER_IP)")
    argp.add_argument("--password", help="datalogger password (or SOLIS_DATALOGGER_PASSWORD)")
    argp.add_argument("--windows", type=int, default=1,
                      help="populated windows to capture (default 1; each waits ~0-5 min)")
    argp.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                      help="total budget in seconds (default 330 = one full stick cycle)")
    argp.add_argument("--out", help="JSONL output path (default burst_probe_<ts>.jsonl)")
    args = argp.parse_args()

    host = args.host or os.environ.get("SOLIS_DATALOGGER_IP")
    password = args.password or os.environ.get("SOLIS_DATALOGGER_PASSWORD")
    if not host or not password:
        print("error: need --host/--password or SOLIS_DATALOGGER_IP/SOLIS_DATALOGGER_PASSWORD")
        return 1

    api = load_pure_module("solis_local/api/local.py")
    parser_mod = load_pure_module("solis_local/parser.py")

    out_path = args.out or (
        ROOT / f"burst_probe_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.jsonl"
    )

    probe = await api.probe_datalogger(host, password)
    print(f"probe: {probe}")
    if probe != "ok":
        return 1

    loop = asyncio.get_running_loop()
    t_start = loop.time()
    deadline = t_start + args.timeout
    windows: list[list[dict]] = []
    current: list[dict] | None = None
    window_t0 = 0.0
    total_samples = 0
    saw_placeholder_close = False

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
        try:
            with out_path.open("w", encoding="utf-8") as out_f:
                while len(windows) < args.windows:
                    if loop.time() >= deadline:
                        break
                    try:
                        raw = await _read_once(api, session, host, password)
                    except aiohttp.ClientError as err:
                        print(f"[warn] read failed: {err!r}; retrying")
                        await asyncio.sleep(DETECT_STEP_S)
                        continue
                    t = loop.time()
                    sample, fields = _sample(raw, t, parser_mod)
                    total_samples += 1

                    if current is None and not sample["placeholder"]:
                        # Window opening: start a fresh capture.
                        current = []
                        window_t0 = t
                        print(
                            f"[window open @ t-{window_t0 - t_start:7.1f}s] "
                            f"{_fields_line(fields)}"
                        )
                    if current is not None:
                        sample["elapsed_s"] = round(t - window_t0, 4)
                        current.append(sample)
                        out_f.write(json.dumps(sample) + "\n")
                        out_f.flush()
                        if sample["placeholder"]:
                            # Window closed: the all-zero buffer is back.
                            saw_placeholder_close = True
                            print(
                                f"[window close @ +{sample['elapsed_s']:6.3f}s] "
                                f"{_fields_line(fields)}"
                            )
                            windows.append(current)
                            current = None

                    if current is not None:
                        await asyncio.sleep(BURST_STEP_S)
                    else:
                        await asyncio.sleep(DETECT_STEP_S)
        except KeyboardInterrupt:
            print("\ninterrupted")

    if current is not None:
        # Deadline hit mid-window: keep the partial capture.
        windows.append(current)

    if not windows:
        print("no populated window observed within the timeout")
        return 2

    _print_summary(windows, totals=total_samples, path=out_path,
                   placeholder_close=saw_placeholder_close)
    return 0


def _fields_line(fields: list[str]) -> str:
    pad = ["…"] * 8
    pad[: len(fields)] = fields
    return ";".join(pad)


def _print_summary(
    windows: list[list[dict]], totals: int, path: Path, placeholder_close: bool
) -> None:
    print("\n=== burst probe summary ===")
    print(f"samples: {totals}   windows: {len(windows)}   "
          f"clean placeholder close: {placeholder_close}")
    print(f"jsonl:   {path}")
    for i, win in enumerate(windows, 1):
        real = [s for s in win if not s["placeholder"]]
        if not real:
            print(f"window {i}: EMPTY (no non-placeholder sample)")
            continue
        dur = win[-1]["elapsed_s"] - win[0]["elapsed_s"]
        open_t = real[0]["elapsed_s"]
        close_t = win[-1]["elapsed_s"]
        print(f"window {i}: duration={dur:6.3f}s  "
              f"(populated t:{open_t:6.3f} -> placeholder t:{close_t:6.3f})")
        # Fill order: first sample where each field left its placeholder value.
        seen: dict[str, str] = {}
        for s in real:
            for key, val in (
                ("serial", s["serial"]),
                ("firmware", s["firmware"]),
                ("model", s["model"]),
                ("temp", s["temp_c"]),
                ("power", s["power_w"]),
                ("yield_today", s["yield_today_kwh"]),
                ("total_yield", s["total_yield_kwh"]),
            ):
                if (
                    val not in ("", None, 0, 0.0)
                    or (key == "total_yield" and val is not None)
                ) and key not in seen:
                    seen[key] = f"{val} @ {s['elapsed_s']:6.3f}s"
        print("  fill order: " + " -> ".join(f"{k} ({v})" for k, v in seen.items()))
        totals_raw = [s["total_yield_raw"] for s in win]
        distinct = sorted(set(totals_raw))
        print(f"  total_yield raw values across {len(win)} samples: {distinct}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
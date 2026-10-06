"""Typed data model for the Solis datalogger local readout.

Pure logic -- no Home Assistant imports, so this (and parser.py) can be unit
tested without HA and smoke-tested against a live datalogger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def is_populated(snapshot: InverterSnapshot) -> bool:
    """True when the cgi payload is real data, not the all-zero placeholder.

    The datalogger serves a zeroed buffer (firmware ``0`` / model ``0``) while
    a refresh is applied and in its normal idle state; only a non-zero
    firmware or model identifies a genuinely populated readout.
    """

    return snapshot.firmware_version not in ("", "0") or snapshot.inverter_model not in ("", "0")


@dataclass(frozen=True, slots=True)
class InverterSnapshot:
    """One parsed readout of the datalogger's /inverter.cgi page.

    Numeric fields are ``None`` when the device reported an unknown value
    (e.g. total yield comes back as ``u`` / ``unknown`` until confirmed).
    """

    serial_no: str = ""
    firmware_version: str = ""
    inverter_model: str = ""
    inverter_temperature_c: float | None = None
    current_power_w: int | None = None
    yield_today_kwh: float | None = None
    total_yield_kwh: float | None = None
    alerts: bool = False
    inverter_online: bool | None = None
    last_updated: datetime | None = None
    raw: str = ""
    data_age_s: float = 0.0
    stale: bool = False


def snapshot_to_dict(snapshot: InverterSnapshot) -> dict:
    """Stable dict form for JSON output (ISO-8601 timestamps)."""
    return {
        "serial_no": snapshot.serial_no,
        "firmware_version": snapshot.firmware_version,
        "inverter_model": snapshot.inverter_model,
        "inverter_temperature_c": snapshot.inverter_temperature_c,
        "current_power_w": snapshot.current_power_w,
        "yield_today_kwh": snapshot.yield_today_kwh,
        "total_yield_kwh": snapshot.total_yield_kwh,
        "alerts": snapshot.alerts,
        "inverter_online": snapshot.inverter_online,
        "last_updated": snapshot.last_updated.isoformat() if snapshot.last_updated else None,
        "data_age_s": snapshot.data_age_s,
        "stale": snapshot.stale,
    }


def snapshot_from_dict(data: dict) -> InverterSnapshot:
    """Inverse of :func:`snapshot_to_dict`; tolerant of missing keys."""
    return InverterSnapshot(
        serial_no=str(data.get("serial_no", "")),
        firmware_version=str(data.get("firmware_version", "")),
        inverter_model=str(data.get("inverter_model", "")),
        inverter_temperature_c=data.get("inverter_temperature_c"),
        current_power_w=data.get("current_power_w"),
        yield_today_kwh=data.get("yield_today_kwh"),
        total_yield_kwh=data.get("total_yield_kwh"),
        alerts=bool(data.get("alerts", False)),
        inverter_online=data.get("inverter_online"),
        last_updated=_parse_dt(data.get("last_updated")),
        data_age_s=float(data.get("data_age_s", 0.0)),
        stale=bool(data.get("stale", False)),
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
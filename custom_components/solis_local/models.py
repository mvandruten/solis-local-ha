"""Typed data model for the Solis datalogger local readout.

Pure logic -- no Home Assistant imports, so this (and parser.py) can be unit
tested without HA and smoke-tested against a live datalogger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def is_placeholder(snapshot: InverterSnapshot) -> bool:
    """True when the cgi body is the idle all-zero buffer.

    The datalogger serves a fully zeroed page (firmware ``0`` / model ``0`` /
    ``0W`` / ``0.0`` temp / empty serial) except for the ~1.6 s populated
    window around each read. Any deviation -- a single real field -- is a
    populated window opening; the device fills the rest within ~1 s. This is
    a pure probe for window detection, NOT a completeness score.
    """
    return (
        snapshot.firmware_version in ("", "0")
        and snapshot.inverter_model in ("", "0")
        and snapshot.inverter_temperature_c in (None, 0.0)
        and snapshot.current_power_w in (None, 0)
        and snapshot.yield_today_kwh in (None, 0.0)
        and snapshot.total_yield_kwh in (None, 0.0)
        and snapshot.serial_no == ""
    )


def finalize_readout(snapshot: InverterSnapshot) -> InverterSnapshot:
    """Clean a collected window union for serving.

    ``0.0`` in temperature is the stick's per-field "not written yet" marker,
    never a genuine measurement (a producing inverter cannot sit at exactly
    0.0 C) -- if the window closed before any read carried a real temperature,
    report it as unknown instead of asserting an impossible reading. Power
    keeps 0 as a real value (idle inverter).
    """
    if snapshot.inverter_temperature_c == 0.0:
        return InverterSnapshot(
            **{f: getattr(snapshot, f) for f in (
                "serial_no", "firmware_version", "inverter_model",
                "current_power_w", "yield_today_kwh", "total_yield_kwh",
            )},
            inverter_temperature_c=None,
            alerts=snapshot.alerts,
            inverter_online=snapshot.inverter_online,
            last_updated=snapshot.last_updated,
            last_reset=snapshot.last_reset,
            raw=snapshot.raw,
            stale=snapshot.stale,
        )
    return snapshot


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
    last_reset: datetime | None = None
    raw: str = ""
    data_age_s: float = 0.0
    stale: bool = False


def _field_empty(value: object) -> bool:
    """True when a field carries no data yet (not-yet-written placeholder).

    Within one populated window the stick fills fields monotonically
    (missing -> real, never contradict), so ``0``/``0.0``/``""`` all mean
    "not written yet" here -- safe for power/yield too, because a genuine
    zero appears in EVERY read of a window (the union keeps it zero), while a
    pre-fill zero is backfilled by the next read's real value. The closing
    placeholder read is never merged, so real values can't be clobbered.
    """
    return value is None or value in ("", "0", "0.0") or value == 0


def merge_readout_fields(
    current: InverterSnapshot, candidate: InverterSnapshot
) -> InverterSnapshot:
    """Union the fields of two reads from the same populated window.

    The stick serves the same snapshot repeatedly while filling its RAM
    buffer field-by-field over ~1 s, serial last (observed live 2026-10-06).
    Reads within one window never contradict -- same values, monotonic fill
    order. Fields fill monotonically (missing -> real), and within the
    window ``0``/``0.0``/``""`` are "not written yet" markers, so a later
    read's real value backfills an earlier zero; genuine zeros (an idle
    inverter) appear in EVERY read of the window, so the union keeps them.
    The closing placeholder read is never merged, so collected real values
    can't be clobbered. ``last_updated`` follows the newest read.
    """
    fields = (
        "serial_no",
        "firmware_version",
        "inverter_model",
        "inverter_temperature_c",
        "current_power_w",
        "yield_today_kwh",
        "total_yield_kwh",
    )
    values = {}
    for field in fields:
        cur = getattr(current, field)
        cand = getattr(candidate, field)
        values[field] = cur if not _field_empty(cur) else cand
    return InverterSnapshot(
        **values,
        alerts=current.alerts,
        inverter_online=current.inverter_online,
        last_updated=candidate.last_updated or current.last_updated,
        last_reset=current.last_reset,
        raw=candidate.raw or current.raw,
        data_age_s=current.data_age_s,
        stale=current.stale,
    )


def reset_for_new_day(
    previous: InverterSnapshot, now: datetime
) -> InverterSnapshot | None:
    """Return a midnight-reset snapshot when the day has rolled over.

    The datalogger is powered by the inverter and unreachable at night; rather
    than carry yesterday's values (chiefly ``yield_today_kwh``) into the next
    morning, the day counters restart from zero at the first poll after the
    local calendar day rolls over -- mirroring what the datalogger itself
    reports after midnight. The lifetime total (``total_yield_kwh``) and the
    device identity fields are preserved.

    Returns ``None`` when no rollover applies (same day, or no timestamp to
    compare against), i.e. normal aged carry-forward should be used instead.
    """
    if previous.last_updated is None or previous.last_updated.date() >= now.date():
        return None
    return InverterSnapshot(
        serial_no=previous.serial_no,
        firmware_version=previous.firmware_version,
        inverter_model=previous.inverter_model,
        # The inverter is not producing at night; a stale temperature would be
        # as misleading as a stale yield, so report the measurement as unknown.
        inverter_temperature_c=None,
        current_power_w=0,
        yield_today_kwh=0.0,
        total_yield_kwh=previous.total_yield_kwh,
        alerts=False,
        inverter_online=False,
        last_updated=now,
        last_reset=now,
        raw=previous.raw,
        data_age_s=0.0,
        stale=True,
    )


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
        "last_reset": snapshot.last_reset.isoformat() if snapshot.last_reset else None,
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
        last_reset=_parse_dt(data.get("last_reset")),
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
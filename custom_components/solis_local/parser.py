"""Parse the raw /inverter.cgi response from a Solis datalogger.

The response is a ``;``-separated row of 8 fields, padded with NUL bytes on
both sides and terminated by CRLF. Real-world capture (2026-10-06)::

    \\x00\\x00...180501024A150053;91004C;501;28.7;520;0.500000;u;NO;\\r\\n\\x00\\x00...

Note the values carry no unit characters in the raw text ("28.7", "520",
"0.500000"), and the total yield field is literally ``u`` (= unknown) until the
datalogger has a confirmed value. Some variants/UI renderings append units
("0.0\\u2103", "0W", "0.000000kWh", "ukWh") -- both forms are handled here.

Field order (fixed)::

    0 serial_no, 1 firmware_version, 2 inverter_model, 3 inverter_temperature,
    4 current_power, 5 yield_today, 6 total_yield, 7 alerts
"""

from __future__ import annotations

import re
from datetime import datetime

from .models import InverterSnapshot

_FIELD_KEYS: tuple[str, ...] = (
    "serial_no",
    "firmware_version",
    "inverter_model",
    "inverter_temperature",
    "current_power",
    "yield_today",
    "total_yield",
    "alerts",
)

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
_ALERTS_ON: frozenset[str] = frozenset({"yes", "1", "true", "warn", "alert", "on", "y"})
_UNKNOWN_TOKENS: frozenset[str] = frozenset({"u", "unknown", "-", "\u2014", "na", "n/a"})


def _clean(raw: str) -> str:
    """Drop NUL padding and surrounding whitespace."""
    return raw.replace("\x00", "").strip()


def _parse_float(field: str) -> float | None:
    """First number in a field, or None when absent / explicitly unknown."""
    token = field.strip().lower()
    if token in _UNKNOWN_TOKENS:
        return None
    match = _NUMBER.search(field)
    if match is None:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _parse_alerts(field: str) -> bool:
    token = field.strip().lower()
    return token in _ALERTS_ON


def parse_inverter_cgi(raw: str, read_at: datetime | None = None) -> InverterSnapshot:
    """Parse a raw /inverter.cgi body into a typed :class:`InverterSnapshot`.

    Never raises on malformed input: missing fields degrade to ``None``/"" and
    the raw text is kept on the snapshot for diagnostics.
    """
    fields = [_clean_part.strip() for _clean_part in _clean(raw).split(";")]
    if len(fields) < len(_FIELD_KEYS):
        fields += [""] * (len(_FIELD_KEYS) - len(fields))
    values = dict(zip(_FIELD_KEYS, fields))

    temperature_raw = _parse_float(values["inverter_temperature"])
    power_raw = _parse_float(values["current_power"])
    today_raw = _parse_float(values["yield_today"])
    total_raw = _parse_float(values["total_yield"])

    return InverterSnapshot(
        serial_no=values["serial_no"],
        firmware_version=values["firmware_version"],
        inverter_model=values["inverter_model"],
        inverter_temperature_c=temperature_raw,
        current_power_w=int(power_raw) if power_raw is not None else None,
        yield_today_kwh=today_raw,
        total_yield_kwh=total_raw,
        alerts=_parse_alerts(values["alerts"]),
        last_updated=read_at,
        raw=raw,
    )
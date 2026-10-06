"""Tests for the snapshot model + the populated-payload gate (pure logic)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.solis_local.models import (
    InverterSnapshot,
    is_populated,
    snapshot_from_dict,
    snapshot_to_dict,
)

SNAPSHOT = InverterSnapshot(
    serial_no="180501024A150053",
    firmware_version="91004C",
    inverter_model="501",
    inverter_temperature_c=29.1,
    current_power_w=570,
    yield_today_kwh=0.6,
    total_yield_kwh=None,
    alerts=False,
    inverter_online=True,
    last_updated=datetime(2026, 10, 6, 10, 28, 14, tzinfo=timezone(timedelta(hours=2))),
)


def test_snapshot_dict_roundtrip() -> None:
    d = snapshot_to_dict(SNAPSHOT)
    assert d["current_power_w"] == 570
    assert d["total_yield_kwh"] is None
    assert d["last_updated"].startswith("2026-10-06T10:28:14")
    assert d["inverter_online"] is True
    assert d["stale"] is False
    assert snapshot_from_dict(d) == SNAPSHOT


def test_roundtrip_tolerates_missing_keys() -> None:
    back = snapshot_from_dict({"serial_no": "x"})
    assert back.serial_no == "x"
    assert back.current_power_w is None
    assert back.stale is False


def test_bad_timestamp_parses_to_none() -> None:
    back = snapshot_from_dict({"last_updated": "not-a-date"})
    assert back.last_updated is None


def test_placeholder_snapshot_is_not_populated() -> None:
    assert is_populated(InverterSnapshot(firmware_version="0", inverter_model="0")) is False
    assert is_populated(InverterSnapshot()) is False


def test_real_snapshot_is_populated() -> None:
    assert is_populated(InverterSnapshot(firmware_version="91004C", inverter_model="501")) is True
    # firmware may be empty on odd syncs; a real model alone still counts.
    assert is_populated(InverterSnapshot(firmware_version="0", inverter_model="501")) is True
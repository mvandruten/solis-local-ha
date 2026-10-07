"""Tests for the snapshot model + the populated-payload gate (pure logic)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.solis_local.models import (
    InverterSnapshot,
    is_populated,
    prefer_serial_bearing_readout,
    reset_for_new_day,
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


def test_partial_buffer_is_not_populated() -> None:
    # Observed live (2026-10-06): the stick serves a partial populated buffer
    # (real firmware/model, temp still 0.0) ~0.5s before the full readout.
    partial = InverterSnapshot(
        firmware_version="91004C",
        inverter_model="501",
        inverter_temperature_c=0.0,
        current_power_w=240,
        yield_today_kwh=5.8,
    )
    assert is_populated(partial) is False


def test_real_snapshot_is_populated() -> None:
    assert is_populated(
        InverterSnapshot(
            firmware_version="91004C", inverter_model="501", inverter_temperature_c=31.0
        )
    ) is True
    # firmware may be empty on odd syncs; a real model alone still counts.
    assert is_populated(
        InverterSnapshot(
            firmware_version="0", inverter_model="501", inverter_temperature_c=31.0
        )
    ) is True
    # A warm-but-zero-power readout is still real data.
    assert is_populated(
        InverterSnapshot(
            firmware_version="91004C", inverter_model="501",
            inverter_temperature_c=12.4, current_power_w=0,
        )
    ) is True


TZ = timezone(timedelta(hours=2))


def test_reset_for_new_day_rolls_over() -> None:
    previous = InverterSnapshot(
        serial_no="180501024A150053",
        firmware_version="91004C",
        inverter_model="501",
        inverter_temperature_c=29.1,
        current_power_w=570,
        yield_today_kwh=12.4,
        total_yield_kwh=3456.7,
        alerts=False,
        inverter_online=True,
        last_updated=datetime(2026, 10, 6, 20, 30, tzinfo=TZ),
    )
    now = datetime(2026, 10, 7, 0, 3, tzinfo=TZ)
    reset = reset_for_new_day(previous, now)
    assert reset is not None
    # Day counters restart at zero, device fields and the lifetime total stay.
    assert reset.yield_today_kwh == 0.0
    assert reset.current_power_w == 0
    assert reset.inverter_temperature_c is None
    assert reset.alerts is False
    assert reset.inverter_online is False
    assert reset.total_yield_kwh == 3456.7
    assert reset.serial_no == "180501024A150053"
    assert reset.firmware_version == "91004C"
    assert reset.inverter_model == "501"
    # The reset is the moment the meter cycle restarted.
    assert reset.last_updated == now
    assert reset.last_reset == now
    assert reset.stale is True


def test_reset_for_new_day_same_day_returns_none() -> None:
    previous = InverterSnapshot(
        current_power_w=570,
        yield_today_kwh=12.4,
        last_updated=datetime(2026, 10, 6, 23, 59, tzinfo=TZ),
    )
    assert reset_for_new_day(previous, datetime(2026, 10, 6, 23, 59, 30, tzinfo=TZ)) is None


def test_reset_for_new_day_without_timestamp_returns_none() -> None:
    assert reset_for_new_day(InverterSnapshot(yield_today_kwh=12.4), datetime(2026, 10, 7, tzinfo=TZ)) is None


def test_prefer_serial_bearing_readout() -> None:
    noserial = InverterSnapshot(firmware_version="91004C", inverter_model="501", inverter_temperature_c=30.7)
    withserial = InverterSnapshot(
        serial_no="180501024A150053", firmware_version="91004C",
        inverter_model="501", inverter_temperature_c=30.7,
    )
    # A serial-bearing candidate wins; a serial-less one never replaces it.
    assert prefer_serial_bearing_readout(noserial, withserial) is withserial
    assert prefer_serial_bearing_readout(withserial, noserial) is withserial
    assert prefer_serial_bearing_readout(noserial, noserial) is noserial
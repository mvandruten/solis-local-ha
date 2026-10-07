"""Tests for the snapshot model + the populated-payload gate (pure logic)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.solis_local.models import (
    InverterSnapshot,
    finalize_readout,
    is_placeholder,
    merge_readout_fields,
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


def test_placeholder_snapshot_is_placeholder() -> None:
    assert is_placeholder(InverterSnapshot(firmware_version="0", inverter_model="0")) is True
    assert is_placeholder(InverterSnapshot()) is True
    # all-zero placeholder: identity, temp, power and serial all zero/empty
    assert is_placeholder(InverterSnapshot(
        firmware_version="0", inverter_model="0", inverter_temperature_c=0.0,
        current_power_w=0, yield_today_kwh=0.0, serial_no="",
    )) is True


def test_partial_buffer_with_identity_is_not_placeholder() -> None:
    # Observed live (2026-10-06): a partial read has real identity/power while
    # temp is still 0.0 -- that is a POPULATED window opening, not the idle buffer.
    partial = InverterSnapshot(
        firmware_version="91004C", inverter_model="501", inverter_temperature_c=0.0,
        current_power_w=240, yield_today_kwh=5.8,
    )
    assert is_placeholder(partial) is False


def test_dead_temperature_with_serial_still_not_placeholder() -> None:
    # No single field is load-bearing: temp dead + serial filling = real data.
    snapshot = InverterSnapshot(
        serial_no="180501024A150053", firmware_version="91004C",
        inverter_model="501", inverter_temperature_c=0.0,
        current_power_w=240, yield_today_kwh=5.8,
    )
    assert is_placeholder(snapshot) is False


def test_real_snapshot_is_not_placeholder() -> None:
    assert is_placeholder(InverterSnapshot(
        firmware_version="91004C", inverter_model="501", inverter_temperature_c=31.0,
    )) is False
    # firmware may be empty on odd syncs; a real model alone still counts.
    assert is_placeholder(InverterSnapshot(
        firmware_version="0", inverter_model="501", inverter_temperature_c=31.0,
    )) is False
    # A warm-but-zero-power readout is real data (idle inverter).
    assert is_placeholder(InverterSnapshot(
        firmware_version="91004C", inverter_model="501",
        inverter_temperature_c=12.4, current_power_w=0,
    )) is False


def test_finalize_readout_maps_zero_temp_to_none() -> None:
    s = InverterSnapshot(firmware_version="91004C", inverter_model="501",
                         inverter_temperature_c=0.0, current_power_w=240,
                         yield_today_kwh=5.8)
    out = finalize_readout(s)
    assert out.inverter_temperature_c is None
    assert out.current_power_w == 240  # power 0 is never rewritten
    assert out.serial_no == s.serial_no


def test_finalize_readout_keeps_real_temp() -> None:
    s = InverterSnapshot(firmware_version="91004C", inverter_model="501",
                         inverter_temperature_c=29.1, current_power_w=240)
    assert finalize_readout(s).inverter_temperature_c == 29.1


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


def test_merge_readout_fields_backfills_missing() -> None:
    first = InverterSnapshot(
        firmware_version="91004C",
        inverter_model="501",
        inverter_temperature_c=30.7,
        current_power_w=240,
        yield_today_kwh=5.8,
        last_updated=datetime(2026, 10, 6, 16, 35, 0, tzinfo=TZ),
    )
    later = InverterSnapshot(
        serial_no="180501024A150053",
        firmware_version="91004C",
        inverter_model="501",
        inverter_temperature_c=30.7,
        current_power_w=240,
        yield_today_kwh=5.8,
        last_updated=datetime(2026, 10, 6, 16, 35, 1, tzinfo=TZ),
    )
    merged = merge_readout_fields(first, later)
    # Serial (the last-written field) is backfilled from the later read.
    assert merged.serial_no == "180501024A150053"
    # Fields already present in the first read are kept untouched.
    assert merged.inverter_temperature_c == 30.7
    assert merged.current_power_w == 240
    assert merged.yield_today_kwh == 5.8
    # last_updated follows the newest read.
    assert merged.last_updated == later.last_updated


def test_merge_readout_fields_never_overwrites_present_values() -> None:
    first = InverterSnapshot(
        serial_no="180501024A150053",
        firmware_version="91004C",
        inverter_model="501",
        inverter_temperature_c=30.7,
        current_power_w=0,  # genuine zero: every read in the window carries it -> union keeps it
        yield_today_kwh=5.8,
    )
    later = InverterSnapshot(
        serial_no="",  # odd-sync: later read lost the serial again
        firmware_version="91004C",
        inverter_model="501",
        inverter_temperature_c=30.7,
        current_power_w=0,
        yield_today_kwh=5.8,
    )
    merged = merge_readout_fields(first, later)
    assert merged.serial_no == "180501024A150053"
    assert merged.current_power_w == 0


def test_merge_backfills_zero_temperature() -> None:
    # Leading partial read: identity+power real, temp still the stick's
    # "not written yet" 0.0. The completed read 0.5 s later must win.
    first = InverterSnapshot(
        firmware_version="91004C", inverter_model="501", inverter_temperature_c=0.0,
        current_power_w=570, yield_today_kwh=5.8,
        last_updated=datetime(2026, 10, 6, 16, 35, 0, tzinfo=TZ),
    )
    later = InverterSnapshot(
        firmware_version="91004C", inverter_model="501", inverter_temperature_c=29.1,
        current_power_w=570, yield_today_kwh=5.8,
        last_updated=datetime(2026, 10, 6, 16, 35, 1, tzinfo=TZ),
    )
    merged = merge_readout_fields(first, later)
    assert merged.inverter_temperature_c == 29.1


def test_merge_backfills_zero_power() -> None:
    # Same monotonic-fill logic for any field a firmware variant fills late.
    first = InverterSnapshot(
        firmware_version="91004C", inverter_model="501", current_power_w=0,
    )
    later = InverterSnapshot(
        firmware_version="91004C", inverter_model="501", current_power_w=520,
    )
    assert merge_readout_fields(first, later).current_power_w == 520


def test_merge_keeps_genuine_zero_power() -> None:
    # Genuinely idle readout: every read in the window carries 0 -> stays 0.
    a = InverterSnapshot(firmware_version="91004C", inverter_model="501",
                         inverter_temperature_c=12.4, current_power_w=0,
                         yield_today_kwh=0.0)
    b = InverterSnapshot(firmware_version="91004C", inverter_model="501",
                         inverter_temperature_c=12.4, current_power_w=0,
                         yield_today_kwh=0.0)
    merged = merge_readout_fields(a, b)
    assert merged.current_power_w == 0
    assert merged.yield_today_kwh == 0.0
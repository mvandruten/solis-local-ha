"""Parser tests for the raw /inverter.cgi response.

The authoritative fixture is tests/fixtures/raw_inverter_cgi.txt, captured from
a real S3-WIFI-ST datalogger on 2026-10-06. It shows the two quirks the parser
must survive:

* the body is NUL (\\x00) padded on both sides,
* the total yield field is literally ``u`` (= unknown) until confirmed,
* values carry no unit characters in the raw text ("28.7", "520", "0.500000")
  -- some datalogger variants / UI renderings append "\u2103"/"W"/"kWh", which
  the synthetic tests below cover.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from custom_components.solis_local.parser import parse_inverter_cgi

FIXTURE = Path(__file__).parent / "fixtures" / "raw_inverter_cgi.txt"
READ_AT = datetime(2026, 10, 6, 10, 16, 5, tzinfo=UTC)


def test_parses_real_capture_fixture() -> None:
    raw = FIXTURE.read_text(encoding="utf-8")
    snap = parse_inverter_cgi(raw, read_at=READ_AT)
    assert snap.serial_no == "180501024A150053"
    assert snap.firmware_version == "91004C"
    assert snap.inverter_model == "501"
    assert snap.inverter_temperature_c == pytest.approx(28.7)
    assert snap.current_power_w == 520
    assert snap.yield_today_kwh == pytest.approx(0.5)
    assert snap.total_yield_kwh is None  # raw value was "u" (unknown)
    assert snap.alerts is False
    assert snap.last_updated == READ_AT
    assert snap.raw == raw


def test_parses_ui_style_values_with_units() -> None:
    # The portal-style rendering: units appended to every value.
    raw = "SN-1;V1.0;501;0.0\u2103;0W;0.000000kWh;ukWh;NO"
    snap = parse_inverter_cgi(raw, read_at=READ_AT)
    assert snap.inverter_model == "501"
    assert snap.inverter_temperature_c == pytest.approx(0.0)
    assert snap.current_power_w == 0
    assert snap.yield_today_kwh == pytest.approx(0.0)
    assert snap.total_yield_kwh is None  # "ukWh" -> "u" -> unknown
    assert snap.alerts is False


def test_degree_sign_variants() -> None:
    assert (
        parse_inverter_cgi("s;f;m;-4.2\u00b0C;0;0;0;NO", READ_AT).inverter_temperature_c
        == pytest.approx(-4.2)
    )
    assert (
        parse_inverter_cgi("s;f;m;12.5C;0;0;0;NO", READ_AT).inverter_temperature_c
        == pytest.approx(12.5)
    )


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("NO", False),
        ("no", False),
        ("0", False),
        ("false", False),
        ("", False),
        ("YES", True),
        ("yes", True),
        ("1", True),
        ("true", True),
        ("WARN", True),
        ("ALERT", True),
    ],
)
def test_alerts_tokens(token: str, expected: bool) -> None:
    raw = f"s;f;m;0;0;0;0;{token}"
    assert parse_inverter_cgi(raw, READ_AT).alerts is expected


def test_unknown_total_yield_variants() -> None:
    for token in ("u", "unknown", "-", "\u2014", ""):
        snap = parse_inverter_cgi(f"s;f;m;0;0;0;{token};NO", READ_AT)
        assert snap.total_yield_kwh is None, token


def test_nul_padding_is_ignored() -> None:
    raw = "\x00\x00" * 20 + "s;f;m;10.0;100;1.2;3.4;NO" + "\x00\x00" * 20
    snap = parse_inverter_cgi(raw, read_at=READ_AT)
    assert snap.serial_no == "s"
    assert snap.current_power_w == 100


def test_truncated_response_does_not_crash() -> None:
    snap = parse_inverter_cgi("only-serial;and-firmware", read_at=READ_AT)
    assert snap.serial_no == "only-serial"
    assert snap.firmware_version == "and-firmware"
    assert snap.current_power_w is None
    assert snap.alerts is False


def test_model_zero_is_preserved_as_string() -> None:
    snap = parse_inverter_cgi("s;f;0;0;0;0;1;NO", READ_AT)
    assert snap.inverter_model == "0"
    assert snap.total_yield_kwh == pytest.approx(1.0)


def test_negative_temperature_and_decimal_power() -> None:
    snap = parse_inverter_cgi("s;f;m;-3.2;520.9;0.5;1;NO", READ_AT)
    assert snap.inverter_temperature_c == pytest.approx(-3.2)
    assert snap.current_power_w == 520  # decimals truncated, not a crash
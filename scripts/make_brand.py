"""Generate the HACS brand assets (icon.png + logo.png) for the integration.

Pure stdlib (zlib + struct) -- renders a flat "solar panel under the sun"
icon at 256x256 and writes both files under
custom_components/solis_local/brand/. Run: uv run python scripts/make_brand.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

SIZE = 256

# Flat colors (RGB).
BG = (16, 26, 46)          # dark navy
PANEL = (96, 148, 255)     # panel blue
GRID = (16, 26, 46)        # grid lines = bg
SUN = (255, 201, 60)       # warm yellow
RAY = (255, 201, 60)

OUT_DIR = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "solis_local"
    / "brand"
)


def _inside_circle(x: int, y: int, cx: float, cy: float, r: float) -> bool:
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _inside_round_rect(x: int, y: int, x0: int, y0: int, x1: int, y1: int, r: int) -> bool:
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    if (x - x0 < r or x1 - x < r) and (y - y0 < r or y1 - y < r):
        # Inside a corner quadrant: check the circle.
        cx = x0 + r if x - x0 < r else x1 - r
        cy = y0 + r if y - y0 < r else y1 - r
        return _inside_circle(x, y, cx, cy, r)
    return True


def _pixel(x: int, y: int) -> tuple[int, int, int]:
    # Sun top-right: disc + 8 rays.
    dxp, dyp = x - 190, y - 60
    if _inside_circle(x, y, 190, 60, 26):
        return SUN
    for i in range(8):
        angle = math.pi / 4 * i
        dx, dy = math.cos(angle), math.sin(angle)
        proj = dxp * dx + dyp * dy
        perp = abs(dxp * dy - dyp * dx)
        if 30 <= proj <= 44 and perp <= 5:
            return RAY

    # Solar panel: rounded rect with a 3x2 cell grid (3 cols, 2 rows).
    if _inside_round_rect(x, y, 40, 96, 216, 200, 14):
        inset = 6
        x0, y0, x1, y1 = 40 + inset, 96 + inset, 216 - inset, 200 - inset
        cols, rows = 3, 2
        cw = (x1 - x0) / cols
        rh = (y1 - y0) / rows
        ci = int((x - x0) // cw)
        ri = int((y - y0) // rh)
        cx0, cy0 = x0 + ci * cw, y0 + ri * rh
        cx1, cy1 = cx0 + cw, cy0 + rh
        pad = 3
        if _inside_round_rect(x, y, int(cx0 + pad), int(cy0 + pad), int(cx1 - pad), int(cy1 - pad), 4):
            return PANEL
        return GRID
    return BG


def _render() -> list[bytes]:
    """Return scanlines (each row: filter byte 0 + RGB bytes)."""
    rows = []
    for y in range(SIZE):
        row = bytearray([0])
        for x in range(SIZE):
            row.extend(_pixel(x, y))
        rows.append(bytes(row))
    return rows


def _png(rows: list[bytes]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = _png(_render())
    for name in ("icon.png", "logo.png"):
        (OUT_DIR / name).write_bytes(data)
        print(f"wrote {OUT_DIR / name} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
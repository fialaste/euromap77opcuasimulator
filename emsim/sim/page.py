"""Tiny PNG screenshots of the 'control panel' for GetPage/GetCurrentPage."""

from __future__ import annotations

import struct
import zlib

W, H = 320, 200
COLORS = {
    0: (120, 120, 120),
    1: (200, 60, 60),
    2: (230, 160, 40),
    3: (60, 120, 220),
    4: (60, 170, 90),
    5: (150, 90, 200),
}


def _png(pixels: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + row for row in pixels)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def page_png(machine, page_id: str) -> bytes:
    """A status bar coloured by ProductionStatus and one bar per temperature zone."""
    status = machine.get("MachineMESStatus/ProductionControl/ProductionStatus", 0) or 0
    top = COLORS.get(status, COLORS[0])
    temps = [
        float(v or 0)
        for p, v in machine.values.items()
        if p.endswith("/ActualTemperature") and "/TemperatureZones/" in p
    ]
    bg = (245, 245, 245)
    rows = []
    n = max(1, len(temps))
    bar_w = max(4, (W - 20) // n)
    for y in range(H):
        row = bytearray()
        for x in range(W):
            if y < 30:
                row += bytes(top)
            elif page_id in ("main", "temperatures") and 40 <= y < H - 10 and 10 <= x < 10 + bar_w * n:
                i = (x - 10) // bar_w
                h = int((H - 50) * min(1.0, temps[i] / 300.0)) if temps else 0
                filled = (H - 10 - y) <= h and (x - 10) % bar_w < bar_w - 2
                row += bytes((220, 90, 40) if filled else bg)
            else:
                row += bytes(bg)
        rows.append(bytes(row))
    return _png(rows)

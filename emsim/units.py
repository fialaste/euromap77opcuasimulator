"""Engineering units (UNECE Rec. 20) and default EURange values.

OPC 40077 does not prescribe units (UnitId = -1 in the NodeSet), so the simulator
uses the units usual on IMM control panels.
"""

from __future__ import annotations

import fnmatch

from asyncua import ua

UNECE_NS = "http://www.opcfoundation.org/UA/units/un/cefact"

# code -> (UnitId, display, description)
UNITS = {
    "CEL": (4408652, "°C", "degree Celsius"),
    "CMQ": (4410705, "cm³", "cubic centimetre"),
    "BAR": (4342098, "bar", "bar"),
    "MMT": (5066068, "mm", "millimetre"),
    "C16": (4403510, "mm/s", "millimetre per second"),
    "RPM": (5394509, "1/min", "revolutions per minute"),
    "C62": (4404786, "1", "one"),
}

# Node name pattern -> unit code. First match wins.
UNIT_RULES = [
    ("*Temperature*", "CEL"),
    ("*Volume*", "CMQ"),
    ("*Pressure*", "BAR"),
    ("*Stroke", "MMT"),
    ("*Position", "MMT"),
    ("ScrewDiameter", "MMT"),
    ("InjectionSpeed*", "C16"),
    ("PlastificationCircumferentialSpeed*", "C16"),
    ("PlastificationRotationalSpeed*", "RPM"),
    ("FlowIndex", "C62"),
]

# Default EURange by node name (overridable per profile by path pattern)
RANGE_RULES = [
    ("PowerUnits/*/ActualTemperature", (0.0, 100.0)),
    ("Moulds/*Temperature*", (0.0, 250.0)),
    ("*Temperature*", (0.0, 450.0)),
    ("CushionVolume", (0.0, 50.0)),
    ("CushionStroke", (0.0, 50.0)),
    ("PlastificationVolume", (0.0, 200.0)),
    ("Decompression*", (0.0, 20.0)),
    ("TransferVolume", (0.0, 200.0)),
    ("ScrewVolume", (0.0, 500.0)),
    ("BackPressure", (0.0, 300.0)),
    ("*HydraulicPressure*", (0.0, 250.0)),
    ("*Pressure*", (0.0, 2500.0)),
    ("ScrewDiameter", (0.0, 120.0)),
    ("*Stroke", (0.0, 400.0)),
    ("*Position", (0.0, 400.0)),
    ("InjectionSpeed*", (0.0, 500.0)),
    ("PlastificationCircumferentialSpeed*", (0.0, 1000.0)),
    ("PlastificationRotationalSpeed*", (0.0, 500.0)),
    ("FlowIndex", (0.0, 10.0)),
]


def unit_code(name: str) -> str | None:
    for pattern, code in UNIT_RULES:
        if fnmatch.fnmatchcase(name, pattern):
            return code
    return None


def eu_information(code: str | None) -> ua.EUInformation | None:
    if code is None:
        return None
    unit_id, display, desc = UNITS[code]
    return ua.EUInformation(
        NamespaceUri=UNECE_NS,
        UnitId=unit_id,
        DisplayName=ua.LocalizedText(display),
        Description=ua.LocalizedText(desc),
    )


def unit_display(code: str | None) -> str:
    return UNITS[code][1] if code else ""


def default_range(path: str, name: str, overrides: dict[str, list[float]] | None = None) -> tuple[float, float]:
    for pattern, rng in (overrides or {}).items():
        if fnmatch.fnmatchcase(path, pattern):
            return float(rng[0]), float(rng[1])
    for pattern, rng in RANGE_RULES:
        target = path if "/" in pattern else name
        if fnmatch.fnmatchcase(target, pattern) or fnmatch.fnmatchcase(path, "*/" + pattern):
            return rng
    return 0.0, 1000.0

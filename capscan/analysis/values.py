"""The part for each stitching capacitor: value, dielectric, package and voltage rating.

The voltage across a stitching capacitor is the difference between its two plane nets,
read from their names (+3V3, 1V8, 5V, VBUS ...). Ground is 0 V.
"""

from __future__ import annotations

import re
from typing import Optional

from capscan.analysis.references import looks_like_ground
from capscan.analysis.rules import VOLTAGE_RATINGS, ScanSettings

_VxV = re.compile(r"(?<![A-Z0-9.])[+-]?(\d+)V(\d+)", re.I)          # 3V3, +1V8, 0V9
_XV = re.compile(r"(?<![A-Z0-9.])([+-]?\d+(?:\.\d+)?)V(?![A-Z0-9])", re.I)   # 5V, +12V, 3.3V
_XV_PREFIX = re.compile(r"(?<![A-Z0-9.])([+-]?\d+(?:\.\d+)?)V", re.I)        # 12V_IN, 5VA
_NAMED = {"VBUS": 5.0, "VUSB": 5.0, "VBAT": 4.2, "VSYS": 5.0}


def net_voltage(name: str) -> Optional[float]:
    """The net's voltage as its name gives it, or None if the name does not say."""
    bare = name.rsplit("/", 1)[-1]
    if looks_like_ground(bare):
        return 0.0
    m = _VxV.search(bare)
    if m:
        sign = -1.0 if bare.lstrip().startswith("-") else 1.0
        return sign * float(f"{m.group(1)}.{m.group(2)}")
    for rx in (_XV, _XV_PREFIX):
        m = rx.search(bare)
        if m:
            return float(m.group(1))
    for key, v in _NAMED.items():
        if bare.upper().startswith(key):
            return v
    return None


def voltage_rating(net_a: str, net_b: str, settings: ScanSettings) -> float:
    va, vb = net_voltage(net_a), net_voltage(net_b)
    if va is None or vb is None:
        need = settings.unknown_voltage_rating_v
    else:
        need = max(settings.voltage_derating * abs(va - vb), settings.min_voltage_rating_v)
    for r in VOLTAGE_RATINGS:
        if r >= need - 1e-9:
            return r
    return VOLTAGE_RATINGS[-1]


def format_voltage(v: float) -> str:
    return f"{v:g} V"


def part_description(value: str, dielectric: str, voltage: float, package: str) -> str:
    return f"{value} {dielectric} {voltage:g} V {package}"

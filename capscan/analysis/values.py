"""The part for each stitching capacitor: value, dielectric, package and voltage rating.

The voltage across a stitching capacitor is the difference between its two plane nets,
read from their names (+3V3, 1V8, 5V, VBUS ...). Ground is 0 V.

The package comes from the edge rate: the largest allowed one whose mounted inductance
slows an edge by no more than the budget, or several of the smallest in parallel. The
value is the largest that package stocks at the rating (docs/research.md).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional, Sequence

from capscan.analysis.references import looks_like_ground
from capscan.analysis.rules import CAP_VALUES_F, PACKAGES, VOLTAGE_RATINGS, Package, ScanSettings

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


@dataclass
class Part:
    value: str
    dielectric: str
    package: str
    voltage_v: float
    count: int = 1          # in parallel, each with its own vias
    note: str = ""


def format_farads(c: float) -> str:
    for unit, scale in (("uF", 1e-6), ("nF", 1e-9), ("pF", 1e-12)):
        if c >= scale - 1e-18:
            return f"{c / scale:g}{unit}"
    return f"{c:g}F"


def stitch_package(settings: ScanSettings, signals: Sequence[str] = ()) -> Package:
    """The package the edge rates of ``signals`` call for, before the voltage is known."""
    packages = list(PACKAGES.values())
    budget = settings.max_cap_inductance_nh(signals)
    fits = [p for p in packages if p.mounted_nh <= budget + 1e-9]
    return fits[-1] if fits else packages[0]


def choose_part(net_a: str, net_b: str, settings: ScanSettings,
                signals: Sequence[str] = ()) -> Part:
    """The part between plane nets ``net_a`` and ``net_b`` returning ``signals`` (the
    default signal when none): the fastest of them sets the package."""
    rating = voltage_rating(net_a, net_b, settings)
    budget = settings.max_cap_inductance_nh(signals)
    allowed = list(PACKAGES.values())
    first = stitch_package(settings, signals)
    notes = []
    # The rating may need a larger body than the inductance asks for.
    for pkg in allowed[allowed.index(first):]:
        value = next((c for c in CAP_VALUES_F if c <= pkg.max_value_f(rating) + 1e-18), 0.0)
        if value >= settings.min_cap_value_f(signals):
            break
    else:
        pkg, value = first, settings.min_cap_value_f(signals)
        notes.append(f"no {first.name} or larger package stocks "
                     f"{format_farads(value)} at {rating:g} V: check availability")
    if pkg is not first:
        notes.append(f"{pkg.name}, not {first.name}: the smaller one does not reach "
                     f"{rating:g} V")
    count = max(1, math.ceil(pkg.mounted_nh / budget - 1e-9))
    if count > 1:
        fastest = min(signals, key=lambda n: settings.max_cap_inductance_nh([n]), default="")
        edge = settings.edge_rate_of(fastest) if fastest else settings.edge_rate_ps
        notes.append(f"{count} in parallel, each with its own vias: one {pkg.name} "
                     f"(~{pkg.mounted_nh:g} nH) is over the {budget:.2g} nH a "
                     f"{edge:g} ps edge{' on ' + fastest if fastest else ''} allows")
    return Part(settings.cap_value.strip() or format_farads(value), "X7R", pkg.name, rating,
                count, "; ".join(notes))


def part_basis(settings: ScanSettings) -> str:
    """Why the parts are what they are, in a sentence or two."""
    pkg = stitch_package(settings)
    value = (f"the value is {settings.cap_value.strip()}, as set" if settings.cap_value.strip()
             else "the value is the largest that package stocks at the rating, up to "
             f"{format_farads(CAP_VALUES_F[0])}")
    own = sorted(set(settings.net_edge_rate_ps) | set(settings.net_impedance_ohm))
    return (f"{settings.edge_rate_ps:g} ps edges on {settings.trace_impedance_ohm:g} Ω traces "
            f"allow a cap {settings.max_cap_inductance_nh():.2g} nH of mounted inductance, so "
            f"{pkg.name} (about {pkg.mounted_nh:g} nH); {value}."
            + (f" {len(own)} net(s) have their own edge rate or impedance; a cap takes the "
               "package its fastest signal needs." if own else ""))


def format_voltage(v: float) -> str:
    return f"{v:g} V"


def part_description(value: str, dielectric: str, voltage: float, package: str) -> str:
    return f"{value} {dielectric} {voltage:g} V {package}"

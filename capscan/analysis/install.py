"""Putting the scan's capacitors and return vias on the board: the choices and the
geometry, with no KiCad in them (``capscan.kicad.install`` does the adding).

Each new capacitor gets a via per pad down to the plane of that pad's net, beside the pad
and joined to it by a short track. A via only goes where it keeps the design rules'
clearance from every other net's track, pad and via on every layer its barrel passes,
the hole-to-hole and board-edge minimums too. Zones are left out: KiCad's refill clears
them round the new copper.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import shapely
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from capscan.analysis.rules import PACKAGES
from capscan.geometry.copper import poly_to_shapely
from capscan.model.board import BoardSnapshot
from capscan.model.layers import B_CU, F_CU

Point = Tuple[float, float]

THROUGH, BLIND, BURIED, MICRO = "through", "blind", "buried", "micro"
#: KiCad 9 has one type for blind and buried vias.
BLIND_BURIED = "blind_buried"

#: KiCad's standard library footprint for each package a stitching cap is picked in.
LIBRARY_FOOTPRINTS = {"0201": "C_0201_0603Metric", "0402": "C_0402_1005Metric",
                      "0603": "C_0603_1608Metric", "0805": "C_0805_2012Metric",
                      "1206": "C_1206_3216Metric"}
LIBRARY = "Capacitor_SMD"


@dataclass(frozen=True)
class ViaSize:
    diameter_mm: float
    drill_mm: float

    @property
    def label(self) -> str:
        return f"{self.diameter_mm:g} / {self.drill_mm:g} mm"


@dataclass
class BoardRules:
    """The design rules a new via and track must keep, as the project sets them."""
    clearance_mm: float = 0.2               # the Default net class
    class_clearance_mm: Dict[str, float] = field(default_factory=dict)
    track_width_mm: float = 0.2
    hole_clearance_mm: float = 0.25
    hole_to_hole_mm: float = 0.25
    edge_clearance_mm: float = 0.5
    min_via_diameter_mm: float = 0.5
    min_via_drill_mm: float = 0.3
    min_annular_mm: float = 0.1
    micro_via: ViaSize = ViaSize(0.3, 0.1)
    #: The board's predefined via sizes, then the net classes' own.
    via_sizes: List[ViaSize] = field(default_factory=list)
    #: Via types the project says no to (KiCad 6 "allow" switches, custom ``disallow``
    #: rules).
    disallowed: Set[str] = field(default_factory=set)
    #: Where each rule came from, for the log.
    notes: List[str] = field(default_factory=list)

    def clearance(self, netclass: str) -> float:
        return self.class_clearance_mm.get(netclass, self.clearance_mm)

    def meets(self, size: ViaSize) -> bool:
        return (size.diameter_mm >= self.min_via_diameter_mm - 1e-6
                and size.drill_mm >= self.min_via_drill_mm - 1e-6
                and (size.diameter_mm - size.drill_mm) / 2 >= self.min_annular_mm - 1e-6)


# ---------------------------------------------------------------------------- choices

def allowed_via_types(snap: BoardSnapshot, rules: BoardRules) -> List[str]:
    """Via types a new via may be: through, and any other the board already uses (the
    sign its fab process makes them) that the rules do not disallow. KiCad 9 and 10 have
    no switch for them in Board Setup."""
    out = [THROUGH]
    if len(snap.stackup.copper) <= 2:
        return out
    used = {v.via_type for v in snap.vias}
    if BLIND_BURIED in used:
        used |= {BLIND, BURIED}
    for t in (MICRO, BLIND, BURIED):
        if t in used and t not in rules.disallowed:
            out.append(t)
    return out


def default_via_size(snap: BoardSnapshot, rules: BoardRules, nets: Iterable[str]) -> ViaSize:
    """The through via size most used on ``nets`` (the planes) that meets the rules, else
    the first of the board's sizes that does, else the smallest the rules allow."""
    nets = set(nets)
    sizes = Counter(ViaSize(round(v.diameter_mm, 4), round(v.drill_mm, 4))
                    for v in snap.vias if v.net in nets and v.via_type == THROUGH)
    for size, _ in sizes.most_common():
        if rules.meets(size):
            return size
    for size in rules.via_sizes:
        if rules.meets(size):
            return size
    drill = rules.min_via_drill_mm
    return ViaSize(max(rules.min_via_diameter_mm, drill + 2 * rules.min_annular_mm), drill)


def via_span(stack: Sequence[int], start: int, end: int,
             allowed: Sequence[str]) -> Tuple[str, int, int]:
    """The via type and the layers it runs between, for a via from ``start`` to ``end``
    (copper layer ids): the shortest the allowed types make. A microvia joins neighbouring
    layers, one of them outer; a blind via starts on an outer layer; a buried via has
    neither end outside."""
    i, j = sorted((stack.index(start), stack.index(end)))
    top, bottom = stack[i], stack[j]
    outer = i == 0 or j == len(stack) - 1
    if i == 0 and j == len(stack) - 1:
        return THROUGH, stack[0], stack[-1]
    if outer and j - i == 1 and MICRO in allowed:
        return MICRO, top, bottom
    if outer and BLIND in allowed:
        return BLIND, top, bottom
    if not outer and BURIED in allowed:
        return BURIED, top, bottom
    return THROUGH, stack[0], stack[-1]


def next_references(taken: Iterable[str], prefix: str, start: int, n: int) -> List[str]:
    """``n`` references ``prefix`` + number from ``start`` up, none of them ``taken``."""
    taken = set(taken)
    out, k = [], max(1, start)
    while len(out) < n:
        ref = f"{prefix}{k}"
        if ref not in taken:
            out.append(ref)
        k += 1
    return out


def split_reference(text: str) -> Optional[Tuple[str, int]]:
    """``"C100"`` as ``("C", 100)``; None unless it is letters (or any prefix) then a
    number."""
    m = re.fullmatch(r"\s*([A-Za-z_][^\s]*?)(\d+)\s*", text or "")
    return (m.group(1), int(m.group(2))) if m else None


def first_free_number(taken: Iterable[str], prefix: str) -> int:
    """One more than the highest ``prefix`` number on the board."""
    pat = re.compile(re.escape(prefix) + r"(\d+)$")
    nums = [int(m.group(1)) for r in taken for m in [pat.match(r)] if m]
    return max(nums, default=0) + 1


def board_footprints(snap: BoardSnapshot, package: str) -> List[Tuple[str, int]]:
    """Library footprints on the board for a capacitor in ``package`` ("0402" matches
    ``Capacitor_SMD:C_0402_1005Metric``), most used first, with how many there are."""
    pat = re.compile(r"(^|:)C_" + re.escape(package) + r"(_|$)")
    counts = Counter(f.footprint for f in snap.footprints if f.footprint
                     and pat.search(f.footprint) and len(f.pad_ids) == 2)
    return counts.most_common()


def parts_at(position: Point, angle_deg: float, count: int, pitch_mm: float) -> List[Point]:
    """Where ``count`` parts in parallel go: side by side across the pads' axis."""
    a = math.radians(angle_deg)
    across = (math.sin(a), math.cos(a))     # square to the axis (cos a, -sin a)
    return [(round(position[0] + across[0] * (k - (count - 1) / 2) * pitch_mm, 4),
             round(position[1] + across[1] * (k - (count - 1) / 2) * pitch_mm, 4))
            for k in range(count)]


def part_pitch(package: str, via: ViaSize, rules: BoardRules) -> float:
    body_w = PACKAGES[package].body_mm[1] if package in PACKAGES else 1.0
    return round(max(body_w + 0.5, via.diameter_mm + rules.clearance_mm) + 0.1, 3)


def plane_layer(snap: BoardSnapshot, net: str, at: Point, layers: Sequence[int],
                reach_mm: float = 1.0) -> Optional[int]:
    """The layer of ``layers`` on which ``net``'s plane copper is nearest ``at`` (within
    ``reach_mm``), or None."""
    found = nearest_plane(snap, net, at, layers, reach_mm)
    return None if found is None else found[1]


def nearest_plane(snap: BoardSnapshot, net: str, at: Point, layers: Sequence[int],
                  reach_mm: float = 1.0) -> Optional[Tuple[float, int]]:
    """How far ``net``'s nearest plane copper on ``layers`` is from ``at``, and its layer;
    None beyond ``reach_mm``."""
    p = shapely.Point(at)
    best = None
    for z in snap.items_on_net(net)[3]:
        for lid, polys in z.filled.items():
            if lid not in layers:
                continue
            for poly in polys:
                g = poly_to_shapely(poly)
                if g is None:
                    continue
                d = g.distance(p)
                if d <= reach_mm and (best is None or d < best[0]):
                    best = (d, lid)
    return best


# ---------------------------------------------------------------------------- clearance

@dataclass
class _Copper:
    geom: BaseGeometry
    net: str
    clearance: float


class Keepout:
    """Every net's copper per layer, and every hole: where a new via or track may go."""

    def __init__(self, snap: BoardSnapshot, rules: BoardRules, board: BaseGeometry):
        self.rules = rules
        self._trees: Dict[int, STRtree] = {}
        self.netclass = {n.name: n.netclass for n in snap.nets}
        self.layers: Dict[int, List[_Copper]] = {}
        self.holes: List[Tuple[Point, float]] = []
        for t in snap.tracks:
            line = shapely.LineString([t.start, t.mid, t.end] if t.is_arc else [t.start, t.end])
            self._add(t.layer, line.buffer(t.width_mm / 2), t.net)
        for v in snap.vias:
            for lid in v.layers:
                d = v.pad_diameter(lid) or v.drill_mm
                self._add(lid, shapely.Point(v.position).buffer(d / 2), v.net)
            self.holes.append((v.position, v.drill_mm))
        for p in snap.pads:
            for lid, shapes in p.shapes.items():
                for s in shapes:
                    g = poly_to_shapely(s)
                    if g is not None:
                        self._add(lid, g, p.net)
            if p.drill_mm[0] > 0:
                self.holes.append((p.position, max(p.drill_mm)))
        self.board = board

    def _add(self, layer: int, geom: BaseGeometry, net: str) -> None:
        c = self.rules.clearance(self.netclass.get(net, ""))
        self.layers.setdefault(layer, []).append(_Copper(geom, net, c))
        self._trees.pop(layer, None)

    def add_via(self, at: Point, size: ViaSize, net: str, layers: Iterable[int]) -> None:
        for lid in layers:
            self._add(lid, shapely.Point(at).buffer(size.diameter_mm / 2), net)
        self.holes.append((at, size.drill_mm))

    def add_copper(self, layer: int, geom: BaseGeometry, net: str) -> None:
        self._add(layer, geom, net)

    def _tree(self, layer: int) -> Optional[STRtree]:
        if layer not in self._trees and self.layers.get(layer):
            self._trees[layer] = STRtree([c.geom for c in self.layers[layer]])
        return self._trees.get(layer)

    def clear(self, geom: BaseGeometry, net: str, layer: int) -> bool:
        """Whether ``geom`` of ``net`` on ``layer`` keeps clear of every other net."""
        own = self.rules.clearance(self.netclass.get(net, ""))
        tree = self._tree(layer)
        if tree is None:
            return True
        reach = max([own] + list(self.rules.class_clearance_mm.values()))
        for i in tree.query(geom.buffer(reach)):
            c = self.layers[layer][int(i)]
            if c.net == net and net:
                continue
            if c.geom.distance(geom) < max(own, c.clearance) - 1e-6:
                return False
        return True

    def via_fits(self, at: Point, size: ViaSize, net: str, layers: Iterable[int]) -> bool:
        r = size.diameter_mm / 2
        if not self.board.is_empty and not self.board.buffer(
                -(self.rules.edge_clearance_mm + r)).contains(shapely.Point(at)):
            return False
        for p, d in self.holes:
            gap = math.hypot(p[0] - at[0], p[1] - at[1]) - (d + size.drill_mm) / 2
            if gap < self.rules.hole_to_hole_mm - 1e-6:
                return False
        pad = shapely.Point(at).buffer(r, quad_segs=16)
        return all(self.clear(pad, net, lid) for lid in layers)


@dataclass
class PadVia:
    """A pad's via and the track from the pad to it."""
    net: str
    position: Point
    track: Tuple[Point, Point]
    via_type: str
    start_layer: int
    end_layer: int


def _extent(half: Tuple[float, float], angle: float) -> float:
    """How far a rectangle of half-sizes ``half`` reaches from its center along ``angle``."""
    c, s = abs(math.cos(angle)), abs(math.sin(angle))
    return min(half[0] / c if c > 1e-9 else math.inf, half[1] / s if s > 1e-9 else math.inf)


def via_beside_pad(keepout: Keepout, pad_center: Point, outward: Point,
                   pad_half: Tuple[float, float], net: str, size: ViaSize,
                   layers: Sequence[int], surface: int, track_mm: float,
                   reach_mm: float = 1.5) -> Optional[Tuple[Point, Tuple[Point, Point]]]:
    """The nearest spot beside a pad for its via, and the track to it: straight out from
    the pad's far end first, then turning to its sides. ``pad_half`` is along and across
    ``outward``."""
    base = math.atan2(outward[1], outward[0])
    tried = []
    for turn in (0, 30, -30, 60, -60, 90, -90):
        a = math.radians(turn)
        start = _extent(pad_half, a) + size.diameter_mm / 2
        k = 0
        while k * 0.1 <= reach_mm:
            tried.append((start + k * 0.1 + abs(turn) / 300.0, base + a, start + k * 0.1))
            k += 1
    tried.sort()
    for _, ang, d in tried:
        at = (round(pad_center[0] + d * math.cos(ang), 4),
              round(pad_center[1] + d * math.sin(ang), 4))
        if not keepout.via_fits(at, size, net, layers):
            continue
        track = shapely.LineString([pad_center, at]).buffer(track_mm / 2)
        if keepout.clear(track, net, surface):
            return at, (pad_center, at)
    return None


def spot_for_via(keepout: Keepout, near: Point, size: ViaSize, net: str,
                 layers: Sequence[int], reach_mm: float = 1.0) -> Optional[Point]:
    """The free spot nearest ``near`` for a via of ``net``."""
    steps = []
    n = int(reach_mm / 0.1)
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            d = math.hypot(i, j) * 0.1
            if d <= reach_mm:
                steps.append((d, i * 0.1, j * 0.1))
    steps.sort()
    for _, dx, dy in steps:
        at = (round(near[0] + dx, 4), round(near[1] + dy, 4))
        if keepout.via_fits(at, size, net, layers):
            return at
    return None


def surface_of(side: str) -> int:
    return F_CU if side == "top" else B_CU

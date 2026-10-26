"""Where the stitching capacitors go.

Every split crossing and every via that changes to a plane of another net needs a
capacitor between those two nets within reach (:class:`Need`). Existing stitching
capacitors are credited first. The rest are covered greedily: each new capacitor goes
where it reaches the most signals still uncovered, up to
:attr:`~capscan.analysis.rules.ScanSettings.signals_per_cap` of them, then is moved to the
nearest spot free of other copper and parts on the board side closest to both planes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import shapely
from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from capscan.analysis.references import References
from capscan.analysis.results import (
    EXISTING, NEW_CAP, OTHER_PLANE, SPLIT, UNFIXABLE, VIA_CAP, Crossing, ExistingCap, StitchCap,
    Transition,
)
from capscan.analysis.rules import ScanSettings
from capscan.analysis.values import voltage_rating
from capscan.geometry.copper import poly_to_shapely
from capscan.model.board import BoardSnapshot
from capscan.model.layers import B_CU, F_CU

Point = Tuple[float, float]
SEARCH_STEP_MM = 0.25


@dataclass
class Need:
    item: object                    # the Crossing or Transition
    position: Point
    pair: Tuple[str, str]           # the two plane nets, sorted
    limit: float                    # furthest the capacitor may be
    key: str                        # one per return path (a differential pair shares one)
    ref_layers: Tuple[int, ...]
    split_dir: Optional[Point] = None
    served_by: str = ""


def needs_of(crossings: Sequence[Crossing], transitions: Sequence[Transition],
             refs: References, settings: ScanSettings) -> List[Need]:
    out = []
    for c in crossings:
        if c.kind == SPLIT and c.net_a and c.net_b:
            out.append(Need(c, c.position, tuple(sorted((c.net_a, c.net_b))),
                            settings.max_cap_distance_mm, refs.signal_key(c.net),
                            (c.ref_layer,), c.split_dir))
    for t in transitions:
        if t.kind == VIA_CAP:
            out.append(Need(t, t.position, tuple(sorted((t.net_a, t.net_b))),
                            settings.via_cap_distance_mm, refs.signal_key(t.net),
                            tuple(t.ref_layers)))
    return out


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def credit_existing(needs: List[Need], existing: List[ExistingCap],
                    settings: ScanSettings) -> None:
    """Existing stitching capacitors take the needs within reach, nearest first, each up
    to signals_per_cap signals; decoupling capacitors within reach are flagged."""
    by_pair: Dict[Tuple[str, str], List[ExistingCap]] = {}
    for e in existing:
        by_pair.setdefault(tuple(sorted((e.net_a, e.net_b))), []).append(e)
    load: Dict[str, set] = {}
    pairs = []
    for i, n in enumerate(needs):
        for e in by_pair.get(n.pair, ()):
            d = _dist(n.position, e.position)
            if d > n.limit:
                continue
            if e.role == "decoupling":
                if e.reference not in n.item.decoupling:
                    n.item.decoupling.append(e.reference)
                    e.flagged.append(n.item.id)
            else:
                pairs.append((d, i, e))
    pairs.sort(key=lambda x: x[0])
    for _, i, e in pairs:
        n = needs[i]
        if n.served_by:
            continue
        keys = load.setdefault(e.reference, set())
        if n.key not in keys and len(keys) >= settings.signals_per_cap:
            continue
        keys.add(n.key)
        n.served_by = e.reference
        n.item.status, n.item.covered_by = EXISTING, e.reference
        e.credited.append(n.item.id)


class Obstacles:
    """Copper and parts on one board side a new capacitor must keep clear of."""

    def __init__(self, snap: BoardSnapshot, side: str, board: BaseGeometry):
        layer = F_CU if side == "top" else B_CU
        geoms: List[BaseGeometry] = []
        for t in snap.tracks:
            if t.layer == layer:
                geoms.append(shapely.LineString([t.start, t.mid, t.end] if t.is_arc
                                                else [t.start, t.end]).buffer(t.width_mm / 2))
        for v in snap.vias:
            if layer in v.layers:
                geoms.append(shapely.Point(v.position).buffer(max(v.diameter_mm, 0.3) / 2))
        pads_by_id = {p.id: p for p in snap.pads}
        for p in snap.pads:
            for g in p.shapes.get(layer, []):
                s = poly_to_shapely(g)
                if s is not None:
                    geoms.append(s)
        for fp in snap.footprints:          # a part's body: round its pads on its side
            if fp.side != side:
                continue
            pts = [pads_by_id[i].position for i in fp.pad_ids if i in pads_by_id]
            if len(pts) >= 2:
                geoms.append(shapely.MultiPoint(pts).convex_hull.buffer(0.4))
        self.tree = STRtree(geoms) if geoms else None
        self.geoms = geoms
        self.board = board.buffer(-0.3) if not board.is_empty else board

    def free(self, shape: BaseGeometry) -> bool:
        if not self.board.is_empty and not self.board.contains(shape):
            return False
        if self.tree is None:
            return True
        return len(self.tree.query(shape, predicate="intersects")) == 0


def cap_outline(pos: Point, angle_deg: float, settings: ScanSettings) -> Polygon:
    l, w = settings.cap_body_mm
    c = settings.placement_clearance_mm
    r = box(-l / 2 - c, -w / 2 - c, l / 2 + c, w / 2 + c)
    # KiCad angles turn counter-clockwise on screen, which with y down is clockwise here.
    return translate(rotate(r, -angle_deg, origin=(0, 0)), pos[0], pos[1])


def _side(refs: References, snap: BoardSnapshot, layers: Sequence[int]) -> str:
    top = snap.stackup.board_thickness_mm
    zs = [refs.z_mid.get(l, top / 2) for l in layers]
    from_top, from_bottom = max(top - z for z in zs), max(zs)
    return "top" if from_top <= from_bottom + 0.05 else "bottom"


def _angle(split_dir: Optional[Point]) -> float:
    """Pads one each side of the split: the body runs across it."""
    if split_dir is None:
        return 0.0
    ax, ay = -split_dir[1], split_dir[0]
    a = math.degrees(math.atan2(-ay, ax)) % 180.0
    return round(a / 45.0) * 45.0 % 180.0      # neat angles: 0, 45, 90, 135


def _spot(center: Point, members: List[Need], angle: float, obstacles: Obstacles,
          settings: ScanSettings, along: Point) -> Optional[Point]:
    """The free spot nearest ``center`` from which every member is within reach."""
    reach = min(n.limit for n in members)
    perp = (-along[1], along[0])
    offsets = []
    n = int(reach / SEARCH_STEP_MM) + 1
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            a, b = i * SEARCH_STEP_MM, j * SEARCH_STEP_MM
            if math.hypot(a, b) <= reach:
                offsets.append((math.hypot(a, b), a, b))
    offsets.sort()
    for _, a, b in offsets:
        p = (center[0] + a * along[0] + b * perp[0], center[1] + a * along[1] + b * perp[1])
        if any(_dist(p, m.position) > m.limit for m in members):
            continue
        if obstacles.free(cap_outline(p, angle, settings)):
            return (round(float(p[0]), 3), round(float(p[1]), 3))
    return None


def place_caps(needs: List[Need], snap: BoardSnapshot, refs: References,
               settings: ScanSettings) -> List[StitchCap]:
    obstacles: Dict[str, Obstacles] = {}

    def obs(side):
        if side not in obstacles:
            obstacles[side] = Obstacles(snap, side, refs.board)
        return obstacles[side]

    caps: List[StitchCap] = []
    groups: Dict[Tuple[str, str], List[Need]] = {}
    for n in needs:
        if not n.served_by:
            groups.setdefault(n.pair, []).append(n)
    for pair in sorted(groups):
        left = list(groups[pair])
        while left:
            pos = np.array([n.position for n in left])
            limits = np.array([n.limit for n in left])
            best = None
            for i, seed in enumerate(left):
                d = np.hypot(pos[:, 0] - seed.position[0], pos[:, 1] - seed.position[1])
                near = [left[j] for j in np.flatnonzero(d <= np.minimum(limits, limits[i]))]
                chosen = _take_signals(near, seed, settings.signals_per_cap)
                score = (len({m.key for m in chosen}), len(chosen), -i)
                if best is None or score > best[0]:
                    best = (score, chosen, seed)
            _, members, seed = best
            center = tuple(float(v) for v in np.mean([m.position for m in members], axis=0))
            if any(_dist(center, m.position) > m.limit for m in members):
                center = seed.position
            split_dir = next((m.split_dir for m in members if m.split_dir), None)
            angle = _angle(split_dir)
            layers = sorted({l for m in members for l in m.ref_layers})
            side = _side(refs, snap, layers)
            along = split_dir or (1.0, 0.0)
            spot = _spot(center, members, angle, obs(side), settings, along)
            crowded = False
            if spot is None:
                other = "bottom" if side == "top" else "top"
                spot = _spot(center, members, angle, obs(other), settings, along)
                if spot is not None:
                    side = other
                else:
                    spot, crowded = (round(center[0], 3), round(center[1], 3)), True
            cap = StitchCap(
                id=f"CS{len(caps) + 1}", net_a=pair[0], net_b=pair[1],
                value=settings.cap_value, dielectric=settings.cap_dielectric,
                package=settings.cap_package,
                voltage_v=voltage_rating(pair[0], pair[1], settings),
                position=spot, angle_deg=angle, side=side,
                serves=[m.item.id for m in members],
                signals=sorted({m.item.net for m in members}), ref_layers=layers,
                crowded=crowded,
                note="No free spot within reach: place by hand near here" if crowded else "")
            caps.append(cap)
            for m in members:
                m.served_by = cap.id
                m.item.status, m.item.covered_by = NEW_CAP, cap.id
            o = obs(side)          # the new part is an obstacle for the next ones
            o.geoms.append(cap_outline(spot, angle, settings))
            o.tree = STRtree(o.geoms)
            served = {id(m) for m in members}
            left = [n for n in left if id(n) not in served]
    return caps


def _take_signals(near: List[Need], seed: Need, per_cap: int) -> List[Need]:
    """Of ``near``, those of at most ``per_cap`` signals: the seed's first, then the
    nearest. Every need of a chosen signal (both halves of a pair) comes along."""
    keys: List[str] = [seed.key]
    for n in sorted(near, key=lambda n: _dist(n.position, seed.position)):
        if n.key not in keys and len(keys) < per_cap:
            keys.append(n.key)
    return [n for n in near if n.key in keys]


def mark_unfixable(crossings: Sequence[Crossing]) -> None:
    """Voids: where a stripline's other plane is solid it carries the return current
    (the impedance still changes); otherwise only rerouting or filling helps."""
    for c in crossings:
        if not c.status:
            c.status = OTHER_PLANE if c.severity < 1.0 else UNFIXABLE

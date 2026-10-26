"""Vias (and through-hole pins) where a signal changes layer, and with it, reference plane.

The signal's return current has to change planes at the same spot. Two planes of the
same net are joined by a return via of that net next to the signal via; two planes of
different nets need a stitching capacitor between them there. Two layers that share a
reference plane need neither: the return current stays on that plane, round the antipad.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from capscan.analysis.references import References, looks_like_ground
from capscan.analysis.results import (
    EXISTING_VIA, NEW_VIA, VIA_CAP, VIA_OK, VIA_STITCH, StitchVia, Transition,
)
from capscan.analysis.rules import ScanSettings
from capscan.model.board import BoardSnapshot
from capscan.progress import NULL, ProgressReporter

#: A track end this much beyond a via's pad still connects to it.
END_TOL_MM = 0.05
#: Of the ring round a via, the share a plane net must cover to be its reference there.
MIN_REF_SHARE = 0.3


def _layers_used(pos, radius: float, net: str, tracks_by_net, pads_by_net) -> List[int]:
    """The copper layers on which ``net``'s routing leaves the barrel at ``pos``."""
    used = set()
    r2 = (radius + END_TOL_MM) ** 2
    for t in tracks_by_net.get(net, ()):
        for e in (t.start, t.end):
            if (e[0] - pos[0]) ** 2 + (e[1] - pos[1]) ** 2 <= r2:
                used.add(t.layer)
    for p in pads_by_net.get(net, ()):        # a via in an SMD pad
        if p.pad_type == "smd" and (p.position[0] - pos[0]) ** 2 + \
                (p.position[1] - pos[1]) ** 2 <= max(r2, 0.25):
            used.update(p.shapes)
    return sorted(used)


def _ref_entries(refs: References, layer: int, pos, r_in: float) -> List[Tuple[int, str]]:
    out = []
    for ref_layer, h in refs.refs(layer):
        net, share = refs.planes[ref_layer].majority_net(pos, r_in, r_in + max(1.0, 3.0 * h))
        if net and share >= MIN_REF_SHARE:
            out.append((ref_layer, net))
    return out


def _pick(nets) -> str:
    nets = sorted(nets)
    ground = [n for n in nets if looks_like_ground(n)]
    return (ground or nets)[0]


def find_transitions(snap: BoardSnapshot, refs: References, settings: ScanSettings,
                     progress: ProgressReporter = NULL
                     ) -> Tuple[List[Transition], List[StitchVia]]:
    signals = set(refs.signal_nets)
    tracks_by_net: Dict[str, list] = {}
    for t in snap.tracks:
        tracks_by_net.setdefault(t.net, []).append(t)
    pads_by_net: Dict[str, list] = {}
    for p in snap.pads:
        pads_by_net.setdefault(p.net, []).append(p)
    order = [c.id for c in snap.stackup.copper]

    # Barrels: vias, and plated through-hole pins.
    barrels = [(v.id, v.net, v.position, max(v.diameter_mm, v.drill_mm) / 2.0)
               for v in snap.vias if v.net in signals]
    barrels += [(p.id, p.net, p.position, max(p.drill_mm) / 2.0 + 0.2)
                for p in snap.pads if p.net in signals and p.pad_type == "pth"]

    # Return paths already there: the vias and through-hole pins of each plane net.
    returns: Dict[str, List[Tuple[str, Tuple[float, float], List[int]]]] = {}
    for v in snap.vias:
        if v.net in refs.plane_nets:
            returns.setdefault(v.net, []).append((v.id, v.position, list(v.layers)))
    for p in snap.pads:
        if p.net in refs.plane_nets and p.pad_type == "pth":
            returns.setdefault(p.net, []).append((p.label, p.position, list(order)))

    out: List[Transition] = []
    new_vias: List[StitchVia] = []
    for k, (bid, net, pos, radius) in enumerate(barrels):
        if k % 100 == 0:
            progress.fraction(k / max(1, len(barrels)))
            progress.check_cancel()
        used = sorted(_layers_used(pos, radius, net, tracks_by_net, pads_by_net),
                      key=lambda l: order.index(l) if l in order else l)
        for la, lb in zip(used, used[1:]):
            ea = _ref_entries(refs, la, pos, radius + 0.1)
            eb = _ref_entries(refs, lb, pos, radius + 0.1)
            t = Transition(id="", kind=VIA_OK, net=net, via_id=bid, position=tuple(pos),
                           from_layer=la, to_layer=lb, from_refs=ea, to_refs=eb)
            out.append(t)
            if not ea or not eb or set(ea) & set(eb):
                continue                    # over a void (the crossings say so), or shared
            common = {n for _, n in ea} & {n for _, n in eb}
            if common:
                t.kind = VIA_STITCH
                t.net_a = _pick(common)
                ra = next(l for l, n in ea if n == t.net_a)
                rb = next(l for l, n in eb if n == t.net_a)
                t.ref_layers = (ra, rb)
                hit = _nearest_return(returns.get(t.net_a, ()), pos, (ra, rb),
                                      settings.stitch_via_distance_mm)
                if hit:
                    t.status, t.covered_by = EXISTING_VIA, hit
                else:
                    t.status = NEW_VIA
                    sv = StitchVia(id=f"SV{len(new_vias) + 1}", net=t.net_a,
                                   position=_via_spot(snap, pos, radius, net),
                                   layers=[ra, rb])
                    new_vias.append(sv)
                    t.covered_by = sv.id
            else:
                t.kind = VIA_CAP
                # The nearest reference of each layer carries most of the return current.
                ra, na = ea[0] if len(ea) == 1 else min(
                    ea, key=lambda e: dict(refs.refs(la)).get(e[0], 1e9))
                rb, nb = eb[0] if len(eb) == 1 else min(
                    eb, key=lambda e: dict(refs.refs(lb)).get(e[0], 1e9))
                t.net_a, t.net_b, t.ref_layers = na, nb, (ra, rb)
    for i, t in enumerate(out, 1):
        t.id = f"T{i}"
    for t in out:
        if t.status == NEW_VIA:
            sv = next(v for v in new_vias if v.id == t.covered_by)
            sv.serves = [t.id]
    return out, new_vias


def _nearest_return(candidates, pos, layers, limit) -> Optional[str]:
    best, best_d = None, limit
    for ident, p, spans in candidates:
        if not all(l in spans for l in layers):
            continue
        d = math.hypot(p[0] - pos[0], p[1] - pos[1])
        if d <= best_d:
            best, best_d = ident, d
    return best


def _via_spot(snap: BoardSnapshot, pos, radius: float, net: str) -> Tuple[float, float]:
    """Next to the signal via, in the direction with the most room."""
    others = [v.position for v in snap.vias if v.net != net]
    others += [t.end for t in snap.tracks if t.net != net]
    others += [t.start for t in snap.tracks if t.net != net]
    pts = np.asarray(others, dtype=float) if others else np.zeros((0, 2))
    dist = radius + 0.55
    best, best_room = None, -1.0
    for k in range(8):
        a = k * math.pi / 4
        c = (pos[0] + dist * math.cos(a), pos[1] + dist * math.sin(a))
        room = float(np.min(np.hypot(pts[:, 0] - c[0], pts[:, 1] - c[1]))) if len(pts) else 9.0
        if room > best_room:
            best, best_room = c, room
    return (round(best[0], 4), round(best[1], 4))

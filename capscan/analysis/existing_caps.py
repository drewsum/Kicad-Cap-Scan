"""Capacitors already on the board between two plane nets.

One that sits by itself is a stitching capacitor and counts. One next to an IC's pin on
either of its nets is that IC's decoupling capacitor: return current through it would
run through the IC's supply connection, so it never counts, and a crossing that has
only such a capacitor near it is flagged.
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Tuple

from capscan.analysis.references import References
from capscan.analysis.results import ExistingCap
from capscan.analysis.rules import ScanSettings
from capscan.model.board import BoardSnapshot

_CAP_REF = re.compile(r"^C\d", re.I)
_IC_REF = re.compile(r"^(U|IC|Q|IOC)\d", re.I)


def find_existing_caps(snap: BoardSnapshot, refs: References,
                       settings: ScanSettings) -> List[ExistingCap]:
    planes = set(refs.plane_nets)
    pads_by_id = {p.id: p for p in snap.pads}
    ic_pads: Dict[str, List[Tuple[str, Tuple[float, float]]]] = {}
    for fp in snap.footprints:
        if _IC_REF.match(fp.reference) and len(fp.pad_ids) > 2:
            for pid in fp.pad_ids:
                p = pads_by_id.get(pid)
                if p is not None and p.net in planes:
                    ic_pads.setdefault(p.net, []).append((p.label, p.position))

    out: List[ExistingCap] = []
    for fp in snap.footprints:
        if not _CAP_REF.match(fp.reference):
            continue
        nets = sorted({pads_by_id[pid].net for pid in fp.pad_ids
                       if pid in pads_by_id and pads_by_id[pid].net})
        if len(nets) != 2 or not all(n in planes for n in nets):
            continue
        near, best = "", settings.decoupling_ic_distance_mm
        for n in nets:
            for label, pos in ic_pads.get(n, ()):
                d = math.hypot(pos[0] - fp.position[0], pos[1] - fp.position[1])
                if d <= best:
                    near, best = label, d
        out.append(ExistingCap(reference=fp.reference, value=fp.value, net_a=nets[0],
                               net_b=nets[1], position=tuple(fp.position), side=fp.side,
                               role="decoupling" if near else "stitch", near_ic=near))
    return out

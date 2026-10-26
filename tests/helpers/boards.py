"""Small boards built in code, each with a known answer.

The standard board is 40 x 30 mm with four copper layers:

* F.Cu: signals, over In1.Cu.
* In1.Cu: a solid GND plane.
* In2.Cu: split down x = 20: +3V3 on the left, +1V8 on the right, with a 0.5 mm gap.
  The +3V3 half has a 4 x 4 mm hole at (5..9, 10..14).
* B.Cu: signals, over In2.Cu (their only reference).
"""

from __future__ import annotations

from typing import List, Optional

from capscan.model.board import (
    BoardSnapshot, Footprint, Net, Pad, Poly, Track, Via, Zone,
)
from capscan.model.layers import B_CU, F_CU, inner_cu
from capscan.model.stackup import build_stackup

IN1, IN2 = inner_cu(1), inner_cu(2)
W, H = 40.0, 30.0


def rect(x0, y0, x1, y1) -> List[tuple]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def stackup(cu=(F_CU, IN1, IN2, B_CU)):
    names = {F_CU: "F.Cu", B_CU: "B.Cu", IN1: "In1.Cu", IN2: "In2.Cu"}
    entries = []
    diel = [0.2, 1.065, 0.2]
    for i, lid in enumerate(cu):
        entries.append(("cu", lid, 0.035, "", 0.0))
        if i < len(cu) - 1:
            entries.append(("diel", -1, diel[i] if len(cu) == 4 else 0.3, "FR4", 4.4))
    return build_stackup(entries, list(cu), names)


def track(net, layer, a, b, w=0.15) -> Track:
    return Track(net=net, layer=layer, width_mm=w, start=a, end=b)


def zone(net, layer, outline, holes=(), ident=None) -> Zone:
    return Zone(id=ident or f"z-{net}-{layer}", net=net, name="", priority=0,
                filled={layer: [Poly(outline=outline, holes=list(holes))]})


def board(tracks=(), vias=(), pads=(), footprints=(), zones: Optional[list] = None,
          cu=(F_CU, IN1, IN2, B_CU)) -> BoardSnapshot:
    if zones is None:
        zones = [zone("GND", IN1, rect(0.2, 0.2, W - 0.2, H - 0.2)),
                 zone("+3V3", IN2, rect(0.2, 0.2, 19.75, H - 0.2),
                      holes=[rect(5, 10, 9, 14)]),
                 zone("+1V8", IN2, rect(20.25, 0.2, W - 0.2, H - 0.2))]
    nets = sorted({t.net for t in tracks} | {z.net for z in zones} | {v.net for v in vias}
                  | {p.net for p in pads})
    return BoardSnapshot(
        board_name="test.kicad_pcb", project_dir="", stackup=stackup(cu),
        nets=[Net(code=i, name=n) for i, n in enumerate(nets)], tracks=list(tracks),
        vias=list(vias), pads=list(pads), footprints=list(footprints), zones=list(zones),
        outline=[Poly(outline=rect(0, 0, W, H))])


def via(net, pos, ident=None, layers=(F_CU, IN1, IN2, B_CU)) -> Via:
    return Via(id=ident or f"v-{net}-{pos}", net=net, position=pos, drill_mm=0.3,
               diameter_mm=0.6, layers=list(layers))


def two_pad_part(ref, value, net_a, net_b, pos, side="top") -> tuple:
    """A footprint with one pad each side of ``pos`` (0402-like), and its pads."""
    layer = F_CU if side == "top" else B_CU
    pads = []
    for i, (net, dx) in enumerate(((net_a, -0.5), (net_b, 0.5)), 1):
        x, y = pos[0] + dx, pos[1]
        pads.append(Pad(id=f"{ref}-{i}", net=net, footprint=ref, number=str(i),
                        position=(x, y), pad_type="smd",
                        shapes={layer: [Poly(outline=rect(x - 0.25, y - 0.25, x + 0.25,
                                                          y + 0.25))]}))
    fp = Footprint(id=ref, reference=ref, value=value, position=pos, side=side,
                   pad_ids=[p.id for p in pads])
    return fp, pads


def ic(ref, pins, pos=(30.0, 25.0)) -> tuple:
    """An IC whose pins are at the given (net, (x, y)) places."""
    pads = [Pad(id=f"{ref}-{i}", net=net, footprint=ref, number=str(i), position=xy,
                pad_type="smd", shapes={F_CU: [Poly(outline=rect(xy[0] - 0.2, xy[1] - 0.2,
                                                                 xy[0] + 0.2, xy[1] + 0.2))]})
            for i, (net, xy) in enumerate(pins, 1)]
    return Footprint(id=ref, reference=ref, value="MCU", position=pos, side="top",
                     pad_ids=[p.id for p in pads]), pads


def scan(snap: BoardSnapshot, settings=None, all_nets: bool = True, nets=()):
    """``run_scan`` of every signal net (the window's default is to scan only picked nets),
    or of ``nets`` alone when ``all_nets`` is False."""
    from dataclasses import replace

    from capscan.analysis.rules import ScanSettings
    from capscan.analysis.scan import run_scan
    s = replace(settings or ScanSettings(), scan_all_nets=all_nets, selected_nets=list(nets))
    return run_scan(snap, s)

"""Which nets are planes, which are signals, and which plane layers each signal layer
returns its current on.

A return current flows on the nearest plane above and the nearest plane below its trace
(one for a microstrip on an outer layer, two for a stripline). A plane layer is any
copper layer that zone fills of plane nets cover a good share of; several plane nets on
one layer are a split plane.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

import shapely
import shapely.prepared
from shapely.geometry import Point as SPoint, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from capscan.analysis.rules import ScanSettings
from capscan.geometry.copper import poly_to_shapely
from capscan.model.board import BoardSnapshot

# Supply and ground names: GND, AGND, GNDA, VSS, VCC, VDD_IO, +3V3, 3.3V, 1V8, VBUS, -12V ...
_POWER = re.compile(
    r"""^(
        [+-]?\d+(\.\d+)?V(\d+)?([_A-Z0-9].*)? |      # +3V3, 5V, 1.8V, 12V_IN, 3V3A
        [+-]?\d+V\d+.* |
        .*GND.* | .*GROUND.* | .*EARTH.* | .*CHASSIS.* |
        V(CC|DD|SS|EE|BUS|BAT|IN|SYS|REF|IO|CORE|PP|MAIN|SUP|USB|AUX|DDQ|TT|PWR|LED)\w* |
        AVDD\w* | DVDD\w* | AVCC\w* | DVCC\w* | PVDD\w* | PVIN\w* |
        \+.* | PWR.* | .*_PWR | VPP | VM | VS
    )$""", re.X | re.I)
_GROUND = re.compile(r"GND|GROUND|EARTH|CHASSIS|^VSS|^AGND|^DGND|^PGND", re.I)


def looks_like_power(name: str) -> bool:
    return bool(_POWER.match(_bare(name)))


def looks_like_ground(name: str) -> bool:
    return bool(_GROUND.search(_bare(name)))


def _bare(name: str) -> str:
    """The net's own name, without its hierarchical sheet path ("/power/+3V3" -> "+3V3")."""
    return name.rsplit("/", 1)[-1]


def matches(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) or fnmatch.fnmatchcase(_bare(name), p)
               for p in patterns if p)


def _clean(g: BaseGeometry) -> BaseGeometry:
    if not g.is_valid:
        g = shapely.make_valid(g)
    if g.geom_type not in ("Polygon", "MultiPolygon"):
        parts = [p for p in getattr(g, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon")]
        g = shapely.union_all(parts) if parts else Polygon()
    return g


@dataclass
class PlaneLayer:
    """The plane copper on one copper layer: one region per plane net."""
    layer: int
    regions: Dict[str, BaseGeometry]
    union: BaseGeometry
    coverage: float                     # share of the board the plane nets cover
    _tree: Optional[STRtree] = field(default=None, repr=False)
    _names: List[str] = field(default_factory=list, repr=False)

    def __post_init__(self):
        self._names = list(self.regions)
        self._tree = STRtree([self.regions[n] for n in self._names]) if self._names else None
        self._prepared = {n: shapely.prepared.prep(g) for n, g in self.regions.items()}

    def nets_touching(self, geom: BaseGeometry) -> List[str]:
        if self._tree is None:
            return []
        return [self._names[i] for i in self._tree.query(geom)]

    def net_at(self, xy) -> str:
        """The plane net under the point, or ""."""
        p = SPoint(xy)
        for n in self.nets_touching(p):
            if self._prepared[n].contains(p):
                return n
        return ""

    def majority_net(self, xy, r_in: float, r_out: float) -> Tuple[str, float]:
        """The plane net covering most of the ring r_in..r_out round ``xy`` and the share
        of the ring it covers ("" and 0 for none). Rings step over a via's own antipad."""
        ring = SPoint(xy).buffer(r_out, quad_segs=8)
        if r_in > 0:
            ring = ring.difference(SPoint(xy).buffer(r_in, quad_segs=8))
        best, share = "", 0.0
        for n in self.nets_touching(ring):
            a = self.regions[n].intersection(ring).area / ring.area
            if a > share:
                best, share = n, a
        return best, share


@dataclass
class References:
    board: BaseGeometry                 # the board outline
    board_area: float
    plane_nets: List[str]
    signal_nets: List[str]
    planes: Dict[int, PlaneLayer]       # reference layers only
    all_planes: Dict[int, PlaneLayer]   # every layer with plane copper
    by_layer: Dict[int, List[Tuple[int, float]]]   # signal layer -> [(ref layer, h)]
    z_mid: Dict[int, float]
    diff_base: Dict[str, str]           # signal net -> its pair's shared name (or itself)

    def refs(self, layer: int) -> List[Tuple[int, float]]:
        return self.by_layer.get(layer, [])

    def signal_key(self, net: str) -> str:
        """One key per return path: both nets of a differential pair share one."""
        return self.diff_base.get(net, net)


def zone_regions(snap: BoardSnapshot) -> Dict[int, Dict[str, BaseGeometry]]:
    """Zone fill copper (and copper shapes with a net) per layer, per net."""
    parts: Dict[Tuple[int, str], list] = {}
    for z in snap.zones:
        if not z.net:
            continue
        for lid, polys in z.filled.items():
            for p in polys:
                g = poly_to_shapely(p)
                if g is not None and not g.is_empty:
                    parts.setdefault((lid, z.net), []).append(g)
    out: Dict[int, Dict[str, BaseGeometry]] = {}
    for (lid, net), gs in parts.items():
        g = _clean(shapely.union_all(gs))
        if not g.is_empty:
            out.setdefault(lid, {})[net] = g
    return out


def board_geometry(snap: BoardSnapshot, regions) -> BaseGeometry:
    polys = [poly_to_shapely(p) for p in snap.outline]
    polys = [p for p in polys if p is not None and not p.is_empty]
    if polys:
        return _clean(shapely.union_all(polys))
    # No outline: the extent of all the copper.
    gs = [g for layer in regions.values() for g in layer.values()]
    pts = [t.start for t in snap.tracks] + [t.end for t in snap.tracks]
    if pts:
        gs.append(shapely.MultiPoint(pts))
    if not gs:
        return Polygon()
    return shapely.union_all(gs).envelope


_DIFF = re.compile(r"^(.*?)(_?)(P|N|\+|-|DP|DN)$")


def diff_pairs(nets: Iterable[str]) -> Dict[str, str]:
    """Each net of a differential pair (USB_D+/USB_D-, PCIE_TX0_P/_N, CLKP/CLKN) mapped
    to a name both share; other nets are left out."""
    nets = set(nets)
    out = {}
    partner = {"P": "N", "N": "P", "+": "-", "-": "+", "DP": "DN", "DN": "DP"}
    for n in nets:
        m = _DIFF.match(n)
        if not m or not m.group(1):
            continue
        other = m.group(1) + m.group(2) + partner[m.group(3)]
        if other in nets:
            out[n] = m.group(1) + m.group(2) + "±"
    return out


def build_references(snap: BoardSnapshot, settings: ScanSettings,
                     warnings: Optional[List[str]] = None) -> References:
    warnings = warnings if warnings is not None else []
    regions = zone_regions(snap)
    board = board_geometry(snap, regions)
    board_area = board.area or 1.0
    min_area = min(settings.plane_min_area_frac * board_area, settings.plane_min_area_mm2)

    track_nets = {t.net for t in snap.tracks if t.net}
    zone_nets = {net for layer in regions.values() for net in layer}
    names = [n.name for n in snap.nets if n.name] or sorted(track_nets | zone_nets)
    plane_nets = set()
    for net in zone_nets:
        biggest = max((layer[net].area for layer in regions.values() if net in layer),
                      default=0.0)
        if biggest >= min_area or looks_like_power(net):
            plane_nets.add(net)
    plane_nets |= {n for n in names if matches(n, settings.extra_plane_nets)}
    signal_nets = sorted(n for n in track_nets
                         if n not in plane_nets and not looks_like_power(n)
                         and not n.lower().startswith("unconnected-")
                         and not matches(n, settings.ignore_nets))

    all_planes: Dict[int, PlaneLayer] = {}
    for lid, layer in regions.items():
        reg = {n: g for n, g in layer.items() if n in plane_nets}
        if not reg:
            continue
        reg = {n: g.intersection(board) if not board.is_empty else g for n, g in reg.items()}
        union = _clean(shapely.union_all(list(reg.values())))
        all_planes[lid] = PlaneLayer(lid, reg, union, union.area / board_area)
    planes = {lid: p for lid, p in all_planes.items()
              if p.coverage >= settings.reference_layer_min_frac}
    if not planes:
        warnings.append("No copper layer is mostly plane: no zone fills of supply or ground "
                        "nets cover a good share of any layer. Fill the zones (B in KiCad) "
                        "or check the board has planes.")

    copper = snap.stackup.copper
    z_mid = {c.id: c.z_mid_mm for c in copper}
    order = [c.id for c in copper]
    by_layer: Dict[int, List[Tuple[int, float]]] = {}
    for i, lid in enumerate(order):
        refs = []
        for step in (-1, 1):
            j = i + step
            while 0 <= j < len(order):
                if order[j] in planes:
                    refs.append((order[j], abs(z_mid[lid] - z_mid[order[j]])))
                    break
                j += step
        by_layer[lid] = refs

    return References(board=board, board_area=board_area, plane_nets=sorted(plane_nets),
                      signal_nets=signal_nets, planes=planes, all_planes=all_planes,
                      by_layer=by_layer, z_mid=z_mid, diff_base=diff_pairs(signal_nets))

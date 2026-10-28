"""The 3D heat map as plain arrays, for the window's pyvista view and the HTML report's
Plotly view alike.

3D coordinates: x as on the board, y flipped (KiCad's y points down, 3D's up), z up from
the bottom of the board, multiplied by ``zscale`` so the layers of a 1.6 mm board can be
told apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np

from capscan.analysis.results import ScanResult
from capscan.geometry.copper import iter_polygons
from capscan.report.maps import MapData

#: Plane holes with a shorter edge than this are not drawn (antipads, thermal reliefs).
MIN_HOLE_MM = 4.0


@dataclass
class HeatSheet:
    layer: int
    name: str
    z: float
    x: np.ndarray           # (nx,)
    y: np.ndarray           # (ny,), already flipped
    values: np.ndarray      # (ny, nx), 0..1


@dataclass
class Scene:
    zscale: float
    thickness: float
    outline: List[np.ndarray]                       # rings (n, 2), flipped
    sheets: List[HeatSheet]
    plane_edges: Dict[Tuple[int, str], List[np.ndarray]]    # (layer, net) -> rings (n, 3)
    hot_tracks: List[Tuple[np.ndarray, float]]      # ((2, 3) segment, heat 0..1)
    cold_tracks: List[np.ndarray]                   # (2, 3) segments
    caps: List[Tuple[str, np.ndarray, float, str]]  # (id, xyz, angle_deg, label)
    vias: List[Tuple[str, np.ndarray]]
    layer_names: Dict[int, str] = field(default_factory=dict)
    preview: bool = False           # before a scan: copper in KiCad's layer colors
    hot_layers: List[int] = field(default_factory=list)     # the layer of each hot track
    cold_layers: List[int] = field(default_factory=list)    # the layer of each cold track


def build_scene(data: MapData, zscale: float = 6.0) -> Scene:
    r: ScanResult = data.result
    snap = data.snap
    z = {c.id: c.z_mid_mm * zscale for c in snap.stackup.copper}
    thickness = snap.stackup.board_thickness_mm * zscale

    outline = []
    for o in data.outline:
        if o is not None:
            for p in iter_polygons(o):
                a = np.asarray(p.exterior.coords)
                outline.append(np.column_stack([a[:, 0], -a[:, 1]]))

    sheets = []
    for h in data.heat:
        ny, nx = h.values.shape
        x = h.origin[0] + h.step_mm * np.arange(nx)
        y = -(h.origin[1] + h.step_mm * np.arange(ny))
        sheets.append(HeatSheet(h.layer, r.layer_name(h.layer), z.get(h.layer, 0.0), x, y,
                                h.values))

    edges: Dict[Tuple[int, str], List[np.ndarray]] = {}
    for lid in data.reference_layers():
        for net, g in data.regions.get(lid, {}).items():
            for p in iter_polygons(g.simplify(0.05)):
                for k, ring in enumerate([p.exterior] + list(p.interiors)):
                    a = np.asarray(ring.coords)
                    if len(a) < 4 or (k > 0 and ring.length < MIN_HOLE_MM):
                        continue        # small holes: antipads, thermal reliefs
                    edges.setdefault((lid, net), []).append(
                        np.column_stack([a[:, 0], -a[:, 1], np.full(len(a), z.get(lid, 0.0))]))

    hot, cold, hot_layers, cold_layers = [], [], [], []
    signals = set(r.signal_nets)
    for t, heat in zip(snap.tracks, data.track_heat):
        if t.net not in signals:
            continue
        zz = z.get(t.layer, 0.0)
        pts = [t.start, t.mid, t.end] if t.is_arc else [t.start, t.end]
        for a, b in zip(pts, pts[1:]):
            seg = np.array([[a[0], -a[1], zz], [b[0], -b[1], zz]])
            if heat > 0.05:
                hot.append((seg, heat))
                hot_layers.append(t.layer)
            else:
                cold.append(seg)
                cold_layers.append(t.layer)

    caps = []
    for c in r.caps:
        zz = thickness + 0.25 * zscale if c.side == "top" else -0.25 * zscale
        caps.append((c.id, np.array([c.position[0], -c.position[1], zz]), c.angle_deg,
                     f"{c.id} {c.amount} {c.voltage_v:g}V"))
    vias = [(v.id, np.array([v.position[0], -v.position[1], thickness / 2]))
            for v in r.stitch_vias]
    return Scene(zscale=zscale, thickness=thickness, outline=outline, sheets=sheets,
                 plane_edges=edges, hot_tracks=hot, cold_tracks=cold, caps=caps, vias=vias,
                 layer_names=dict(r.layer_names), preview=r.preview, hot_layers=hot_layers,
                 cold_layers=cold_layers)

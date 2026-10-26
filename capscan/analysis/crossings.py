"""Traces whose return current meets a break in its reference plane.

Each signal's routing on a layer is walked along its centerline, sampled every
:data:`STEP_MM`, against the plane copper of each of the layer's reference layers. The
samples run over plane net A, then nothing, then plane net B: a split, which a
stitching capacitor between A and B bridges. A long stretch over nothing, or a hole in a
single net's plane, is a void, which no capacitor helps.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Tuple

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, Point as SPoint
from shapely.ops import linemerge

from capscan.analysis.references import PlaneLayer, References
from capscan.analysis.results import SPLIT, VOID, Crossing
from capscan.analysis.rules import ScanSettings
from capscan.geometry.arcs import arc_points
from capscan.model.board import BoardSnapshot, Track
from capscan.progress import NULL, ProgressReporter

STEP_MM = 0.05
#: A stripline whose other plane covers this share of a disc 3h round the crossing still
#: has a return path there: half the severity.
SOLID_OTHER_SHARE = 0.9


def track_points(t: Track) -> List[Tuple[float, float]]:
    if t.is_arc:
        return [t.start] + arc_points(t.start, t.mid, t.end)
    return [t.start, t.end]


def routed_lines(tracks: Iterable[Track]) -> List[LineString]:
    """The tracks joined into as few lines as their junctions allow."""
    segs = [LineString(track_points(t)) for t in tracks]
    segs = [s for s in segs if s.length > 0]
    if not segs:
        return []
    merged = linemerge(MultiLineString(segs))
    return [g for g in getattr(merged, "geoms", [merged]) if g.length > 0]


def _runs(labels: np.ndarray, s: np.ndarray) -> List[List]:
    """Runs of equal labels as [label, s_start, s_end]; a run spans to the midpoints
    between its first/last sample and its neighbours'."""
    out = []
    if len(labels) == 0:
        return out
    edges = np.flatnonzero(np.diff(labels)) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [len(labels)]))
    for a, b in zip(starts, ends):
        s0 = s[0] if a == 0 else 0.5 * (s[a - 1] + s[a])
        s1 = s[-1] if b == len(labels) else 0.5 * (s[b - 1] + s[b])
        out.append([int(labels[a]), float(s0), float(s1)])
    return out


def _absorb_small_voids(runs: List[List], min_void: float) -> List[List]:
    """Drop holes shorter than ``min_void`` inside one net's plane (antipads, thermal
    spokes), and the short ones at the line's ends (a via's own antipad)."""
    out: List[List] = []
    for i, r in enumerate(runs):
        if r[0] < 0 and r[2] - r[1] < min_void:
            prev = out[-1] if out else None
            nxt = runs[i + 1] if i + 1 < len(runs) else None
            if prev is not None and nxt is not None and prev[0] == nxt[0]:
                prev[2] = r[2]
                continue
            if prev is None or nxt is None:          # at an end of the line
                continue
        if out and out[-1][0] == r[0]:
            out[-1][2] = r[2]
        else:
            out.append(list(r))
    return out


def _unit(v) -> Tuple[float, float]:
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n > 1e-12 else (1.0, 0.0)


def _direction(line: LineString, s: float) -> Tuple[float, float]:
    a = line.interpolate(max(0.0, s - 0.1))
    b = line.interpolate(min(line.length, s + 0.1))
    return _unit((b.x - a.x, b.y - a.y))


def _split_direction(plane: PlaneLayer, net: str, xy, trace_dir) -> Tuple[float, float]:
    """Which way the edge of ``net``'s plane runs near ``xy``."""
    g = plane.regions.get(net)
    if g is not None:
        edge = g.boundary
        s = edge.project(SPoint(xy))
        a, b = edge.interpolate(s - 0.3), edge.interpolate(s + 0.3)
        d = (b.x - a.x, b.y - a.y)
        if math.hypot(*d) > 1e-6:
            return _unit(d)
    return (-trace_dir[1], trace_dir[0])


def _severity(refs: References, layer: int, broken: int, xy) -> float:
    """1, unless the trace has another reference plane that is solid here."""
    for other, h in refs.refs(layer):
        if other == broken:
            continue
        plane = refs.planes[other]
        _, share = plane.majority_net(xy, 0.0, max(3.0 * h, 0.3))
        if share >= SOLID_OTHER_SHARE:
            return 0.5
    return 1.0


def find_crossings(snap: BoardSnapshot, refs: References, settings: ScanSettings,
                   progress: ProgressReporter = NULL) -> List[Crossing]:
    by_net_layer: Dict[Tuple[str, int], List[Track]] = {}
    signals = set(refs.signal_nets)
    for t in snap.tracks:
        if t.net in signals and refs.refs(t.layer):
            by_net_layer.setdefault((t.net, t.layer), []).append(t)

    out: List[Crossing] = []
    keys = sorted(by_net_layer)
    for k, (net, layer) in enumerate(keys):
        if k % 50 == 0:
            progress.fraction(k / max(1, len(keys)))
            progress.check_cancel()
        for line in routed_lines(by_net_layer[(net, layer)]):
            for ref_layer, h in refs.refs(layer):
                out.extend(_line_crossings(line, net, layer, ref_layer, h, refs, settings))
    for i, c in enumerate(out, 1):
        c.id = f"X{i}"
    return out


def _line_crossings(line: LineString, net: str, layer: int, ref_layer: int, h: float,
                    refs: References, settings: ScanSettings) -> List[Crossing]:
    plane = refs.planes[ref_layer]
    n = max(2, int(math.ceil(line.length / STEP_MM)) + 1)
    s = np.linspace(0.0, line.length, n)
    pts = shapely.line_interpolate_point(line, s)
    x, y = shapely.get_x(pts), shapely.get_y(pts)
    names = plane.nets_touching(line)
    labels = np.full(n, -1, dtype=int)
    for i, nm in enumerate(names):
        labels[shapely.contains_xy(plane.regions[nm], x, y)] = i
    runs = _absorb_small_voids(_runs(labels, s), settings.void_ignore_mm)

    found: List[Crossing] = []

    def add(kind, a, b, s_mid, gap):
        p = line.interpolate(s_mid)
        xy = (p.x, p.y)
        tdir = _direction(line, s_mid)
        sdir = _split_direction(plane, a or b, xy, tdir) if (a or b) else (-tdir[1], tdir[0])
        found.append(Crossing(
            id="", kind=kind, net=net, layer=layer, ref_layer=ref_layer, net_a=a, net_b=b,
            position=(round(xy[0], 4), round(xy[1], 4)), gap_mm=round(gap, 4), h_mm=h,
            severity=_severity(refs, layer, ref_layer, xy), trace_dir=tdir, split_dir=sdir))

    def name(label):
        return names[label] if label >= 0 else ""

    for i, r in enumerate(runs):
        label, s0, s1 = r
        if label >= 0:
            # Two plane nets touching with no void between (overlapping zone fills).
            if i + 1 < len(runs) and runs[i + 1][0] >= 0 and runs[i + 1][0] != label:
                add(SPLIT, name(label), name(runs[i + 1][0]), s1, 0.0)
            continue
        prev = runs[i - 1][0] if i > 0 else None
        nxt = runs[i + 1][0] if i + 1 < len(runs) else None
        gap = s1 - s0
        a, b = name(prev) if prev is not None else "", name(nxt) if nxt is not None else ""
        mid = 0.5 * (s0 + s1)
        if a and b and a != b and gap <= settings.split_gap_max_mm:
            add(SPLIT, a, b, mid, gap)
        elif gap >= settings.void_ignore_mm:
            add(VOID, a, b, mid, gap)
    return found


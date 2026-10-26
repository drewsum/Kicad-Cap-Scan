"""Heat maps: where return paths break most densely.

Each break adds a Gaussian on its reference layer, weighted by its severity, with a spread
of about one capacitor's reach: a split, a void, a via onto another net's plane, or a via
onto another plane of the same net that still needs a return via. Each kind of break gets
its own map, so the window can show any of them; ``ScanResult.heat_map`` adds the ones
shown and scales them by the hottest spot on the board, so layers compare directly. Hot
areas are where many signals lose their return path close together.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

import numpy as np

from capscan.analysis.results import Crossing, HeatLayer, Transition, heat_kind
from capscan.analysis.rules import ScanSettings
from capscan.model.board import Track

MAX_CELLS = 400_000          # a coarser grid on a huge board


def _grid(bounds, step: float):
    x0, y0, x1, y1 = bounds
    nx = max(2, int(math.ceil((x1 - x0) / step)) + 1)
    ny = max(2, int(math.ceil((y1 - y0) / step)) + 1)
    if nx * ny > MAX_CELLS:
        step *= math.sqrt(nx * ny / MAX_CELLS)
        nx = max(2, int(math.ceil((x1 - x0) / step)) + 1)
        ny = max(2, int(math.ceil((y1 - y0) / step)) + 1)
    return x0, y0, step, nx, ny


def heat_maps(bounds, crossings: Sequence[Crossing], transitions: Sequence[Transition],
              settings: ScanSettings) -> List[HeatLayer]:
    """The unscaled heat of each kind of break on each layer it breaks."""
    if bounds is None:
        return []
    x0, y0, step, nx, ny = _grid(bounds, settings.heat_grid_mm)
    sigma = max(settings.max_cap_distance_mm, 4 * step)
    xs = x0 + step * np.arange(nx)
    ys = y0 + step * np.arange(ny)
    points: Dict[Tuple[str, int], List[Tuple[float, float, float]]] = {}
    for c in crossings:
        if heat_kind(c):
            points.setdefault((c.kind, c.ref_layer), []).append((*c.position, c.severity))
    for t in transitions:
        if heat_kind(t):                # the return current changes planes here
            for layer in set(t.ref_layers):
                if layer >= 0:
                    points.setdefault((t.kind, layer), []).append((*t.position, 1.0))
    out = []
    for (kind, layer), pts in sorted(points.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        arr = np.asarray(pts)
        # Separable Gaussians: one row and one column factor per point.
        gx = np.exp(-0.5 * ((xs[None, :] - arr[:, 0:1]) / sigma) ** 2)      # (n, nx)
        gy = np.exp(-0.5 * ((ys[None, :] - arr[:, 1:2]) / sigma) ** 2)      # (n, ny)
        out.append(HeatLayer(layer=layer, origin=(x0, y0), step_mm=step,
                             values=(gy * arr[:, 2:3]).T @ gx, kind=kind))   # (ny, nx)
    return out


def sample(heat: HeatLayer, xy) -> float:
    i = int(round((xy[1] - heat.origin[1]) / heat.step_mm))
    j = int(round((xy[0] - heat.origin[0]) / heat.step_mm))
    ny, nx = heat.values.shape
    if 0 <= i < ny and 0 <= j < nx:
        return float(heat.values[i, j])
    return 0.0


def track_heat(tracks: Sequence[Track], heat: Sequence[HeatLayer],
               references: Dict[int, List[Tuple[int, float]]],
               signal_nets: Sequence[str]) -> List[Tuple[int, float]]:
    """Each signal track's heat: the hottest of its reference layers' maps along it.
    ``heat`` is a combined map (:meth:`ScanResult.heat_map`)."""
    by_layer = {h.layer: h for h in heat}
    signals = set(signal_nets)
    out = []
    for i, t in enumerate(tracks):
        if t.net not in signals:
            continue
        maps = [by_layer[r] for r, _ in references.get(t.layer, []) if r in by_layer]
        if not maps:
            continue
        pts = [t.start, t.end, ((t.start[0] + t.end[0]) / 2, (t.start[1] + t.end[1]) / 2)]
        v = max(sample(m, p) for m in maps for p in pts)
        if v > 0.01:
            out.append((i, round(v, 3)))
    return out

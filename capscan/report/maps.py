"""The 2D placement map: planes, signals, crossings and capacitors on one layer, drawn
with matplotlib. The window draws it dark (KiPI's plot style); reports draw it light.
Before a scan the copper is drawn in KiCad's layer colors, so the board looks as it does
in KiCad.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence

import numpy as np
from shapely.geometry.base import BaseGeometry

from capscan.analysis.heatmap import track_heat
from capscan.analysis.references import zone_regions
from capscan.analysis.results import HEAT_KINDS, SPLIT, UNFIXABLE, VOID, ScanResult
from capscan.geometry.copper import iter_polygons, poly_to_shapely
from capscan.model.board import BoardSnapshot
from capscan.model.layers import B_CU, F_CU, is_inner

# KiPI's series colors, for plane nets.
NET_COLORS = ("#4fa3ff", "#ffa040", "#60d060", "#ff6080", "#c080ff", "#40d0d0", "#e0e060")

# KiCad's default PCB color theme for copper (s_defaultTheme in
# common/settings/builtin_color_themes.h). The IPC API does not give the user's own theme.
KICAD_F_CU = "#c83434"
KICAD_B_CU = "#4d7fc4"
KICAD_INNER_CU = (                                          # In1.Cu .. In30.Cu
    "#7fc87f", "#ce7d2c", "#4fcbcb", "#db628b", "#a7a5c6", "#28ccd9", "#e8b2a7", "#f2eda1",
    "#8dcb81", "#ed7c33", "#5bc3eb", "#f76f8e", "#a7a5c6", "#28ccd9", "#e8b2a7", "#f2eda1",
    "#ed7c33", "#5bc3eb", "#f76f8e", "#a7a5c6", "#28ccd9", "#e8b2a7", "#f2eda1", "#ed7c33",
    "#5bc3eb", "#f76f8e", "#a7a5c6", "#28ccd9", "#e8b2a7", "#f2eda1")
#: Before a scan: the tracks of nets not picked are drawn this faint, the picked ones solid.
PREVIEW_DIM = 0.35


def layer_color(layer_id: int) -> str:
    """KiCad's default color for a copper layer."""
    if layer_id == F_CU:
        return KICAD_F_CU
    if layer_id == B_CU:
        return KICAD_B_CU
    if is_inner(layer_id):
        return KICAD_INNER_CU[layer_id - F_CU - 1]
    return "#888888"


def _stack_order(layers):
    """Bottom layer first, so the top layer is drawn over the others, as in KiCad."""
    return sorted(layers, key=lambda l: -l)


@dataclass
class Theme:
    face: str
    axes: str
    text: str
    grid: str
    board: str
    track: str
    split: str
    void: str
    cap: str
    cap_edge: str
    via: str
    highlight: str
    plane_alpha: float = 0.28


DARK = Theme(face="#1e1e1e", axes="#1e1e1e", text="#cccccc", grid="#333333", board="#888888",
             track="#9a9a9a", split="#ff4040", void="#ffb020", cap="#30e0ff",
             cap_edge="#ffffff", via="#c080ff", highlight="#ffe000")
LIGHT = Theme(face="#ffffff", axes="#ffffff", text="#222222", grid="#e4e4e4", board="#555555",
              track="#777777", split="#d01010", void="#d08000", cap="#0090c0",
              cap_edge="#003040", via="#8040c0", highlight="#e0a000", plane_alpha=0.22)


class MapData:
    """What every map of one board draws, worked out once."""

    def __init__(self, snap: BoardSnapshot, result: ScanResult,
                 heat_kinds: Optional[Iterable[str]] = None):
        self.snap = snap
        self.result = result
        planes = set(result.plane_nets)
        self.regions: Dict[int, Dict[str, BaseGeometry]] = {
            lid: {n: g for n, g in layer.items() if n in planes}
            for lid, layer in zone_regions(snap).items()}
        self.outline = [poly_to_shapely(p) for p in snap.outline]
        self.net_color = {n: NET_COLORS[i % len(NET_COLORS)]
                          for i, n in enumerate(sorted(planes))}
        self.cache: dict = {}           # drawings of the planes, kept while they stay the same
        self.set_heat_kinds(heat_kinds)

    def use_result(self, result: ScanResult):
        """Show ``result`` over the same planes: a preview with other nets picked. The
        planes (the slow part) are kept."""
        self.result = result
        self.set_heat_kinds(self.heat_kinds)

    def set_heat_kinds(self, kinds: Optional[Iterable[str]] = None):
        """Show the heat of these kinds of break (None: all), on the planes and tracks."""
        r = self.result
        kinds = list(HEAT_KINDS if kinds is None else kinds)
        self.heat_kinds = kinds
        self.heat = r.heat_map(kinds)
        if r.preview or set(kinds) >= set(HEAT_KINDS):
            heat = dict(r.track_heat)           # as scanned (or the preview's highlight)
        else:
            heat = dict(track_heat(self.snap.tracks, self.heat, r.references, r.signal_nets))
        self.track_heat = [heat.get(i, 0.0) for i in range(len(self.snap.tracks))]

    def reference_layers(self):
        """The reference layers, the one with the most crossings first."""
        layers = {r for refs in self.result.references.values() for r, _ in refs}
        count = {l: 0 for l in layers}
        for c in self.result.crossings:
            count[c.ref_layer] = count.get(c.ref_layer, 0) + 1
        for t in self.result.via_changes():
            for l in t.ref_layers:
                if l in count:
                    count[l] += 1
        return sorted(count, key=lambda l: (-count[l], l))

    def signal_layers_over(self, ref_layer: int):
        return [l for l, refs in self.result.references.items()
                if any(r == ref_layer for r, _ in refs)]


def _poly_patches(ax, geom, color, alpha, edge=None):
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MPath
    for p in iter_polygons(geom):
        rings = [np.asarray(p.exterior.coords)] + [np.asarray(r.coords) for r in p.interiors]
        verts, codes = [], []
        for r in rings:
            verts.extend(r.tolist())
            codes.extend([MPath.MOVETO] + [MPath.LINETO] * (len(r) - 2) + [MPath.CLOSEPOLY])
        ax.add_patch(PathPatch(MPath(verts, codes), facecolor=color, alpha=alpha,
                               edgecolor=edge or color, linewidth=0.6))


def draw_map(ax, data: MapData, ref_layer: Optional[int], theme: Theme = DARK,
             show_heat: bool = True, highlight: Optional[str] = None,
             labels: bool = True):
    """Draw one reference layer (None: every layer) and what crosses it on ``ax``. For a
    preview, returns the tracks drawn (see :func:`redraw_preview_tracks`)."""
    from matplotlib.collections import LineCollection

    r = data.result
    ax.set_facecolor(theme.axes)
    ax.figure.set_facecolor(theme.face)
    for o in data.outline:
        if o is not None:
            x, y = o.exterior.xy
            ax.plot(x, y, color=theme.board, linewidth=1.0)
    layers = data.reference_layers() if ref_layer is None else [ref_layer]

    if show_heat:
        for h in data.heat:
            if h.layer in layers:
                ny, nx = h.values.shape
                x0, y0 = h.origin
                ax.imshow(np.ma.masked_less(h.values, 0.03), cmap="inferno", vmin=0, vmax=1,
                          origin="lower",
                          extent=(x0 - h.step_mm / 2, x0 + (nx - 0.5) * h.step_mm,
                                  y0 - h.step_mm / 2, y0 + (ny - 0.5) * h.step_mm),
                          alpha=0.75, zorder=1, interpolation="bilinear")
    for lid in _stack_order(layers) if r.preview else layers:
        for net, g in data.regions.get(lid, {}).items():
            color = layer_color(lid) if r.preview else data.net_color.get(net, "#888888")
            _poly_patches(ax, g, color, theme.plane_alpha)

    sig_layers = _signal_layers(data, layers)
    signals = set(r.signal_nets)
    tracks = None
    if r.preview:
        tracks = _preview_tracks(ax, data, sig_layers)
    else:
        segs, colors = [], []
        import matplotlib
        cmap = matplotlib.colormaps["inferno"]
        for t, heat in zip(data.snap.tracks, data.track_heat):
            if t.layer in sig_layers and t.net in signals:
                segs.append([t.start, t.mid, t.end] if t.is_arc else [t.start, t.end])
                colors.append(cmap(0.35 + 0.65 * heat) if heat > 0.05 else theme.track)
        if segs:
            ax.add_collection(LineCollection(segs, colors=colors, linewidths=0.8, zorder=3))

    for c in r.crossings:
        if c.ref_layer not in layers:
            continue
        hl = highlight == c.id
        if c.kind == SPLIT:
            ax.plot(*c.position, marker="x", color=theme.highlight if hl else theme.split,
                    markersize=9 if hl else 5, mew=1.5, zorder=5)
        elif c.kind == VOID and c.status == UNFIXABLE:
            ax.plot(*c.position, marker="D", mfc="none",
                    mec=theme.highlight if hl else theme.void, markersize=9 if hl else 5,
                    zorder=5)
    for t in r.via_changes():
        if not set(t.ref_layers) & set(layers):
            continue
        hl = highlight == t.id
        ax.plot(*t.position, marker="o", mfc="none",
                mec=theme.highlight if hl else theme.split, markersize=9 if hl else 6, zorder=5)
    for sv in r.stitch_vias:
        if set(sv.layers) & set(layers):
            ax.plot(*sv.position, marker="o", color=theme.via, markersize=5, zorder=6)
            if labels:
                ax.annotate(sv.id, sv.position, xytext=(4, 4), textcoords="offset points",
                            color=theme.via, fontsize=7, zorder=7)
    for cap in r.caps:
        if not set(cap.ref_layers) & set(layers):
            continue
        hl = highlight == cap.id
        ax.plot(*cap.position, marker="s", color=theme.highlight if hl else theme.cap,
                mec=theme.cap_edge, markersize=10 if hl else 7, zorder=8,
                alpha=1.0 if cap.side == "top" else 0.75)
        if labels:
            ax.annotate(cap.id, cap.position, xytext=(5, 5), textcoords="offset points",
                        color=theme.highlight if hl else theme.cap, fontsize=7,
                        fontweight="bold", zorder=9)
    for e in r.existing:
        if e.credited or e.flagged:
            color = theme.cap if e.credited else theme.void
            ax.plot(*e.position, marker="s", mfc="none", mec=color, markersize=7, zorder=7)
            if labels:
                ax.annotate(e.reference, e.position, xytext=(5, -9), textcoords="offset points",
                            color=color, fontsize=6.5, zorder=7)

    ax.set_aspect("equal")
    ax.tick_params(colors=theme.text, labelsize=7)
    for s in ax.spines.values():
        s.set_color("#888888")
    ax.grid(True, color=theme.grid, linewidth=0.5)
    ax.set_xlabel("x (mm)", color=theme.text, fontsize=8)
    ax.set_ylabel("y (mm)", color=theme.text, fontsize=8)
    title = ("All reference layers" if ref_layer is None
             else f"{r.layer_name(ref_layer)}: planes and what crosses them")
    ax.set_title(title, color=theme.text, fontsize=9)
    bounds = [o.bounds for o in data.outline if o is not None]
    if bounds:
        x0 = min(b[0] for b in bounds)
        y0 = min(b[1] for b in bounds)
        x1 = max(b[2] for b in bounds)
        y1 = max(b[3] for b in bounds)
        pad = 0.03 * max(x1 - x0, y1 - y0)
        ax.set_xlim(x0 - pad, x1 + pad)
        ax.set_ylim(y1 + pad, y0 - pad)          # KiCad's y points down
    return tracks


def _signal_layers(data: MapData, layers) -> set:
    out = set()
    for lid in layers:
        out.update(data.signal_layers_over(lid))
    return out


def redraw_preview_tracks(ax, data: MapData, ref_layer: Optional[int], old=None):
    """Draw a preview's tracks again, with other nets picked, leaving the rest of the map;
    ``old`` is what :func:`draw_map` (or this) returned."""
    if old is not None:
        old.remove()
    layers = data.reference_layers() if ref_layer is None else [ref_layer]
    return _preview_tracks(ax, data, _signal_layers(data, layers))


def _preview_tracks(ax, data: MapData, sig_layers):
    """The signal routing in its layers' colors, the picked nets solid and wider and the
    rest faint (KiCad's net highlight). With nothing picked every track is solid."""
    from matplotlib.collections import LineCollection
    from matplotlib.colors import to_rgba

    signals = set(data.result.signal_nets)
    picked = any(h > 0.05 for h in data.track_heat)
    rows = [(t, h) for t, h in zip(data.snap.tracks, data.track_heat)
            if t.layer in sig_layers and t.net in signals]
    rows.sort(key=lambda row: (row[1] > 0.05, -row[0].layer))  # picked and top layer last
    segs, colors, widths = [], [], []
    for t, heat in rows:
        hot = heat > 0.05 or not picked
        segs.append([t.start, t.mid, t.end] if t.is_arc else [t.start, t.end])
        colors.append(to_rgba(layer_color(t.layer), 1.0 if hot else PREVIEW_DIM))
        widths.append(1.4 if hot and picked else 0.8)
    if segs:
        return ax.add_collection(LineCollection(segs, colors=colors, linewidths=widths,
                                                zorder=3))
    return None


def preview_layers(data: MapData, layers: Sequence[int]):
    """The copper layers a preview of ``layers`` draws: the planes and the signals over them,
    top first."""
    shown = set(layers)
    for l in layers:
        shown.update(data.signal_layers_over(l))
    return sorted(shown)


def legend_handles(theme: Theme, data: MapData, layers: Sequence[int]):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    if data.result.preview:
        return [Patch(facecolor=layer_color(l), label=data.result.layer_name(l))
                for l in preview_layers(data, layers)]
    nets = sorted({n for l in layers for n in data.regions.get(l, {})})
    h = [Patch(facecolor=data.net_color[n], alpha=0.6, label=n) for n in nets]
    h += [Line2D([], [], marker="x", ls="", color=theme.split, label="Split crossing"),
          Line2D([], [], marker="D", ls="", mfc="none", mec=theme.void, label="Void"),
          Line2D([], [], marker="o", ls="", mfc="none", mec=theme.split,
                 label="Via changing plane"),
          Line2D([], [], marker="s", ls="", color=theme.cap, mec=theme.cap_edge,
                 label="New stitching cap"),
          Line2D([], [], marker="o", ls="", color=theme.via, label="New return via")]
    return h


def map_png(data: MapData, ref_layer: Optional[int], theme: Theme = LIGHT,
            width_in: float = 7.5, dpi: int = 150) -> str:
    """The map as a base64 PNG, for the reports."""
    import matplotlib
    matplotlib.use("Agg", force=False)
    from matplotlib.figure import Figure
    bounds = [o.bounds for o in data.outline if o is not None]
    aspect = 0.7
    if bounds:
        b = bounds[0]
        aspect = max(0.3, min(1.5, (b[3] - b[1]) / max(1e-6, b[2] - b[0])))
    fig = Figure(figsize=(width_in, width_in * aspect + 0.8), dpi=dpi)
    ax = fig.add_subplot(111)
    draw_map(ax, data, ref_layer, theme)
    layers = data.reference_layers() if ref_layer is None else [ref_layer]
    leg = ax.legend(handles=legend_handles(theme, data, layers), fontsize=6.5, loc="upper left",
                    bbox_to_anchor=(1.01, 1.0), frameon=False)
    for t in leg.get_texts():
        t.set_color(theme.text)
    fig.tight_layout()
    buf = io.BytesIO()
    # Trimmed to the plot and its legend: no blank band above and below it on the page.
    fig.savefig(buf, format="png", facecolor=theme.face, bbox_inches="tight", pad_inches=0.1)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def unfixable(result: ScanResult):
    return [c for c in result.crossings if c.status == UNFIXABLE]

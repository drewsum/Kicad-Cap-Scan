"""The 3D heat map: each reference plane's split-crossing density on the board in 3D,
with the plane edges, the signal routing colored by heat, and the new capacitors.

KiPI's look: the same gradient background, anti-aliasing and the inferno colormap.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import pyvista as pv
from pyvistaqt import QtInteractor
from qtpy.QtWidgets import QVBoxLayout, QWidget

from capscan.report.maps import NET_COLORS, PREVIEW_DIM, layer_color
from capscan.report.scene3d import Scene

_BOARD_COLOR = (0.15, 0.40, 0.20)
_CAP_COLOR = (0.19, 0.88, 1.0)
_VIA_COLOR = (0.75, 0.50, 1.0)
_COLD_TRACK = (0.55, 0.55, 0.55)
#: Heat below this is transparent, so the planes and routing show through.
_HEAT_OPACITY = [0.0, 0.0, 0.35, 0.6, 0.75, 0.85, 0.9]
# The scale bar sits near the view's right edge (next to the setup panes), so it covers as
# little of the board as it can, with room for its title (wider than the bar) to the right.
_BAR_RIGHT = 0.975
_BAR_WIDTH = 0.03
_BAR_Y, _BAR_HEIGHT = 0.25, 0.5


def _bar_args(title: str, fmt: str) -> dict:
    return dict(title=title, vertical=True, position_x=_BAR_RIGHT - _BAR_WIDTH,
                position_y=_BAR_Y, height=_BAR_HEIGHT, width=_BAR_WIDTH, fmt=fmt,
                title_font_size=14, color="white", label_font_size=12)


def _lines(segments: List[np.ndarray]) -> Optional[pv.PolyData]:
    if not segments:
        return None
    pts = np.vstack(segments)
    n = len(segments)
    cells = np.column_stack([np.full(n, 2), 2 * np.arange(n), 2 * np.arange(n) + 1]).ravel()
    return pv.PolyData(pts, lines=cells)


def _polyline(rings: List[np.ndarray]) -> Optional[pv.PolyData]:
    if not rings:
        return None
    pts, cells, off = [], [], 0
    for r in rings:
        pts.append(r)
        cells.extend([len(r) + 1, *range(off, off + len(r)), off])
        off += len(r)
    return pv.PolyData(np.vstack(pts), lines=np.asarray(cells))


def _layer_rgb(layers: List[int]) -> np.ndarray:
    return np.array([[int(layer_color(l)[i:i + 2], 16) for i in (1, 3, 5)] for l in layers],
                    dtype=np.uint8)


class Viewer3D(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plotter = QtInteractor(self)
        layout.addWidget(self.plotter.interactor)
        self.plotter.set_background("#1e1f24", top="#3a3d46")
        self.plotter.enable_anti_aliasing("fxaa")
        self.plotter.add_axes()
        self._scene: Optional[Scene] = None
        self._actors: Dict[str, list] = {}
        self._hidden_layers: set = set()
        self._hidden_groups: set = set()        # "tracks", "caps", ... ticked off

    def _preview_tracks(self, scene: Scene):
        """Before a scan: the routing in its layers' colors, the picked nets solid and wider
        and the rest faint (all solid when nothing is picked)."""
        picked = bool(scene.hot_tracks)
        hot = _lines([s for s, _ in scene.hot_tracks])
        if hot is not None:
            hot.cell_data["rgb"] = _layer_rgb(scene.hot_layers)
            self._add("tracks", hot, scalars="rgb", rgb=True, line_width=4,
                      render_lines_as_tubes=True)
        cold = _lines(scene.cold_tracks)
        if cold is not None:
            cold.cell_data["rgb"] = _layer_rgb(scene.cold_layers)
            self._add("tracks", cold, scalars="rgb", rgb=True, line_width=1.5,
                      opacity=PREVIEW_DIM if picked else 1.0)

    def show_tracks(self, scene: Scene):
        """A preview with other nets picked: replace the tracks, keeping the rest of the
        scene and the view."""
        if self._scene is None or not scene.preview:
            self.show_scene(scene, keep_camera=True)
            return
        for actor in self._actors.pop("tracks", []):
            self.plotter.remove_actor(actor, render=False)
        self._scene = scene
        self._preview_tracks(scene)
        self._apply_visibility()

    def clear(self):
        self.plotter.clear()
        self._actors = {}
        self._scene = None

    def show_scene(self, scene: Scene, keep_camera: bool = False):
        """Draw ``scene``; ``keep_camera`` keeps the view as it is (the same board redrawn)."""
        camera = self.plotter.camera_position if keep_camera and self._scene else None
        self.plotter.clear()
        self._actors = {}
        self._scene = scene
        p = self.plotter
        for ring in scene.outline:
            poly = pv.PolyData(np.column_stack([ring, np.zeros(len(ring))]),
                               faces=np.r_[len(ring), np.arange(len(ring))])
            try:
                body = poly.triangulate().extrude((0, 0, scene.thickness), capping=True)
                self._add("board", body, color=_BOARD_COLOR, opacity=0.18, smooth_shading=False)
            except Exception:  # noqa: BLE001 - an outline VTK cannot triangulate: edges only
                pass
            edge = _polyline([np.column_stack([ring, np.full(len(ring), z)])
                              for z in (0.0, scene.thickness)])
            self._add("board", edge, color=(0.6, 0.6, 0.6), line_width=1)

        nets = sorted({net for _, net in scene.plane_edges})
        for (layer, net), rings in scene.plane_edges.items():
            color = (layer_color(layer) if scene.preview
                     else NET_COLORS[nets.index(net) % len(NET_COLORS)])
            self._add(f"layer{layer}", _polyline(rings), color=color, line_width=2)

        for i, sheet in enumerate(scene.sheets):
            step = float(sheet.x[1] - sheet.x[0]) if len(sheet.x) > 1 else 1.0
            ny, nx = sheet.values.shape
            # The sheet's y is flipped (falling with the row); the grid's rises, x fastest.
            grid = pv.ImageData(dimensions=(nx, ny, 1), spacing=(step, step, 1.0),
                                origin=(float(sheet.x[0]), float(sheet.y[-1]), sheet.z + 0.01))
            grid.point_data["heat"] = sheet.values[::-1].ravel(order="C")
            self._add(f"layer{sheet.layer}", grid, scalars="heat", cmap="inferno", clim=(0, 1),
                      opacity=_HEAT_OPACITY, lighting=False, show_scalar_bar=i == 0,
                      scalar_bar_args=_bar_args("Transitions", "%.1f"))
        for bar in p.scalar_bars.values():
            bar.SetTextPositionToPrecedeScalarBar()     # value ticks left of the bar

        if scene.preview:
            self._preview_tracks(scene)
            hot = None
        else:
            hot = _lines([s for s, _ in scene.hot_tracks])
        if hot is not None:
            hot.cell_data["heat"] = np.array([h for _, h in scene.hot_tracks])
            self._add("tracks", hot, scalars="heat", cmap="inferno", clim=(0, 1), line_width=4,
                      render_lines_as_tubes=True, show_scalar_bar=False)
        if not scene.preview:
            self._add("tracks", _lines(scene.cold_tracks), color=_COLD_TRACK, line_width=1.5,
                      opacity=0.6)

        for _, xyz, angle, _ in scene.caps:
            a = math.radians(angle)
            body = pv.Cube(center=xyz, x_length=1.0, y_length=0.5, z_length=0.4)
            body.rotate_z(math.degrees(a), point=xyz, inplace=True)
            self._add("caps", body, color=_CAP_COLOR)
        if scene.caps:
            pts = np.array([c[1] for c in scene.caps]) + np.array([0, 0, 0.6])
            self._actors.setdefault("caps", []).append(p.add_point_labels(
                pts, [c[3] for c in scene.caps], font_size=11, text_color="#30e0ff",
                shape_opacity=0.0, show_points=False, always_visible=True))
        if scene.vias:
            pts = np.array([v[1] for v in scene.vias])
            self._add("vias", pv.PolyData(pts), color=_VIA_COLOR, point_size=10,
                      render_points_as_spheres=True)

        if camera is not None:
            p.camera_position = camera
        else:
            p.view_isometric()
            p.reset_camera()
        self._apply_visibility()
        p.render()

    def _add(self, group: str, mesh, **kw):
        if mesh is None:
            return
        actor = self.plotter.add_mesh(mesh, **kw)
        self._actors.setdefault(group, []).append(actor)

    def set_layer_visible(self, layer: int, visible: bool):
        (self._hidden_layers.discard if visible else self._hidden_layers.add)(layer)
        self._apply_visibility()

    def layer_visible(self, layer: int) -> bool:
        return layer not in self._hidden_layers

    def _apply_visibility(self):
        for group, actors in self._actors.items():
            shown = group not in self._hidden_groups and not (
                group.startswith("layer") and int(group[5:]) in self._hidden_layers)
            for a in actors:
                try:
                    a.SetVisibility(shown)
                except AttributeError:
                    pass
        self.plotter.render()

    def set_group_visible(self, group: str, visible: bool):
        (self._hidden_groups.discard if visible else self._hidden_groups.add)(group)
        self._apply_visibility()

    def focus(self, xy, span_mm: float = 8.0):
        if self._scene is None:
            return
        center = (xy[0], -xy[1], self._scene.thickness / 2)
        cam = self.plotter.camera
        cam.focal_point = center
        cam.position = (center[0] + span_mm, center[1] - span_mm, center[2] + span_mm * 1.6)
        self.plotter.render()

    def screenshot(self, path) -> None:
        self.plotter.screenshot(str(path))

    def close_view(self):
        try:
            self.plotter.close()
        except Exception:  # noqa: BLE001
            pass

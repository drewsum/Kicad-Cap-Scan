"""The 2D Placement tab: the map of :mod:`capscan.report.maps` on a Qt canvas, one
reference layer at a time, with matplotlib's pan and zoom. :class:`HeatToggles` picks the
kinds of break whose heat is shown, here and on the 3D tab."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from qtpy.QtCore import Signal
from qtpy.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from capscan.analysis.results import HEAT_KINDS, HEAT_LABELS
from capscan.report.maps import DARK, MapData, draw_map, legend_handles, redraw_preview_tracks

#: A click this near (in mm) a capacitor or crossing picks it.
PICK_MM = 1.2


class HeatToggles(QWidget):
    """A tick box per kind of break: the heat map adds up those ticked."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        bar = QHBoxLayout(self)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(QLabel("Heat:"))
        self.boxes: Dict[str, QCheckBox] = {}
        for kind in HEAT_KINDS:
            cb = QCheckBox(HEAT_LABELS[kind])
            cb.setChecked(True)
            cb.toggled.connect(lambda _: self.changed.emit())
            bar.addWidget(cb)
            self.boxes[kind] = cb

    def kinds(self) -> List[str]:
        return [k for k, cb in self.boxes.items() if cb.isChecked()]

    def set_kinds(self, kinds: Iterable[str], emit: bool = False):
        kinds = set(kinds)
        if kinds == set(self.kinds()):
            return
        for k, cb in self.boxes.items():
            cb.blockSignals(True)
            cb.setChecked(k in kinds)
            cb.blockSignals(False)
        if emit:
            self.changed.emit()

    def set_counts(self, counts: Optional[Dict[str, int]]):
        """Show how many breaks of each kind add heat (None: before a scan)."""
        for k, cb in self.boxes.items():
            n = (counts or {}).get(k)
            cb.setText(HEAT_LABELS[k] + (f" ({n})" if n is not None else ""))


class BoardMap(QWidget):
    picked = Signal(str)            # the id of the capacitor or crossing clicked
    heat_changed = Signal()         # other kinds of heat shown

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: Optional[MapData] = None
        self._highlight: Optional[str] = None
        self._ax = self._tracks = None          # a preview's tracks, redrawn alone
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Reference layer:"))
        self.layer = QComboBox()
        self.layer.currentIndexChanged.connect(lambda _: self.redraw())
        bar.addWidget(self.layer)
        self.heat = HeatToggles()
        self.heat.changed.connect(self._heat_changed)
        bar.addWidget(self.heat)
        self.labels = QCheckBox("Labels")
        self.labels.setChecked(True)
        self.labels.toggled.connect(lambda _: self.redraw())
        bar.addWidget(self.labels)
        bar.addStretch(1)
        self.fig = Figure(facecolor=DARK.face)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setStyleSheet("background: #2a2a2a; color: #ccc;")
        bar.addWidget(self.toolbar)
        layout.addLayout(bar)
        layout.addWidget(self.canvas, 1)
        self.canvas.mpl_connect("button_press_event", self._on_click)

    def set_data(self, data: Optional[MapData]):
        self._data = data
        if data is not None:
            data.set_heat_kinds(self.heat.kinds())
        self.heat.set_counts(None if data is None or data.result.preview
                             else data.result.heat_counts())
        self.layer.blockSignals(True)
        self.layer.clear()
        if data is not None:
            for lid in data.reference_layers():
                self.layer.addItem(data.result.layer_name(lid), lid)
            if self.layer.count() > 1:
                self.layer.addItem("All reference layers", None)
        self.layer.blockSignals(False)
        self.redraw()

    def _heat_changed(self):
        if self._data is not None:
            self._data.set_heat_kinds(self.heat.kinds())
        self.redraw()
        self.heat_changed.emit()

    def show_item(self, ident: str, ref_layers=()):
        """Highlight ``ident`` and switch to a layer it is on."""
        self._highlight = ident
        current = self.layer.currentData()
        if ref_layers and current is not None and current not in ref_layers:
            for i in range(self.layer.count()):
                if self.layer.itemData(i) in ref_layers:
                    self.layer.setCurrentIndex(i)
                    return              # redrawn by the change
        self.redraw()

    def update_tracks(self):
        """Other nets picked in a preview: draw the tracks again, and only them (the
        planes are what is slow to draw)."""
        if self._ax is None or self._data is None or not self._data.result.preview:
            self.redraw()
            return
        self._tracks = redraw_preview_tracks(self._ax, self._data, self.layer.currentData(),
                                             self._tracks)
        self.canvas.draw_idle()

    def redraw(self):
        self.fig.clear()
        ax = self._ax = self.fig.add_subplot(111)
        self._tracks = None
        if self._data is None:
            ax.set_facecolor(DARK.axes)
            ax.text(0.5, 0.5, "Run a scan to see where stitching capacitors go",
                    color="#999", ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off()
            self.canvas.draw_idle()
            return
        layer = self.layer.currentData()
        self._tracks = draw_map(ax, self._data, layer, DARK,
                                highlight=self._highlight, labels=self.labels.isChecked())
        layers = self._data.reference_layers() if layer is None else [layer]
        leg = ax.legend(handles=legend_handles(DARK, self._data, layers), fontsize=7,
                        loc="upper left", bbox_to_anchor=(1.01, 1.0), facecolor="#2a2a2a",
                        edgecolor="#555")
        for t in leg.get_texts():
            t.set_color("#ccc")
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _on_click(self, event):
        if self._data is None or event.inaxes is None or event.xdata is None \
                or self.toolbar.mode:
            return
        r = self._data.result
        candidates = [(c.id, c.position) for c in r.caps]
        candidates += [(c.id, c.position) for c in r.crossings]
        candidates += [(t.id, t.position) for t in r.via_changes()]
        best, best_d = None, PICK_MM
        for ident, (x, y) in candidates:
            d = ((x - event.xdata) ** 2 + (y - event.ydata) ** 2) ** 0.5
            if d < best_d:
                best, best_d = ident, d
        if best:
            self._highlight = best
            self.redraw()
            self.picked.emit(best)

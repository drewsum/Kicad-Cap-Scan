"""Which signal nets to scan: all of them, or those ticked in a filterable list, each with
its edge rate and trace impedance (the defaults unless set)."""

from __future__ import annotations

import fnmatch
from typing import Dict, Iterable, List, Mapping, Tuple

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QAbstractItemView, QCheckBox, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from capscan.gui.tooltip_art import art_tip

HINT = "color: #999;"
WARN = "color: #ffc060;"
DEFAULT_COLOR = QColor("#888888")

NET, EDGE, Z0 = 0, 1, 2
OWN = Qt.UserRole + 1       # a speed cell holds a value set for this net, not the default

EDGE_TEXT = ("10-90 % edge rate of the signal. Faster edges need a stitching cap with less "
             "inductance: a smaller package, or several in parallel.")
Z0_TEXT = ("Characteristic impedance of the trace, single-ended (half a differential pair's): "
           "what an edge travelling down it sees, set by the trace's width, its height over "
           "the plane and the dielectric. The lower it is, the more a cap's inductance slows "
           "the edge.")
CELL_TEXT = ("Grey: the default. Double-click to set it for this net; clear the cell to go "
             "back to the default.")
SPIN_TEXT = "'Set shown' gives it to every net the filter shows."


def matches_filter(net: str, text: str) -> bool:
    """A filter with * ? or [ is a pattern; anything else matches anywhere in the name.
    Case does not matter."""
    text = text.strip()
    if not text:
        return True
    if any(c in text for c in "*?["):
        return fnmatch.fnmatch(net.lower(), text.lower())
    return text.lower() in net.lower()


def _spin(suffix: str, lo: float, hi: float, value: float) -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setDecimals(0)
    w.setRange(lo, hi)
    w.setValue(value)
    w.setSuffix(suffix)
    return w


class NetPicker(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._defaults = (100.0, 50.0)       # edge rate ps, impedance ohm
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.all_box = QCheckBox("Scan all signal nets")
        self.all_box.setToolTip("Every net that is not a plane or supply, taken as a fast "
                                "digital signal. Untick to scan only the nets ticked below.")
        self.all_box.toggled.connect(self._all_toggled)
        layout.addWidget(self.all_box)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter nets: USB, DDR*, CLK?")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter)
        self.list = QTableWidget(0, 3)
        self.list.setHorizontalHeaderLabels(["Net", "Edge ps", "Z0 Ω"])
        self._cell_tips = {
            EDGE: art_tip(f"{EDGE_TEXT} {CELL_TEXT}", "edge_rate", "tr"),
            Z0: art_tip(f"{Z0_TEXT} {CELL_TEXT}", "impedance", "z0")}
        for col, tip in self._cell_tips.items():
            self.list.horizontalHeaderItem(col).setToolTip(tip)
        self.list.verticalHeader().setVisible(False)
        self.list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.list.setEditTriggers(QAbstractItemView.DoubleClicked
                                  | QAbstractItemView.EditKeyPressed)
        head = self.list.horizontalHeader()
        head.setSectionResizeMode(NET, QHeaderView.Stretch)
        head.setSectionResizeMode(EDGE, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(Z0, QHeaderView.ResizeToContents)
        self.list.setMinimumHeight(160)
        self.list.itemChanged.connect(self._item_changed)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.select_shown = QPushButton("Select shown")
        self.select_shown.clicked.connect(lambda: self._set_shown(True))
        self.clear_shown = QPushButton("Clear shown")
        self.clear_shown.clicked.connect(lambda: self._set_shown(False))
        row.addWidget(self.select_shown)
        row.addWidget(self.clear_shown)
        layout.addLayout(row)
        speed = QHBoxLayout()
        self.edge_spin = _spin(" ps", 1, 100000, self._defaults[0])
        self.z0_spin = _spin(" Ω", 1, 500, self._defaults[1])
        self.edge_spin.setToolTip(art_tip(f"{EDGE_TEXT} {SPIN_TEXT}", "edge_rate", "tr"))
        self.z0_spin.setToolTip(art_tip(f"{Z0_TEXT} {SPIN_TEXT}", "impedance", "z0"))
        self.set_speed = QPushButton("Set shown")
        self.set_speed.setToolTip("Give every net shown this edge rate and impedance")
        self.set_speed.clicked.connect(self._set_shown_speed)
        self.reset_speed = QPushButton("Default")
        self.reset_speed.setToolTip("Put the nets shown back on the default edge rate and "
                                    "impedance")
        self.reset_speed.clicked.connect(lambda: self._set_shown_speed(reset=True))
        for w in (self.edge_spin, self.z0_spin, self.set_speed, self.reset_speed):
            speed.addWidget(w)
        layout.addLayout(speed)
        self.count = QLabel("")
        self.count.setWordWrap(True)
        layout.addWidget(self.count)
        self._all_toggled(False)

    # ------------------------------------------------------------------ state
    def set_nets(self, nets: Iterable[str], selected: Iterable[str] = (),
                 edge_ps: Mapping[str, float] = None,
                 impedance: Mapping[str, float] = None) -> None:
        """List ``nets``, with ``selected`` ticked and the given speeds set (each kept where
        already ticked or set)."""
        keep = set(selected) | set(self.selected())
        old_edge, old_z = self.speeds()
        edge = {**old_edge, **(edge_ps or {})}
        z = {**old_z, **(impedance or {})}
        self.list.blockSignals(True)
        self.list.setRowCount(0)
        for n in nets:
            r = self.list.rowCount()
            self.list.insertRow(r)
            it = QTableWidgetItem(n)
            it.setFlags((it.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            it.setCheckState(Qt.Checked if n in keep else Qt.Unchecked)
            self.list.setItem(r, NET, it)
            for col, own in ((EDGE, edge), (Z0, z)):
                cell = QTableWidgetItem()
                cell.setToolTip(self._cell_tips[col])
                self.list.setItem(r, col, cell)
                self._show_speed(r, col, own.get(n))
        self.list.blockSignals(False)
        self._apply_filter()
        self._changed()

    def nets(self) -> List[str]:
        return [self.list.item(r, NET).text() for r in range(self.list.rowCount())]

    def shown(self) -> List[str]:
        return [self.list.item(r, NET).text() for r in range(self.list.rowCount())
                if not self.list.isRowHidden(r)]

    def selected(self) -> List[str]:
        return [self.list.item(r, NET).text() for r in range(self.list.rowCount())
                if self.list.item(r, NET).checkState() == Qt.Checked]

    def set_selected(self, nets: Iterable[str]) -> None:
        want = set(nets)
        self.list.blockSignals(True)
        for r in range(self.list.rowCount()):
            it = self.list.item(r, NET)
            it.setCheckState(Qt.Checked if it.text() in want else Qt.Unchecked)
        self.list.blockSignals(False)
        self._changed()

    def speeds(self) -> Tuple[Dict[str, float], Dict[str, float]]:
        """The edge rates and impedances set for particular nets (not the defaults)."""
        edge: Dict[str, float] = {}
        z: Dict[str, float] = {}
        for r in range(self.list.rowCount()):
            net = self.list.item(r, NET).text()
            for col, out in ((EDGE, edge), (Z0, z)):
                it = self.list.item(r, col)
                if it is not None and it.data(OWN) is not None:
                    out[net] = float(it.data(OWN))
        return edge, z

    def set_speeds(self, edge_ps: Mapping[str, float], impedance: Mapping[str, float]) -> None:
        self.list.blockSignals(True)
        for r in range(self.list.rowCount()):
            net = self.list.item(r, NET).text()
            self._show_speed(r, EDGE, edge_ps.get(net))
            self._show_speed(r, Z0, impedance.get(net))
        self.list.blockSignals(False)
        self._changed()

    def set_defaults(self, edge_ps: float, impedance: float) -> None:
        """The edge rate and impedance of nets not given their own, shown in grey."""
        self._defaults = (float(edge_ps), float(impedance))
        self.set_speeds(*self.speeds())

    def scan_all(self) -> bool:
        return self.all_box.isChecked()

    def set_scan_all(self, on: bool) -> None:
        self.all_box.setChecked(bool(on))

    def ready(self) -> bool:
        """Whether a scan has nets to look at."""
        return self.scan_all() or bool(self.selected())

    # ------------------------------------------------------------------ internals
    def _show_speed(self, row: int, col: int, own) -> None:
        it = self.list.item(row, col)
        value = self._defaults[0 if col == EDGE else 1] if own is None else float(own)
        it.setData(OWN, None if own is None else float(own))
        it.setText(f"{value:g}")
        it.setForeground(DEFAULT_COLOR if own is None else self.palette().text().color())
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def _item_changed(self, it: QTableWidgetItem):
        if it.column() == NET:
            self._changed()
            return
        text = it.text().strip()
        try:
            own = float(text) if text else None
        except ValueError:
            own = it.data(OWN)              # not a number: keep what it was
        if own is not None and own <= 0:
            own = it.data(OWN)
        self.list.blockSignals(True)
        self._show_speed(it.row(), it.column(), own)
        self.list.blockSignals(False)
        self.changed.emit()

    def _all_toggled(self, on: bool):
        # Speeds still apply when every net is scanned.
        for w in (self.select_shown, self.clear_shown):
            w.setEnabled(not on)
        self._changed()

    def _apply_filter(self, *_):
        text = self.filter.text()
        for r in range(self.list.rowCount()):
            self.list.setRowHidden(r, not matches_filter(self.list.item(r, NET).text(), text))

    def _set_shown(self, on: bool):
        self.list.blockSignals(True)
        for r in range(self.list.rowCount()):
            if not self.list.isRowHidden(r):
                self.list.item(r, NET).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.list.blockSignals(False)
        self._changed()

    def _set_shown_speed(self, reset: bool = False):
        edge = None if reset else self.edge_spin.value()
        z = None if reset else self.z0_spin.value()
        self.list.blockSignals(True)
        for r in range(self.list.rowCount()):
            if not self.list.isRowHidden(r):
                self._show_speed(r, EDGE, edge)
                self._show_speed(r, Z0, z)
        self.list.blockSignals(False)
        self._changed()

    def _changed(self):
        n, total = len(self.selected()), self.list.rowCount()
        own = len(set().union(*self.speeds()))
        tail = f" {own} with their own edge rate or impedance." if own else ""
        if self.scan_all():
            self.count.setText((f"All {total} signal nets will be scanned." if total
                                else "All signal nets will be scanned.") + tail)
            self.count.setStyleSheet(HINT)
        elif n:
            self.count.setText(f"{n} of {total} signal nets selected." + tail)
            self.count.setStyleSheet(HINT)
        else:
            self.count.setText("No nets selected: tick some, or scan all signal nets." + tail)
            self.count.setStyleSheet(WARN)
        self.changed.emit()

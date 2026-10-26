"""Which signal nets to scan: all of them, or those ticked in a filterable list."""

from __future__ import annotations

import fnmatch
from typing import Iterable, List

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
)

HINT = "color: #999;"
WARN = "color: #ffc060;"


def matches_filter(net: str, text: str) -> bool:
    """A filter with * ? or [ is a pattern; anything else matches anywhere in the name.
    Case does not matter."""
    text = text.strip()
    if not text:
        return True
    if any(c in text for c in "*?["):
        return fnmatch.fnmatch(net.lower(), text.lower())
    return text.lower() in net.lower()


class NetPicker(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
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
        self.list = QListWidget()
        self.list.setMinimumHeight(160)
        self.list.itemChanged.connect(lambda _: self._changed())
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.select_shown = QPushButton("Select shown")
        self.select_shown.clicked.connect(lambda: self._set_shown(True))
        self.clear_shown = QPushButton("Clear shown")
        self.clear_shown.clicked.connect(lambda: self._set_shown(False))
        row.addWidget(self.select_shown)
        row.addWidget(self.clear_shown)
        layout.addLayout(row)
        self.count = QLabel("")
        self.count.setWordWrap(True)
        layout.addWidget(self.count)
        self._all_toggled(False)

    # ------------------------------------------------------------------ state
    def set_nets(self, nets: Iterable[str], selected: Iterable[str] = ()) -> None:
        """List ``nets``, with ``selected`` ticked (kept where already ticked)."""
        keep = set(selected) | set(self.selected())
        self.list.blockSignals(True)
        self.list.clear()
        for n in nets:
            it = QListWidgetItem(n)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if n in keep else Qt.Unchecked)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self._apply_filter()
        self._changed()

    def nets(self) -> List[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def selected(self) -> List[str]:
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def set_selected(self, nets: Iterable[str]) -> None:
        want = set(nets)
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setCheckState(Qt.Checked if it.text() in want else Qt.Unchecked)
        self.list.blockSignals(False)
        self._changed()

    def scan_all(self) -> bool:
        return self.all_box.isChecked()

    def set_scan_all(self, on: bool) -> None:
        self.all_box.setChecked(bool(on))

    def ready(self) -> bool:
        """Whether a scan has nets to look at."""
        return self.scan_all() or bool(self.selected())

    # ------------------------------------------------------------------ internals
    def _all_toggled(self, on: bool):
        for w in (self.filter, self.list, self.select_shown, self.clear_shown):
            w.setEnabled(not on)
        self._changed()

    def _apply_filter(self, *_):
        text = self.filter.text()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(not matches_filter(it.text(), text))

    def _set_shown(self, on: bool):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            it = self.list.item(i)
            if not it.isHidden():
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.list.blockSignals(False)
        self._changed()

    def _changed(self):
        n, total = len(self.selected()), self.list.count()
        if self.scan_all():
            self.count.setText(f"All {total} signal nets will be scanned." if total
                               else "All signal nets will be scanned.")
            self.count.setStyleSheet(HINT)
        elif n:
            self.count.setText(f"{n} of {total} signal nets selected.")
            self.count.setStyleSheet(HINT)
        else:
            self.count.setText("No nets selected: tick some, or scan all signal nets.")
            self.count.setStyleSheet(WARN)
        self.changed.emit()

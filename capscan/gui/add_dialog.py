"""Adding the scan's capacitors and return vias to the board: what to add them with, and
the step-through review.

:class:`AddDialog` shows what was picked for the board (a footprint for each package, the
via size, the via types the design allows, the first reference) and lets each be changed.
It ends with *Add all* or *Step through*. :class:`StepPanel` then shows each part as it is
added, KiCad showing it too, and asks whether to keep it.
"""

from __future__ import annotations

import html
from dataclasses import replace
from typing import Dict, List, Optional

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout,
)

from capscan.analysis.install import (
    BLIND, BURIED, MICRO, ViaSize, next_references, split_reference,
)

HINT = "color: #999;"
WARN = "color: #ffc060;"
ADD_ALL, STEP = "all", "step"
TYPE_LABELS = {MICRO: "Microvias", BLIND: "Blind vias", BURIED: "Buried vias"}


class AddDialog(QDialog):
    """``installer`` is a :class:`capscan.kicad.install.Installer` (or one like it)."""

    def __init__(self, installer, parent=None, first_reference: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Add to board")
        self.installer = installer
        self.mode: Optional[str] = None
        self._defaults = installer.defaults()
        self._sources: Dict[str, list] = {}
        r = installer.result
        parts = sum(c.count for c in r.caps)
        layout = QVBoxLayout(self)
        intro = QLabel(
            f"Adds {len(r.caps)} stitching capacitor(s) ({parts} part(s)), each pad with a "
            f"via to its plane, and {len(r.stitch_vias)} return via(s). Parts and vias only "
            "go where the design rules leave room, and DRC must find the board as it was "
            "before; anything that breaks it is taken off again. <b>Add all</b> is one undo "
            "step in KiCad; <b>Step through</b> shows each in KiCad and asks to keep it.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        fps = QGroupBox("Footprints")
        fl = QFormLayout(fps)
        self.footprint_boxes: Dict[str, QComboBox] = {}
        for package in sorted({c.package for c in r.caps}):
            box = QComboBox()
            self._sources[package] = list(installer.sources(package))
            for s in self._sources[package]:
                box.addItem(s.label)
            if not self._sources[package]:
                box.addItem("(none found: browse for one)")
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _=False, p=package: self._browse(p))
            row = QHBoxLayout()
            row.addWidget(box, 1)
            row.addWidget(browse)
            fl.addRow(f"{package} capacitor", row)
            self.footprint_boxes[package] = box
        layout.addWidget(fps)

        vias = QGroupBox("Vias")
        vl = QFormLayout(vias)
        self.via_box = QComboBox()
        self._via_sizes: List[ViaSize] = installer.via_sizes()
        for s in self._via_sizes:
            self.via_box.addItem(s.label)
        if self._defaults.via_size in self._via_sizes:
            self.via_box.setCurrentIndex(self._via_sizes.index(self._defaults.via_size))
        self.via_box.setToolTip("Diameter / drill. The board's predefined sizes and its net "
                                "classes' that meet the design rules; first the size most used "
                                "on the plane nets")
        vl.addRow("Size", self.via_box)
        self.type_boxes: Dict[str, QCheckBox] = {}
        types = QHBoxLayout()
        others = [t for t in installer.allowed if t in TYPE_LABELS]
        for t in others:
            cb = QCheckBox(TYPE_LABELS[t])
            cb.setChecked(True)
            self.type_boxes[t] = cb
            types.addWidget(cb)
        if not others:
            types.addWidget(QLabel("Through vias only: the board has no blind, buried or "
                                   "microvias, or the rules disallow them"))
        types.addStretch(1)
        vl.addRow("Types", types)
        layout.addWidget(vias)

        refs = QGroupBox("Reference designators")
        rl = QHBoxLayout(refs)
        self.first_ref = QLineEdit(first_reference or
                                   f"{self._defaults.prefix}{self._defaults.start}")
        self.first_ref.setMaximumWidth(120)
        self.first_ref.setToolTip("The first stitching cap's reference; the rest count up "
                                  "from it, skipping any already on the board")
        self.first_ref.textChanged.connect(self._ref_changed)
        rl.addWidget(QLabel("First stitching cap"))
        rl.addWidget(self.first_ref)
        self.ref_hint = QLabel("")
        rl.addWidget(self.ref_hint, 1)
        layout.addWidget(refs)

        cli = installer.cli
        self.drc_label = QLabel(f"DRC: {cli}" if cli else
                                "DRC cannot run: kicad-cli was not found. Nothing is added "
                                "without it.")
        self.drc_label.setStyleSheet(HINT if cli else WARN)
        self.drc_label.setWordWrap(True)
        layout.addWidget(self.drc_label)
        notes = QLabel("<br>".join(installer.rules.notes + [
            "The parts are not in the schematic: add them there, or <i>Update PCB from "
            "Schematic</i> with <i>Delete footprints with no symbols</i> takes them off."]))
        notes.setStyleSheet(HINT)
        notes.setWordWrap(True)
        layout.addWidget(notes)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.add_all = QPushButton("Add all")
        self.add_all.clicked.connect(lambda: self._done(ADD_ALL))
        self.step = QPushButton("Step through…")
        self.step.clicked.connect(lambda: self._done(STEP))
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        for b in (self.add_all, self.step, cancel):
            buttons.addWidget(b)
        layout.addLayout(buttons)
        self._cli = bool(cli)
        self._ref_changed()
        self.resize(640, self.sizeHint().height())

    def _ref_changed(self, *_):
        split = split_reference(self.first_ref.text())
        if split is None:
            self.ref_hint.setText("Letters then a number, like C100")
            self.ref_hint.setStyleSheet(WARN)
        else:
            parts = sum(c.count for c in self.installer.result.caps)
            refs = next_references(self.installer.taken, split[0], split[1], parts)
            self.ref_hint.setText(f"{refs[0]} to {refs[-1]}" if len(refs) > 1
                                  else (refs[0] if refs else ""))
            self.ref_hint.setStyleSheet(HINT)
        for b in (self.add_all, self.step):
            b.setEnabled(self._cli and split is not None)

    def _browse(self, package: str):
        path, _ = QFileDialog.getOpenFileName(self, f"Footprint for a {package} capacitor",
                                              "", "KiCad footprint (*.kicad_mod)")
        if path:
            self.add_file(package, path)

    def add_file(self, package: str, path: str) -> bool:
        from capscan.kicad.install import file_source
        src = file_source(path, package)
        if src is None:
            return False
        box = self.footprint_boxes[package]
        if not self._sources[package]:
            box.clear()
        self._sources[package].append(src)
        box.addItem(src.label)
        box.setCurrentIndex(box.count() - 1)
        return True

    def options(self):
        fps = {}
        for package, box in self.footprint_boxes.items():
            found = self._sources[package]
            if found and 0 <= box.currentIndex() < len(found):
                fps[package] = found[box.currentIndex()]
        types = [t for t in self.installer.allowed
                 if t not in self.type_boxes or self.type_boxes[t].isChecked()]
        prefix, start = split_reference(self.first_ref.text()) or (
            self._defaults.prefix, self._defaults.start)
        return replace(self._defaults, footprints=fps,
                       via_size=self._via_sizes[max(0, self.via_box.currentIndex())],
                       via_types=types, prefix=prefix, start=start)

    def _done(self, mode: str):
        self.mode = mode
        self.accept()


class StepPanel(QDialog):
    """One part at a time: what was added (KiCad shows it selected), and what next."""

    keep = Signal()
    take_off = Signal()
    rest = Signal()
    stop = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Step through")
        self.setWindowModality(Qt.NonModal)
        layout = QVBoxLayout(self)
        self.title = QLabel("")
        self.title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.title)
        self.text = QLabel("")
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.text, 1)
        row = QHBoxLayout()
        self.keep_button = QPushButton("Keep, next")
        self.keep_button.clicked.connect(self.keep)
        self.off_button = QPushButton("Take it off, next")
        self.off_button.clicked.connect(self.take_off)
        self.rest_button = QPushButton("Add the rest")
        self.rest_button.clicked.connect(self.rest)
        stop = QPushButton("Stop")
        stop.clicked.connect(self.stop)
        for b in (self.keep_button, self.off_button, self.rest_button, stop):
            row.addWidget(b)
        layout.addLayout(row)
        self.resize(560, 220)

    def show_added(self, index: int, total: int, ident: str, text: str):
        self.title.setText(f"{index} of {total}: {ident} added")
        self.text.setText(text + "<br><span style='color:#999'>Selected in KiCad. Keep it, "
                                 "or take it off.</span>")
        self.text.setStyleSheet("")
        self.keep_button.setText("Keep, next")
        self.off_button.setEnabled(True)
        self._ready(True)

    def show_skipped(self, index: int, total: int, ident: str, text: str):
        self.title.setText(f"{index} of {total}: {ident} not added")
        self.text.setText(text)
        self.text.setStyleSheet(WARN)
        self.keep_button.setText("Next")
        self.off_button.setEnabled(False)
        self._ready(True)

    def busy(self, text: str):
        self.title.setText(text)
        self.text.setText("")
        self._ready(False)

    def _ready(self, on: bool):
        for b in (self.keep_button, self.rest_button):
            b.setEnabled(on)
        if not on:
            self.off_button.setEnabled(False)


def describe(result, ident: str) -> str:
    """A cap or return via of the scan, in a line, then the signals it gives a return path."""
    cap = next((c for c in result.caps if c.id == ident), None)
    if cap is not None:
        return (f"{cap.amount} {cap.package} between {cap.net_a} and {cap.net_b}, "
                f"{cap.side} side, at x {cap.position[0]:.2f}, y {cap.position[1]:.2f} mm"
                + serving(result, cap.serves))
    sv = next((v for v in result.stitch_vias if v.id == ident), None)
    if sv is not None:
        return (f"{sv.net} return via at x {sv.position[0]:.2f}, y {sv.position[1]:.2f} mm, "
                + "-".join(result.layer_name(l) for l in sv.layers)
                + serving(result, sv.serves))
    return ident


def serving(result, serves) -> str:
    """"<br>For: D0 (crosses the split on In2.Cu), ..." - each signal net a part is for,
    with where its return path breaks; "" for none."""
    found = {}
    for x in result.crossings:
        if x.id in serves:
            found.setdefault(x.net, []).append(
                f"crosses the {x.kind} on {result.layer_name(x.ref_layer)}")
    for t in result.transitions:
        if t.id in serves:
            found.setdefault(t.net, []).append(
                f"via {result.layer_name(t.from_layer)} to {result.layer_name(t.to_layer)}")
    if not found:
        return ""
    parts = [f"<b>{html.escape(net)}</b> ({', '.join(dict.fromkeys(where))})"
             for net, where in sorted(found.items())]
    return "<br>For: " + "; ".join(parts)


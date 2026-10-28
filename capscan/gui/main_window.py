"""CapScan's window: KiPI's layout and dark look.

Tabs in the middle (3D heat map, 2D placement, the tables), the board and its stackup on
the left, the scan settings on the right, the log at the bottom. The scan runs on a
worker thread (:class:`~capscan.gui.worker.Task`); KiCad is only read while it runs.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from qtpy.QtCore import QSettings, Qt
from qtpy.QtGui import QAction, QColor, QFont
from qtpy.QtWidgets import (
    QCheckBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QToolBar, QVBoxLayout,
    QWidget,
)

from capscan import __version__
from capscan.analysis.results import NEW_CAP, UNFIXABLE, VIA_CAP, ScanResult
from capscan.analysis.rules import DESCRIPTIONS, ScanSettings
from capscan.analysis.values import part_basis
from capscan.gui import app as capscan_app
from capscan.gui.board_map import BoardMap, HeatToggles
from capscan.gui.docks import make_detachable
from capscan.gui.net_picker import NetPicker
from capscan.gui.worker import QtProgress, Task
from capscan.model.board import BoardSnapshot, CopperLayer
from capscan.report.maps import MapData

log = logging.getLogger(__name__)

try:                        # no OpenGL (CI's offscreen Qt): the 3D tab says so instead
    from capscan.gui.viewer3d import Viewer3D
except Exception as _e:  # noqa: BLE001
    Viewer3D = None
    _VIEWER_ERROR = str(_e)
else:
    _VIEWER_ERROR = ""

HINT = "color: #999;"
ERROR = "color: #ff8080;"
STATUS_COLORS = {NEW_CAP: "#ffc060", "existing cap": "#60d060", UNFIXABLE: "#ff6080",
                 "new via": "#ffc060", "existing via": "#60d060"}
LOG_COLORS = {logging.WARNING: "#ffc060", logging.ERROR: "#ff8080"}
SETTINGS_KEY = "scan_settings"
NETS_KEY = "nets/"              # + board file name: the nets picked for that board
SPEEDS_KEY = "speeds/"          # + board file name: edge rates and impedances set per net
FIRST_REF_KEY = "first_ref/"    # + board file name: the first reference of the caps added


class _Item(QTableWidgetItem):
    """A table cell that sorts by number where it holds one."""

    def __init__(self, value):
        super().__init__("" if value is None else str(value))
        self._key = value if isinstance(value, (int, float)) else None

    def __lt__(self, other):
        if self._key is not None and getattr(other, "_key", None) is not None:
            return self._key < other._key
        return self.text() < other.text()


def fill_table(table: QTableWidget, head: Sequence[str], rows: Sequence[Sequence],
               ids: Sequence[str] = (), status_col: int = -1) -> None:
    table.setSortingEnabled(False)
    table.clear()
    table.setColumnCount(len(head))
    table.setHorizontalHeaderLabels(list(head))
    table.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            it = _Item(round(v, 3) if isinstance(v, float) else v)
            if j == 0 and i < len(ids):
                it.setData(Qt.UserRole, ids[i])
            if j == status_col and str(v) in STATUS_COLORS:
                it.setForeground(QColor(STATUS_COLORS[str(v)]))
            table.setItem(i, j, it)
    table.setSortingEnabled(True)
    table.resizeColumnsToContents()
    table.horizontalHeader().setStretchLastSection(True)


def _table() -> QTableWidget:
    t = QTableWidget()
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    return t


class SettingsPanel(QWidget):
    """The scan settings: the few that matter up top, every rule under Advanced."""

    BASIC = ("check_splits", "check_via_caps", "check_via_stitch", "check_voids",
             "edge_rate_ps", "trace_impedance_ohm", "max_cap_distance_mm",
             "via_cap_distance_mm", "signals_per_cap")

    def __init__(self, settings: ScanSettings, parent=None):
        super().__init__(parent)
        self._fields = {}
        layout = QVBoxLayout(self)
        intro = QLabel("Pick the nets to scan, or scan them all. Each net is taken as a fast "
                       "digital signal with the default edge rate and impedance unless given "
                       "its own (double-click a grey value); the fastest signal a cap returns "
                       "picks its package.")
        intro.setWordWrap(True)
        intro.setStyleSheet(HINT)
        layout.addWidget(intro)
        nets_box = QGroupBox("Nets to scan")
        QVBoxLayout(nets_box).addWidget(NetPicker())
        self.nets: NetPicker = nets_box.layout().itemAt(0).widget()
        self.nets.set_scan_all(settings.scan_all_nets)
        self.nets.set_defaults(settings.edge_rate_ps, settings.trace_impedance_ohm)
        layout.addWidget(nets_box, 1)
        basic = QFormLayout()
        layout.addLayout(basic)
        self.advanced = QGroupBox("Advanced")
        self.advanced.setCheckable(True)
        self.advanced.setChecked(False)
        adv = QFormLayout(self.advanced)
        self.advanced.toggled.connect(lambda on: [w.setVisible(on) for w in self._adv_widgets])
        self._adv_widgets: List[QWidget] = []
        for name, label in DESCRIPTIONS.items():
            w = self._editor(getattr(settings, name), name in self.CAN_BE_OFF)
            self._fields[name] = w
            if name in self.BASIC:
                basic.addRow(label, w)
            else:
                adv.addRow(label, w)
                self._adv_widgets.append(w)
                self._adv_widgets.append(adv.labelForField(w))
        for name in ("edge_rate_ps", "trace_impedance_ohm"):
            self._fields[name].valueChanged.connect(lambda _: self.nets.set_defaults(
                self._fields["edge_rate_ps"].value(),
                self._fields["trace_impedance_ohm"].value()))
        self.ignore = QLineEdit(", ".join(settings.ignore_nets))
        self.ignore.setPlaceholderText("e.g. TP*, LED_*")
        self.extra = QLineEdit(", ".join(settings.extra_plane_nets))
        self.extra.setPlaceholderText("nets to treat as planes")
        adv.addRow("Ignore nets", self.ignore)
        adv.addRow("Also planes", self.extra)
        self._adv_widgets += [self.ignore, self.extra, adv.labelForField(self.ignore),
                              adv.labelForField(self.extra)]
        layout.addWidget(self.advanced)
        for w in self._adv_widgets:
            w.setVisible(False)
        self.refill = QCheckBox("Refill zones before scanning")
        self.refill.setChecked(False)
        self.refill.setToolTip("Zone fills decide where the planes are; stale fills give "
                               "stale results. Refilling changes the board in KiCad.")
        layout.addWidget(self.refill)
        self.run = QPushButton("Scan board")
        self.run.setStyleSheet("font-weight: bold; padding: 6px;")
        layout.addWidget(self.run)
        self.defaults = QPushButton("Restore defaults")
        self.defaults.clicked.connect(lambda: self.load(ScanSettings()))
        layout.addWidget(self.defaults)

    #: Counts where 0 turns the rule off.
    CAN_BE_OFF = ("power_min_zone_layers", "power_via_array")

    @staticmethod
    def _editor(value, can_be_off: bool = False):
        if isinstance(value, bool):
            w = QCheckBox()
            w.setChecked(value)
        elif isinstance(value, int):
            w = QSpinBox()
            w.setRange(0 if can_be_off else 1, 64)
            w.setValue(value)
        elif isinstance(value, float):
            w = QDoubleSpinBox()
            w.setDecimals(2)
            w.setRange(0.0, 100000.0)
            w.setSingleStep(0.1)
            w.setValue(value)
        else:
            w = QLineEdit(str(value))
        return w

    def load(self, s: ScanSettings):
        for name, w in self._fields.items():
            v = getattr(s, name)
            if isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                w.setValue(v)
            else:
                w.setText(str(v))
        self.ignore.setText(", ".join(s.ignore_nets))
        self.extra.setText(", ".join(s.extra_plane_nets))
        self.nets.set_scan_all(s.scan_all_nets)      # the ticked nets belong to the board
        self.nets.set_defaults(s.edge_rate_ps, s.trace_impedance_ohm)
        if s.net_edge_rate_ps or s.net_impedance_ohm:
            self.nets.set_speeds(s.net_edge_rate_ps, s.net_impedance_ohm)

    def value(self) -> ScanSettings:
        s = ScanSettings()
        for name, w in self._fields.items():
            if isinstance(w, QCheckBox):
                v = w.isChecked()
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                v = w.value()
            else:
                v = w.text().strip() or getattr(s, name)
            setattr(s, name, v)
        split = lambda t: [x.strip() for x in t.split(",") if x.strip()]
        s.ignore_nets = split(self.ignore.text())
        s.extra_plane_nets = split(self.extra.text())
        s.scan_all_nets = self.nets.scan_all()
        s.selected_nets = self.nets.selected()
        s.net_edge_rate_ps, s.net_impedance_ohm = self.nets.speeds()
        return s


class MainWindow(QMainWindow):
    def __init__(self, kicad=None, board=None, snapshot: Optional[BoardSnapshot] = None,
                 result: Optional[ScanResult] = None):
        super().__init__()
        self.kicad, self.board = kicad, board
        self.snapshot = snapshot
        self.result: Optional[ScanResult] = None
        self._task: Optional[Task] = None
        self._data: Optional[MapData] = None
        self._preview: Optional[ScanResult] = None     # the board as read, before a scan
        self._installed: dict = {}      # cap or via id -> what became of it on the board
        self._steps: Optional[dict] = None
        self.setWindowTitle(f"CapScan {__version__}: stitching capacitor advisor")
        self.resize(1500, 950)
        self._qsettings = QSettings("CapScan", "CapScan")

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self._build_3d_tab()
        self.map = BoardMap()
        self.map.picked.connect(self._on_picked)
        self.map.heat_changed.connect(self._heat_changed)
        self.tabs.addTab(self.map, "2D Placement")
        self.caps_table, self.signals_table = _table(), _table()
        self.crossings_table, self.vias_table, self.existing_table = _table(), _table(), _table()
        for t, name in ((self.caps_table, "Stitching Caps"), (self.signals_table, "Signals"),
                        (self.crossings_table, "Crossings"), (self.vias_table, "Via Transitions"),
                        (self.existing_table, "Existing Caps")):
            t.itemDoubleClicked.connect(lambda item, t=t: self._on_row(t, item))
            self.tabs.addTab(t, name)

        self._build_docks()
        self._build_toolbar()
        self._restore_settings()
        if result is not None and snapshot is not None:
            self._show_result(snapshot, result)
        elif board is not None or snapshot is not None:
            self.load_board()
        from qtpy.QtCore import QTimer
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._show_preview)
        self.settings_panel.nets.changed.connect(self._picks_changed)
        self._update_actions()

    # ------------------------------------------------------------------ layout
    def _build_3d_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        self.layer_bar = QHBoxLayout()
        self.layer_bar.addWidget(QLabel("Show:"))
        self._layer_boxes: List[QCheckBox] = []
        self._group_boxes = {}
        for group, label in (("tracks", "Signals"), ("caps", "Caps"), ("vias", "Return vias"),
                             ("board", "Board")):
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.toggled.connect(lambda on, g=group: self.viewer and self.viewer.set_group_visible(g, on))
            self.layer_bar.addWidget(cb)
            self._group_boxes[group] = cb
        self.layer_bar.addStretch(1)
        layout.addLayout(self.layer_bar)
        heat_bar = QHBoxLayout()
        self.heat_3d = HeatToggles()        # kept in step with the 2D tab's
        self.heat_3d.changed.connect(lambda: self.map.heat.set_kinds(self.heat_3d.kinds(),
                                                                     emit=True))
        heat_bar.addWidget(self.heat_3d)
        heat_bar.addStretch(1)
        layout.addLayout(heat_bar)
        self.viewer = None
        if Viewer3D is not None:
            try:
                self.viewer = Viewer3D(w)
            except Exception as e:  # noqa: BLE001 - no OpenGL here
                log.warning("3D view unavailable: %s", e)
        if self.viewer is not None:
            layout.addWidget(self.viewer, 1)
        else:
            msg = QLabel(f"The 3D view is not available here ({_VIEWER_ERROR or 'no OpenGL'}). "
                         "The 2D map and the reports have everything it shows.")
            msg.setStyleSheet(HINT)
            msg.setAlignment(Qt.AlignCenter)
            layout.addWidget(msg, 1)
        self.tabs.addTab(w, "3D Heat Map")

    def _build_docks(self):
        self.settings_panel = SettingsPanel(ScanSettings())
        self.settings_panel.run.clicked.connect(self.start_scan)
        self._dock("Scan", self.settings_panel, Qt.RightDockWidgetArea)

        board = QWidget()
        bl = QVBoxLayout(board)
        self.board_label = QLabel("No board yet")
        self.board_label.setWordWrap(True)
        self.board_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bl.addWidget(self.board_label)
        self.stackup_table = _table()
        bl.addWidget(self.stackup_table, 1)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        bl.addWidget(self.summary_label)
        self._dock("Board", board, Qt.LeftDockWidgetArea)

        logw = QWidget()
        ll = QVBoxLayout(logw)
        ll.setContentsMargins(4, 4, 4, 4)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))
        self.log_view.setMaximumBlockCount(5000)
        ll.addWidget(self.log_view)
        row = QHBoxLayout()
        self.stage_label = QLabel("")
        self.stage_label.setStyleSheet(HINT)
        row.addWidget(self.stage_label, 1)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setVisible(False)
        row.addWidget(self.progress)
        self.cancel = QPushButton("Cancel")
        self.cancel.setVisible(False)
        self.cancel.clicked.connect(self._cancel)
        row.addWidget(self.cancel)
        ll.addLayout(row)
        self._dock("Log", logw, Qt.BottomDockWidgetArea)

    def _dock(self, title: str, widget: QWidget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(title)
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        make_detachable(d)
        self.addDockWidget(area, d)
        return d

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setObjectName("main-toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot: Callable, tip=""):
            a = QAction(text, self)
            a.setToolTip(tip or text)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        self.act_scan = act("Scan", self.start_scan, "Read the board from KiCad and scan it")
        tb.addSeparator()
        self.act_markers = act("Place markers", self.place_markers,
                               "Draw the capacitors, return vias and voids on User.Comments "
                               "(one undo step in KiCad)")
        self.act_clear = act("Clear markers", self.clear_markers,
                             "Remove the markers CapScan drew")
        self.act_add = act("Add to board…", self.add_to_board,
                           "Add the stitching capacitors, their vias and the return vias to "
                           "the board in KiCad, keeping to the design rules (DRC checked)")
        tb.addSeparator()
        self.act_html = act("Export HTML…", self.export_html)
        self.act_pdf = act("Export PDF…", self.export_pdf)
        self.act_save = act("Save results…", self.save_results,
                            "Save the scan (JSON) to compare or reopen later")
        tb.addSeparator()
        act("Help", self.show_help)
        act("About", self.show_about, "Version, author, links, and what to put in a bug report")

    def _update_actions(self):
        has = self.result is not None
        running = self._task is not None and self._task.isRunning()
        live = self.board is not None
        ready = self.settings_panel.nets.ready()
        self.act_scan.setEnabled(not running and ready and (live or self.snapshot is not None))
        self.settings_panel.run.setEnabled(self.act_scan.isEnabled())
        tip = "" if ready else "Pick nets to scan, or tick 'Scan all signal nets'"
        self.settings_panel.run.setToolTip(tip)
        for a in (self.act_html, self.act_pdf, self.act_save):
            a.setEnabled(has and not running)
        self.act_markers.setEnabled(has and live and not running)
        self.act_clear.setEnabled(live and not running)
        self.act_add.setEnabled(has and live and not running and self._steps is None
                                and bool(self.result.caps or self.result.stitch_vias))

    # ------------------------------------------------------------------ settings
    def _restore_settings(self):
        raw = self._qsettings.value(SETTINGS_KEY, "")
        if raw:
            try:
                self.settings_panel.load(ScanSettings.from_dict(json.loads(raw)))
            except (ValueError, TypeError) as e:
                log.warning("Saved settings ignored: %s", e)

    def _save_settings(self, s: ScanSettings):
        d = s.to_dict()
        for k in ("selected_nets", "net_edge_rate_ps", "net_impedance_ohm"):
            d.pop(k)
        self._qsettings.setValue(SETTINGS_KEY, json.dumps(d))
        name = self._board_name()
        if name:
            self._qsettings.setValue(NETS_KEY + name, json.dumps(s.selected_nets))
            self._qsettings.setValue(SPEEDS_KEY + name, json.dumps(
                {"edge_ps": s.net_edge_rate_ps, "impedance": s.net_impedance_ohm}))

    def _board_name(self) -> str:
        if self.snapshot is not None:
            return self.snapshot.board_name
        try:
            return self.board.name if self.board is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    def _saved_nets(self) -> List[str]:
        name = self._board_name()
        try:
            return list(json.loads(self._qsettings.value(NETS_KEY + name, "[]") or "[]"))
        except (ValueError, TypeError):
            return []

    def _saved_speeds(self):
        """The edge rates and impedances set per net for this board."""
        name = self._board_name()
        try:
            d = json.loads(self._qsettings.value(SPEEDS_KEY + name, "{}") or "{}")
            return ({k: float(v) for k, v in d.get("edge_ps", {}).items()},
                    {k: float(v) for k, v in d.get("impedance", {}).items()})
        except (ValueError, TypeError, AttributeError):
            return {}, {}

    # ------------------------------------------------------------------ the net list
    def load_board(self):
        """Read the board and list its signal nets, ticking those picked for this board
        before. It is not scanned until asked."""
        settings = self.settings_panel.value()
        board, kicad, cached = self.board, self.kicad, self.snapshot

        def job(progress):
            from capscan.analysis.scan import preview_result
            snap = cached
            if board is not None:
                from capscan.kicad.extract import extract_board
                snap = extract_board(board, refill_zones=False, progress=progress)
                try:
                    snap.kicad_version = str(kicad.get_version()) if kicad else ""
                except Exception:  # noqa: BLE001
                    pass
            return snap, preview_result(snap, settings, progress=progress)

        def done(out):
            snap, preview = out
            self.snapshot, self._preview = snap, preview
            self.settings_panel.nets.blockSignals(True)
            self.settings_panel.nets.set_nets(preview.candidate_nets, self._saved_nets(),
                                              *self._saved_speeds())
            self.settings_panel.nets.blockSignals(False)
            self._show_preview()

        self._run(job, done)

    # ------------------------------------------------------------------ scanning
    def start_scan(self):
        if self._task is not None and self._task.isRunning():
            return
        settings = self.settings_panel.value()
        if not (settings.scan_all_nets or settings.selected_nets):
            self._log("Pick the nets to scan, or tick 'Scan all signal nets'.", logging.WARNING)
            return
        self._save_settings(settings)
        refill = self.settings_panel.refill.isChecked()
        board, kicad, cached = self.board, self.kicad, self.snapshot

        def job(progress):
            from capscan.analysis.scan import run_scan
            if board is not None:
                from capscan.kicad.extract import extract_board
                snap = extract_board(board, refill_zones=refill, progress=progress)
                try:
                    snap.kicad_version = str(kicad.get_version()) if kicad else ""
                except Exception:  # noqa: BLE001
                    pass
            else:
                snap = cached
            return snap, run_scan(snap, settings, progress)

        self.log_view.clear()
        self._run(job, self._scan_done)

    def _run(self, fn, on_done):
        progress = QtProgress()
        progress.sig_message.connect(self._log)
        progress.sig_stage.connect(self.stage_label.setText)
        progress.sig_fraction.connect(self._fraction)
        self._task = Task(fn, progress, self)
        self._task.done.connect(on_done)
        self._task.failed.connect(self._failed)
        self._task.canceled.connect(self._finished)
        self._task.finished.connect(self._finished)
        self.progress.setVisible(True)
        self.cancel.setVisible(True)
        capscan_app.watchdog_start()
        self._task.start()
        self._update_actions()

    def _cancel(self):
        if self._task is not None:
            self._task.progress.cancel()

    def _fraction(self, f: float):
        if f < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(f * 1000))

    def _finished(self):
        capscan_app.watchdog_stop()
        self.progress.setVisible(False)
        self.cancel.setVisible(False)
        self.stage_label.setText("")
        self._update_actions()

    def _failed(self, text: str):
        self._log(text, logging.ERROR)
        QMessageBox.critical(self, "CapScan", f"The scan failed:\n\n{text.splitlines()[-1]}"
                             f"\n\nLog: {capscan_app.LOG_PATH}")

    def _log(self, text: str, level: int = logging.INFO):
        color = LOG_COLORS.get(level)
        esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.log_view.appendHtml(f"<span style='color:{color}'>{esc}</span>" if color
                                 else f"<span>{esc}</span>")

    def _scan_done(self, out):
        snap, result = out
        self._show_result(snap, result)
        if result.candidate_nets:
            self.settings_panel.nets.set_nets(result.candidate_nets)
        self._log(f"Scan done in {result.seconds:.1f} s: {len(result.caps)} stitching "
                  f"capacitor(s) to add.")
        self._autosave()

    def _autosave(self):
        if self.result is None or not self.snapshot or not self.snapshot.project_dir:
            return
        path = Path(self.snapshot.project_dir) / (
            Path(self.snapshot.board_name).stem + ".capscan.json.gz")
        try:
            self.result.save(path)
            self._log(f"Results saved to {path}")
        except OSError as e:
            log.warning("Could not save results to %s: %s", path, e)

    # ------------------------------------------------------------------ showing
    def _show_preview(self):
        """The board as read, before a scan, with the picked nets highlighted."""
        if self._preview is None or self.result is not None:
            return
        from dataclasses import replace

        from capscan.analysis.scan import highlight_tracks
        p = self._preview
        picker = self.settings_panel.nets
        nets = p.candidate_nets if picker.scan_all() else picker.selected()
        p = replace(p, track_heat=highlight_tracks(self.snapshot, nets))
        data = self._data
        if (data is None or data.cache.get("preview") is not self._preview
                or data.snap is not self.snapshot):
            self._data = MapData(self.snapshot, p)
            self._data.cache["preview"] = self._preview
            self._show_board(self.snapshot, p)
            self.map.set_data(self._data)
            self._show_3d()
            return
        # Other nets picked: the planes stay as they are (slow to work out and draw), and
        # only the tracks are drawn again.
        data.use_result(p)
        self._show_board(self.snapshot, p)
        self.map.update_tracks()
        if self.viewer is not None:
            from capscan.report.scene3d import build_scene
            try:
                self.viewer.show_tracks(build_scene(data))
            except Exception as e:  # noqa: BLE001 - the rest of the window still works
                log.exception("3D view failed")
                self._log(f"3D view failed: {e}", logging.WARNING)

    def _picks_changed(self):
        self._update_actions()
        if self.result is None and self._preview is not None:
            self._preview_timer.start()     # redrawn once the clicking stops

    def _show_result(self, snap: BoardSnapshot, r: ScanResult):
        if r is not self.result:
            self._installed = {}
        self.snapshot, self.result = snap, r
        self._data = MapData(snap, r)
        self._show_board(snap, r)
        self.map.set_data(self._data)
        self._fill_tables(r)
        self._show_3d()
        c = r.counts()
        self.tabs.setTabText(2, f"Stitching Caps ({c['caps']})")
        self.tabs.setTabText(4, f"Crossings ({len(r.crossings)})")
        self.tabs.setTabText(5, f"Via Transitions ({c['via_changes']})")
        self._update_actions()

    def _heat_changed(self):
        """Other kinds of heat ticked (on either tab): the 2D map is redrawn already."""
        self.heat_3d.set_kinds(self.map.heat.kinds())
        self._show_3d(keep_camera=True)

    def _show_3d(self, keep_camera: bool = False):
        self.heat_3d.set_counts(None if self._data is None or self._data.result.preview
                                else self._data.result.heat_counts())
        for cb in self._layer_boxes:
            self.layer_bar.removeWidget(cb)
            cb.deleteLater()
        self._layer_boxes = []
        if self.viewer is None or self._data is None:
            return
        from capscan.report.scene3d import build_scene
        try:
            self.viewer.show_scene(build_scene(self._data), keep_camera=keep_camera)
        except Exception as e:  # noqa: BLE001 - the rest of the window still works
            log.exception("3D view failed")
            self._log(f"3D view failed: {e}", logging.WARNING)
            return
        shown = self._data.result
        for h in self._data.heat:
            cb = QCheckBox(shown.layer_name(h.layer))
            cb.setChecked(self.viewer.layer_visible(h.layer))
            cb.toggled.connect(lambda on, l=h.layer: self.viewer.set_layer_visible(l, on))
            self.layer_bar.insertWidget(1 + len(self._layer_boxes), cb)
            self._layer_boxes.append(cb)

    def _show_board(self, snap: BoardSnapshot, r: ScanResult):
        self.board_label.setText(
            f"<b>{snap.board_name}</b><br>{len(snap.stackup.copper)} copper layers, "
            f"{snap.stackup.board_thickness_mm:.2f} mm"
            + (f"<br>KiCad {snap.kicad_version}" if snap.kicad_version else "")
            + f"<br>Plane nets: {', '.join(r.plane_nets) or 'none'}"
            + (f"<br>{len(r.candidate_nets)} signal nets on the board" if r.preview
               else f"<br>{len(r.signal_nets)} signal nets scanned"))
        from capscan.model.stackup import stack_order
        rows = []
        planes = {l for rl in r.references.values() for l, _ in rl}
        for e in stack_order(snap.stackup):
            if isinstance(e, CopperLayer):
                ret = ("" if e.id in planes else
                       ", ".join(r.layer_name(l) for l, _ in r.references.get(e.id, [])))
                rows.append((e.name, f"{e.thickness_mm * 1000:.1f} µm",
                             "plane" if e.id in planes else "signal", ret))
            else:
                rows.append((e.material or "dielectric", f"{e.thickness_mm * 1000:.0f} µm",
                             f"εr {e.epsilon_r:g}" if e.epsilon_r else "", ""))
        fill_table(self.stackup_table, ["Layer", "Thickness", "Role", "Returns on"], rows)
        if r.preview:
            picker = self.settings_panel.nets
            picked = (f"all {len(r.candidate_nets)}" if picker.scan_all()
                      else str(len(picker.selected())))
            lines = ["<b>Not scanned yet.</b>",
                     f"The board as read: planes, and the signal routing. The {picked} nets "
                     "picked to scan are highlighted.",
                     "Pick nets on the right, then <b>Scan board</b>."]
            lines += [f"<span style='color:#ffc060'>{w}</span>" for w in r.warnings]
            self.summary_label.setText("<br>".join(lines))
            return
        c = r.counts()
        lines = [f"<b>{c['caps']}</b> stitching caps to add",
                 f"<b>{c['stitch_vias']}</b> return vias to add",
                 f"{c['splits']} split crossings, {c['via_changes']} vias changing plane",
                 f"{c['existing_credited']} existing caps credited, "
                 f"{c['existing_decoupling']} decoupling caps found"]
        if c["voids"]:
            lines.append(f"<span style='{ERROR}'>{c['voids']} voids: reroute or fill</span>")
        if c["decoupling_flags"]:
            lines.append(f"<span style='{ERROR}'>{c['decoupling_flags']} returns would use "
                         "decoupling caps</span>")
        lines.append(f"<span style='{HINT}'>{part_basis(r.settings)}</span>")
        for w in r.warnings:
            lines.append(f"<span style='color:#ffc060'>{w}</span>")
        self.summary_label.setText("<br>".join(lines))

    def _fill_tables(self, r: ScanResult):
        from capscan.report import html as rep
        fill_table(self.caps_table,
                   ["Cap", "Qty", "Value", "Dielectric", "Package", "Rating", "Net A", "Net B",
                    "X", "Y", "Side", "Angle", "Planes on", "Signals", "Signal nets", "Note",
                    "On board"],
                   [(c.id, c.count, c.value, c.dielectric, c.package, f"{c.voltage_v:g} V", c.net_a,
                     c.net_b, c.position[0], c.position[1], c.side, c.angle_deg,
                     ", ".join(r.layer_name(l) for l in c.ref_layers), len(c.signals),
                     ", ".join(c.signals), c.note, self._installed.get(c.id, ""))
                    for c in r.caps],
                   ids=[c.id for c in r.caps])
        fill_table(self.signals_table,
                   rep.SIGNAL_HEAD, list(rep._signal_rows(r)))
        fill_table(self.crossings_table,
                   ["Id", "Kind", "Signal", "Layer", "Reference", "Net before", "Net after", "X",
                    "Y", "Gap mm", "h mm", "Severity", "Fix", "By", "Decoupling caps"],
                   [(c.id, c.kind, c.net, r.layer_name(c.layer), r.layer_name(c.ref_layer),
                     c.net_a or "(none)", c.net_b or "(none)", c.position[0], c.position[1],
                     c.gap_mm, c.h_mm, c.severity, c.status, c.covered_by,
                     ", ".join(c.decoupling)) for c in r.crossings],
                   ids=[c.id for c in r.crossings], status_col=12)
        vias = r.via_changes()
        fill_table(self.vias_table,
                   ["Id", "Signal", "From", "To", "Reference from", "Reference to", "X", "Y",
                    "Needs", "Fix", "By", "Decoupling caps"],
                   [(t.id, t.net, r.layer_name(t.from_layer), r.layer_name(t.to_layer),
                     ", ".join(f"{n} ({r.layer_name(l)})" for l, n in t.from_refs),
                     ", ".join(f"{n} ({r.layer_name(l)})" for l, n in t.to_refs),
                     t.position[0], t.position[1],
                     "stitching cap" if t.kind == VIA_CAP else "return via", t.status,
                     t.covered_by, ", ".join(t.decoupling)) for t in vias],
                   ids=[t.id for t in vias], status_col=9)
        shown = [e for e in r.existing]
        fill_table(self.existing_table,
                   ["Ref", "Value", "Net A", "Net B", "X", "Y", "Side", "Role", "Near IC",
                    "Credited for", "Flagged for"],
                   [(e.reference, e.value, e.net_a, e.net_b, e.position[0], e.position[1],
                     e.side, e.role, e.near_ic, ", ".join(e.credited), ", ".join(e.flagged))
                    for e in shown], ids=[e.reference for e in shown])

    # ------------------------------------------------------------------ cross-probing
    def _on_row(self, table: QTableWidget, item: QTableWidgetItem):
        first = table.item(item.row(), 0)
        ident = first.data(Qt.UserRole) if first is not None else None
        if not ident or self.result is None:
            return
        self._focus(ident)

    def _on_picked(self, ident: str):
        self._focus(ident, switch=False)

    def _focus(self, ident: str, switch: bool = True):
        r = self.result
        cap = next((c for c in r.caps if c.id == ident), None)
        item = r.item(ident)
        existing = next((e for e in r.existing if e.reference == ident), None)
        if cap is not None:
            pos, layers, net = cap.position, cap.ref_layers, None
        elif item is not None:
            pos = item.position
            layers = [item.ref_layer] if hasattr(item, "ref_layer") else list(item.ref_layers)
            net = item.net
        elif existing is not None:
            pos, layers, net = existing.position, [], None
        else:
            return
        self.map.show_item(ident, layers)
        if self.viewer is not None:
            self.viewer.focus(pos)
        if switch:
            self.tabs.setCurrentWidget(self.map)
        if self.board is not None:
            from capscan.kicad import markers
            if existing is not None:
                markers.select_footprint(self.board, existing.reference)
            elif net:
                markers.select_net(self.board, net)
        self.statusBar().showMessage(f"{ident} at x {pos[0]:.2f}, y {pos[1]:.2f} mm", 8000)

    # ------------------------------------------------------------------ actions
    def place_markers(self):
        from capscan.kicad.markers import place_markers
        try:
            n = place_markers(self.board, self.result)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "CapScan", f"Could not draw the markers: {e}")
            return
        self._log(f"Drew {n} marker items on User.Comments (undo in KiCad removes them).")

    def clear_markers(self):
        from capscan.kicad.markers import clear_markers
        try:
            n = clear_markers(self.board)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "CapScan", f"Could not clear the markers: {e}")
            return
        self._log(f"Removed {n} marker items.")

    # ------------------------------------------------------------------ adding to the board
    def add_to_board(self):
        from qtpy.QtWidgets import QApplication, QDialog

        from capscan.gui.add_dialog import ADD_ALL, AddDialog
        from capscan.kicad.install import Installer
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            inst = Installer(self.kicad, self.board, self.snapshot, self.result)
            dlg = AddDialog(inst, self, self._next_first_ref(inst))
        except Exception as e:  # noqa: BLE001
            log.exception("Add to board")
            QMessageBox.warning(self, "CapScan", f"Could not get ready to add parts: {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        if dlg.exec() != QDialog.Accepted:
            return
        opts = dlg.options()
        self._qsettings.setValue(FIRST_REF_KEY + self._board_name(),
                                 f"{opts.prefix}{opts.start}")
        if dlg.mode == ADD_ALL:
            self._run(lambda p: self._with(inst, p).add_all(opts), self._added)
        else:
            self._start_steps(inst, opts)

    def _next_first_ref(self, inst) -> str:
        """The first reference given last time for this board, moved past what is on the
        board now (so adding again carries on the numbering); "" for none."""
        from capscan.analysis.install import next_references, split_reference
        split = split_reference(str(self._qsettings.value(FIRST_REF_KEY + self._board_name(),
                                                          "") or ""))
        if split is None:
            return ""
        return next_references(inst.taken, split[0], split[1], 1)[0]

    @staticmethod
    def _with(inst, progress):
        inst.progress = progress
        return inst

    def _record(self, out) -> None:
        from capscan.kicad.install import Added
        if isinstance(out, Added):
            self._installed[out.ident] = ", ".join(out.references) or "added"
        else:
            self._installed[out.ident] = f"not added: {out.reason}"

    def _added(self, report):
        for out in report.added + report.skipped:
            self._record(out)
        for line in report.lines():
            self._log(line, logging.WARNING if "not added" in line or line.startswith(" ")
                      else logging.INFO)
        self._log(f"Added {len(report.added)}, not added {len(report.skipped)}; DRC finds "
                  "nothing new. Scan again to check the board with them.")
        self._fill_tables(self.result)
        QMessageBox.information(
            self, "CapScan", f"{len(report.added)} added, {len(report.skipped)} not added "
            "(the log says why). Undo in KiCad takes the added ones off.")

    def _start_steps(self, inst, opts):
        from capscan.gui.add_dialog import StepPanel
        queue = [c.id for c in self.result.caps] + [v.id for v in self.result.stitch_vias]
        panel = StepPanel(self)
        self._steps = {"inst": inst, "opts": opts, "queue": queue, "total": len(queue),
                       "index": 0, "current": None, "kept": [], "panel": panel}
        panel.keep.connect(self._step_next)
        panel.take_off.connect(self._step_take_off)
        panel.rest.connect(self._step_rest)
        panel.stop.connect(self._step_stop)
        panel.show()
        panel.busy("Running DRC before adding…")
        self._run(lambda p: self._with(inst, p).begin_steps(), lambda _: self._step_next())

    def _step_next(self):
        s = self._steps
        if s is None:
            return
        if not s["queue"]:
            self._step_stop()
            return
        ident = s["queue"].pop(0)
        s["index"] += 1
        s["panel"].busy(f"Adding {ident}…")
        inst, opts = s["inst"], s["opts"]
        self._run(lambda p: self._with(inst, p).add_one(ident, opts), self._step_shown)

    def _step_shown(self, out):
        from capscan.gui.add_dialog import describe
        from capscan.kicad.install import Added
        s = self._steps
        if s is None:
            return
        self._record(out)
        self._fill_tables(self.result)
        text = describe(self.result, out.ident)
        if isinstance(out, Added):
            s["current"] = out
            s["kept"].append(out)
            refs = ", ".join(out.references)
            s["panel"].show_added(s["index"], s["total"], out.ident,
                                  (f"As {refs}: " if refs else "") + text
                                  + "".join(f"<br>{n}" for n in out.notes))
        else:
            s["current"] = None
            s["panel"].show_skipped(s["index"], s["total"], out.ident,
                                    f"{text}<br>{out.reason}"
                                    + "".join(f"<br>{v}" for v in out.violations[:5]))

    def _step_take_off(self):
        s = self._steps
        if s is None or s["current"] is None:
            self._step_next()
            return
        out, inst = s["current"], s["inst"]
        s["panel"].busy(f"Taking {out.ident} off…")
        s["kept"].remove(out)
        self._installed[out.ident] = "taken off"
        self._run(lambda p: self._with(inst, p).remove([out], f"CapScan: take {out.ident} off"),
                  lambda _: self._step_next())

    def _step_rest(self):
        """Add what is left in one go; DRC then checks it with the parts kept so far."""
        s, self._steps = self._steps, None
        if s is None:
            return
        s["panel"].close()
        inst, opts, left = s["inst"], s["opts"], set(s["queue"])
        caps = [c for c in self.result.caps if c.id in left]
        vias = [v for v in self.result.stitch_vias if v.id in left]
        self._run(lambda p: self._with(inst, p).add_all(opts, caps, vias, kept=s["kept"]),
                  self._added)

    def _step_stop(self):
        """End a step-through: DRC checks the parts kept (once, not after each)."""
        s, self._steps = self._steps, None
        if s is not None:
            s["panel"].close()
            inst, opts, kept = s["inst"], s["opts"], s["kept"]
            if kept:
                self._log(f"Step through ended with {len(kept)} kept; running DRC on them.")
                self._run(lambda p: self._with(inst, p).finish_steps(opts, kept),
                          self._added)
            else:
                self._log("Step through ended: nothing added.")
        self._update_actions()

    def _default_path(self, suffix: str) -> str:
        snap = self.snapshot
        base = Path(snap.project_dir) if snap and snap.project_dir else Path.home()
        stem = Path(snap.board_name).stem if snap else "board"
        return str(base / f"{stem}-capscan{suffix}")

    def export_html(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export HTML report",
                                              self._default_path(".html"), "HTML (*.html)")
        if not path:
            return
        from capscan.report.html import write_html
        write_html(self.snapshot, self.result, path)
        self._log(f"HTML report written to {path}")
        from capscan.gui.browser import open_in_browser
        open_in_browser(path)

    def export_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF report",
                                              self._default_path(".pdf"), "PDF (*.pdf)")
        if not path:
            return
        from capscan.report.html import write_pdf
        try:
            write_pdf(self.snapshot, self.result, path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "CapScan", f"Could not write the PDF: {e}")
            return
        self._log(f"PDF report written to {path}")
        from capscan.gui.browser import open_in_browser
        open_in_browser(path)

    def save_results(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save results",
                                              self._default_path(".json.gz"),
                                              "CapScan results (*.json.gz *.json)")
        if path:
            self.result.save(path)
            self._log(f"Results saved to {path}")

    def show_help(self):
        from capscan.gui.browser import open_in_browser
        help_file = Path(__file__).resolve().parents[2] / "docs" / "help.html"
        if not open_in_browser(help_file):
            QMessageBox.information(self, "CapScan", f"The user guide is at {help_file}")

    def show_about(self):
        from capscan.gui.about import AboutDialog
        kicad = self.snapshot.kicad_version if self.snapshot is not None else ""
        AboutDialog(self, kicad_version=kicad).exec()

    def closeEvent(self, event):
        if self.viewer is not None:
            self.viewer.close_view()
        super().closeEvent(event)

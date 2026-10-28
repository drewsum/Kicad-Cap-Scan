"""The window, offscreen, on a board built in code."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qtpy")

from boards import B_CU, board, track  # noqa: E402

NETS = ["D0", "D1", "D2"]


@pytest.fixture(scope="module")
def app():
    from capscan.gui.app import make_app
    return make_app()


def _wait(app, cond, timeout=30):
    t0 = time.time()
    while not cond():
        assert time.time() - t0 < timeout, "timed out"
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def make_window(app, monkeypatch, tmp_path):
    from qtpy.QtCore import QSettings
    from capscan.gui import main_window
    monkeypatch.setattr(main_window, "Viewer3D", None)       # no OpenGL offscreen
    monkeypatch.setattr(main_window, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    windows = []

    def make():
        tracks = [track(n, B_CU, (12.0, y), (28.0, y)) for n, y in zip(NETS, (5.0, 6.0, 7.0))]
        w = main_window.MainWindow(snapshot=board(tracks=tracks))
        windows.append(w)
        _wait(app, lambda: w.settings_panel.nets.nets() and not w._task.isRunning())
        return w

    yield make
    for w in windows:
        w.close()


def _scan(app, w):
    w.result = None
    w.start_scan()
    _wait(app, lambda: w.result is not None and not w._task.isRunning())


def test_opens_with_the_net_list_and_does_not_scan(app, make_window):
    w = make_window()
    picker = w.settings_panel.nets
    assert picker.nets() == NETS and picker.selected() == [] and not picker.scan_all()
    assert w.result is None
    assert not w.settings_panel.run.isEnabled() and not w.act_scan.isEnabled()
    assert "No nets selected" in picker.count.text()


def test_scan_all_nets(app, make_window):
    w = make_window()
    w.settings_panel.nets.set_scan_all(True)
    assert w.settings_panel.run.isEnabled()
    _scan(app, w)
    r = w.result
    assert r.signal_nets == NETS and len(r.caps) == 2
    assert w.caps_table.rowCount() == 2 and w.crossings_table.rowCount() == 3
    assert "Stitching Caps (2)" in [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert w.act_html.isEnabled() and not w.act_markers.isEnabled()      # no KiCad


def test_scan_picked_nets_only(app, make_window):
    w = make_window()
    w.settings_panel.nets.set_selected(["D1"])
    assert w.settings_panel.run.isEnabled()
    _scan(app, w)
    assert w.result.signal_nets == ["D1"] and len(w.result.caps) == 1
    assert w.settings_panel.nets.selected() == ["D1"]                     # kept after the scan


def test_picked_nets_are_remembered_for_the_board(app, make_window):
    first = make_window()
    first.settings_panel.nets.set_selected(["D0", "D2"])
    _scan(app, first)
    again = make_window()                        # same board: the same nets, not scanned
    assert again.settings_panel.nets.selected() == ["D0", "D2"]
    assert again.result is None and again._data.result.preview
    assert not again.settings_panel.refill.isChecked()     # zones only refilled on request
    _scan(app, again)
    assert again.result.signal_nets == ["D0", "D2"]


def test_filter_and_select_shown(app, make_window):
    w = make_window()
    picker = w.settings_panel.nets
    picker.filter.setText("d[02]")
    assert picker.shown() == ["D0", "D2"]
    picker.select_shown.click()
    assert picker.selected() == ["D0", "D2"]
    picker.filter.setText("1")
    picker.select_shown.click()
    picker.filter.clear()
    assert picker.selected() == NETS
    picker.clear_shown.click()
    assert picker.selected() == []


def test_all_nets_greys_out_the_list(app, make_window):
    picker = make_window().settings_panel.nets
    picker.set_scan_all(True)
    assert not picker.select_shown.isEnabled() and not picker.clear_shown.isEnabled()
    assert picker.list.isEnabled()          # speeds still apply to every net
    assert "All 3 signal nets" in picker.count.text()


def test_speed_per_net(app, make_window):
    w = make_window()
    panel, picker = w.settings_panel, w.settings_panel.nets
    assert picker.speeds() == ({}, {})
    assert picker.list.item(0, 1).text() == "100"                      # the default, in grey
    picker.list.item(1, 1).setText("50")                               # D1 typed in
    picker.filter.setText("D2")
    picker.edge_spin.setValue(300)
    picker.z0_spin.setValue(45)
    picker.set_speed.click()
    picker.filter.clear()
    assert picker.speeds() == ({"D1": 50.0, "D2": 300.0}, {"D2": 45.0})
    assert "2 with their own" in picker.count.text()
    picker.list.item(1, 1).setText("")                                 # back to the default
    assert picker.speeds()[0] == {"D2": 300.0}
    panel._fields["edge_rate_ps"].setValue(80)
    assert picker.list.item(0, 1).text() == "80"                       # defaults follow
    s = panel.value()
    assert (s.edge_rate_ps, s.net_edge_rate_ps, s.net_impedance_ohm) == \
        (80.0, {"D2": 300.0}, {"D2": 45.0})
    picker.filter.setText("D2")
    picker.reset_speed.click()
    assert picker.speeds() == ({}, {})


def test_double_click_highlights_on_the_map(app, make_window):
    from qtpy.QtCore import Qt
    w = make_window()
    w.settings_panel.nets.set_scan_all(True)
    _scan(app, w)
    item = w.caps_table.item(0, 0)
    w._on_row(w.caps_table, item)
    assert w.map._highlight == item.data(Qt.UserRole)
    assert w.tabs.currentWidget() is w.map


def test_settings_round_trip(app, make_window):
    from capscan.analysis.rules import ScanSettings
    w = make_window()
    s = ScanSettings(max_cap_distance_mm=4.0, signals_per_cap=3, ignore_nets=["TP*"],
                     check_via_caps=False, scan_all_nets=True)
    w.settings_panel.load(s)
    back = w.settings_panel.value()
    assert (back.max_cap_distance_mm, back.signals_per_cap, back.ignore_nets) == (4.0, 3, ["TP*"])
    assert back.check_via_caps is False and back.check_splits is True
    assert back.scan_all_nets is True


def test_check_switches_are_always_shown(app, make_window):
    from qtpy.QtWidgets import QCheckBox
    panel = make_window().settings_panel
    for name in ("check_splits", "check_via_caps", "check_via_stitch", "check_voids"):
        w = panel._fields[name]
        assert isinstance(w, QCheckBox) and w not in panel._adv_widgets


def test_net_filter_patterns():
    from capscan.gui.net_picker import matches_filter
    assert matches_filter("USB_D+", "usb") and matches_filter("DDR_DQ3", "DDR*")
    assert matches_filter("CLK1", "clk?") and not matches_filter("CLK10", "clk?")
    assert matches_filter("anything", "")


def test_board_is_shown_before_a_scan(app, make_window):
    w = make_window()
    assert w.result is None and w._data is not None and w._data.result.preview
    assert w.map._data is w._data and w.stackup_table.rowCount() == 7
    assert "Not scanned yet" in w.summary_label.text()
    assert not w.act_html.isEnabled()                       # nothing to report yet
    assert w._data.track_heat == [0.0, 0.0, 0.0]            # nothing picked


def test_picked_nets_light_up_in_the_preview(app, make_window):
    w = make_window()
    w.settings_panel.nets.set_selected(["D1"])
    _wait(app, lambda: w._data.track_heat == [0.0, 1.0, 0.0])
    w.settings_panel.nets.set_scan_all(True)
    _wait(app, lambda: w._data.track_heat == [1.0, 1.0, 1.0])
    _scan(app, w)
    assert not w._data.result.preview and "Not scanned yet" not in w.summary_label.text()


def test_picking_nets_redraws_only_the_tracks(app, make_window, monkeypatch):
    """#17: the planes are slow to work out and draw, and picking nets does not move them."""
    from capscan.report import maps
    w = make_window()
    w.map.layer.setCurrentIndex(w.map.layer.findText("All reference layers"))
    data, ax = w._data, w.map._ax
    assert w.map._tracks in ax.collections
    calls = []
    real = maps.zone_regions
    monkeypatch.setattr(maps, "zone_regions", lambda snap: calls.append(1) or real(snap))
    w.settings_panel.nets.set_selected(["D0", "D2"])
    _wait(app, lambda: w._data.track_heat == [1.0, 0.0, 1.0])
    assert w._data is data and w.map._ax is ax and not calls       # the same planes
    assert w.map._tracks in ax.collections                          # the new tracks drawn
    assert sum(isinstance(c, type(w.map._tracks)) for c in ax.collections) == 1
    assert "The 2 nets picked" in w.summary_label.text()


def test_heat_tick_boxes_pick_the_heat_shown_on_both_tabs(app, make_window):
    from capscan.analysis.results import SPLIT, VIA_CAP, VIA_STITCH, VOID
    from capscan.report.maps import MapData
    w = make_window()
    assert w.map.heat.boxes[SPLIT].text() == "Splits"      # no counts before a scan
    w.settings_panel.nets.set_scan_all(True)
    _scan(app, w)
    assert w.map.heat.boxes[SPLIT].text() == "Splits (3)"
    assert w.heat_3d.boxes[VIA_STITCH].text() == "Return vias (0)"
    assert w._data.heat and max(w._data.track_heat) > 0.5

    w.map.heat.boxes[SPLIT].setChecked(False)               # on the 2D tab
    assert w._data.heat_kinds == [VOID, VIA_CAP, VIA_STITCH]
    assert not w.heat_3d.boxes[SPLIT].isChecked()           # the 3D tab follows
    assert w._data.heat == [] and max(w._data.track_heat) == 0.0

    w.heat_3d.boxes[SPLIT].setChecked(True)                 # and back, on the 3D tab
    assert w.map.heat.boxes[SPLIT].isChecked()
    assert w._data.track_heat == MapData(w.snapshot, w.result).track_heat   # all, as scanned


def test_bring_to_front_restores_and_forces_the_foreground(app, make_window, monkeypatch):
    from capscan.gui import app as capscan_app
    w = make_window()
    calls = []
    monkeypatch.setattr(capscan_app.sys, "platform", "win32")
    monkeypatch.setattr(capscan_app, "_win32_foreground", calls.append)
    w.showMinimized()
    capscan_app.bring_to_front(w)
    assert w.isVisible() and not w.isMinimized()
    assert calls == [int(w.winId())]


def test_add_to_board_needs_a_live_board(app, make_window):
    w = make_window()
    w.settings_panel.nets.set_scan_all(True)
    _scan(app, w)
    assert w.result.caps and not w.act_add.isEnabled()      # a board file, not KiCad
    assert w.caps_table.horizontalHeaderItem(w.caps_table.columnCount() - 1).text() \
        == "On board"


def test_add_dialog_takes_the_first_reference(app):
    from types import SimpleNamespace

    from capscan.analysis.install import BoardRules, ViaSize
    from capscan.gui.add_dialog import AddDialog
    from capscan.kicad.install import AddOptions
    from boards import scan

    r = scan(board(tracks=[track("D0", B_CU, (12.0, 5.0), (28.0, 5.0))]))
    src = SimpleNamespace(label="Capacitor_SMD:C_0402_1005Metric (KiCad library)")
    inst = SimpleNamespace(
        result=r, cli="kicad-cli", rules=BoardRules(), allowed=["through", "micro"],
        taken={"C100"}, sources=lambda p: [src], via_sizes=lambda: [ViaSize(0.6, 0.3)],
        defaults=lambda: AddOptions({}, ViaSize(0.6, 0.3), ["through", "micro"], "C", 7))
    d = AddDialog(inst)
    assert d.first_ref.text() == "C7" and d.add_all.isEnabled()
    d.first_ref.setText("C100")
    assert d.ref_hint.text() == "C101"                       # C100 is on the board
    opts = d.options()
    assert (opts.prefix, opts.start, opts.footprints["0402"]) == ("C", 100, src)
    assert opts.via_types == ["through", "micro"]
    d.type_boxes["micro"].setChecked(False)
    assert d.options().via_types == ["through"]
    d.first_ref.setText("100")
    assert not d.add_all.isEnabled() and not d.step.isEnabled()
    d2 = AddDialog(inst, first_reference="CS4")
    assert d2.first_ref.text() == "CS4"
    # Without a parent, the dialogs would otherwise be freed as Python exits, after the
    # QApplication: a crash once the tests are over.
    from qtpy.QtCore import QEvent
    for dlg in (d, d2):
        dlg.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def test_step_through_names_the_signal_each_part_is_for(app):
    from capscan.gui.add_dialog import describe
    from boards import scan

    r = scan(board(tracks=[track("D0", B_CU, (12.0, 5.0), (28.0, 5.0))]))
    (cap,) = r.caps
    text = describe(r, cap.id)
    assert "For: <b>D0</b> (crosses the split on In2.Cu)" in text


@pytest.mark.parametrize("mode", ["all", "step"])
def test_add_to_board_flow(app, make_window, monkeypatch, mode):
    from qtpy.QtWidgets import QDialog, QMessageBox

    from capscan.gui import add_dialog
    from capscan.kicad import install

    calls = []

    class Inst:
        def __init__(self, kicad, board, snap, result):
            self.taken, self.progress = set(), None

        def add_all(self, opts, caps=None, vias=None):
            calls.append(("all", [c.id for c in caps] if caps is not None else None))
            return install.AddReport(added=[install.Added("CS1", ["C1"])])

        def begin_steps(self):
            calls.append("begin")

        def add_one(self, ident, opts):
            calls.append(("one", ident))
            return install.Added(ident, ["C9"])

        def finish_steps(self, opts, kept):
            calls.append(("finish", [a.ident for a in kept]))
            return install.AddReport(added=list(kept))

    class Dialog:
        def __init__(self, inst, parent=None, first_reference=""):
            self.mode = mode

        def exec(self):
            return QDialog.Accepted

        def options(self):
            return install.AddOptions({}, None, [], "C", 9)

    monkeypatch.setattr(install, "Installer", Inst)
    monkeypatch.setattr(add_dialog, "AddDialog", Dialog)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    w = make_window()
    w.settings_panel.nets.set_scan_all(True)
    _scan(app, w)
    w.board = object()
    w._update_actions()
    assert w.act_add.isEnabled()
    w.add_to_board()
    if mode == "all":
        _wait(app, lambda: w._installed.get("CS1") == "C1" and not w._task.isRunning())
        assert calls == [("all", None)]
    else:
        _wait(app, lambda: w._installed.get("CS1") == "C9" and not w._task.isRunning())
        assert calls == ["begin", ("one", "CS1")]
        assert w._steps["panel"].title.text().startswith("1 of")
        w._steps["panel"].stop.emit()
        assert w._steps is None
        _wait(app, lambda: not w._task.isRunning() and w.act_add.isEnabled())
        assert calls[-1] == ("finish", ["CS1"])                 # DRC once, at the end
    col = w.caps_table.columnCount() - 1
    assert w.caps_table.item(0, col).text() in ("C1", "C9")
    w.board = None

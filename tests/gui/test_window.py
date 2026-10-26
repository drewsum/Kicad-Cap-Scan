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
    again = make_window()                        # same board: scans the same nets at once
    _wait(app, lambda: again.result is not None and not again._task.isRunning())
    assert again.settings_panel.nets.selected() == ["D0", "D2"]
    assert again.result.signal_nets == ["D0", "D2"]


def test_filter_and_select_shown(app, make_window):
    w = make_window()
    picker = w.settings_panel.nets
    picker.filter.setText("d[02]")
    shown = [picker.list.item(i).text() for i in range(picker.list.count())
             if not picker.list.item(i).isHidden()]
    assert shown == ["D0", "D2"]
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
    assert not picker.list.isEnabled() and not picker.filter.isEnabled()
    assert "All 3 signal nets" in picker.count.text()


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

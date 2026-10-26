"""The window, offscreen, on a board built in code."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qtpy")

from boards import B_CU, board, track  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from capscan.gui.app import make_app
    return make_app()


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    from qtpy.QtCore import QSettings
    from capscan.gui import main_window
    monkeypatch.setattr(main_window, "Viewer3D", None)       # no OpenGL offscreen
    monkeypatch.setattr(main_window, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    tracks = [track(f"D{i}", B_CU, (12.0, y), (28.0, y)) for i, y in enumerate((5.0, 6.0, 7.0))]
    w = main_window.MainWindow(snapshot=board(tracks=tracks))
    t0 = time.time()
    while (w.result is None or w._task.isRunning()) and time.time() - t0 < 30:
        app.processEvents()
        time.sleep(0.01)
    yield w
    w.close()


def test_scan_fills_the_window(window):
    r = window.result
    assert r is not None and len(r.caps) == 2
    assert window.caps_table.rowCount() == 2
    assert window.crossings_table.rowCount() == 3
    assert "Stitching Caps (2)" in [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert window.act_html.isEnabled() and not window.act_markers.isEnabled()   # no KiCad


def test_double_click_highlights_on_the_map(window):
    from qtpy.QtCore import Qt
    item = window.caps_table.item(0, 0)
    window._on_row(window.caps_table, item)
    assert window.map._highlight == item.data(Qt.UserRole)
    assert window.tabs.currentWidget() is window.map


def test_settings_round_trip(window):
    from capscan.analysis.rules import ScanSettings
    s = ScanSettings(max_cap_distance_mm=4.0, signals_per_cap=3, ignore_nets=["TP*"])
    window.settings_panel.load(s)
    back = window.settings_panel.value()
    assert (back.max_cap_distance_mm, back.signals_per_cap, back.ignore_nets) == (4.0, 3, ["TP*"])

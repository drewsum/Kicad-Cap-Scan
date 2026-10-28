"""Drawings in tooltips (#16): SVG templates in capscan/gui/graphics, coloured from the
palette, rendered once and shown under the tooltip text, or the text alone without them."""

import os
import re

import pytest

pytest.importorskip("qtpy")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtGui import QColor, QImage, QPalette  # noqa: E402
from qtpy.QtWidgets import QApplication, QWidget  # noqa: E402

from capscan.gui import tooltip_art as ta  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    """Render into a folder of this test's own, without the drawings already made."""
    monkeypatch.setattr(ta.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(ta, "_rendered", {})


def test_the_graphics_folder_holds_the_drawings():
    assert ta.GRAPHICS.is_dir()
    assert {"split_crossing", "via_transition", "void", "edge_rate", "decoupling",
            "supply_detect", "derating", "impedance"} <= set(ta.names())


@pytest.mark.parametrize("name", ta.names())
def test_each_drawing_renders_in_the_palette(app, name):
    svg = (ta.GRAPHICS / f"{name}.svg").read_text(encoding="utf-8")
    assert '"{bg}"' in svg                       # on the GUI's background
    shown = ta.image(name)
    assert shown is not None, name
    path, w, h = shown
    img = QImage(str(path))
    assert (img.width(), img.height()) == (w * ta.SCALE, h * ta.SCALE)
    bg = QColor(ta.palette_colors()["bg"])
    assert img.pixelColor(w, 4 * ta.SCALE).name() == bg.name()   # top edge, inside the corner


def test_fill_colours_every_token():
    colors = {"bg": "#111111", "fg": "#eeeeee", "muted": "#888888", "grid": "#222222",
              "accent": "#ff8800", "warn": "#663322"}
    out = ta.fill('<a f="{bg}"/><b f="{c}"/><c f="{l}"/>', colors, ["l"])
    assert out == '<a f="#111111"/><b f="#eeeeee"/><c f="#ff8800"/>'
    for name in ta.names():
        svg = ta.fill((ta.GRAPHICS / f"{name}.svg").read_text(encoding="utf-8"), colors)
        assert not re.search(r"\{\w+\}", svg), name


def test_accent_stands_out_even_where_the_highlight_is_grey(app):
    pal = QPalette(QColor(40, 40, 40))
    pal.setColor(QPalette.Highlight, QColor(70, 70, 70))
    assert ta.accent_color(pal).hsvSaturation() > 90               # the orange instead
    pal.setColor(QPalette.Highlight, QColor(48, 140, 198))
    assert ta.accent_color(pal) == QColor(48, 140, 198)


def test_a_drawing_follows_the_palette(app):
    dark = QPalette(QColor(40, 40, 40))
    light = QPalette(QColor(240, 240, 240))
    assert ta.image("split_crossing", (), dark)[0] != ta.image("split_crossing", (), light)[0]
    assert ta.image("split_crossing", ("gap",))[0] != ta.image("split_crossing", ("dist",))[0]


def test_art_tip_puts_the_drawing_under_the_text(app):
    tip = ta.art_tip("Gap & cap\nof <one> split", "split_crossing", "gap")
    assert "Gap &amp; cap<br>of &lt;one&gt; split" in tip
    assert re.search(r'<img src="file:[^"]+split_crossing-\w+\.png"', tip)


def test_without_svg_support_the_tooltip_is_its_text(app, monkeypatch):
    monkeypatch.setattr(ta, "_render_png", lambda svg, path: None)
    assert ta.art_tip("Edge rate", "edge_rate", "tr") == "Edge rate"
    assert ta.art_tip("Unknown", "no_such_drawing") == "Unknown"


def test_add_text_goes_above_the_drawing(app):
    assert ta.add_text("", "Why") == "Why"
    assert ta.add_text("Plain", "Why") == "Plain\n\nWhy"
    tip = ta.add_text(ta.art_tip("Edge rate", "edge_rate", "tr"), "Used by <caps>")
    assert tip.index("Used by &lt;caps&gt;") < tip.index("<img")


@pytest.fixture
def panel(app):
    from qtpy.QtCore import QEvent
    from capscan.analysis.rules import ScanSettings
    from capscan.gui.main_window import SettingsPanel
    p = SettingsPanel(ScanSettings())
    yield p
    # Deleted now: freed as Python exits, after the QApplication, it would crash.
    p.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def test_every_setting_has_a_tooltip(panel):
    from capscan.analysis.rules import DESCRIPTIONS
    from capscan.gui.main_window import TIPS, setting_tip
    assert set(TIPS) == set(DESCRIPTIONS)
    for name, w in panel._fields.items():
        assert w.toolTip() == setting_tip(name) != "", name
    assert "/split_crossing-" in panel._fields["split_gap_max_mm"].toolTip()


def test_every_drawing_shows_in_a_tooltip(panel):
    """Each drawing in the folder is used, so the folder holds no strays."""
    table = panel.nets.list
    tips = [c.toolTip() for c in panel.findChildren(QWidget)]
    tips += [table.horizontalHeaderItem(i).toolTip() for i in range(table.columnCount())]
    for name in ta.names():
        assert f"/{name}-" in " ".join(tips), name


def test_the_per_net_speed_fields_have_tooltips(panel):
    """The net list's edge rate and impedance: their headers, each net's cells and the
    boxes 'Set shown' takes them from, the impedance with what Z0 is."""
    from capscan.gui.net_picker import EDGE, Z0
    nets = panel.nets
    nets.set_nets(["CLK", "D0"])
    for col, drawing, box in ((EDGE, "edge_rate", nets.edge_spin),
                              (Z0, "impedance", nets.z0_spin)):
        head = nets.list.horizontalHeaderItem(col).toolTip()
        assert f"/{drawing}-" in head
        for r in range(nets.list.rowCount()):
            assert nets.list.item(r, col).toolTip() == head
        assert f"/{drawing}-" in box.toolTip() and "Set shown" in box.toolTip()
    assert "Characteristic impedance" in nets.z0_spin.toolTip()

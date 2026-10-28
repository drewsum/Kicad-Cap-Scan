"""HTML and PDF reports of a scan."""

import os

import pytest

from boards import B_CU, F_CU, board, ic, track, two_pad_part, via
from boards import scan as run_scan
from capscan.report.html import report_html, write_html


def _scan():
    tracks = [track(f"D{i}", B_CU, (12.0, y), (28.0, y)) for i, y in enumerate((5.0, 6.0, 7.0))]
    tracks += [track("SIG", B_CU, (2.0, 12.0), (12.0, 12.0)),
               track("SPI_CLK", F_CU, (5.0, 25.0), (10.0, 25.0)),
               track("SPI_CLK", B_CU, (10.0, 25.0), (15.0, 25.0))]
    fp, pads = two_pad_part("C3", "100n", "+3V3", "+1V8", (20.0, 6.0), side="bottom")
    u, upads = ic("U1", [("+3V3", (21.0, 8.0)), ("GND", (22, 8)), ("+1V8", (23, 8))])
    snap = board(tracks=tracks, vias=[via("SPI_CLK", (10.0, 25.0))], footprints=[fp, u],
                 pads=pads + upads)
    return snap, run_scan(snap)


def test_html_report_has_every_section(tmp_path):
    snap, r = _scan()
    text = write_html(snap, r, tmp_path / "r.html").read_text(encoding="utf-8")
    for anchor in ("summary", "caps", "signals", "crossings", "vias", "existing", "maps", "3d"):
        assert f"id='{anchor}'" in text
    for cap in r.caps:
        assert f"<td class='l'>{cap.id}</td>" in text
    assert "C3" in text and "decoupling" in text
    assert "data:image/png;base64," in text and "Plotly" in text
    assert "SPI_CLK" in text and "In2.Cu" in text


def test_html_report_lists_decoupling_caps_no_crossing_reached():
    fp, pads = two_pad_part("C40", "100n", "+3V3", "+1V8", (5.0, 2.0))
    u, upads = ic("U9", [("+3V3", (3.5, 2.0)), ("GND", (3.5, 3.0)), ("+1V8", (3.5, 4.0))])
    snap = board(footprints=[fp, u], pads=pads + upads)
    r = run_scan(snap)
    (e,) = r.existing
    assert e.role == "decoupling" and not (e.credited or e.flagged)
    text = report_html(snap, r)
    assert "<td class='l'>C40</td>" in text and "next to U9.1" in text


def test_pdf_html_is_static():
    snap, r = _scan()
    text = report_html(snap, r, pdf=True)
    assert "<script" not in text and "[[capscan-user-notes]]" in text


def test_pdf_report(tmp_path):
    pytest.importorskip("qtpy")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qtpy.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from capscan.report.html import write_pdf
    snap, r = _scan()
    data = write_pdf(snap, r, tmp_path / "r.pdf").read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 20_000
    assert b"/EmbeddedFile" in data or (tmp_path / "r-3d.html").exists()


def test_pdf_images_are_shrunk_to_fit_the_page():
    """#7: a map wider or taller than the page was cut off at its edge."""
    from capscan.report.pdf import fit_image
    assert fit_image(1500, 1000, 150, 700, 900) == (700, 466)    # too wide
    assert fit_image(1000, 3000, 96, 700, 900) == (300, 900)     # too tall
    assert fit_image(300, 200, 150, 700, 900) == (192, 128)      # its own size
    assert fit_image(300, 200, 0, 700, 900) == (300, 200)        # no dpi: 96


def _first_page(doc, h, text):
    block = doc.begin()
    while block.isValid():
        if block.text().startswith(text):
            top = doc.documentLayout().blockBoundingRect(block).top()
            return int(top // h)
        block = block.next()


def test_pdf_heading_and_note_go_over_with_their_table():
    """#7: a heading and the note under it ended a page and their table began the next."""
    pytest.importorskip("qtpy")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qtpy.QtCore import QSizeF
    from qtpy.QtGui import QTextDocument
    from qtpy.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from capscan.report.pdf import _stranded_heading

    h = 400
    table = "<table><tr><th>Setting</th></tr><tr><td>row one</td></tr></table>"
    for n in range(1, 40):
        doc = QTextDocument()
        doc.setPageSize(QSizeF(300, h))
        doc.setHtml("<p>filler</p>" * n + "<h2>Method</h2><p>A short note.</p>" + table)
        pages = [_first_page(doc, h, t) for t in ("Method", "A short note", "row one")]
        if pages[0] == pages[1] != pages[2]:
            break
    else:
        pytest.fail("no layout left the table alone on the next page")
    assert _stranded_heading(doc, h) == 0


# Before a scan the copper is drawn in KiCad's layer colors (#14).

def _maps(highlight=()):
    import matplotlib
    matplotlib.use("Agg", force=False)
    from matplotlib.figure import Figure

    from capscan.analysis.scan import preview_result
    from capscan.report.maps import DARK, MapData, draw_map, legend_handles
    snap, scanned = _scan()
    preview = MapData(snap, preview_result(snap, highlight=highlight))
    out = []
    for data in (preview, MapData(snap, scanned)):
        ax = Figure().add_subplot(111)
        draw_map(ax, data, None)
        out.append((data, ax, legend_handles(DARK, data, data.reference_layers())))
    return out


def _plane_colors(ax):
    from matplotlib.colors import to_hex
    return {to_hex(p.get_facecolor(), keep_alpha=False) for p in ax.patches}


def test_kicad_layer_colors():
    from capscan.model.layers import inner_cu
    from capscan.report.maps import KICAD_INNER_CU, layer_color
    assert (layer_color(F_CU), layer_color(B_CU)) == ("#c83434", "#4d7fc4")
    assert (layer_color(inner_cu(1)), layer_color(inner_cu(2))) == ("#7fc87f", "#ce7d2c")
    assert len(KICAD_INNER_CU) == 30 and layer_color(inner_cu(30)) == "#f2eda1"


def test_preview_draws_copper_in_kicad_layer_colors():
    from capscan.report.maps import NET_COLORS, layer_color
    (pre, pre_ax, pre_legend), (scanned, ax, legend) = _maps()
    planes = pre.reference_layers()
    assert _plane_colors(pre_ax) == {layer_color(l) for l in planes}
    assert _plane_colors(ax) <= set(NET_COLORS)            # scanned: planes by net, as before
    names = [h.get_label() for h in pre_legend]
    assert "In1.Cu" in names and "B.Cu" in names and "Split crossing" not in names
    assert "Split crossing" in [h.get_label() for h in legend]


def test_preview_tracks_take_their_layers_color_and_picked_nets_stand_out():
    from matplotlib.colors import to_hex
    from capscan.report.maps import PREVIEW_DIM, layer_color
    (_, ax, _), _ = _maps()
    (tracks,) = ax.collections
    colors = {(to_hex(c, keep_alpha=False), round(c[3], 2)) for c in tracks.get_colors()}
    assert colors == {(layer_color(F_CU), 1.0), (layer_color(B_CU), 1.0)}  # nothing picked
    snap, _ = _scan()
    picked = [i for i, t in enumerate(snap.tracks) if t.net == "SIG"]
    (pre, ax, _), _ = _maps(highlight=["SIG"])
    (tracks,) = ax.collections
    alphas = [round(c[3], 2) for c in tracks.get_colors()]
    widths = list(tracks.get_linewidths())
    assert alphas.count(1.0) == len(picked) and alphas[-1] == 1.0     # picked: drawn last
    assert set(alphas) == {1.0, PREVIEW_DIM} and max(widths) > min(widths)


def test_preview_scene_carries_each_tracks_layer():
    from capscan.report.scene3d import build_scene
    (pre, _, _), (scanned, _, _) = _maps(highlight=["SIG"])
    sc = build_scene(pre)
    assert sc.preview and sc.hot_layers == [B_CU]
    assert len(sc.cold_layers) == len(sc.cold_tracks) and F_CU in sc.cold_layers
    assert not build_scene(scanned).preview

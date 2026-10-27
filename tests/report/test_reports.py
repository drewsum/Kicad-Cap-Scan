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

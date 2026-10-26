"""HTML and PDF reports of a scan."""

import os

import pytest

from boards import B_CU, F_CU, board, ic, track, two_pad_part, via
from capscan.analysis.scan import run_scan
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

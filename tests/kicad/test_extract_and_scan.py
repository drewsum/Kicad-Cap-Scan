"""The board read from each KiCad version scans the same."""

import pytest

pytest.importorskip("kipy")

from kicad_fake import VERSIONS, FakeKiCad  # noqa: E402
from capscan.analysis.scan import run_scan  # noqa: E402
from capscan.kicad.extract import extract_board  # noqa: E402


@pytest.mark.parametrize("version", VERSIONS)
def test_scan_of_each_version(version):
    snap = extract_board(FakeKiCad(version).board())
    r = run_scan(snap)
    # The fake board's GND zone fills In1.Cu; +3V3 is a supply routed as a track.
    assert "GND" in r.plane_nets and "+3V3" not in r.signal_nets
    assert r.board_name == "test.kicad_pcb"

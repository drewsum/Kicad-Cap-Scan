"""Markers drawn on User.Comments through kipy's Board, against a recording stand-in."""

import json

import pytest

pytest.importorskip("kipy")

from boards import B_CU, board, track  # noqa: E402
from capscan.analysis.scan import run_scan  # noqa: E402
from capscan.kicad import markers  # noqa: E402


class FakeBoard:
    """Just what markers.py calls of kipy's Board."""

    def __init__(self, project):
        self.name = "test.kicad_pcb"
        self.document = type("D", (), {"project": type("P", (), {"path": str(project)})()})()
        self.items, self.commits, self.removed = [], [], []

    def begin_commit(self):
        return object()

    def push_commit(self, commit, message=""):
        self.commits.append(message)

    def drop_commit(self, commit):
        self.commits.append("dropped")

    def create_items(self, items):
        out = []
        for it in items:
            it._proto.id.value = f"id-{len(self.items) + len(out)}"
            out.append(it)
        self.items.extend(out)
        return out

    def get_text(self):
        from kipy.board_types import BoardText
        return [i for i in self.items if isinstance(i, BoardText)]

    def get_shapes(self):
        from kipy.board_types import BoardText
        return [i for i in self.items if not isinstance(i, BoardText)]

    def remove_items(self, items):
        self.removed.extend(items)
        gone = {i.id.value for i in items}
        self.items = [i for i in self.items if i.id.value not in gone]


def _result():
    tracks = [track(f"D{i}", B_CU, (12.0, y), (28.0, y)) for i, y in enumerate((5.0, 6.0, 7.0))]
    tracks.append(track("SIG", B_CU, (2.0, 12.0), (12.0, 12.0)))       # a void
    return run_scan(board(tracks=tracks))


def test_markers_go_on_user_comments_in_one_commit(tmp_path):
    from kipy.proto.board.board_types_pb2 import BoardLayer
    r = _result()
    b = FakeBoard(tmp_path)
    n = markers.place_markers(b, r)
    assert n == len(b.items) and b.commits == ["CapScan stitching capacitor markers"]
    assert all(i.layer == BoardLayer.BL_Cmts_User for i in b.items)
    texts = [t.value for t in b.get_text()]
    assert sum(t.startswith("[CapScan] CS") for t in texts) == len(r.caps) == 2
    assert any(t.endswith(" void") for t in texts)
    recorded = json.loads((tmp_path / "test.capscan-markers.json").read_text())
    assert len(recorded["ids"]) == n


def test_clearing_removes_only_capscan_items(tmp_path):
    from kipy.board_types import BoardText
    r = _result()
    b = FakeBoard(tmp_path)
    mine = BoardText()
    mine.value = "Rev A"
    b.create_items([mine])
    markers.place_markers(b, r)
    assert markers.clear_markers(b) > 0
    assert [t.value for t in b.get_text()] == ["Rev A"] and b.get_shapes() == []
    assert not (tmp_path / "test.capscan-markers.json").exists()


def test_placing_again_replaces_the_old_markers(tmp_path):
    r = _result()
    b = FakeBoard(tmp_path)
    first = markers.place_markers(b, r)
    markers.place_markers(b, r)
    assert len(b.items) == first

"""Adding the scan's caps and return vias to the board (#6), against a stand-in for kipy's
Board that answers the calls the installer makes the way KiCad does."""

import json
import math
from types import SimpleNamespace

import pytest

pytest.importorskip("kipy")

from boards import B_CU, F_CU, IN1, IN2, H, W, board, rect, track, via, zone  # noqa: E402
from boards import scan as run_scan  # noqa: E402
from capscan.analysis import install as plan  # noqa: E402
from capscan.analysis.install import (  # noqa: E402
    BLIND, BURIED, MICRO, THROUGH, BoardRules, Keepout, ViaSize,
)
from capscan.kicad import design_rules, drc, install, sexpr  # noqa: E402

FOOTPRINT = """(footprint "C_{name}"
  (version 20260206) (generator "kicad-footprint-generator")
  (layer "F.Cu")
  (property "Reference" "REF**" (at 0 -1.16 0) (layer "F.SilkS"))
  (property "Value" "C" (at 0 1.16 0) (layer "F.Fab"))
  (attr smd)
  (fp_rect (start -0.91 -0.46) (end 0.91 0.46) (layer "F.CrtYd"))
  (pad "1" smd roundrect (at -{x} 0) (size {w} 0.62) (layers "F.Cu" "F.Mask" "F.Paste"))
  (pad "2" smd roundrect (at {x} 0) (size {w} 0.62) (layers "F.Cu" "F.Mask" "F.Paste"))
)"""


@pytest.fixture
def library(tmp_path, monkeypatch):
    pretty = tmp_path / "footprints" / "Capacitor_SMD.pretty"
    pretty.mkdir(parents=True)
    for name in plan.LIBRARY_FOOTPRINTS.values():
        (pretty / f"{name}.kicad_mod").write_text(
            FOOTPRINT.format(name=name[2:], x=0.48, w=0.56), encoding="utf-8")
    monkeypatch.setattr(install, "default_library_dir", lambda cli: tmp_path / "footprints")
    return pretty


class FakeBoard:
    """What the installer calls of kipy's Board, recording what it is asked to do."""

    def __init__(self, nets):
        from kipy.proto.common.types import DocumentSpecifier
        self.document = DocumentSpecifier()
        self.nets = nets
        self.items, self.removed, self.commits, self.selected = {}, [], [], []
        self.text = ""

    # --- items
    def _id(self):
        return f"new-{len(self.items) + len(self.removed) + 1}"

    def create_items(self, items):
        from kipy.board_types import FootprintInstance
        for it in items:
            if not isinstance(it, FootprintInstance):
                it.proto.id.value = self._id()
            self.items[it.proto.id.value] = it
        return list(items)

    def update_items(self, fp):
        self.items[fp.id.value] = fp
        return [fp]

    def flip_items(self, fp, direction=None):
        from kipy.proto.board import board_types_pb2 as bt
        cx = fp.position.x
        for p in fp.definition.pads:
            p.position.x = 2 * cx - p.position.x
        fp.proto.layer = bt.BoardLayer.BL_B_Cu
        self.items[fp.id.value] = fp
        return [fp]

    def remove_items(self, items):
        for it in items:
            self.items.pop(it.id.value, None)
            self.removed.append(it.id.value)

    def get_nets(self):
        from kipy.board_types import Net
        return [Net(name=n) for n in self.nets]

    def get_as_string(self):
        return self.text

    # --- commits, selection
    def begin_commit(self):
        return object()

    def push_commit(self, commit, message=""):
        self.commits.append(message)

    def drop_commit(self, commit):
        self.commits.append("dropped")

    def clear_selection(self):
        self.selected = []

    def add_to_selection(self, items):
        self.selected = list(items)

    # --- views
    def vias(self):
        from kipy.board_types import Via
        return [i for i in self.items.values() if isinstance(i, Via)]

    def tracks(self):
        from kipy.board_types import Track
        return [i for i in self.items.values() if isinstance(i, Track)]


class FakeKiCad:
    def __init__(self, version=(10, 0, 6)):
        self.version, self.actions = version, []

    def get_version(self):
        return SimpleNamespace(major=self.version[0], minor=self.version[1],
                               patch=self.version[2])

    def run_action(self, name):
        from kipy.proto.common.commands.editor_commands_pb2 import RunActionStatus
        self.actions.append(name)
        return SimpleNamespace(status=RunActionStatus.RAS_OK)


@pytest.fixture
def drc_runs(monkeypatch):
    """Each DRC run takes the next report given (an empty one when none are left)."""
    reports = []
    monkeypatch.setattr(install, "find_cli", lambda kicad=None: "kicad-cli")
    monkeypatch.setattr(install, "run_drc",
                        lambda *a, **k: reports.pop(0) if reports else drc.DrcReport())
    monkeypatch.setattr(install.Installer, "refill", lambda self: None)
    return reports


def _split_board():
    """A B.Cu signal across the In2.Cu split: one cap, +3V3 to +1V8, on the bottom."""
    snap = board(tracks=[track("D0", B_CU, (12.0, 5.0), (28.0, 5.0))])
    return snap, run_scan(snap)


def _installer(snap, result, version=(10, 0, 6)):
    nets = sorted({n.name for n in snap.nets})
    b = FakeBoard(nets)
    return install.Installer(FakeKiCad(version), b, snap, result, rules=BoardRules()), b


def test_add_all_places_the_cap_with_a_via_to_each_plane(library, drc_runs):
    snap, r = _split_board()
    (cap,) = r.caps
    assert cap.side == "bottom"
    inst, b = _installer(snap, r)
    opts = inst.defaults()
    assert opts.footprints["0402"].lib_id == "Capacitor_SMD:C_0402_1005Metric"
    assert (opts.prefix, opts.start) == ("C", 1)
    report = inst.add_all(opts)
    assert not report.skipped and [a.references for a in report.added] == [["C1"]]
    assert b.commits == ["CapScan: stitching capacitors and return vias"]   # one undo
    fp = next(i for i in b.items.values() if hasattr(i, "definition"))
    assert fp.proto.reference_field.text.text.text == "C1"
    assert fp.proto.value_field.text.text.text == "100nF"
    assert fp.proto.definition.id.entry_name == "C_0402_1005Metric"
    assert not fp.proto.attributes.not_in_schematic      # an ordinary part
    from kipy.proto.board.board_types_pb2 import BoardLayer
    assert fp.layer == BoardLayer.BL_B_Cu                       # flipped to the bottom
    nets = {p.net.name: p.position.x / 1e6 for p in fp.definition.pads}
    assert nets["+3V3"] < 20.0 < nets["+1V8"]                   # each pad over its plane
    vias, tracks = b.vias(), b.tracks()
    assert sorted(v.net.name for v in vias) == ["+1V8", "+3V3"]
    for v in vias:
        x = v.position.x / 1e6
        assert (x < 19.75) if v.net.name == "+3V3" else (x > 20.25)
        assert v.drill_diameter > 0 and v.padstack.drill.end_layer == BoardLayer.BL_B_Cu
    assert all(t.layer == BoardLayer.BL_B_Cu for t in tracks) and len(tracks) == 2
    for v in vias:                       # clear of the signal on B.Cu by the clearance
        assert abs(v.position.y / 1e6 - 5.0) - 0.3 - 0.075 >= 0.2 - 1e-6


def test_parts_that_fail_drc_come_off_again(library, drc_runs):
    snap, r = _split_board()
    inst, b = _installer(snap, r)
    opts = inst.defaults()
    bad = drc.Violation("clearance", "error", "too close", ("new-1", "old"))
    drc_runs.extend([drc.DrcReport(), drc.DrcReport([bad])])     # before, after
    report = inst.add_all(opts)
    assert not report.added and report.skipped[0].reason == "failed DRC"
    assert report.skipped[0].violations == [bad]
    assert not b.items and b.commits[-1] == "CapScan: remove parts that failed DRC"


def test_violations_already_there_do_not_count(library, drc_runs):
    snap, r = _split_board()
    inst, b = _installer(snap, r)
    old = drc.Violation("clearance", "error", "old", ("a", "b"))
    drc_runs.extend([drc.DrcReport([old]), drc.DrcReport([old])])
    assert len(inst.add_all(inst.defaults()).added) == 1


def test_no_flip_before_kicad_10_0_6(library, drc_runs):
    snap, r = _split_board()
    inst, b = _installer(snap, r, version=(10, 0, 1))
    report = inst.add_all(inst.defaults())
    assert "flip" in report.skipped[0].reason and not b.items
    assert b.commits == ["dropped"]


def test_first_reference_is_the_users(library, drc_runs):
    snap, r = _split_board()
    inst, b = _installer(snap, r)
    inst.taken.add("C100")
    opts = inst.defaults()
    opts.prefix, opts.start = "C", 100
    assert inst.add_all(opts).added[0].references == ["C101"]   # C100 is on the board


def test_step_through_shows_each_part_in_kicad(library, drc_runs):
    snap, r = _split_board()
    inst, b = _installer(snap, r)
    inst.begin_steps()
    out = inst.add_one("CS1", inst.defaults())
    assert isinstance(out, install.Added) and b.commits == ["CapScan: CS1"]
    assert b.selected and inst.kicad.actions[0] == install.VIEW_ACTIONS[0]
    inst.remove([out], "CapScan: take CS1 off")
    assert not b.items


def test_step_through_runs_drc_once_before_and_once_after(library, drc_runs):
    snap, r = _split_board()
    inst, b = _installer(snap, r)
    bad = drc.Violation("clearance", "error", "too close", ("new-1", "old"))
    drc_runs.extend([drc.DrcReport(), drc.DrcReport([bad])])     # before, after
    inst.begin_steps()
    opts = inst.defaults()
    out = inst.add_one("CS1", opts)
    assert isinstance(out, install.Added) and len(drc_runs) == 1    # no DRC for the part
    report = inst.finish_steps(opts, [out])
    assert not drc_runs and not report.added and report.skipped[0].reason == "failed DRC"
    assert not b.items and b.commits[-1] == "CapScan: remove parts that failed DRC"


def test_return_via_goes_beside_the_signal_via(library, drc_runs):
    zones = [zone("GND", IN1, rect(0.2, 0.2, W - 0.2, H - 0.2)),
             zone("GND", IN2, rect(0.2, 0.2, W - 0.2, H - 0.2), ident="z2")]
    snap = board(tracks=[track("S", F_CU, (5.0, 25.0), (10.0, 25.0)),
                         track("S", B_CU, (10.0, 25.0), (15.0, 25.0))],
                 vias=[via("S", (10.0, 25.0), "sv")], zones=zones)
    r = run_scan(snap)
    (sv,) = r.stitch_vias
    inst, b = _installer(snap, r)
    report = inst.add_all(inst.defaults())
    (v,) = b.vias()
    assert report.added[0].ident == sv.id and v.net.name == "GND"
    at = (v.position.x / 1e6, v.position.y / 1e6)
    assert math.dist(at, (10.0, 25.0)) >= 0.3 + 0.3 + 0.25 - 1e-6     # hole to hole


# ---------------------------------------------------------------------------- the choices

def test_via_types_follow_the_board_and_the_rules():
    stack = [F_CU, IN1, IN2, B_CU]
    snap = board()
    rules = BoardRules()
    assert plan.allowed_via_types(snap, rules) == [THROUGH]
    snap.vias.append(via("GND", (1.0, 1.0), layers=(F_CU, IN1)))
    snap.vias[-1].via_type = "micro"
    snap.vias.append(via("GND", (2.0, 1.0), layers=(F_CU, IN2)))
    snap.vias[-1].via_type = "blind_buried"                         # KiCad 9's one type
    assert plan.allowed_via_types(snap, rules) == [THROUGH, MICRO, BLIND, BURIED]
    rules.disallowed = {MICRO}
    allowed = plan.allowed_via_types(snap, rules)
    assert allowed == [THROUGH, BLIND, BURIED]
    assert plan.via_span(stack, F_CU, IN1, allowed) == (BLIND, F_CU, IN1)
    assert plan.via_span(stack, F_CU, IN1, [THROUGH, MICRO]) == (MICRO, F_CU, IN1)
    assert plan.via_span(stack, IN1, IN2, allowed) == (BURIED, IN1, IN2)
    assert plan.via_span(stack, B_CU, IN1, [THROUGH]) == (THROUGH, F_CU, B_CU)
    assert plan.via_span(stack, B_CU, IN2, [THROUGH, MICRO]) == (MICRO, IN2, B_CU)


def test_references_and_via_size():
    assert plan.first_free_number(["C1", "C12", "R40", "CS3"], "C") == 13
    assert plan.next_references({"C5", "C6"}, "C", 4, 3) == ["C4", "C7", "C8"]
    assert plan.split_reference("CS10") == ("CS", 10)
    assert plan.split_reference("10") is None
    rules = BoardRules(min_via_diameter_mm=0.45, min_via_drill_mm=0.2,
                       via_sizes=[ViaSize(0.6, 0.3)])
    snap = board(vias=[via("GND", (1, 1)), via("GND", (2, 2))])
    assert plan.default_via_size(snap, rules, ["GND"]) == ViaSize(0.6, 0.3)
    small = board(vias=[via("GND", (1, 1))])
    small.vias[0].diameter_mm = 0.4                               # under the minimum
    assert plan.default_via_size(small, rules, ["GND"]) == ViaSize(0.6, 0.3)


def test_keepout_keeps_clear_of_other_nets_on_inner_layers():
    snap = board(tracks=[track("SIG", IN2, (0.0, 10.0), (40.0, 10.0), w=0.2)])
    k = Keepout(snap, BoardRules(clearance_mm=0.2), __import__("shapely").box(0, 0, W, H))
    size = ViaSize(0.6, 0.3)
    stack = [F_CU, IN1, IN2, B_CU]
    assert not k.via_fits((5.0, 10.45), size, "GND", stack)      # 0.05 from the track
    assert k.via_fits((5.0, 10.45), size, "GND", [F_CU, IN1])   # a blind via misses it
    assert k.via_fits((5.0, 10.61), size, "GND", stack)
    assert k.via_fits((5.0, 10.0), size, "SIG", stack)           # its own net
    assert not k.via_fits((0.5, 5.0), size, "GND", stack)        # the board edge


def test_project_rules_and_custom_disallows(tmp_path):
    pro = {"board": {"design_settings": {
        "rules": {"min_clearance": 0.1, "min_via_diameter": 0.4,
                  "min_through_hole_diameter": 0.2, "min_hole_to_hole": 0.3,
                  "allow_microvias": False},
        "via_dimensions": [{"diameter": 0.0, "drill": 0.0}, {"diameter": 0.5, "drill": 0.25}]}},
        "net_settings": {"classes": [
            {"name": "Default", "clearance": 0.15, "track_width": 0.2, "via_diameter": 0.6,
             "via_drill": 0.3},
            {"name": "HV", "clearance": 0.5}]}}
    (tmp_path / "b.kicad_pro").write_text(json.dumps(pro))
    (tmp_path / "b.kicad_dru").write_text(
        '(version 1)\n# no buried vias\n(rule "nb" (constraint disallow buried_via))\n'
        '(rule "area" (constraint disallow blind_via) (condition "A.insideArea(\'X\')"))\n')
    rules = design_rules.read_rules(str(tmp_path), "b.kicad_pcb")
    assert rules.clearance("HV") == 0.5 and rules.clearance("Default") == 0.15
    assert rules.hole_to_hole_mm == 0.3 and rules.min_via_diameter_mm == 0.4
    assert rules.via_sizes == [ViaSize(0.5, 0.25), ViaSize(0.6, 0.3)]
    assert rules.disallowed == {MICRO, BURIED}                   # the area rule is DRC's


def test_drc_report_compares_by_type_and_items():
    data = {"violations": [
        {"type": "clearance", "severity": "error", "description": "x 0.1",
         "items": [{"uuid": "b", "pos": {"x": 1, "y": 2}}, {"uuid": "a"}]}],
        "unconnected_items": [{"type": "unconnected_items", "severity": "error",
                               "description": "", "items": [{"uuid": "c"}]}]}
    before = drc.parse_report(data)
    assert [v.items for v in before.violations] == [("a", "b"), ("c",)]
    data["violations"][0]["description"] = "x 0.12"          # measured again: the same one
    data["violations"].append({"type": "clearance", "severity": "error", "description": "",
                               "items": [{"uuid": "new"}]})
    (new,) = drc.parse_report(data).new_since(before)
    assert new.items == ("new",)


def test_library_footprint_is_built_moved_and_turned():
    from capscan.kicad import footprint_proto as fpp
    node = sexpr.parse(FOOTPRINT.format(name="0402_1005Metric", x=0.48, w=0.56))
    src = fpp.from_library(node, "Capacitor_SMD")
    fp = fpp.placed(src, position=(10.0, 20.5), angle_deg=90, reference="C7", value="10nF",
                    nets={"1": "GND"})
    assert (fp.position.x_nm, fp.position.y_nm) == (10_000_000, 20_500_000)
    assert fp.orientation.value_degrees == 90
    assert fp.reference_field.text.text.text == "C7"
    pads = fpp.pad_positions(fp)
    assert pads["1"] == pytest.approx((10.0, 20.98))             # turned up the screen
    assert pads["2"] == pytest.approx((10.0, 20.02))
    from kipy.proto.board import board_types_pb2 as bt
    found = {}
    for item in fp.definition.items:
        if item.TypeName() == "kiapi.board.types.Pad":
            pad = bt.Pad()
            item.Unpack(pad)
            found[pad.number] = (pad.net.name, pad.pad_stack.angle.value_degrees, pad.id.value)
    assert found["1"][:2] == ("GND", 90) and found["2"][0] == ""
    assert found["1"][2] != fpp.placed(src, position=(0, 0), angle_deg=0, reference="C",
                                       value="").definition.items[0]   # fresh ids
    turned = fpp.placed(src, position=(0, 0), angle_deg=45, reference="C", value="")
    shapes = []
    for item in turned.definition.items:
        if item.TypeName() == "kiapi.board.types.BoardGraphicShape":
            g = bt.BoardGraphicShape()
            item.Unpack(g)
            shapes.append(g.shape.WhichOneof("geometry") if g.shape.DESCRIPTOR.oneofs
                          else ("polygon" if g.shape.HasField("polygon") else "rectangle"))
    assert shapes == ["polygon"]                                   # a turned rectangle


def test_a_cap_footprint_on_the_board_is_copied(library, drc_runs):
    """A board 0402 standing at 90 degrees, copied into the cap lying across the split."""
    from boards import two_pad_part
    from capscan.kicad import footprint_proto as fpp
    snap, r = _split_board()
    part, pads = two_pad_part("C3", "1uF", "GND", "+3V3", (5.0, 5.0))
    part.footprint = "Capacitor_SMD:C_0402_1005Metric"
    snap.footprints.append(part)
    snap.pads.extend(pads)
    snap.invalidate_index()
    lib = install.file_source(library / "C_0402_1005Metric.kicad_mod", "0402")
    existing = fpp.placed(lib.proto, position=(5.0, 5.0), angle_deg=90, reference="C3",
                          value="1uF", nets={"1": "GND", "2": "+3V3"})
    inst, b = _installer(snap, r)
    from kipy.board_types import FootprintInstance
    b.get_footprints = lambda: [FootprintInstance(existing)]
    opts = inst.defaults()
    assert opts.footprints["0402"].origin == "board" and opts.start == 4
    report = inst.add_all(opts)
    assert report.added[0].references == ["C4"]
    fp = next(i for i in b.items.values() if hasattr(i, "definition"))
    assert fp.proto.value_field.text.text.text == "100nF"
    ys = [p.position.y / 1e6 for p in fp.definition.pads]
    xs = sorted(p.position.x / 1e6 for p in fp.definition.pads)
    assert ys == pytest.approx([5.75, 5.75]) and xs == pytest.approx([19.52, 20.48])
    assert not fp.proto.symbol_path.path

"""The scan on boards with a known answer (tests/helpers/boards.py)."""

import math

import pytest

from boards import B_CU, F_CU, IN1, IN2, board, ic, rect, track, two_pad_part, via, zone
from capscan.analysis.references import build_references, diff_pairs, looks_like_power
from capscan.analysis.results import (
    EXISTING, EXISTING_VIA, NEW_CAP, NEW_VIA, SPLIT, UNFIXABLE, VIA_CAP, VIA_OK, VIA_STITCH,
    VOID, ScanResult,
)
from capscan.analysis.rules import ScanSettings
from boards import scan as run_scan
from capscan.analysis.values import net_voltage, voltage_rating


def crossing_bus(ys=(5.0, 6.0, 7.0), layer=B_CU, prefix="D"):
    return [track(f"{prefix}{i}", layer, (12.0, y), (28.0, y)) for i, y in enumerate(ys)]


# ------------------------------------------------------------------ planes and signals

def test_planes_signals_and_references():
    snap = board(tracks=crossing_bus())
    refs = build_references(snap, ScanSettings(scan_all_nets=True))
    assert refs.plane_nets == ["+1V8", "+3V3", "GND"]
    assert refs.signal_nets == ["D0", "D1", "D2"]
    assert set(refs.planes) == {IN1, IN2}
    assert [r for r, _ in refs.refs(F_CU)] == [IN1]
    assert [r for r, _ in refs.refs(B_CU)] == [IN2]
    (_, h), = refs.refs(B_CU)
    assert h == pytest.approx(0.2 + 0.035, abs=1e-6)


@pytest.mark.parametrize("name, power", [
    ("GND", True), ("/power/+3V3", True), ("VCC_IO", True), ("1V8", True), ("AGND", True),
    ("VBUS", True), ("+5V", True), ("SPI_CLK", False), ("USB_D+", False), ("DDR_DQ0", False),
    ("LED1", False), ("Net-(U1-Pad3)", False),
])
def test_power_names(name, power):
    assert looks_like_power(name) == power


def test_diff_pairs():
    pairs = diff_pairs(["USB_D+", "USB_D-", "TX_P", "TX_N", "CLK", "RXP"])
    assert pairs["USB_D+"] == pairs["USB_D-"]
    assert pairs["TX_P"] == pairs["TX_N"]
    assert "CLK" not in pairs and "RXP" not in pairs


def _supply_board(**kw):
    return board(tracks=[track("Net-(U2-OUT)", F_CU, (5.0, 5.0), (15.0, 5.0)),
                         track("SPI_CLK", F_CU, (5.0, 12.0), (15.0, 12.0))], **kw)


def test_net_poured_on_several_layers_is_a_supply():
    zones = [zone("GND", IN1, rect(0.2, 0.2, 39.8, 29.8)),
             zone("Net-(U2-OUT)", F_CU, rect(4, 4, 6, 6), ident="o1"),
             zone("Net-(U2-OUT)", B_CU, rect(4, 4, 6, 6), ident="o2")]
    refs = build_references(_supply_board(zones=zones), ScanSettings(scan_all_nets=True))
    assert refs.copper_power == ["Net-(U2-OUT)"] and "Net-(U2-OUT)" in refs.plane_nets
    assert refs.signal_nets == ["SPI_CLK"]
    off = ScanSettings(scan_all_nets=True, power_min_zone_layers=0)
    assert build_references(_supply_board(zones=zones), off).copper_power == []


def test_net_with_a_via_array_is_a_supply():
    array = [via("Net-(U2-OUT)", (6.0 + 0.5 * i, 5.5), f"a{i}") for i in range(4)]
    refs = build_references(_supply_board(vias=array), ScanSettings(scan_all_nets=True))
    assert refs.copper_power == ["Net-(U2-OUT)"] and refs.signal_nets == ["SPI_CLK"]
    # The same vias spread out are a signal changing layer here and there.
    spread = [via("Net-(U2-OUT)", (6.0 + 3.0 * i, 5.5), f"s{i}") for i in range(4)]
    refs = build_references(_supply_board(vias=spread), ScanSettings(scan_all_nets=True))
    assert refs.copper_power == [] and "Net-(U2-OUT)" in refs.signal_nets
    three = array[:3]
    refs = build_references(_supply_board(vias=three), ScanSettings(scan_all_nets=True))
    assert refs.copper_power == []


# ------------------------------------------------------------------ crossings

def test_microstrip_over_split_needs_caps():
    r = run_scan(board(tracks=crossing_bus()))
    splits = r.splits()
    assert len(splits) == 3
    for c in splits:
        assert {c.net_a, c.net_b} == {"+3V3", "+1V8"}
        assert c.layer == B_CU and c.ref_layer == IN2
        assert c.position[0] == pytest.approx(20.0, abs=0.1)
        assert c.gap_mm == pytest.approx(0.5, abs=0.1)
        assert c.severity == 1.0
        assert c.status == NEW_CAP
    # Three signals, two per capacitor.
    assert len(r.caps) == 2
    assert sorted(len(c.signals) for c in r.caps) == [1, 2]
    for cap in r.caps:
        assert (cap.net_a, cap.net_b) == ("+1V8", "+3V3")
        assert cap.side == "bottom"          # In2 is nearer the bottom
        for ident in cap.serves:
            c = r.item(ident)
            assert math.dist(cap.position, c.position) <= r.settings.max_cap_distance_mm
        assert cap.angle_deg == 0.0          # pads across the split, along the traces
        assert not cap.crowded


def test_caps_do_not_sit_on_the_traces():
    r = run_scan(board(tracks=crossing_bus()))
    for cap in r.caps:
        for y in (5.0, 6.0, 7.0):
            assert abs(cap.position[1] - y) > 0.25 + 0.075 or abs(cap.position[0] - 20) > 0.75


def test_far_apart_signals_get_a_cap_each():
    r = run_scan(board(tracks=crossing_bus(ys=(3.0, 9.0, 15.0))))
    assert len(r.caps) == 3


def test_differential_pair_is_one_return_path():
    tracks = [track("USB_D+", B_CU, (12, 20.0), (28, 20.0)),
              track("USB_D-", B_CU, (12, 20.3), (28, 20.3))]
    r = run_scan(board(tracks=tracks))
    assert len(r.splits()) == 2
    (cap,) = r.caps
    assert cap.signals == ["USB_D+", "USB_D-"]


def test_traces_over_solid_ground_are_fine():
    r = run_scan(board(tracks=crossing_bus(layer=F_CU)))
    assert r.crossings == [] and r.caps == []


def test_trace_over_a_hole_is_a_void():
    r = run_scan(board(tracks=[track("SIG", B_CU, (2.0, 12.0), (12.0, 12.0))]))
    (v,) = r.voids()
    assert (v.net_a, v.net_b) == ("+3V3", "+3V3")
    assert v.gap_mm == pytest.approx(4.0, abs=0.1)
    assert v.position == pytest.approx((7.0, 12.0), abs=0.1)
    assert v.status == UNFIXABLE
    assert r.caps == []


def test_small_holes_are_ignored():
    zones = [zone("GND", IN1, rect(0.2, 0.2, 39.8, 29.8)),
             zone("+3V3", IN2, rect(0.2, 0.2, 39.8, 29.8), holes=[rect(9.7, 11.7, 10.3, 12.3)])]
    r = run_scan(board(tracks=[track("SIG", B_CU, (2, 12), (30, 12))], zones=zones))
    assert r.crossings == []


def test_trace_running_off_the_plane_is_a_void():
    zones = [zone("GND", IN1, rect(0.2, 0.2, 39.8, 29.8)),
             zone("+3V3", IN2, rect(0.2, 0.2, 25, 29.8))]
    r = run_scan(board(tracks=[track("SIG", B_CU, (10, 12), (35, 12))], zones=zones))
    (v,) = r.voids()
    assert (v.net_a, v.net_b) == ("+3V3", "")


def test_stripline_with_one_solid_plane_is_half_as_severe():
    # 6 layers: In2 signals between In1 (GND, solid) and In3 (split).
    from capscan.model.layers import inner_cu
    in3, in4 = inner_cu(3), inner_cu(4)
    cu = (F_CU, IN1, IN2, in3, in4, B_CU)
    zones = [zone("GND", IN1, rect(0.2, 0.2, 39.8, 29.8)),
             zone("+3V3", in3, rect(0.2, 0.2, 19.75, 29.8)),
             zone("+1V8", in3, rect(20.25, 0.2, 39.8, 29.8)),
             zone("GND", in4, rect(0.2, 0.2, 39.8, 29.8), ident="z2")]
    r = run_scan(board(tracks=[track("SIG", IN2, (12, 5), (28, 5))], zones=zones, cu=cu))
    (c,) = r.splits()
    assert c.ref_layer == in3 and c.severity == 0.5
    assert len(r.caps) == 1


def test_stripline_void_with_a_solid_other_plane_is_not_a_reroute():
    from capscan.analysis.results import OTHER_PLANE
    from capscan.model.layers import inner_cu
    in3, in4 = inner_cu(3), inner_cu(4)
    cu = (F_CU, IN1, IN2, in3, in4, B_CU)
    zones = [zone("GND", IN1, rect(0.2, 0.2, 39.8, 29.8)),
             zone("+3V3", in3, rect(0.2, 0.2, 39.8, 29.8), holes=[rect(15, 2, 25, 10)]),
             zone("GND", in4, rect(0.2, 0.2, 39.8, 29.8), ident="z2")]
    r = run_scan(board(tracks=[track("SIG", IN2, (5, 5), (35, 5))], zones=zones, cu=cu))
    (c,) = r.crossings
    assert c.kind == VOID and c.status == OTHER_PLANE
    assert r.voids() == [] and r.caps == []


# ------------------------------------------------------------------ via transitions

def _via_board(extra=(), zones=None):
    tracks = [track("SPI_CLK", F_CU, (5.0, 25.0), (10.0, 25.0)),
              track("SPI_CLK", B_CU, (10.0, 25.0), (15.0, 25.0))]
    return board(tracks=tracks, vias=[via("SPI_CLK", (10.0, 25.0), "sigvia"), *extra],
                 zones=zones)


def test_via_onto_another_net_needs_a_cap():
    r = run_scan(_via_board())
    (t,) = r.via_changes()
    assert t.kind == VIA_CAP and {t.net_a, t.net_b} == {"GND", "+3V3"}
    assert (t.from_layer, t.to_layer) == (F_CU, B_CU)
    (cap,) = r.caps
    assert t.status == NEW_CAP and t.covered_by == cap.id
    assert (cap.net_a, cap.net_b) == ("+3V3", "GND")
    assert math.dist(cap.position, t.position) <= r.settings.via_cap_distance_mm
    assert cap.voltage_v == 16.0


def _gnd_gnd_zones():
    return [zone("GND", IN1, rect(0.2, 0.2, 39.8, 29.8)),
            zone("GND", IN2, rect(0.2, 0.2, 39.8, 29.8), ident="z2")]


def test_via_between_two_ground_planes_needs_a_return_via():
    r = run_scan(_via_board(zones=_gnd_gnd_zones()))
    (t,) = r.via_changes()
    assert t.kind == VIA_STITCH and t.net_a == "GND" and t.status == NEW_VIA
    (sv,) = r.stitch_vias
    assert sv.serves == [t.id] and sv.net == "GND"
    assert math.dist(sv.position, t.position) <= r.settings.stitch_via_distance_mm
    assert r.caps == []


def test_existing_ground_via_is_enough():
    r = run_scan(_via_board(extra=[via("GND", (10.0, 26.0), "gv")], zones=_gnd_gnd_zones()))
    (t,) = r.via_changes()
    assert t.status == EXISTING_VIA and t.covered_by == "gv"
    assert r.stitch_vias == []


def test_via_between_layers_sharing_a_plane_is_fine():
    # F.Cu to In2.Cu with one plane (In1, GND) between them: both return on In1.
    zones = [zone("GND", IN1, rect(0.2, 0.2, 39.8, 29.8))]
    tracks = [track("S", F_CU, (5, 25), (10, 25)), track("S", IN2, (10, 25), (15, 25))]
    r = run_scan(board(tracks=tracks, vias=[via("S", (10, 25))], zones=zones))
    assert [t.kind for t in r.transitions] == [VIA_OK]
    assert r.via_changes() == [] and r.caps == []


# ------------------------------------------------------------------ existing capacitors

def test_existing_stitching_cap_is_credited():
    fp, pads = two_pad_part("C7", "100n", "+3V3", "+1V8", (20.0, 8.0), side="bottom")
    r = run_scan(board(tracks=crossing_bus(), footprints=[fp], pads=pads))
    (e,) = r.existing
    assert e.role == "stitch"
    # C7 takes two signals; the third gets a new cap.
    assert len(e.credited) == 2
    assert sum(c.status == EXISTING for c in r.splits()) == 2
    assert len(r.caps) == 1


def test_decoupling_cap_is_flagged_not_credited():
    fp, pads = two_pad_part("C3", "100n", "+3V3", "+1V8", (20.0, 6.0), side="bottom")
    u, upads = ic("U1", [("+3V3", (21.0, 8.0)), ("GND", (22, 8)), ("+1V8", (23, 8))])
    r = run_scan(board(tracks=crossing_bus(), footprints=[fp, u], pads=pads + upads))
    (e,) = r.existing
    assert e.role == "decoupling" and e.near_ic == "U1.1"
    assert e.credited == [] and len(e.flagged) == 3
    assert all(c.decoupling == ["C3"] and c.status == NEW_CAP for c in r.splits())
    assert len(r.caps) == 2


def test_decoupling_is_measured_from_the_pad_on_the_ic_net():
    # A 1206: its centre is 3.4 mm from U1's +3V3 pin, its +3V3 pad 1.8 mm.
    fp, pads = two_pad_part("C5", "10u", "+3V3", "+1V8", (40.0, 20.0))
    pads[0].position, pads[1].position = (38.4, 20.0), (41.6, 20.0)
    u, upads = ic("U1", [("+3V3", (36.6, 20.0)), ("GND", (36.6, 21)), ("SIG", (36.6, 22))])
    r = run_scan(board(tracks=crossing_bus(), footprints=[fp, u], pads=pads + upads))
    (e,) = r.existing
    assert e.role == "decoupling" and e.near_ic == "U1.1"
    assert r.counts()["existing_decoupling"] == 1


def test_far_cap_is_not_credited():
    fp, pads = two_pad_part("C9", "100n", "+3V3", "+1V8", (20.0, 25.0), side="bottom")
    r = run_scan(board(tracks=crossing_bus(), footprints=[fp], pads=pads))
    assert r.existing[0].credited == [] and len(r.caps) == 2


# ------------------------------------------------------------------ parts and heat

@pytest.mark.parametrize("name, v", [("+3V3", 3.3), ("1V8", 1.8), ("+5V", 5.0), ("GND", 0.0),
                                     ("VBUS", 5.0), ("12V_IN", 12.0), ("+0V9", 0.9),
                                     ("VCC", None), ("/sheet/3.3V", 3.3)])
def test_net_voltage(name, v):
    assert net_voltage(name) == v


def test_voltage_rating():
    s = ScanSettings()
    assert voltage_rating("GND", "+3V3", s) == 16.0
    assert voltage_rating("GND", "12V", s) == 25.0
    assert voltage_rating("GND", "+24V", s) == 50.0
    assert voltage_rating("GND", "VCC", s) == 50.0       # unknown voltage


def test_heat_is_hottest_at_the_bus():
    r = run_scan(board(tracks=crossing_bus() + [track("SIG", B_CU, (2.0, 12.0), (12.0, 12.0))]))
    (h,) = r.heat_map()
    assert h.layer == IN2 and h.values.max() == pytest.approx(1.0)
    from capscan.analysis.heatmap import sample
    assert sample(h, (20, 6)) > sample(h, (7, 12)) > sample(h, (35, 25))
    assert r.track_heat
    assert {(p.kind, p.layer) for p in r.heat} == {(SPLIT, IN2), (VOID, IN2)}


def test_heat_of_each_kind_shows_on_its_own():
    r = run_scan(board(tracks=crossing_bus() + [track("SIG", B_CU, (2.0, 12.0), (12.0, 12.0))]))
    from capscan.analysis.heatmap import sample
    (void,) = r.heat_map([VOID])                # scaled by its own peak: the void alone
    assert void.values.max() == pytest.approx(1.0)
    assert sample(void, (7, 12)) > 0.9 and sample(void, (20, 6)) < 0.05
    (both,) = r.heat_map()
    assert sample(both, (7, 12)) < sample(void, (7, 12))
    assert r.heat_map([]) == [] and r.heat_map([VIA_CAP]) == []
    assert r.heat_counts() == {SPLIT: 3, VOID: 1, VIA_CAP: 0, VIA_STITCH: 0}


def test_missing_return_vias_add_heat():
    r = run_scan(_via_board(zones=_gnd_gnd_zones()))
    assert {(p.kind, p.layer) for p in r.heat} == {(VIA_STITCH, IN1), (VIA_STITCH, IN2)}
    assert r.heat_counts()[VIA_STITCH] == 1
    from capscan.analysis.heatmap import sample
    (h, _) = r.heat_map([VIA_STITCH])
    assert sample(h, (10.0, 25.0)) == pytest.approx(1.0, abs=0.05)
    # One already there adds none.
    r = run_scan(_via_board(extra=[via("GND", (10.0, 26.0), "gv")], zones=_gnd_gnd_zones()))
    assert r.heat == [] and r.heat_counts()[VIA_STITCH] == 0


def test_heat_saved_before_kinds_were_kept_apart_still_shows():
    r = run_scan(board(tracks=crossing_bus()))
    d = r.to_dict()
    for h in d["heat"]:
        del h["kind"]
    back = ScanResult.from_dict(d)
    assert [h.kind for h in back.heat] == [""]
    assert len(back.heat_map([VOID])) == 1      # shown whatever is ticked


def test_result_round_trips(tmp_path):
    fp, pads = two_pad_part("C3", "100n", "+3V3", "+1V8", (20.0, 9.0), side="bottom")
    r = run_scan(_via_board(extra=()))
    r2 = run_scan(board(tracks=crossing_bus(), footprints=[fp], pads=pads))
    for res in (r, r2):
        path = res.save(tmp_path / "r.capscan.json.gz")
        back = ScanResult.load(path)
        assert back.to_dict() == res.to_dict()


def test_kinds_are_exclusive():
    assert len({SPLIT, VOID}) == 2


# ------------------------------------------------------------------ turning checks off

def _everything_board():
    tracks = crossing_bus() + [track("SIG", B_CU, (2.0, 12.0), (12.0, 12.0)),
                               track("SPI_CLK", F_CU, (5.0, 25.0), (10.0, 25.0)),
                               track("SPI_CLK", B_CU, (10.0, 25.0), (15.0, 25.0))]
    return board(tracks=tracks, vias=[via("SPI_CLK", (10.0, 25.0), "sigvia")])


def test_everything_is_checked_by_default():
    r = run_scan(_everything_board())
    assert len(r.splits()) == 3 and len(r.voids()) == 1 and len(r.via_changes()) == 1
    assert len(r.caps) == 3 and not any("Not checked" in w for w in r.warnings)


@pytest.mark.parametrize("off, caps, splits, voids, vias", [
    ("check_splits", 1, 0, 1, 1),
    ("check_via_caps", 2, 3, 1, 0),
    ("check_voids", 3, 3, 0, 1),
])
def test_a_check_turned_off_adds_nothing(off, caps, splits, voids, vias):
    r = run_scan(_everything_board(), ScanSettings(**{off: False}))
    assert (len(r.caps), len(r.splits()), len(r.voids()), len(r.via_changes())) == \
        (caps, splits, voids, vias)
    assert any(w.startswith("Not checked (turned off)") for w in r.warnings)
    if off == "check_splits":                # no heat left where the bus crosses the split
        from capscan.analysis.heatmap import sample
        (h,) = [h for h in r.heat_map() if h.layer == IN2]
        assert sample(h, (20, 6)) < 0.05


def test_return_via_check_turned_off():
    r = run_scan(_via_board(zones=_gnd_gnd_zones()), ScanSettings(check_via_stitch=False))
    assert r.via_changes() == [] and r.stitch_vias == []
    assert "vias onto another plane of the same net" in r.warnings[-1]


def test_all_checks_off():
    s = ScanSettings(check_splits=False, check_via_caps=False, check_via_stitch=False,
                     check_voids=False)
    r = run_scan(_everything_board(), s)
    assert r.crossings == [] and r.transitions == [] and r.caps == [] and r.heat == []


def test_checks_survive_saving():
    s = ScanSettings(check_via_caps=False)
    assert ScanSettings.from_dict(s.to_dict()).check_via_caps is False


# ------------------------------------------------------------------ picking nets

def test_by_default_only_picked_nets_are_scanned():
    s = ScanSettings()
    assert s.scan_all_nets is False and s.selected_nets == []
    from capscan.analysis.scan import run_scan as plain_run_scan
    r = plain_run_scan(_everything_board(), s)
    assert r.signal_nets == [] and r.crossings == [] and r.caps == []
    assert r.candidate_nets == ["D0", "D1", "D2", "SIG", "SPI_CLK"]
    assert any("No nets were picked" in w for w in r.warnings)


def test_picked_nets_alone_are_scanned():
    r = run_scan(_everything_board(), all_nets=False, nets=["D0", "D1"])
    assert r.signal_nets == ["D0", "D1"]
    assert {c.net for c in r.crossings} == {"D0", "D1"} and len(r.caps) == 1
    assert r.via_changes() == [] and r.voids() == []
    assert any("Only 2 of 5 signal nets were scanned" in w for w in r.warnings)


def test_picking_a_net_the_board_lacks_is_said():
    r = run_scan(_everything_board(), all_nets=False, nets=["SPI_CLK", "GONE", "GND"])
    assert r.signal_nets == ["SPI_CLK"]
    assert any("not signal nets on this board" in w and "GONE" in w and "GND" in w
               for w in r.warnings)


def test_candidates_ignore_the_selection():
    from capscan.analysis.references import signal_net_candidates
    s = ScanSettings(selected_nets=["D0"], ignore_nets=["SPI*"])
    assert signal_net_candidates(_everything_board(), s) == ["D0", "D1", "D2", "SIG"]


def test_clearance_round_a_through_hole_pad_at_a_trace_end_is_not_a_void():
    from capscan.model.board import Pad, Poly
    # A 1.7 mm through-hole pad at (30, 12), with a 1.2 mm-radius hole in both planes.
    hole = [(30 + 1.2 * math.cos(a / 16 * math.pi * 2), 12 + 1.2 * math.sin(a / 16 * math.pi * 2))
            for a in range(16)]
    zones = [zone("GND", IN1, rect(0.2, 0.2, 39.8, 29.8), holes=[hole]),
             zone("GND", IN2, rect(0.2, 0.2, 39.8, 29.8), holes=[hole], ident="z2")]
    pad = Pad(id="J1-1", net="SIG", footprint="J1", number="1", position=(30.0, 12.0),
              pad_type="pth", drill_mm=(1.0, 1.0),
              shapes={B_CU: [Poly(outline=rect(29.15, 11.15, 30.85, 12.85))]})
    r = run_scan(board(tracks=[track("SIG", B_CU, (5, 12), (30, 12))], pads=[pad], zones=zones))
    assert r.crossings == []
    # The same hole in the middle of a trace is a void.
    r = run_scan(board(tracks=[track("SIG", B_CU, (5, 12), (38, 12))], zones=zones))
    (c,) = r.voids()
    assert c.position == pytest.approx((30, 12), abs=0.1)


def test_clearance_round_a_pin_the_trace_runs_through_is_not_a_void():
    from capscan.model.board import Pad, Poly
    hole = [(20 + 1.2 * math.cos(a / 16 * math.pi * 2), 12 + 1.2 * math.sin(a / 16 * math.pi * 2))
            for a in range(16)]
    zones = [zone("GND", IN1, rect(0.2, 0.2, 39.8, 29.8), holes=[hole]),
             zone("GND", IN2, rect(0.2, 0.2, 39.8, 29.8), holes=[hole], ident="z2")]
    pin = Pad(id="J1-1", net="SIG", footprint="J1", number="1", position=(20.0, 12.0),
              pad_type="pth", drill_mm=(1.0, 1.0),
              shapes={B_CU: [Poly(outline=rect(19.15, 11.15, 20.85, 12.85))]})
    tracks = [track("SIG", B_CU, (5, 12), (20, 12)), track("SIG", B_CU, (20, 12), (35, 12))]
    r = run_scan(board(tracks=tracks, pads=[pin], zones=zones))
    assert r.crossings == []

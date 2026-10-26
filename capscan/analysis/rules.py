"""Every rule CapScan applies, in one place, with where each default comes from.

CapScan is meant to need no setup: every net that is not a plane is taken to be a fast
digital signal, and each default leans towards more stitching than strictly needed.
docs/research.md gives the sources; the Advanced panel of the window edits these.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Dict, List

# Standard ceramic capacitor voltage ratings, volts.
VOLTAGE_RATINGS = (6.3, 10.0, 16.0, 25.0, 35.0, 50.0, 100.0)


@dataclass
class ScanSettings:
    # --- what to check ------------------------------------------------------------------
    #: Each kind of return-path break can be left out of the scan. One left out is not
    #: looked for: it adds no capacitor, via, table row or heat, and the summary says it
    #: was not checked.
    check_splits: bool = True           # traces over a split: stitching caps
    check_via_caps: bool = True         # vias onto another net's plane: stitching caps
    check_via_stitch: bool = True       # vias onto another plane of the same net: return vias
    check_voids: bool = True            # traces over no plane: flagged for rerouting

    # --- the signals ------------------------------------------------------------------
    #: Scan every signal net, or only :attr:`selected_nets` (picked by hand in the window).
    scan_all_nets: bool = False
    #: The signal nets to scan when :attr:`scan_all_nets` is off, by exact name.
    selected_nets: List[str] = field(default_factory=list)
    #: Edge rate every signal is assumed to have. 100 ps (knee frequency 0.35 / tr, about
    #: 3.5 GHz) covers DDR, PCIe, USB 3, fast SPI and GPIO edges from modern parts.
    edge_rate_ps: float = 100.0
    #: Nets left out of the scan, as names or shell patterns ("TP*").
    ignore_nets: List[str] = field(default_factory=list)
    #: Nets always taken as planes, as names or shell patterns, on top of those found.
    extra_plane_nets: List[str] = field(default_factory=list)

    # --- what a plane is ------------------------------------------------------------------
    #: A net is a plane net when its zone fills cover this share of the board on a layer
    #: (or :attr:`plane_min_area_mm2`, whichever is smaller) ...
    plane_min_area_frac: float = 0.02
    plane_min_area_mm2: float = 25.0
    #: ... or when it is named like a supply or ground and has any zone fill at all.
    #: A copper layer is a reference layer when plane-net fills cover this share of it.
    reference_layer_min_frac: float = 0.20

    # --- what a crossing is ---------------------------------------------------------------
    #: A gap between two plane nets up to this wide, under a trace, is a split, which a
    #: stitching capacitor bridges. A wider one is a void the return current cannot cross.
    split_gap_max_mm: float = 2.0
    #: Holes in a plane under a trace shorter than this are ignored (via antipads, thermal
    #: relief spokes); the return current flows round them with little detour.
    void_ignore_mm: float = 1.0

    # --- where a capacitor must go --------------------------------------------------------
    #: Furthest a stitching capacitor may be from the split crossing it returns current for.
    #: Riazi (PCD&M): a 0.1 µF cap 100 mil from the crossing works; at 500 mil it does not.
    max_cap_distance_mm: float = 2.5
    #: Furthest from a via whose signal changes onto a reference plane of another net.
    #: "As close to the transition via as possible"; 50 mil is a reachable tight figure.
    via_cap_distance_mm: float = 1.27
    #: Furthest a same-net return via may be from a via changing reference layers (TI's
    #: high-speed guidance says 200 mil for some interfaces; tightened here).
    stitch_via_distance_mm: float = 1.27
    #: Signals one stitching capacitor returns current for. A mounted 0402 has about 1 nH
    #: of loop inductance; sharing it among many switching signals defeats it.
    signals_per_cap: int = 2

    # --- existing capacitors ------------------------------------------------------------------
    #: A capacitor this close to an IC's pin on either of its nets is decoupling that IC.
    #: Return current through it would flow through the IC's supply, so it never counts as a
    #: stitching capacitor; crossings that only have one nearby are flagged.
    decoupling_ic_distance_mm: float = 3.0

    # --- the part ------------------------------------------------------------------------
    #: 0.1 µF X7R in 0402: TI recommends 1 µF or below; the package's low inductance
    #: matters more at these edge rates than the value.
    cap_value: str = "100nF"
    cap_dielectric: str = "X7R"
    cap_package: str = "0402"
    #: Voltage rating: at least this many times the voltage between the two plane nets,
    #: and never below :attr:`min_voltage_rating_v`.
    voltage_derating: float = 2.0
    min_voltage_rating_v: float = 16.0
    #: Rating used when a net's voltage cannot be read from its name.
    unknown_voltage_rating_v: float = 50.0

    # --- placement ----------------------------------------------------------------------
    #: Body of the capacitor (length, width) and the clearance kept round it when looking
    #: for a free spot; an 0402 is 1.0 x 0.5 mm.
    cap_body_mm: tuple = (1.0, 0.5)
    placement_clearance_mm: float = 0.25

    # --- the heat map -----------------------------------------------------------------------
    heat_grid_mm: float = 0.5

    def unchecked(self) -> List[str]:
        """What the scan was told not to look for, in words."""
        names = {"check_splits": "traces over plane splits",
                 "check_via_caps": "vias onto another net's plane",
                 "check_via_stitch": "vias onto another plane of the same net",
                 "check_voids": "traces over voids"}
        return [text for key, text in names.items() if not getattr(self, key)]

    @property
    def knee_ghz(self) -> float:
        return 0.35 / self.edge_rate_ps * 1000.0

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["cap_body_mm"] = list(self.cap_body_mm)
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "ScanSettings":
        names = {f.name for f in fields(cls)}
        kw = {k: v for k, v in (d or {}).items() if k in names}
        if "cap_body_mm" in kw:
            kw["cap_body_mm"] = tuple(kw["cap_body_mm"])
        return cls(**kw)


#: What the window says of each setting, in the Advanced panel and the report.
DESCRIPTIONS = {
    "check_splits": "Check traces over plane splits (caps)",
    "check_via_caps": "Check vias onto another net's plane (caps)",
    "check_via_stitch": "Check vias onto another same-net plane (return vias)",
    "check_voids": "Check traces over voids (reroute)",
    "edge_rate_ps": "Assumed signal edge rate (ps)",
    "max_cap_distance_mm": "Max cap distance from a split crossing (mm)",
    "via_cap_distance_mm": "Max cap distance from a reference-changing via (mm)",
    "stitch_via_distance_mm": "Max return-via distance from a reference-changing via (mm)",
    "signals_per_cap": "Signals per stitching cap",
    "split_gap_max_mm": "Widest plane gap treated as a split (mm)",
    "void_ignore_mm": "Shortest plane hole that counts as a void (mm)",
    "decoupling_ic_distance_mm": "Cap within this of an IC pin is decoupling (mm)",
    "cap_value": "Capacitor value",
    "cap_dielectric": "Dielectric",
    "cap_package": "Package",
    "voltage_derating": "Voltage rating x the voltage across it",
    "min_voltage_rating_v": "Lowest voltage rating (V)",
}

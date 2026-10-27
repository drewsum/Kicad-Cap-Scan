"""Every rule CapScan applies, in one place, with where each default comes from.

CapScan is meant to need no setup: every net that is not a plane is taken to be a fast
digital signal, and each default leans towards more stitching than strictly needed.
docs/research.md gives the sources; the Advanced panel of the window edits these.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Dict, Iterable, List, Tuple

# Standard ceramic capacitor voltage ratings, volts.
VOLTAGE_RATINGS = (6.3, 10.0, 16.0, 25.0, 35.0, 50.0, 100.0)


@dataclass(frozen=True)
class Package:
    name: str
    body_mm: Tuple[float, float]        # length, width
    #: Loop inductance of the part mounted with one via per pad to its plane. A well-mounted
    #: 0603 has "at least 1-2 nH", about 1 nH of it from the vias (LearnEMC); the rest scales
    #: with the body length. Estimates: a field solver gives the real figure for a stackup.
    mounted_nh: float
    #: Largest X7R value commonly stocked at each voltage rating, as (rating V, farads),
    #: lowest rating first (spot-checked on DigiKey: GRM033R71H102KA12, CC0201KRX7R7BB103,
    #: GRM155R71H104KE14, GRM188R72A104KA35).
    x7r_max: Tuple[Tuple[float, float], ...]

    def max_value_f(self, rating_v: float) -> float:
        """The largest X7R value stocked at ``rating_v`` or above, 0 if none."""
        return next((c for v, c in self.x7r_max if v >= rating_v - 1e-9), 0.0)


#: Packages a stitching capacitor is picked from, smallest first. Nobody sets the package:
#: an 0402 is the smallest that most assembly (and a steady hand) places without trouble,
#: and an 0805 already stocks 100 nF at 100 V, so a larger one would only add inductance.
PACKAGES: Dict[str, Package] = {p.name: p for p in (
    Package("0402", (1.0, 0.5), 1.0, ((50.0, 100e-9), (100.0, 1e-9))),
    Package("0603", (1.6, 0.8), 1.2, ((100.0, 100e-9),)),
    Package("0805", (2.0, 1.25), 1.5, ((100.0, 100e-9),)),
)}

#: Values a stitching capacitor is picked from, largest first. The value only matters below
#: the cap's self-resonance, where a larger one is always better; 100 nF is the cheapest part
#: there is and within TI's "1 µF or less".
CAP_VALUES_F = (100e-9, 10e-9, 1e-9)

#: The return path may add this share of the trace impedance (10 % of 50 Ω is 5 Ω, about a
#: 5 % reflection) ...
RETURN_IMPEDANCE_FRAC = 0.1
#: ... at the parallel resonance of the cap with the detour round the split, taken as 10 nH
#: (about an inch at Bogatin's 10 nH/inch). So the value must be at least
#: L_detour / Z_return², 0.4 nF at 50 Ω.
DETOUR_INDUCTANCE_NH = 10.0


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
    #: Edge rate of a signal not given its own. 100 ps (knee frequency 0.35 / tr, about
    #: 3.5 GHz) covers DDR, PCIe, USB 3, fast SPI and GPIO edges from modern parts.
    edge_rate_ps: float = 100.0
    #: Impedance of a trace not given its own: 50 Ω single-ended, which is also each half
    #: of a 90-100 Ω differential pair (USB, PCIe, Ethernet, LVDS).
    trace_impedance_ohm: float = 50.0
    #: Edge rate (ps) and impedance (Ω) of particular signals, by exact name, set in the
    #: net list. They decide the stitching cap's package (see :attr:`cap_rise_time_budget`).
    net_edge_rate_ps: Dict[str, float] = field(default_factory=dict)
    net_impedance_ohm: Dict[str, float] = field(default_factory=dict)
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
    #: A net is a supply, not a signal, whatever its name, when it has zone fills on this
    #: many copper layers (0: never). Signals are routed, not poured; a regulator or load
    #: switch output ("Net-(U2-OUT)") is poured on each layer it carries current on.
    power_min_zone_layers: int = 2
    #: ... or when this many of its vias stand in one array, each within
    #: :attr:`power_via_pitch_mm` of the next (0: never). A signal changes layer through one
    #: via; an array of them carries current.
    power_via_array: int = 4
    power_via_pitch_mm: float = 1.5
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
    #: A capacitor with a pad this close to an IC's pin on the same net is decoupling that IC.
    #: Return current through it would flow through the IC's supply, so it never counts as a
    #: stitching capacitor; crossings that only have one nearby are flagged.
    decoupling_ic_distance_mm: float = 3.0

    # --- the part ------------------------------------------------------------------------
    #: The package and value are picked from the edge rate (docs/research.md, "Value and
    #: package"): the largest package whose inductance is within budget, or several 0402s
    #: in parallel, at the largest value it stocks at the rating.
    #: The cap's mounted inductance may slow an edge by this share of the edge rate. Its
    #: L/2Z0 time constant adds a rise time of 1.1 L / Z0; Bogatin's rule is that it starts
    #: to matter at half the edge rate, a quarter leaves margin.
    cap_rise_time_budget: float = 0.25
    #: A value to use instead of the picked one ("" picks it). The package is still picked.
    cap_value: str = ""
    #: Voltage rating: at least this many times the voltage between the two plane nets,
    #: and never below :attr:`min_voltage_rating_v`.
    voltage_derating: float = 2.0
    min_voltage_rating_v: float = 16.0
    #: Rating used when a net's voltage cannot be read from its name.
    unknown_voltage_rating_v: float = 50.0

    # --- placement ----------------------------------------------------------------------
    #: Clearance kept round the capacitor's body (from its package) when looking for a spot.
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

    def edge_rate_of(self, net: str) -> float:
        return float(self.net_edge_rate_ps.get(net, self.edge_rate_ps))

    def impedance_of(self, net: str) -> float:
        return float(self.net_impedance_ohm.get(net, self.trace_impedance_ohm))

    def max_cap_inductance_nh(self, nets: Iterable[str] = ()) -> float:
        """Most mounted inductance a stitching cap returning ``nets`` may have (the default
        signal's when none): the tightest of theirs. Its L/2Z0 time constant adds a rise
        time of 1.1 L / Z0, held to :attr:`cap_rise_time_budget` of the edge rate."""
        pairs = [(self.edge_rate_of(n), self.impedance_of(n)) for n in nets] or \
            [(self.edge_rate_ps, self.trace_impedance_ohm)]
        return min(self.cap_rise_time_budget * tr * z / 1100.0 for tr, z in pairs)

    def min_cap_value_f(self, nets: Iterable[str] = ()) -> float:
        """Least value that keeps the cap's resonance with the detour under the return-path
        budget, for the lowest impedance among ``nets``."""
        z0 = min([self.impedance_of(n) for n in nets] or [self.trace_impedance_ohm])
        z = RETURN_IMPEDANCE_FRAC * z0
        return DETOUR_INDUCTANCE_NH * 1e-9 / (z * z)

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "ScanSettings":
        names = {f.name for f in fields(cls)}
        d = dict(d or {})
        if "cap_package" in d:      # saved before the part was picked: its value was not a choice
            d.pop("cap_value", None)
        return cls(**{k: v for k, v in d.items() if k in names})


#: What the window says of each setting, in the Advanced panel and the report.
DESCRIPTIONS = {
    "check_splits": "Check traces over plane splits (caps)",
    "check_via_caps": "Check vias onto another net's plane (caps)",
    "check_via_stitch": "Check vias onto another same-net plane (return vias)",
    "check_voids": "Check traces over voids (reroute)",
    "edge_rate_ps": "Default edge rate (ps)",
    "trace_impedance_ohm": "Default trace impedance (Ω)",
    "max_cap_distance_mm": "Max cap distance from a split crossing (mm)",
    "via_cap_distance_mm": "Max cap distance from a reference-changing via (mm)",
    "stitch_via_distance_mm": "Max return-via distance from a reference-changing via (mm)",
    "signals_per_cap": "Signals per stitching cap",
    "split_gap_max_mm": "Widest plane gap treated as a split (mm)",
    "void_ignore_mm": "Shortest plane hole that counts as a void (mm)",
    "decoupling_ic_distance_mm": "Cap pad within this of an IC pin is decoupling (mm)",
    "power_min_zone_layers": "Supply if poured on this many layers (0: off)",
    "power_via_array": "Supply if this many vias stand in an array (0: off)",
    "power_via_pitch_mm": "Via array pitch, at most (mm)",
    "cap_rise_time_budget": "Edge slowing a cap may add (share of edge rate)",
    "cap_value": "Capacitor value (blank: picked)",
    "voltage_derating": "Voltage rating x the voltage across it",
    "min_voltage_rating_v": "Lowest voltage rating (V)",
}

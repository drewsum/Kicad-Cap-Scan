"""What a scan finds, as plain dataclasses that save to JSON.

Coordinates are board millimeters as KiCad shows them (x right, y down), layers are
KiCad's copper layer ids (:mod:`capscan.model.layers`).
"""

from __future__ import annotations

import dataclasses
import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from capscan.analysis.rules import ScanSettings

Point = Tuple[float, float]

# Crossing kinds
SPLIT = "split"         # over a gap between two plane nets: a stitching cap bridges it
VOID = "void"           # over no plane copper: no capacitor helps; reroute or fill
# Via transition kinds
VIA_CAP = "cap"         # the reference changes to another net: a stitching cap
VIA_STITCH = "via"      # the reference stays the same net on another layer: a return via
VIA_OK = "ok"

# How a discontinuity is dealt with
NEW_CAP = "new cap"
EXISTING = "existing cap"
NEW_VIA = "new via"
EXISTING_VIA = "existing via"
UNFIXABLE = "reroute"
OTHER_PLANE = "other plane"     # a void on one side of a stripline; the other plane is solid

#: The breaks that add heat, each on its own map, and what the window calls them.
HEAT_KINDS = (SPLIT, VOID, VIA_CAP, VIA_STITCH)
HEAT_LABELS = {SPLIT: "Splits", VOID: "Voids", VIA_CAP: "Via caps", VIA_STITCH: "Return vias"}


@dataclass
class Crossing:
    id: str
    kind: str                       # SPLIT or VOID
    net: str                        # the signal
    layer: int                      # the signal's layer
    ref_layer: int                  # the plane layer it is broken on
    net_a: str                      # plane net before the break ("" for none)
    net_b: str                      # plane net after it ("" for none)
    position: Point                 # the middle of the gap or void, on the trace
    gap_mm: float                   # length of trace over no plane there
    h_mm: float                     # dielectric height from the trace to the plane
    severity: float = 1.0           # 1: the only reference; 0.5: another solid one remains
    trace_dir: Point = (1.0, 0.0)   # unit vector along the trace at the crossing
    split_dir: Point = (0.0, 1.0)   # unit vector along the split there
    status: str = ""
    covered_by: str = ""            # CS# or the existing capacitor's reference
    decoupling: List[str] = field(default_factory=list)   # decoupling caps it would use


@dataclass
class Transition:
    id: str
    kind: str                       # VIA_CAP, VIA_STITCH or VIA_OK
    net: str
    via_id: str
    position: Point
    from_layer: int
    to_layer: int
    from_refs: List[Tuple[int, str]]    # (reference layer, plane net) of from_layer here
    to_refs: List[Tuple[int, str]]
    net_a: str = ""                 # VIA_CAP: the nets to bridge; VIA_STITCH: the net
    net_b: str = ""
    ref_layers: Tuple[int, int] = (-1, -1)
    status: str = ""
    covered_by: str = ""
    decoupling: List[str] = field(default_factory=list)


@dataclass
class StitchCap:
    id: str                         # CS1, CS2, ...
    net_a: str
    net_b: str
    value: str
    dielectric: str
    package: str
    voltage_v: float
    position: Point
    angle_deg: float                # pads along this direction, one each side of the split
    side: str                       # "top" or "bottom"
    serves: List[str] = field(default_factory=list)      # crossing / transition ids
    signals: List[str] = field(default_factory=list)     # signal nets
    ref_layers: List[int] = field(default_factory=list)
    crowded: bool = False           # no free spot found; place by hand near here
    note: str = ""
    count: int = 1                  # parts in parallel at this spot, each with its own vias

    @property
    def amount(self) -> str:
        """The value, with how many when more than one: "2 x 100nF"."""
        return f"{self.count} x {self.value}" if self.count > 1 else self.value


@dataclass
class StitchVia:
    id: str                         # SV1, ...
    net: str
    position: Point
    layers: List[int]
    serves: List[str] = field(default_factory=list)


@dataclass
class ExistingCap:
    reference: str
    value: str
    net_a: str
    net_b: str
    position: Point
    side: str
    role: str                       # "stitch" or "decoupling"
    near_ic: str = ""               # the IC pin that makes it decoupling, e.g. "U3.12"
    credited: List[str] = field(default_factory=list)
    flagged: List[str] = field(default_factory=list)


@dataclass
class HeatLayer:
    layer: int
    origin: Point                   # x, y of grid cell [0, 0]
    step_mm: float
    values: np.ndarray              # (ny, nx): unscaled for one kind, 0..1 once combined
    #: The break that made it (one of :data:`HEAT_KINDS`); "" for a combined map, or one
    #: saved before the kinds were kept apart.
    kind: str = ""

    def to_dict(self):
        return {"layer": self.layer, "origin": list(self.origin), "step_mm": self.step_mm,
                "shape": list(self.values.shape), "kind": self.kind,
                "values": np.round(self.values, 4).ravel().tolist()}

    @classmethod
    def from_dict(cls, d):
        return cls(layer=d["layer"], origin=tuple(d["origin"]), step_mm=d["step_mm"],
                   values=np.asarray(d["values"], dtype=float).reshape(d["shape"]),
                   kind=d.get("kind", ""))


def heat_kind(x) -> str:
    """The kind of heat a crossing or via transition adds, or "" for none: every split and
    void (but a void the other plane of a stripline covers), every via onto another net's
    plane, and the vias onto another plane of the same net that still need a return via."""
    if isinstance(x, Crossing):
        return x.kind if x.status != OTHER_PLANE else ""
    if x.kind == VIA_CAP or (x.kind == VIA_STITCH and x.status == NEW_VIA):
        return x.kind
    return ""


def combine_heat(parts: Sequence[HeatLayer], kinds: Optional[Iterable[str]] = None
                 ) -> List[HeatLayer]:
    """One map per layer: the ``kinds`` of heat (None: all) added up, scaled by the hottest
    spot on the board so layers compare directly. Every part shares one grid."""
    kinds = set(HEAT_KINDS if kinds is None else kinds) | {""}
    sums: Dict[int, HeatLayer] = {}
    for h in parts:
        if h.kind not in kinds:
            continue
        if h.layer in sums:
            sums[h.layer].values = sums[h.layer].values + h.values
        else:
            sums[h.layer] = HeatLayer(h.layer, h.origin, h.step_mm, np.array(h.values, float))
    peak = max((h.values.max() for h in sums.values()), default=0.0)
    if peak <= 0:
        return []
    for h in sums.values():
        h.values = h.values / peak
    return [sums[l] for l in sorted(sums)]


@dataclass
class ScanResult:
    board_name: str
    settings: ScanSettings
    layer_names: Dict[int, str]
    plane_nets: List[str]
    signal_nets: List[str]                          # the signals scanned
    references: Dict[int, List[Tuple[int, float]]]  # signal layer -> [(ref layer, h mm)]
    crossings: List[Crossing] = field(default_factory=list)
    transitions: List[Transition] = field(default_factory=list)
    caps: List[StitchCap] = field(default_factory=list)
    stitch_vias: List[StitchVia] = field(default_factory=list)
    existing: List[ExistingCap] = field(default_factory=list)
    #: The heat of each kind of break on each reference layer, unscaled: :meth:`heat_map`
    #: adds them up.
    heat: List[HeatLayer] = field(default_factory=list)
    track_heat: List[Tuple[int, float]] = field(default_factory=list)   # (track index, 0..1)
    warnings: List[str] = field(default_factory=list)
    kicad_version: str = ""
    candidate_nets: List[str] = field(default_factory=list)   # every signal net on the board
    #: The board as read, before any scan: planes and routing only, nothing found yet.
    preview: bool = False
    created: str = ""
    seconds: float = 0.0

    # --- views ---------------------------------------------------------------------
    def layer_name(self, layer: int) -> str:
        return self.layer_names.get(layer, str(layer))

    def splits(self) -> List[Crossing]:
        return [c for c in self.crossings if c.kind == SPLIT]

    def voids(self) -> List[Crossing]:
        """Voids no plane returns the current over (not those a solid second plane covers)."""
        return [c for c in self.crossings if c.kind == VOID and c.status != OTHER_PLANE]

    def heat_map(self, kinds: Optional[Iterable[str]] = None) -> List[HeatLayer]:
        """The heat of the ``kinds`` of break (None: all) as one map per layer, 0..1."""
        return combine_heat(self.heat, kinds)

    def heat_counts(self) -> Dict[str, int]:
        """How many breaks of each kind add heat."""
        out = {k: 0 for k in HEAT_KINDS}
        for x in list(self.crossings) + list(self.transitions):
            if heat_kind(x):
                out[heat_kind(x)] += 1
        return out

    def via_changes(self) -> List[Transition]:
        return [t for t in self.transitions if t.kind != VIA_OK]

    def decoupling_flags(self) -> List[object]:
        return [x for x in list(self.crossings) + list(self.transitions) if x.decoupling]

    def item(self, ident: str):
        for x in list(self.crossings) + list(self.transitions):
            if x.id == ident:
                return x
        return None

    def counts(self) -> Dict[str, int]:
        return {"caps": len(self.caps), "splits": len(self.splits()),
                "voids": len(self.voids()), "via_changes": len(self.via_changes()),
                "stitch_vias": len(self.stitch_vias),
                "existing_credited": sum(1 for e in self.existing if e.credited),
                "existing_decoupling": sum(1 for e in self.existing if e.role == "decoupling"),
                "decoupling_flags": len(self.decoupling_flags())}

    # --- files ---------------------------------------------------------------------
    def to_dict(self) -> Dict:
        def dc(x):
            return dataclasses.asdict(x)
        return {"format": "capscan-result", "version": 1,
                "board_name": self.board_name, "settings": self.settings.to_dict(),
                "layer_names": {str(k): v for k, v in self.layer_names.items()},
                "plane_nets": self.plane_nets, "signal_nets": self.signal_nets,
                "references": {str(k): [list(r) for r in v]
                               for k, v in self.references.items()},
                "crossings": [dc(c) for c in self.crossings],
                "transitions": [dc(t) for t in self.transitions],
                "caps": [dc(c) for c in self.caps],
                "stitch_vias": [dc(v) for v in self.stitch_vias],
                "existing": [dc(e) for e in self.existing],
                "heat": [h.to_dict() for h in self.heat],
                "track_heat": [list(t) for t in self.track_heat],
                "warnings": self.warnings, "kicad_version": self.kicad_version,
                "candidate_nets": self.candidate_nets, "preview": self.preview,
                "created": self.created, "seconds": self.seconds}

    @classmethod
    def from_dict(cls, d: Dict) -> "ScanResult":
        def tup(x):
            return tuple(x) if isinstance(x, list) else x

        def crossing(c):
            c = dict(c)
            for k in ("position", "trace_dir", "split_dir"):
                c[k] = tuple(c[k])
            return Crossing(**c)

        def transition(t):
            t = dict(t)
            t["position"] = tuple(t["position"])
            t["from_refs"] = [tuple(r) for r in t["from_refs"]]
            t["to_refs"] = [tuple(r) for r in t["to_refs"]]
            t["ref_layers"] = tuple(t["ref_layers"])
            return Transition(**t)

        return cls(
            board_name=d["board_name"], settings=ScanSettings.from_dict(d["settings"]),
            layer_names={int(k): v for k, v in d["layer_names"].items()},
            plane_nets=list(d["plane_nets"]), signal_nets=list(d["signal_nets"]),
            references={int(k): [tuple(r) for r in v] for k, v in d["references"].items()},
            crossings=[crossing(c) for c in d["crossings"]],
            transitions=[transition(t) for t in d["transitions"]],
            caps=[StitchCap(**{**c, "position": tup(c["position"])}) for c in d["caps"]],
            stitch_vias=[StitchVia(**{**v, "position": tup(v["position"])})
                         for v in d["stitch_vias"]],
            existing=[ExistingCap(**{**e, "position": tup(e["position"])})
                      for e in d["existing"]],
            heat=[HeatLayer.from_dict(h) for h in d["heat"]],
            track_heat=[tuple(t) for t in d.get("track_heat", [])],
            warnings=list(d.get("warnings", [])), kicad_version=d.get("kicad_version", ""),
            candidate_nets=list(d.get("candidate_nets", [])), preview=d.get("preview", False),
            created=d.get("created", ""), seconds=d.get("seconds", 0.0))

    def save(self, path) -> Path:
        path = Path(path)
        data = json.dumps(self.to_dict(), separators=(",", ":"))
        if path.suffix == ".gz":
            with gzip.open(path, "wt", encoding="utf-8") as f:
                f.write(data)
        else:
            path.write_text(data, encoding="utf-8")
        return path

    @classmethod
    def load(cls, path) -> "ScanResult":
        path = Path(path)
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def find(items, ident: str) -> Optional[object]:
    return next((x for x in items if getattr(x, "id", None) == ident), None)

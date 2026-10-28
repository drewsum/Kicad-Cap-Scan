"""Add the scan's stitching capacitors and return vias to the board in KiCad.

A capacitor's footprint is a copy of one already on the board, or one built from a
library file, moved and turned into place (:mod:`capscan.kicad.footprint_proto`). It is
flipped to the bottom when the scan put it there, each pad takes the net of the plane under
it, and each pad gets a via down to that plane, beside the pad and joined by a short track,
where the design rules leave room (:mod:`capscan.analysis.install`). The parts are ordinary
footprints: the user adds them to the schematic.

DRC runs before and after (:mod:`capscan.kicad.drc`). Whatever a new violation names is
taken off again and DRC rerun, until the board checks as it did before.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import shapely
from shapely.affinity import rotate, translate
from shapely.geometry import box

from capscan.analysis.install import (
    BLIND, BLIND_BURIED, BURIED, LIBRARY, LIBRARY_FOOTPRINTS, MICRO, BoardRules, Keepout,
    ViaSize, allowed_via_types, board_footprints, default_via_size, first_free_number,
    nearest_plane, next_references, part_pitch, parts_at, spot_for_via, surface_of, via_span,
    via_beside_pad,
)
from capscan.analysis.results import ScanResult, StitchCap, StitchVia
from capscan.geometry.copper import poly_to_shapely
from capscan.kicad import footprint_proto, sexpr
from capscan.kicad.convert import mm, pt
from capscan.kicad.design_rules import default_library_dir, read_rules
from capscan.kicad.drc import DrcError, DrcReport, Violation, find_cli, run_drc, touching
from capscan.model.board import BoardSnapshot
from capscan.progress import NULL, ProgressReporter

log = logging.getLogger(__name__)

NM = 1_000_000
#: KiCad actions that bring the selection into view, best first.
VIEW_ACTIONS = ("common.Control.centerSelection", "common.Control.zoomFitSelection")


class InstallError(RuntimeError):
    pass


@dataclass
class FootprintSource:
    """Where a package's footprint comes from: one on the board, or a library file."""
    package: str
    lib_id: str
    proto: object                   # a FootprintInstance message to copy
    origin: str                     # "board", "library" or "file"
    uses: int = 0

    @property
    def bottom(self) -> bool:
        from kipy.proto.board.board_types_pb2 import BoardLayer
        return self.proto.layer == BoardLayer.BL_B_Cu

    @property
    def label(self) -> str:
        where = {"board": f"on the board, {self.uses} used", "library": "KiCad library",
                 "file": "file"}[self.origin]
        return f"{self.lib_id} ({where})"


@dataclass
class AddOptions:
    footprints: Dict[str, FootprintSource]  # by package
    via_size: ViaSize
    via_types: List[str]
    prefix: str = "C"
    start: int = 1


@dataclass
class Added:
    ident: str                      # CS3 or SV2
    references: List[str] = field(default_factory=list)
    items: list = field(default_factory=list)        # what to remove: footprints, vias, tracks
    ids: List[str] = field(default_factory=list)     # every new item's id, pads included
    notes: List[str] = field(default_factory=list)


@dataclass
class Skipped:
    ident: str
    reason: str
    violations: List[Violation] = field(default_factory=list)


@dataclass
class AddReport:
    added: List[Added] = field(default_factory=list)
    skipped: List[Skipped] = field(default_factory=list)
    before: Optional[DrcReport] = None
    after: Optional[DrcReport] = None

    def lines(self) -> List[str]:
        out = [f"{a.ident}: added {', '.join(a.references) or 'via'}"
               + (f" ({'; '.join(a.notes)})" if a.notes else "") for a in self.added]
        for s in self.skipped:
            out.append(f"{s.ident}: not added: {s.reason}")
            out += [f"    {v}" for v in s.violations[:5]]
        return out


def _vec(xy):
    from kipy.geometry import Vector2
    return Vector2.from_xy(round(xy[0] * NM), round(xy[1] * NM))


def _unsupported(e: Exception) -> bool:
    from capscan.kicad.extract import _unsupported as unsupported
    return unsupported(e)


class Installer:
    """Adds a scan's parts to the board open in KiCad. Build it once a scan is done."""

    def __init__(self, kicad, board, snap: BoardSnapshot, result: ScanResult,
                 progress: ProgressReporter = NULL, rules: Optional[BoardRules] = None):
        self.kicad, self.board, self.snap, self.result = kicad, board, snap, result
        self.progress = progress
        self.rules = rules or read_rules(snap.project_dir, snap.board_name)
        self.cli = find_cli(kicad)
        self.allowed = allowed_via_types(snap, self.rules)
        self.stack = [c.id for c in snap.stackup.copper]
        outline = [g for g in (poly_to_shapely(p) for p in snap.outline) if g is not None]
        self.keepout = Keepout(snap, self.rules,
                               shapely.unary_union(outline) if outline else shapely.Polygon())
        self.taken = {f.reference for f in snap.footprints}
        self.baseline: Optional[DrcReport] = None
        self._nets = None
        self._board_fps: Optional[list] = None
        self._via_codes = self._type_codes()

    # ------------------------------------------------------------------ choices
    def board_sources(self, package: str) -> List[FootprintSource]:
        """Footprints on the board for ``package``, most used first (a top one of each)."""
        if self._board_fps is None:
            try:
                self._board_fps = list(self.board.get_footprints())
            except Exception as e:  # noqa: BLE001 - library footprints still work
                log.warning("Could not read the board's footprints: %s", e)
                self._board_fps = []
        from kipy.proto.board.board_types_pb2 import BoardLayer
        out = []
        for name, uses in board_footprints(self.snap, package):
            found = [f for f in self._board_fps if _lib_id(f) == name]
            found.sort(key=lambda f: f.layer != BoardLayer.BL_F_Cu)
            if found:
                out.append(FootprintSource(package, name, found[0].proto, "board", uses=uses))
        return out

    def library_source(self, package: str) -> Optional[FootprintSource]:
        name = LIBRARY_FOOTPRINTS.get(package)
        lib = default_library_dir(self.cli)
        if not name or lib is None:
            return None
        return file_source(lib / f"{LIBRARY}.pretty" / f"{name}.kicad_mod", package)

    def sources(self, package: str) -> List[FootprintSource]:
        out = self.board_sources(package)
        lib = self.library_source(package)
        return out + ([lib] if lib is not None else [])

    def defaults(self) -> AddOptions:
        packages = sorted({c.package for c in self.result.caps})
        fps = {}
        for p in packages:
            found = self.sources(p)
            if found:
                fps[p] = found[0]
        planes = {n for c in self.result.caps for n in (c.net_a, c.net_b)}
        planes |= {v.net for v in self.result.stitch_vias}
        return AddOptions(footprints=fps,
                          via_size=default_via_size(self.snap, self.rules, planes),
                          via_types=list(self.allowed), prefix="C",
                          start=first_free_number(self.taken, "C"))

    def via_sizes(self) -> List[ViaSize]:
        sizes = list(self.rules.via_sizes)
        d = default_via_size(self.snap, self.rules, self.result.plane_nets)
        return [s for s in dict.fromkeys([d] + sizes) if self.rules.meets(s)] or [d]

    # ------------------------------------------------------------------ DRC
    def drc(self) -> DrcReport:
        if not self.cli:
            raise InstallError("kicad-cli was not found, so DRC cannot run; nothing is added "
                               "without it")
        self.progress.info("Running DRC (kicad-cli)...")
        try:
            report = run_drc(self.cli, self.board.get_as_string(), self.snap.project_dir,
                             self.snap.board_name)
        except DrcError as e:
            raise InstallError(str(e)) from e
        self.progress.info(f"DRC: {len(report.violations)} violation(s)")
        return report

    # ------------------------------------------------------------------ adding all
    def add_all(self, opts: AddOptions, caps: Optional[Sequence[StitchCap]] = None,
                vias: Optional[Sequence[StitchVia]] = None,
                kept: Optional[Sequence[Added]] = None) -> AddReport:
        """Every cap and return via in one commit (one undo), DRC before and after.

        ``kept`` ends a step-through: the parts kept so far are checked with these, against
        the DRC from :meth:`begin_steps`."""
        caps = self.result.caps if caps is None else caps
        vias = self.result.stitch_vias if vias is None else vias
        if kept is None or self.baseline is None:
            self.baseline = self.drc()
        report = AddReport(added=list(kept or ()), before=self.baseline)
        fresh = len(report.added)
        commit = self.board.begin_commit()
        try:
            for i, c in enumerate(caps):
                self.progress.check_cancel()
                self.progress.fraction(i / max(1, len(caps) + len(vias)))
                self._file(report, self._add_cap(c, opts))
            for sv in vias:
                self.progress.check_cancel()
                self._file(report, self._add_via(sv, opts))
        except BaseException:
            self.board.drop_commit(commit)
            raise
        if len(report.added) > fresh:
            self.board.push_commit(commit, "CapScan: stitching capacitors and return vias")
        else:
            self.board.drop_commit(commit)
        if not report.added:
            report.after = report.before
            return report
        self._settle(report)
        return report

    def _file(self, report: AddReport, out) -> None:
        (report.added if isinstance(out, Added) else report.skipped).append(out)

    def _settle(self, report: AddReport, rounds: int = 4) -> None:
        """Refill and check until DRC finds nothing new, taking off what new violations
        name (everything just added, if a violation names none of it)."""
        for _ in range(rounds):
            self.refill()
            report.after = self.drc()
            new = report.after.new_since(self.baseline)
            if not new:
                return
            guilty = [a for a in report.added if touching(new, a.ids)]
            if not guilty:
                guilty = list(report.added)
            self.remove(guilty, "CapScan: remove parts that failed DRC")
            for a in guilty:
                report.added.remove(a)
                report.skipped.append(Skipped(a.ident, "failed DRC", touching(new, a.ids)
                                              or new))
            if not report.added:
                self.refill()
                report.after = self.drc()
                return
        raise InstallError("DRC still finds new violations; undo in KiCad to take the "
                           "parts off")

    # ------------------------------------------------------------------ one at a time
    # DRC takes long on a big board, so a step-through runs it once before the first part
    # (begin_steps) and once after the last (finish_steps), not for each part.
    def begin_steps(self) -> None:
        self.baseline = self.drc()

    def finish_steps(self, opts: AddOptions, kept: Sequence[Added]) -> AddReport:
        """Check the parts kept in a step-through, taking off what DRC finds new."""
        return self.add_all(opts, [], [], kept=kept)

    def add_one(self, ident: str, opts: AddOptions):
        """Add one cap or return via (its own commit) and show it in KiCad: an ``Added``,
        or a ``Skipped``. DRC waits for :meth:`finish_steps`."""
        cap = next((c for c in self.result.caps if c.id == ident), None)
        sv = next((v for v in self.result.stitch_vias if v.id == ident), None)
        commit = self.board.begin_commit()
        try:
            out = self._add_cap(cap, opts) if cap is not None else self._add_via(sv, opts)
        except BaseException:
            self.board.drop_commit(commit)
            raise
        if isinstance(out, Skipped):
            self.board.drop_commit(commit)
            return out
        self.board.push_commit(commit, f"CapScan: {ident}")
        self.focus(out)
        return out

    # ------------------------------------------------------------------ parts
    def _add_cap(self, cap: StitchCap, opts: AddOptions):
        src = opts.footprints.get(cap.package)
        if src is None:
            return Skipped(cap.id, f"no footprint for a {cap.package} capacitor")
        if cap.crowded:
            return Skipped(cap.id, "no free spot was found by the scan; place by hand")
        bottom = cap.side == "bottom"
        if bottom and not src.bottom and not self._can_flip():
            return Skipped(cap.id, "this KiCad cannot flip a part to the bottom (KiCad "
                                   "10.0.6 can); place by hand")
        refs = next_references(self.taken, opts.prefix, opts.start, cap.count)
        pitch = part_pitch(cap.package, opts.via_size, self.rules)
        done = Added(cap.id, refs)
        for ref, at in zip(refs, parts_at(cap.position, cap.angle_deg, cap.count, pitch)):
            why = self._add_part(cap, src, ref, at, opts, done)
            if why:
                self.remove([done])
                return Skipped(cap.id, why)
        self.taken |= set(refs)
        return done

    def _add_part(self, cap: StitchCap, src: FootprintSource, ref: str, at, opts: AddOptions,
                  done: Added) -> str:
        """Put one part in; "" when it is in, else why not (``done`` keeps what is in)."""
        bottom = cap.side == "bottom"
        flip = bottom and not src.bottom
        # A flip mirrors left to right, which turns the angle the other way.
        angle = (-cap.angle_deg if flip else cap.angle_deg) % 360.0

        def message(nets=None):
            return footprint_proto.placed(src.proto, position=at, angle_deg=angle,
                                          reference=ref, value=cap.value, nets=nets)
        where = footprint_proto.pad_positions(message())
        if len(where) != 2:
            return f"its footprint {src.lib_id} has {len(where)} pads, not 2"
        if flip:
            where = {n: (2 * at[0] - x, y) for n, (x, y) in where.items()}
        numbers = list(where)
        nets = self._pad_nets(cap, [where[n] for n in numbers])
        if nets is None:
            return "no plane of its nets under either pad"
        fp = self._create_footprint(message({n: net for n, (net, _) in zip(numbers, nets)}))
        done.items.append(fp)
        if math.dist(pt(fp.position), at) > 0.01:
            return "KiCad put its footprint somewhere else"
        if flip:
            from kipy.proto.board.board_commands_pb2 import BoardFlipDirection
            flipped = self.board.flip_items(fp, BoardFlipDirection.BFD_LEFT_RIGHT)
            if not flipped:
                return "KiCad did not flip it to the bottom"
            fp = flipped[0]
            done.items[-1] = fp
        # KiCad has the last word on where the pads are: give the nets again if need be.
        pads = sorted(fp.definition.pads, key=lambda p: numbers.index(p.number)
                      if p.number in numbers else 9)
        nets = self._pad_nets(cap, [pt(p.position) for p in pads]) or nets
        if [p.net.name for p in pads] != [n for n, _ in nets]:
            knets = self._kicad_nets()
            for p, (net, _) in zip(pads, nets):
                p.net = knets[net]
            fp = (self.board.update_items(fp) or [fp])[0]
            done.items[-1] = fp
            pads = sorted(fp.definition.pads, key=lambda p: numbers.index(p.number)
                          if p.number in numbers else 9)
        surface = surface_of(cap.side)
        center = pt(fp.position)
        vias = []
        for p, (net, plane) in zip(pads, nets):
            pos = pt(p.position)
            out = (pos[0] - center[0], pos[1] - center[1])
            n = math.hypot(*out) or 1.0
            out = (out[0] / n, out[1] / n)
            kind, a, b = via_span(self.stack, surface, plane, opts.via_types)
            span = self.stack[self.stack.index(a):self.stack.index(b) + 1]
            size = self.rules.micro_via if kind == MICRO else opts.via_size
            spot = via_beside_pad(self.keepout, pos, out, _pad_half(p, out), net, size, span,
                                  surface, self._track_width(p))
            if spot is None:
                return f"no room for {net}'s via beside pad {p.number} by the design rules"
            vias.append((p, net, kind, a, b, size, span, spot))
        done.ids += [fp.id.value] + [p.id.value for p in fp.definition.pads]
        for p, net, kind, a, b, size, span, (spot, track) in vias:
            created = self.board.create_items([
                self._via(spot, net, kind, a, b, size),
                self._track(track, net, surface, self._track_width(p))])
            done.items += created
            done.ids += [c.id.value for c in created]
            self.keepout.add_via(spot, size, net, span)
            self.keepout.add_copper(surface, shapely.LineString(track).buffer(
                self._track_width(p) / 2), net)
            self.keepout.add_copper(surface, _pad_shape(p), net)
            if kind != "through":
                done.notes.append(f"{kind} via {self._layer(a)}-{self._layer(b)}")
        return ""

    def _add_via(self, sv: StitchVia, opts: AddOptions):
        kind, a, b = via_span(self.stack, sv.layers[0], sv.layers[-1], opts.via_types)
        span = self.stack[self.stack.index(a):self.stack.index(b) + 1]
        size = self.rules.micro_via if kind == MICRO else opts.via_size
        spot = spot_for_via(self.keepout, sv.position, size, sv.net, span)
        if spot is None:
            return Skipped(sv.id, "no room for the via by the design rules")
        (v,) = self.board.create_items([self._via(spot, sv.net, kind, a, b, size)])
        self.keepout.add_via(spot, size, sv.net, span)
        return Added(sv.id, [], [v], [v.id.value],
                     [] if kind == "through" else [f"{kind} via"])

    def _pad_nets(self, cap: StitchCap, pads: List[Tuple[float, float]]):
        """Each pad's net and the plane layer its via goes to: the way round that puts each
        pad nearest its own net's plane. None when a net has no plane on its layers."""
        layers = list(cap.ref_layers) or self.stack
        best = None
        for order in ((cap.net_a, cap.net_b), (cap.net_b, cap.net_a)):
            found = [nearest_plane(self.snap, n, p, layers) for n, p in zip(order, pads)]
            score = (sum(f is None for f in found), sum(f[0] for f in found if f))
            if best is None or score < best[0]:
                best = (score, order, [None if f is None else f[1] for f in found])
        _, order, found = best
        for i, net in enumerate(order):         # else the plane anywhere on its layers
            if found[i] is None:
                found[i] = next((l for l in layers for z in self.snap.items_on_net(net)[3]
                                 if l in z.filled), None)
        if any(f is None for f in found):
            return None
        return list(zip(order, found))

    # ------------------------------------------------------------------ removing, showing
    def remove(self, added: Sequence[Added], message: str = "") -> None:
        items = [i for a in added for i in a.items]
        if not items:
            return
        commit = self.board.begin_commit() if message else None
        try:
            self.board.remove_items(items)
        except Exception:
            if commit is not None:
                self.board.drop_commit(commit)
            raise
        if commit is not None:
            self.board.push_commit(commit, message)
        for a in added:
            self.taken -= set(a.references)
            a.items = []

    def focus(self, added: Added) -> None:
        """Select the part in KiCad and bring it into view."""
        try:
            self.board.clear_selection()
            self.board.add_to_selection(added.items)
        except Exception as e:  # noqa: BLE001 - showing it is a nicety
            log.debug("Could not select %s: %s", added.ident, e)
            return
        if self.kicad is None:
            return
        from kipy.proto.common.commands.editor_commands_pb2 import RunActionStatus
        for action in VIEW_ACTIONS:
            try:
                if self.kicad.run_action(action).status == RunActionStatus.RAS_OK:
                    return
            except Exception as e:  # noqa: BLE001
                log.debug("KiCad action %s: %s", action, e)

    # ------------------------------------------------------------------ KiCad items
    def _create_footprint(self, msg):
        from kipy.board_types import FootprintInstance
        try:
            items = self.board.create_items([FootprintInstance(msg)])
        except Exception as e:  # noqa: BLE001
            if _unsupported(e):
                raise InstallError("This KiCad cannot add footprints through its API; "
                                   "update KiCad") from e
            raise
        fp = next((i for i in items if isinstance(i, FootprintInstance)), None)
        if fp is None:
            raise InstallError("KiCad did not create the footprint")
        return fp

    def _can_flip(self) -> bool:
        try:
            v = self.kicad.get_version()
            return (int(v.major), int(v.minor), int(v.patch)) >= (10, 0, 6)
        except Exception:  # noqa: BLE001
            return False

    def _kicad_nets(self):
        if self._nets is None:
            self._nets = {n.name: n for n in self.board.get_nets()}
        return self._nets

    def _type_codes(self) -> Dict[str, int]:
        from kipy.proto.board import board_types_pb2 as bt
        kicad9 = any(v.via_type == BLIND_BURIED for v in self.snap.vias)
        return {"through": bt.VT_THROUGH, MICRO: bt.VT_MICRO,
                BLIND: bt.VT_BLIND_BURIED if kicad9 else bt.VT_BLIND,
                BURIED: bt.VT_BLIND_BURIED if kicad9 else bt.VT_BURIED}

    def _via(self, at, net: str, kind: str, start: int, end: int, size: ViaSize):
        from kipy.board_types import Via
        from kipy.proto.board import board_types_pb2 as bt
        v = Via()
        v.position = _vec(at)
        v.net = self._kicad_nets()[net]
        v.proto.type = self._via_codes[kind]
        ps = v.proto.pad_stack
        ps.type = bt.PST_NORMAL
        ps.drill.start_layer, ps.drill.end_layer = start, end
        ps.drill.diameter.x_nm = ps.drill.diameter.y_nm = round(size.drill_mm * NM)
        cl = ps.copper_layers.add()
        cl.layer = bt.BoardLayer.BL_F_Cu
        cl.shape = bt.PSS_CIRCLE
        cl.size.x_nm = cl.size.y_nm = round(size.diameter_mm * NM)
        return v

    def _track(self, ends, net: str, layer: int, width_mm: float):
        from kipy.board_types import Track
        t = Track()
        t.start, t.end = _vec(ends[0]), _vec(ends[1])
        t.layer = layer
        t.net = self._kicad_nets()[net]
        t.proto.width.value_nm = round(width_mm * NM)
        return t

    def _track_width(self, pad) -> float:
        w, h = _pad_size(pad)
        return round(min(self.rules.track_width_mm * 1.5, min(w, h)), 4)

    def _layer(self, lid: int) -> str:
        return self.result.layer_name(lid)

    def refill(self) -> None:
        from capscan.kicad.extract import refill_and_wait
        self.progress.info("Refilling zones...")
        refill_and_wait(self.board, self.progress)


# ---------------------------------------------------------------------------- pads

def _pad_size(pad) -> Tuple[float, float]:
    cls = list(pad.padstack.copper_layers)
    if not cls:
        return 0.5, 0.5
    return mm(cls[0].size.x), mm(cls[0].size.y)


def _pad_angle(pad) -> float:
    try:
        return float(pad.padstack.angle.degrees)
    except Exception:  # noqa: BLE001
        return 0.0


def _pad_half(pad, outward) -> Tuple[float, float]:
    """The pad's half-sizes along and across ``outward``."""
    w, h = _pad_size(pad)
    a = math.radians(_pad_angle(pad))
    along_x = abs(outward[0] * math.cos(a) - outward[1] * math.sin(a))
    return (w / 2, h / 2) if along_x >= math.sqrt(0.5) else (h / 2, w / 2)


def _pad_shape(pad):
    w, h = _pad_size(pad)
    x, y = pt(pad.position)
    return translate(rotate(box(-w / 2, -h / 2, w / 2, h / 2), -_pad_angle(pad),
                            origin=(0, 0)), x, y)


def file_source(path: Path, package: str) -> Optional[FootprintSource]:
    """A footprint from a ``.kicad_mod`` file; its library is the ``.pretty`` folder's."""
    path = Path(path)
    try:
        node = sexpr.parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("Could not read %s: %s", path, e)
        return None
    lib = path.parent.stem if path.parent.suffix == ".pretty" else ""
    name = sexpr.lib_id(node).split(":")[-1]
    origin = "library" if lib == LIBRARY else "file"
    return FootprintSource(package, f"{lib}:{name}" if lib else name,
                           footprint_proto.from_library(node, lib), origin)


def _lib_id(fp) -> str:
    try:
        i = fp.definition.id
        return f"{i.library}:{i.name}" if i.library else i.name
    except Exception:  # noqa: BLE001
        return ""

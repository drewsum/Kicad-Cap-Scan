"""The whole scan: board snapshot in, :class:`ScanResult` out. No Qt, no KiCad."""

from __future__ import annotations

import time
from typing import Optional

from capscan.analysis.crossings import find_crossings
from capscan.analysis.existing_caps import find_existing_caps
from capscan.analysis.heatmap import heat_maps, track_heat
from capscan.analysis.placement import credit_existing, mark_unfixable, needs_of, place_caps
from capscan.analysis.references import build_references
from capscan.analysis.results import SPLIT, VIA_CAP, VIA_STITCH, VOID, ScanResult
from capscan.analysis.rules import ScanSettings
from capscan.analysis.transitions import find_transitions
from capscan.model.board import BoardSnapshot
from capscan.progress import NULL, ProgressReporter


def run_scan(snap: BoardSnapshot, settings: Optional[ScanSettings] = None,
             progress: ProgressReporter = NULL) -> ScanResult:
    settings = settings or ScanSettings()
    t0 = time.perf_counter()
    warnings = list(snap.assumed)

    with progress.timed("Finding planes and signals"):
        refs = build_references(snap, settings, warnings)
        names = {c.id: c.name for c in snap.stackup.copper}
        progress.info(f"{len(refs.plane_nets)} plane nets, {len(refs.signal_nets)} of "
                      f"{len(refs.candidate_nets)} signal nets to scan")
        for lid, plane in refs.all_planes.items():
            role = "reference" if lid in refs.planes else "partial pour"
            progress.info(f"  {names.get(lid, lid)}: {', '.join(sorted(plane.regions))} "
                          f"({plane.coverage:.0%} of the board, {role})")
        for lid, rl in refs.by_layer.items():
            if rl:
                progress.info(f"  {names.get(lid, lid)} returns on "
                              + ", ".join(f"{names.get(r, r)} (h {h:.3f} mm)" for r, h in rl))
    progress.check_cancel()

    if settings.unchecked():
        warnings.append("Not checked (turned off): " + "; ".join(settings.unchecked()) + ".")

    with progress.timed("Walking every trace over its reference planes"):
        crossings = []
        if settings.check_splits or settings.check_voids:
            crossings = find_crossings(snap, refs, settings, progress)
        wanted = {SPLIT: settings.check_splits, VOID: settings.check_voids}
        crossings = [c for c in crossings if wanted[c.kind]]
        progress.info(f"{sum(c.kind == 'split' for c in crossings)} split crossings, "
                      f"{sum(c.kind == 'void' for c in crossings)} voids")
    progress.check_cancel()

    with progress.timed("Checking vias that change reference planes"):
        transitions, stitch_vias = [], []
        if settings.check_via_caps or settings.check_via_stitch:
            transitions, stitch_vias = find_transitions(snap, refs, settings, progress)
        if not settings.check_via_caps:
            transitions = [t for t in transitions if t.kind != VIA_CAP]
        if not settings.check_via_stitch:
            transitions = [t for t in transitions if t.kind != VIA_STITCH]
            stitch_vias = []
        progress.info(f"{sum(t.kind == 'cap' for t in transitions)} need a stitching cap, "
                      f"{sum(t.kind == 'via' for t in transitions)} a return via "
                      f"({len(stitch_vias)} missing)")
    progress.check_cancel()

    with progress.timed("Placing stitching capacitors"):
        existing = find_existing_caps(snap, refs, settings)
        needs = needs_of(crossings, transitions, refs, settings)
        credit_existing(needs, existing, settings)
        caps = place_caps(needs, snap, refs, settings)
        mark_unfixable(crossings)
        progress.info(f"{len(caps)} new stitching capacitors; "
                      f"{sum(1 for e in existing if e.credited)} existing ones credited")
        crowded = sum(1 for c in caps if c.crowded)
        if crowded:
            warnings.append(f"{crowded} capacitor(s) found no free spot within reach and are "
                            "marked at the crossing: place them by hand there.")

    with progress.timed("Heat map"):
        bounds = refs.board.bounds if not refs.board.is_empty else None
        heat = heat_maps(bounds, crossings, transitions, settings)
        theat = track_heat(snap.tracks, heat, refs.by_layer, refs.signal_nets)

    return ScanResult(
        board_name=snap.board_name, settings=settings, layer_names=names,
        plane_nets=refs.plane_nets, signal_nets=refs.signal_nets,
        references={k: list(v) for k, v in refs.by_layer.items()},
        crossings=crossings, transitions=transitions, caps=caps, stitch_vias=stitch_vias,
        existing=existing, heat=heat, track_heat=theat, warnings=warnings,
        kicad_version=snap.kicad_version, candidate_nets=refs.candidate_nets,
        created=time.strftime("%Y-%m-%d %H:%M"),
        seconds=round(time.perf_counter() - t0, 2))

"""Draw the scan's recommendations on the board's User.Comments layer, and take them off.

Each new stitching capacitor gets a circle, a line across the split showing which way its
pads go, and a label ("[CapScan] CS3 100nF 16V"; the nets are in the report). Each missing return via
gets a small circle and its label; each void a cross. All of it goes in in one commit, so
one undo in KiCad takes it all off again.

Nothing else on the board is touched. The markers are found again by their label prefix
and by the ids written to ``<board>.capscan-markers.json`` beside the board, so clearing
them removes only what CapScan drew.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import List, Optional

from capscan.analysis.results import UNFIXABLE, ScanResult

log = logging.getLogger(__name__)

PREFIX = "[CapScan]"
NM = 1_000_000
CAP_RADIUS_MM = 0.7
VIA_RADIUS_MM = 0.35
LINE_MM = 0.1
TEXT_MM = 0.6


def _vec(x_mm: float, y_mm: float):
    from kipy.geometry import Vector2
    return Vector2.from_xy(round(x_mm * NM), round(y_mm * NM))


def _layer():
    from kipy.proto.board.board_types_pb2 import BoardLayer
    return BoardLayer.BL_Cmts_User


def _circle(xy, r):
    from kipy.board_types import BoardCircle
    c = BoardCircle()
    c.layer = _layer()
    c.center = _vec(*xy)
    c.radius_point = _vec(xy[0] + r, xy[1])
    c.attributes.stroke.width = round(LINE_MM * NM)
    return c


def _segment(a, b):
    from kipy.board_types import BoardSegment
    s = BoardSegment()
    s.layer = _layer()
    s.start = _vec(*a)
    s.end = _vec(*b)
    s.attributes.stroke.width = round(LINE_MM * NM)
    return s


def _text(xy, value):
    from kipy.board_types import BoardText
    t = BoardText()
    t.layer = _layer()
    t.position = _vec(*xy)
    t.value = value
    attrs = t.attributes
    attrs.size = _vec(TEXT_MM, TEXT_MM)
    attrs.stroke_width = round(TEXT_MM * 0.15 * NM)
    # Starting at the marker rather than centered on it, so long labels stay on the board side.
    from kipy.proto.common.types.enums_pb2 import HorizontalAlignment
    attrs.horizontal_alignment = HorizontalAlignment.HA_LEFT
    return t


def marker_items(result: ScanResult) -> list:
    items = []
    for cap in result.caps:
        x, y = cap.position
        a = math.radians(cap.angle_deg)
        dx, dy = math.cos(a) * CAP_RADIUS_MM, -math.sin(a) * CAP_RADIUS_MM
        items.append(_circle(cap.position, CAP_RADIUS_MM))
        items.append(_segment((x - dx, y - dy), (x + dx, y + dy)))
        side = "" if cap.side == "top" else " (bottom)"
        items.append(_text((x + CAP_RADIUS_MM + 0.2, y),
                           f"{PREFIX} {cap.id} {cap.amount} {cap.voltage_v:g}V{side}"))
    for sv in result.stitch_vias:
        items.append(_circle(sv.position, VIA_RADIUS_MM))
        items.append(_text((sv.position[0] + VIA_RADIUS_MM + 0.2, sv.position[1]),
                           f"{PREFIX} {sv.id} {sv.net} via"))
    for c in result.voids():
        if c.status != UNFIXABLE:
            continue
        x, y = c.position
        r = 0.5
        items.append(_segment((x - r, y - r), (x + r, y + r)))
        items.append(_segment((x - r, y + r), (x + r, y - r)))
        items.append(_text((x + r + 0.2, y), f"{PREFIX} {c.id} void"))
    return items


def sidecar(board) -> Optional[Path]:
    try:
        name = board.name
        project = board.document.project.path
    except Exception:  # noqa: BLE001
        return None
    if not project or not name:
        return None
    return Path(project) / (Path(name).stem + ".capscan-markers.json")


def place_markers(board, result: ScanResult, replace: bool = True) -> int:
    """Draw ``result``'s markers in one commit; returns how many items were made."""
    if replace:
        clear_markers(board)
    items = marker_items(result)
    if not items:
        return 0
    commit = board.begin_commit()
    try:
        created = board.create_items(items)
    except Exception:
        board.drop_commit(commit)
        raise
    board.push_commit(commit, "CapScan stitching capacitor markers")
    path = sidecar(board)
    if path is not None:
        try:
            path.write_text(json.dumps({"ids": [c.id.value for c in created]}), encoding="utf-8")
        except OSError as e:
            log.warning("Could not record the markers in %s: %s", path, e)
    return len(created)


def clear_markers(board) -> int:
    """Remove every marker CapScan drew; returns how many were removed."""
    ids: List[str] = []
    path = sidecar(board)
    if path is not None and path.is_file():
        try:
            ids = list(json.loads(path.read_text(encoding="utf-8")).get("ids", []))
        except (OSError, ValueError):
            ids = []
    layer = _layer()
    doomed = []
    try:
        for t in board.get_text():
            if getattr(t, "layer", None) == layer and str(getattr(t, "value", "")).startswith(PREFIX):
                doomed.append(t)
    except Exception as e:  # noqa: BLE001 - text listing is best effort
        log.debug("Could not list board text: %s", e)
    known = {t.id.value for t in doomed}
    if ids:
        for s in board.get_shapes():
            if s.layer == layer and s.id.value in ids and s.id.value not in known:
                doomed.append(s)
    if not doomed:
        return 0
    commit = board.begin_commit()
    try:
        board.remove_items(doomed)
    except Exception:
        board.drop_commit(commit)
        raise
    board.push_commit(commit, "Clear CapScan markers")
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return len(doomed)


def select_net(board, net: str) -> bool:
    """Select the net's routing in KiCad (cross-probe); False if this KiCad cannot."""
    try:
        items = [t for t in board.get_tracks() if t.net.name == net]
        items += [v for v in board.get_vias() if v.net.name == net]
        if not items:
            return False
        board.clear_selection()
        board.add_to_selection(items)
        return True
    except Exception as e:  # noqa: BLE001 - cross-probing is a nicety
        log.debug("Could not select %s in KiCad: %s", net, e)
        return False


def select_footprint(board, reference: str) -> bool:
    try:
        fps = [f for f in board.get_footprints() if f.reference_field.text.value == reference]
        if not fps:
            return False
        board.clear_selection()
        board.add_to_selection(fps)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("Could not select %s in KiCad: %s", reference, e)
        return False

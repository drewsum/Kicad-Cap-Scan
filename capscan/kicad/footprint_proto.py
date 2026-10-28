"""A new capacitor's footprint as a KiCad API item, ready for ``CreateItems``.

KiCad reads a footprint's position and turn first, then every pad, graphic and field at the
board position (and angle) the message gives (``FOOTPRINT::Deserialize``). So a footprint
copied from one on the board, or built from a library ``.kicad_mod`` file at the origin,
has each of those moved and turned here. KiCad 10.0's ``ParseAndCreateItemsFromString``,
which would paste footprint text, creates nothing yet.

The new footprint has fresh ids and no schematic link: it is an ordinary part, to be
added to the schematic by the user (and annotated to the reference given here).
"""

from __future__ import annotations

import math
import uuid
from typing import Callable, Dict, Tuple

from capscan.kicad import sexpr

NM = 1_000_000
#: Vector2 fields that are points on the board (the rest are sizes or offsets).
POINTS = {"position", "start", "end", "mid", "center", "radius_point", "top_left",
          "bottom_right", "control1", "control2", "point"}
#: Messages whose ``angle`` is on the board, footprint turn included.
TURNED = {"kiapi.board.types.PadStack", "kiapi.common.types.TextAttributes"}
#: Not moved: a custom pad's shapes are in the pad's own frame.
SKIP = {"custom_shapes", "symbol_path"}


def _message_class(full_name: str):
    from google.protobuf import descriptor_pool, message_factory
    return message_factory.GetMessageClass(
        descriptor_pool.Default().FindMessageTypeByName(full_name))


def _rect_to_polygon(shape) -> None:
    """A rectangle turned off square can only be a polygon in KiCad."""
    r = shape.rectangle
    (x0, y0), (x1, y1) = (r.top_left.x_nm, r.top_left.y_nm), (r.bottom_right.x_nm,
                                                             r.bottom_right.y_nm)
    shape.ClearField("rectangle")
    poly = shape.polygon.polygons.add()
    poly.outline.closed = True
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        node = poly.outline.nodes.add()
        node.point.x_nm, node.point.y_nm = x, y


def _walk(msg, move: Callable, turn: float) -> None:
    from google.protobuf.descriptor import FieldDescriptor
    if msg.DESCRIPTOR.full_name == "kiapi.common.types.GraphicShape" and \
            msg.HasField("rectangle") and turn % 90:
        _rect_to_polygon(msg)
    turned = msg.DESCRIPTOR.full_name in TURNED
    if turned:                  # an angle of 0 is not sent, so not listed below
        msg.angle.value_degrees = (msg.angle.value_degrees + turn) % 360.0
    for fd, value in list(msg.ListFields()):
        if fd.message_type is None or fd.name in SKIP:
            continue
        values = value if fd.label == FieldDescriptor.LABEL_REPEATED else [value]
        for v in values:
            kind = v.DESCRIPTOR.full_name
            if kind == "google.protobuf.Any":
                inner = _message_class(v.TypeName())()
                v.Unpack(inner)
                _walk(inner, move, turn)
                v.Pack(inner)
            elif kind == "kiapi.common.types.Vector2":
                if fd.name in POINTS:
                    v.x_nm, v.y_nm = move(v.x_nm, v.y_nm)
            elif kind == "kiapi.common.types.Angle":
                pass                # the footprint's own is set by the caller
            elif kind == "kiapi.common.types.KIID":
                if fd.name == "id":
                    v.value = str(uuid.uuid4())
                elif fd.name == "parent":
                    msg.ClearField("parent")
            else:
                _walk(v, move, turn)


def placed(src, *, position: Tuple[float, float], angle_deg: float, reference: str,
           value: str, nets: Dict[str, str] = None):
    """A copy of footprint message ``src`` (a ``FootprintInstance``) at ``position`` (mm)
    turned to ``angle_deg`` as KiCad shows it, named ``reference``, valued ``value``, its
    pads on ``nets`` (pad number -> net name; others none)."""
    from kipy.proto.board import board_types_pb2 as bt
    fp = bt.FootprintInstance()
    fp.CopyFrom(src)
    cx, cy = fp.position.x_nm, fp.position.y_nm
    turn = (angle_deg - fp.orientation.value_degrees) % 360.0
    a = math.radians(turn)
    c, s = math.cos(a), math.sin(a)
    tx, ty = round(position[0] * NM), round(position[1] * NM)

    def move(x, y):
        dx, dy = x - cx, y - cy                 # y down: counter-clockwise on screen
        return round(tx + dx * c + dy * s), round(ty - dx * s + dy * c)

    _walk(fp, move, turn)
    fp.position.x_nm, fp.position.y_nm = tx, ty
    fp.orientation.value_degrees = angle_deg % 360.0
    for f in ("symbol_path", "symbol_sheet_name", "symbol_sheet_filename", "parent"):
        if f in fp.DESCRIPTOR.fields_by_name:       # kicad-python 0.7 has no "parent"
            fp.ClearField(f)
    for field in (fp.reference_field, fp.definition.reference_field):
        field.text.text.text = reference
    for field in (fp.value_field, fp.definition.value_field):
        field.text.text.text = value
    nets = nets or {}
    for item in fp.definition.items:
        if item.TypeName() == "kiapi.board.types.Pad":
            pad = bt.Pad()
            item.Unpack(pad)
            pad.ClearField("net")
            pad.ClearField("symbol_pin")
            if pad.number in nets:
                pad.net.name = nets[pad.number]
            item.Pack(pad)
    return fp


def pad_positions(fp) -> Dict[str, Tuple[float, float]]:
    """Each pad's board position (mm) in footprint message ``fp``."""
    from kipy.proto.board import board_types_pb2 as bt
    out = {}
    for item in fp.definition.items:
        if item.TypeName() == "kiapi.board.types.Pad":
            pad = bt.Pad()
            item.Unpack(pad)
            out[pad.number] = (pad.position.x_nm / NM, pad.position.y_nm / NM)
    return out


# ---------------------------------------------------------------------------- library

def _layer_ids() -> Dict[str, int]:
    from kipy.util.board_layer import CANONICAL_LAYER_NAMES
    return {name: lid for lid, name in CANONICAL_LAYER_NAMES.items()}


def _xy(v, node) -> None:
    v.x_nm, v.y_nm = round(float(node[1]) * NM), round(float(node[2]) * NM)


def _text(text, node, layers) -> None:
    """A property or text's position, angle, layer, size and visibility."""
    at = sexpr.child(node, "at")
    if at is not None:
        _xy(text.text.position, at)
        if len(at) > 3 and sexpr.is_number(at[3]):
            text.text.attributes.angle.value_degrees = float(at[3])
    layer = sexpr.child(node, "layer")
    if layer is not None and str(layer[1]) in layers:
        text.layer = layers[str(layer[1])]
    font = next((n for n in sexpr.walk(node) if n[0] == "font"), None)
    size = sexpr.child(font, "size") if font else None
    if size is not None:
        text.text.attributes.size.x_nm = round(float(size[2]) * NM)
        text.text.attributes.size.y_nm = round(float(size[1]) * NM)
    thick = sexpr.child(font, "thickness") if font else None
    if thick is not None:
        text.text.attributes.stroke_width.value_nm = round(float(thick[1]) * NM)
    hide = sexpr.child(node, "hide")
    text.text.attributes.visible = not (hide is not None and (len(hide) < 2 or hide[1] == "yes")
                                        or "hide" in node[3:])


def _stroke(shape, node) -> None:
    w = next((sexpr.child(n, "width") for n in sexpr.children(node, "stroke")), None) \
        or sexpr.child(node, "width")
    if w is not None:
        shape.attributes.stroke.width.value_nm = round(float(w[1]) * NM)


def from_library(node: list, library: str):
    """A ``FootprintInstance`` message at the origin, unturned, built from a footprint's
    ``.kicad_mod`` text: its pads, lines, rectangles, circles, arcs and polygons, its
    reference and value, and its 3D models."""
    from kipy.proto.board import board_types_pb2 as bt
    layers = _layer_ids()
    fp = bt.FootprintInstance()
    fp.layer = bt.BoardLayer.BL_F_Cu
    fp.definition.id.library_nickname = library
    fp.definition.id.entry_name = sexpr.lib_id(node).split(":")[-1]
    attr = sexpr.child(node, "attr")
    if attr is not None:
        fp.attributes.mounting_style = (bt.FootprintMountingStyle.FMS_SMD if "smd" in attr
                                        else bt.FootprintMountingStyle.FMS_THROUGH_HOLE
                                        if "through_hole" in attr
                                        else bt.FootprintMountingStyle.FMS_UNSPECIFIED)
    descr = sexpr.child(node, "descr")
    if descr is not None:
        fp.definition.attributes.description = str(descr[1])
    for prop in sexpr.children(node, "property"):
        field = {"Reference": fp.reference_field, "Value": fp.value_field}.get(str(prop[1]))
        if field is not None:
            field.text.text.text = str(prop[2])
            _text(field.text, prop, layers)
            field.visible = field.text.text.attributes.visible
    shapes = {"rect": bt.PSS_RECTANGLE, "roundrect": bt.PSS_ROUNDRECT, "oval": bt.PSS_OVAL,
              "circle": bt.PSS_CIRCLE, "trapezoid": bt.PSS_TRAPEZOID}
    for p in sexpr.children(node, "pad"):
        pad = bt.Pad()
        pad.id.value = str(uuid.uuid4())
        pad.number = str(p[1])
        pad.type = {"smd": bt.PT_SMD, "thru_hole": bt.PT_PTH, "np_thru_hole": bt.PT_NPTH,
                    "connect": bt.PT_SMD}.get(str(p[2]), bt.PT_SMD)
        at, size = sexpr.child(p, "at"), sexpr.child(p, "size")
        _xy(pad.position, at)
        ps = pad.pad_stack
        ps.type = bt.PST_NORMAL
        if len(at) > 3 and sexpr.is_number(at[3]):
            ps.angle.value_degrees = float(at[3])
        for name in (sexpr.child(p, "layers") or ["layers"])[1:]:
            name = str(name)
            if name.startswith("*."):
                ps.layers.extend(layers[n] for n in (f"F.{name[2:]}", f"B.{name[2:]}")
                                 if n in layers)
            elif name in layers:
                ps.layers.append(layers[name])
        cl = ps.copper_layers.add()
        cl.layer = bt.BoardLayer.BL_F_Cu
        cl.shape = shapes.get(str(p[3]), bt.PSS_RECTANGLE)
        _xy(cl.size, size)
        ratio = sexpr.child(p, "roundrect_rratio")
        if ratio is not None:
            cl.corner_rounding_ratio = float(ratio[1])
        drill = sexpr.child(p, "drill")
        if drill is not None:
            d = [x for x in drill[1:] if sexpr.is_number(x)]
            if d:
                ps.drill.diameter.x_nm = round(float(d[0]) * NM)
                ps.drill.diameter.y_nm = round(float(d[-1]) * NM)
                ps.drill.start_layer, ps.drill.end_layer = bt.BL_F_Cu, bt.BL_B_Cu
        fp.definition.items.add().Pack(pad)
    for kind in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly"):
        for n in sexpr.children(node, kind):
            layer = sexpr.child(n, "layer")
            if layer is None or str(layer[1]) not in layers:
                continue
            s = bt.BoardGraphicShape()
            s.id.value = str(uuid.uuid4())
            s.layer = layers[str(layer[1])]
            g = s.shape
            _stroke(g, n)
            fill = sexpr.child(n, "fill")
            if fill is not None and str(fill[1]) in ("yes", "solid"):
                g.attributes.fill.fill_type = 2      # GFT_FILLED
            if kind == "fp_line":
                _xy(g.segment.start, sexpr.child(n, "start"))
                _xy(g.segment.end, sexpr.child(n, "end"))
            elif kind == "fp_rect":
                _xy(g.rectangle.top_left, sexpr.child(n, "start"))
                _xy(g.rectangle.bottom_right, sexpr.child(n, "end"))
            elif kind == "fp_circle":
                _xy(g.circle.center, sexpr.child(n, "center"))
                _xy(g.circle.radius_point, sexpr.child(n, "end"))
            elif kind == "fp_arc":
                for f in ("start", "mid", "end"):
                    _xy(getattr(g.arc, f), sexpr.child(n, f))
            else:
                poly = g.polygon.polygons.add()
                poly.outline.closed = True
                for xy in sexpr.children(sexpr.child(n, "pts") or ["pts"], "xy"):
                    _xy(poly.outline.nodes.add().point, xy)
            fp.definition.items.add().Pack(s)
    if hasattr(bt, "Footprint3DModel"):
        for m in sexpr.children(node, "model"):
            model = bt.Footprint3DModel()
            model.filename = str(m[1])
            model.visible = True
            for f, key in (("offset", "offset"), ("scale", "scale"), ("rotation", "rotate")):
                xyz = sexpr.child(sexpr.child(m, key) or [key], "xyz")
                if xyz is not None:
                    vec = getattr(model, f)
                    for axis, v in zip(("x", "y", "z"), xyz[1:4]):
                        if axis in vec.DESCRIPTOR.fields_by_name:
                            setattr(vec, axis, float(v))
            fp.definition.items.add().Pack(model)
    return fp

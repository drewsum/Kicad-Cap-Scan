"""The board's design rules, from the project files.

KiCad's API does not give the design rules, so they are read from the ``.kicad_pro`` file
(Board Setup: constraints, predefined via sizes, net classes) and the ``.kicad_dru`` custom
rules beside the board. KiCad writes both when Board Setup is closed with OK.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from capscan.analysis.install import BLIND, BURIED, MICRO, THROUGH, BoardRules, ViaSize
from capscan.kicad import sexpr

#: Custom rule names for each via type in a ``disallow`` constraint.
DISALLOW_NAMES = {"through_via": THROUGH, "blind_via": BLIND, "buried_via": BURIED,
                  "micro_via": MICRO}


def project_files(project_dir: str, board_name: str):
    stem = Path(board_name).stem
    d = Path(project_dir) if project_dir else Path(".")
    return d / f"{stem}.kicad_pro", d / f"{stem}.kicad_dru"


def read_rules(project_dir: str, board_name: str) -> BoardRules:
    pro, dru = project_files(project_dir, board_name)
    rules = BoardRules()
    try:
        data = json.loads(pro.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        rules.notes.append(f"No project file read ({e}): KiCad's default rules assumed")
        data = {}
    apply_project(rules, data)
    if pro.is_file():
        rules.notes.append(f"Design rules from {pro.name}")
    try:
        text = dru.read_text(encoding="utf-8")
    except OSError:
        text = ""
    if text:
        apply_custom_rules(rules, text)
        rules.notes.append(f"Custom rules from {dru.name}")
    return rules


def _get(d: dict, key: str, default: float) -> float:
    try:
        v = float(d.get(key, default))
    except (TypeError, ValueError):
        return default
    return v


def apply_project(rules: BoardRules, data: dict) -> None:
    ds = data.get("board", {}).get("design_settings", {})
    r = ds.get("rules", {})
    rules.hole_clearance_mm = _get(r, "min_hole_clearance", rules.hole_clearance_mm)
    rules.hole_to_hole_mm = _get(r, "min_hole_to_hole", rules.hole_to_hole_mm)
    rules.edge_clearance_mm = _get(r, "min_copper_edge_clearance", rules.edge_clearance_mm)
    rules.min_via_diameter_mm = _get(r, "min_via_diameter", rules.min_via_diameter_mm)
    rules.min_via_drill_mm = _get(r, "min_through_hole_diameter", rules.min_via_drill_mm)
    rules.min_annular_mm = _get(r, "min_via_annular_width", rules.min_annular_mm)
    min_clear = _get(r, "min_clearance", 0.0)
    # KiCad 6 projects still carry the switches; later ones allow every via type.
    if r.get("allow_blind_buried_vias") is False:
        rules.disallowed |= {BLIND, BURIED}
    if r.get("allow_microvias") is False:
        rules.disallowed.add(MICRO)
    sizes = []
    for v in ds.get("via_dimensions", []):
        d, k = _get(v, "diameter", 0.0), _get(v, "drill", 0.0)
        if d > 0 and k > 0:
            sizes.append(ViaSize(d, k))
    for c in data.get("net_settings", {}).get("classes", []):
        name = c.get("name", "")
        clear = max(_get(c, "clearance", rules.clearance_mm), min_clear)
        rules.class_clearance_mm[name] = clear
        if name == "Default":
            rules.clearance_mm = clear
            rules.track_width_mm = _get(c, "track_width", rules.track_width_mm)
            rules.micro_via = ViaSize(_get(c, "microvia_diameter", rules.micro_via.diameter_mm),
                                      _get(c, "microvia_drill", rules.micro_via.drill_mm))
        d, k = _get(c, "via_diameter", 0.0), _get(c, "via_drill", 0.0)
        if d > 0 and k > 0:
            sizes.append(ViaSize(d, k))
    rules.clearance_mm = max(rules.clearance_mm, min_clear)
    rules.via_sizes = list(dict.fromkeys(sizes))


def apply_custom_rules(rules: BoardRules, text: str) -> None:
    """Via types a custom rule disallows everywhere (a rule with a condition is left to
    DRC, which runs before and after anything is added)."""
    body = re.sub(r"#[^\n]*", "", text)
    for m in re.finditer(r"\(rule\s", body):
        try:
            rule = sexpr.parse(body[m.start():])
        except (ValueError, IndexError):
            continue
        if sexpr.child(rule, "condition") is not None:
            continue
        for c in sexpr.children(rule, "constraint"):
            if len(c) > 1 and c[1] == "disallow":
                for name in c[2:]:
                    t = DISALLOW_NAMES.get(str(name))
                    if t:
                        rules.disallowed.add(t)
                    elif str(name) == "via":
                        rules.disallowed |= {BLIND, BURIED, MICRO}


def default_library_dir(kicad_cli: Optional[str]) -> Optional[Path]:
    """KiCad's standard footprint libraries, beside the kicad-cli that came with it."""
    import os
    for var in ("KICAD10_FOOTPRINT_DIR", "KICAD9_FOOTPRINT_DIR"):
        if os.environ.get(var) and Path(os.environ[var]).is_dir():
            return Path(os.environ[var])
    if kicad_cli:
        exe = Path(kicad_cli).resolve()
        for d in (exe.parent.parent / "share" / "kicad" / "footprints",       # Windows
                  exe.parent.parent / "SharedSupport" / "footprints",         # macOS
                  Path("/usr/share/kicad/footprints"), Path("/usr/local/share/kicad/footprints")):
            if d.is_dir():
                return d
    return None

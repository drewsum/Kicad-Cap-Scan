"""The scan report, as one self-contained HTML file or a PDF.

Built the way KiPI builds its reports: f-strings with every value escaped, one inline
stylesheet, the 2D maps as base64 PNGs (matplotlib) and the 3D heat map as an embedded
Plotly figure, so the file opens offline. The PDF is the same report printed by Qt
(:mod:`capscan.report.pdf`), static, with the 3D page attached.
"""

from __future__ import annotations

import html as _html
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from capscan import __version__
from capscan.analysis.results import (
    EXISTING, NEW_CAP, SPLIT, UNFIXABLE, VIA_CAP, VIA_STITCH, ScanResult,
)
from capscan.analysis.rules import DESCRIPTIONS
from capscan.analysis.values import part_basis
from capscan.model.board import BoardSnapshot
from capscan.model.stackup import summary as stackup_summary
from capscan.report.maps import LIGHT, MapData, map_png
from capscan.report.scene3d import build_scene

CSS = """
body { font-family: Segoe UI, Helvetica, Arial, sans-serif; margin: 24px auto; max-width: 1180px;
       color: #222; background: #fafafa; padding: 0 16px; }
h1 { margin-bottom: 0; } h2 { border-bottom: 2px solid #ddd; padding-bottom: 4px; margin-top: 36px; }
h3 { margin-top: 26px; }
.meta { color: #666; margin-top: 4px; }
table { border-collapse: collapse; margin: 8px 0 16px; font-size: 13px; background: white; }
th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: right; }
th { background: #f0f0f0; } td:first-child, th:first-child { text-align: left; }
td.l, th.l { text-align: left; }
.pass { color: #1a8f2c; font-weight: bold; } .fail { color: #c62828; font-weight: bold; }
.unjudged { color: #b26a00; font-weight: bold; }
.maps { display: flex; flex-wrap: wrap; gap: 12px; }
.maps figure { margin: 0; background: white; border: 1px solid #ddd; padding: 6px; }
.maps img { width: 1000px; max-width: 100%; }
.note { color: #666; font-size: 12px; }
nav a { margin-right: 12px; }
.tiles { display: flex; flex-wrap: wrap; gap: 10px; margin: 12px 0; }
.tile { background: white; border: 1px solid #ddd; padding: 8px 14px; min-width: 120px; }
.tile b { display: block; font-size: 22px; }
details > summary { cursor: pointer; margin: 6px 0; }
thead th { cursor: pointer; user-select: none; }
thead th[data-sort=asc]::after { content: " \\25B2"; } thead th[data-sort=desc]::after { content: " \\25BC"; }
.table-wrap { overflow-x: auto; }
"""

# Click a column heading to sort by it.
SORT_JS = """
document.querySelectorAll('table.sortable thead th').forEach(function (th, col) {
  th.addEventListener('click', function () {
    var table = th.closest('table'), body = table.tBodies[0];
    var dir = th.dataset.sort === 'asc' ? 'desc' : 'asc';
    table.querySelectorAll('thead th').forEach(function (h) { delete h.dataset.sort; });
    th.dataset.sort = dir;
    var rows = Array.prototype.slice.call(body.rows);
    var key = function (r) { var t = r.cells[col].innerText.trim();
      var n = parseFloat(t.replace(/[^0-9.+-eE]/g, '')); return isNaN(n) ? t : n; };
    rows.sort(function (a, b) { var x = key(a), y = key(b);
      var c = (typeof x === 'number' && typeof y === 'number') ? x - y : String(x).localeCompare(String(y));
      return dir === 'asc' ? c : -c; });
    rows.forEach(function (r) { body.appendChild(r); });
  });
});
"""

#: The 3D page attached to a PDF report.
VIEWS_SUFFIX = "-3d.html"


def _e(x) -> str:
    return _html.escape(str(x), quote=True)


def _mm(v: float) -> str:
    return f"{v:.2f}"


def _table(head: Sequence[str], rows: Iterable[Sequence], left: Sequence[int] = (0,),
           cls: str = "sortable") -> str:
    rows = list(rows)
    if not rows:
        return "<p class='note'>None.</p>"
    th = "".join(f"<th class='l'>{_e(h)}</th>" if i in left else f"<th>{_e(h)}</th>"
                 for i, h in enumerate(head))
    body = []
    for r in rows:
        tds = []
        for i, v in enumerate(r):
            raw = v if isinstance(v, _Raw) else _e(v)
            tds.append(f"<td class='l'>{raw}</td>" if i in left else f"<td>{raw}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return (f"<div class='table-wrap'><table class='{cls}'><thead><tr>{th}</tr></thead><tbody>"
            + "".join(body) + "</tbody></table></div>")


class _Raw(str):
    """Table cell HTML, not escaped."""


def _status(s: str) -> _Raw:
    cls = {NEW_CAP: "unjudged", EXISTING: "pass", UNFIXABLE: "fail",
           "new via": "unjudged", "existing via": "pass"}.get(s, "")
    return _Raw(f"<span class='{cls}'>{_e(s)}</span>")


def _tiles(r: ScanResult) -> str:
    c = r.counts()
    items = [("Stitching caps needed", c["caps"]), ("Split crossings", c["splits"]),
             ("Vias changing plane", c["via_changes"]), ("Return vias needed", c["stitch_vias"]),
             ("Voids (reroute)", c["voids"]),
             ("Returns via decoupling", c["decoupling_flags"]),
             ("Existing caps credited", c["existing_credited"]),
             ("Decoupling caps found", c["existing_decoupling"])]
    return "<div class='tiles'>" + "".join(
        f"<div class='tile'><b>{v}</b>{_e(k)}</div>" for k, v in items) + "</div>"


def _cap_rows(r: ScanResult):
    for c in r.caps:
        yield (c.id, c.count, c.value, c.dielectric, c.package, f"{c.voltage_v:g} V", c.net_a,
               c.net_b, _mm(c.position[0]), _mm(c.position[1]), c.side, f"{c.angle_deg:g}°",
               ", ".join(r.layer_name(l) for l in c.ref_layers), len(c.signals),
               ", ".join(c.signals), c.note)


def _crossing_rows(r: ScanResult):
    for c in r.crossings:
        yield (c.id, c.kind, c.net, r.layer_name(c.layer), r.layer_name(c.ref_layer),
               c.net_a or "(none)", c.net_b or "(none)", _mm(c.position[0]), _mm(c.position[1]),
               _mm(c.gap_mm), f"{c.h_mm:.3f}", f"{c.severity:g}", _status(c.status),
               c.covered_by, ", ".join(c.decoupling))


def _transition_rows(r: ScanResult):
    for t in r.via_changes():
        need = ("stitching cap" if t.kind == VIA_CAP else "return via" if t.kind == VIA_STITCH
                else "")
        refs = lambda e: ", ".join(f"{n} ({r.layer_name(l)})" for l, n in e) or "(none)"
        yield (t.id, t.net, r.layer_name(t.from_layer), r.layer_name(t.to_layer), refs(t.from_refs),
               refs(t.to_refs), _mm(t.position[0]), _mm(t.position[1]), need,
               _status(t.status), t.covered_by, ", ".join(t.decoupling))


SIGNAL_HEAD = ["Signal", "Edge ps", "Z0 Ω", "Routing layers", "Reference layers",
               "Split crossings", "Plane-changing vias", "Voids", "Returned by"]


def _signal_rows(r: ScanResult):
    """Every signal that loses its return path, with the layers involved."""
    by: dict = {}
    for c in r.crossings:
        e = by.setdefault(c.net, {"layers": set(), "refs": set(), "splits": 0, "voids": 0,
                                  "vias": 0, "caps": set()})
        e["layers"].add(r.layer_name(c.layer))
        e["refs"].add(r.layer_name(c.ref_layer))
        e["splits" if c.kind == SPLIT else "voids"] += 1
        if c.covered_by:
            e["caps"].add(c.covered_by)
    for t in r.via_changes():
        e = by.setdefault(t.net, {"layers": set(), "refs": set(), "splits": 0, "voids": 0,
                                  "vias": 0, "caps": set()})
        e["layers"].update({r.layer_name(t.from_layer), r.layer_name(t.to_layer)})
        e["refs"].update(r.layer_name(l) for l in t.ref_layers if l >= 0)
        e["vias"] += 1
        if t.covered_by:
            e["caps"].add(t.covered_by)
    for net in sorted(by):
        e = by[net]
        yield (net, f"{r.settings.edge_rate_of(net):g}", f"{r.settings.impedance_of(net):g}",
               ", ".join(sorted(e["layers"])), ", ".join(sorted(e["refs"])), e["splits"],
               e["vias"], e["voids"], ", ".join(sorted(e["caps"])))


def _existing_rows(r: ScanResult):
    """Every capacitor between two planes, the ones a scan used first, then decoupling ones
    before stitching ones: one no crossing reached still shows it was found."""
    used = sorted(r.existing, key=lambda e: (not (e.credited or e.flagged),
                                             e.role != "decoupling", e.reference))
    for e in used:
        yield (e.reference, e.value, e.net_a, e.net_b, _mm(e.position[0]), _mm(e.position[1]),
               e.side, e.role + (f" (next to {e.near_ic})" if e.near_ic else ""),
               ", ".join(e.credited), ", ".join(e.flagged))


def _stackup_rows(snap: BoardSnapshot, r: ScanResult):
    from capscan.model.board import CopperLayer
    from capscan.model.stackup import stack_order
    for e in stack_order(snap.stackup):
        if isinstance(e, CopperLayer):
            refs = r.references.get(e.id, [])
            role = ("reference plane" if any(e.id == x for rl in r.references.values()
                                             for x, _ in rl) else "signal")
            ret = ", ".join(f"{r.layer_name(l)} (h {h:.3f} mm)" for l, h in refs)
            yield (e.name, "copper", f"{e.thickness_mm * 1000:.1f} µm", "", "", role, ret)
        else:
            yield ("", "dielectric", f"{e.thickness_mm * 1000:.0f} µm", e.material,
                   f"{e.epsilon_r:g}" if e.epsilon_r else "", "", "")


def _settings_rows(r: ScanResult):
    d = r.settings.to_dict()
    for k, label in DESCRIPTIONS.items():
        yield (label, d.get(k, ""))


def plotly_3d(snap: BoardSnapshot, r: ScanResult, data: Optional[MapData] = None) -> str:
    """The 3D heat map as a Plotly div (with Plotly's JS inlined once by the caller)."""
    import numpy as np
    import plotly.graph_objects as go

    data = data or MapData(snap, r)
    sc = build_scene(data)
    fig = go.Figure()
    for ring in sc.outline:
        for z in (0.0, sc.thickness):
            fig.add_trace(go.Scatter3d(x=ring[:, 0], y=ring[:, 1], z=[z] * len(ring),
                                       mode="lines", line=dict(color="#666", width=2),
                                       hoverinfo="skip", showlegend=False))
    for i, sh in enumerate(sc.sheets):
        z = np.full(sh.values.shape, sh.z)
        fig.add_trace(go.Surface(
            x=sh.x, y=sh.y, z=z, surfacecolor=sh.values, cmin=0, cmax=1, colorscale="Inferno",
            opacity=0.85, name=f"Heat {sh.name}", showscale=i == 0,
            colorbar=dict(title="Crossing<br>density"),
            hovertemplate=f"{_e(sh.name)}<br>x %{{x:.1f}} y %{{y:.1f}}<br>heat %{{surfacecolor:.2f}}"
                          "<extra></extra>"))
    from capscan.report.maps import NET_COLORS
    nets = sorted({n for _, n in sc.plane_edges})
    for (layer, net), rings in sc.plane_edges.items():
        xs, ys, zs = [], [], []
        for ring in rings:
            xs += list(ring[:, 0]) + [None]
            ys += list(ring[:, 1]) + [None]
            zs += list(ring[:, 2]) + [None]
        fig.add_trace(go.Scatter3d(x=xs, y=ys, z=zs, mode="lines",
                                   line=dict(color=NET_COLORS[nets.index(net) % len(NET_COLORS)],
                                             width=3),
                                   name=f"{net} ({r.layer_name(layer)})", hoverinfo="name"))
    if sc.hot_tracks:
        xs, ys, zs, cs = [], [], [], []
        for seg, h in sc.hot_tracks:
            xs += [seg[0, 0], seg[1, 0], None]
            ys += [seg[0, 1], seg[1, 1], None]
            zs += [seg[0, 2], seg[1, 2], None]
            cs += [h, h, h]
        fig.add_trace(go.Scatter3d(x=xs, y=ys, z=zs, mode="lines", name="Signals near splits",
                                   line=dict(color=cs, colorscale="Inferno", cmin=0, cmax=1,
                                             width=5), hoverinfo="name"))
    if sc.caps:
        pts = np.array([c[1] for c in sc.caps])
        fig.add_trace(go.Scatter3d(x=pts[:, 0], y=pts[:, 1], z=pts[:, 2],
                                   mode="markers+text", text=[c[0] for c in sc.caps],
                                   hovertext=[c[3] for c in sc.caps], hoverinfo="text",
                                   textfont=dict(color="#0090c0"), name="Stitching caps",
                                   marker=dict(size=5, symbol="square", color="#30e0ff",
                                               line=dict(color="#003040", width=1))))
    if sc.vias:
        pts = np.array([v[1] for v in sc.vias])
        fig.add_trace(go.Scatter3d(x=pts[:, 0], y=pts[:, 1], z=pts[:, 2], mode="markers",
                                   text=[v[0] for v in sc.vias], hoverinfo="text",
                                   name="Return vias", marker=dict(size=4, color="#8040c0")))
    fig.update_layout(
        height=640, margin=dict(l=0, r=0, t=30, b=0), paper_bgcolor="#1e1f24",
        font=dict(color="#ccc"), legend=dict(bgcolor="rgba(0,0,0,0)"),
        scene=dict(aspectmode="data", bgcolor="#2a2c33",
                   xaxis=dict(title="x (mm)"), yaxis=dict(title="y (mm)"),
                   zaxis=dict(title=f"z (×{sc.zscale:g})")))
    return fig.to_html(full_html=False, include_plotlyjs=False)


def report_html(snap: BoardSnapshot, r: ScanResult, pdf: bool = False,
                views_link: str = "") -> str:
    data = MapData(snap, r)
    title = f"CapScan report: {r.board_name}"
    meta = (f"Scanned {_e(r.created)} with CapScan {__version__}"
            + (f", KiCad {_e(r.kicad_version)}" if r.kicad_version else "")
            + f". Signals taken as fast digital: {r.settings.edge_rate_ps:g} ps edges "
            f"(knee {r.settings.knee_ghz:.1f} GHz) on {r.settings.trace_impedance_ohm:g} Ω "
            "unless set per net.")
    out: List[str] = [f"<h1>{_e(title)}</h1><div class='meta'>{meta}</div>"]
    if not pdf:
        out.append("<nav><a href='#summary'>Summary</a><a href='#caps'>Stitching caps</a>"
                   "<a href='#signals'>Signals</a><a href='#crossings'>Crossings</a>"
                   "<a href='#vias'>Vias</a><a href='#existing'>Existing caps</a>"
                   "<a href='#maps'>Maps</a><a href='#3d'>3D heat map</a>"
                   "<a href='#method'>Method</a></nav>")
    out.append("<h2 id='summary'>Summary</h2>")
    out.append(_tiles(r))
    c = r.counts()
    verdict = ("No signal crosses a plane split or changes onto another plane without a "
               "return path." if not (c["caps"] or c["voids"] or c["stitch_vias"]) else
               f"Add {c['caps']} stitching capacitor(s) and {c['stitch_vias']} return via(s) "
               "at the places below, and reroute or fill under the voids, so every signal's "
               "return current has a path next to it.")
    out.append(f"<p>{_e(verdict)}</p>")
    if c["decoupling_flags"]:
        out.append(f"<p class='fail'>{c['decoupling_flags']} crossing(s) would otherwise return "
                   "their current through decoupling capacitors (and so through an IC's "
                   "supply); each has a dedicated stitching capacitor of its own below.</p>")
    for w in r.warnings:
        out.append(f"<p class='unjudged'>{_e(w)}</p>")
    out.append(f"<p class='note'>Plane nets: {_e(', '.join(r.plane_nets) or 'none')}. "
               f"{len(r.signal_nets)} signal nets scanned. Stackup: "
               f"{_e(stackup_summary(snap.stackup))}.</p>")
    out.append(_table(["Layer", "Type", "Thickness", "Material", "εr", "Role", "Returns on"],
                      _stackup_rows(snap, r), left=(0, 1, 3, 5, 6), cls=""))

    out.append("<h2 id='caps'>Stitching capacitors to add</h2>")
    out.append("<p class='note'>Place each between the two nets, pads across the split, with "
               "a via from each pad straight down to its plane. X / Y are KiCad board "
               "coordinates in mm; angle is the footprint rotation. "
               f"{_e(part_basis(r.settings))}</p>")
    out.append(_table(["Cap", "Qty", "Value", "Dielectric", "Package", "Rating", "Net A",
                       "Net B", "X", "Y", "Side", "Angle", "Planes on", "Signals",
                       "Signal nets", "Note"], _cap_rows(r),
                      left=(0, 2, 3, 4, 6, 7, 10, 12, 14, 15)))
    if r.stitch_vias:
        out.append("<h3>Return vias to add</h3>")
        out.append(_table(["Via", "Net", "X", "Y", "Between", "For"],
                          [(v.id, v.net, _mm(v.position[0]), _mm(v.position[1]),
                            " – ".join(r.layer_name(l) for l in v.layers), ", ".join(v.serves))
                           for v in r.stitch_vias], left=(0, 1, 4, 5)))

    out.append("<h2 id='signals'>Signals crossing plane splits</h2>")
    out.append(_table(SIGNAL_HEAD, _signal_rows(r), left=(0, 3, 4, 8)))
    out.append("<h2 id='crossings'>Every crossing</h2>")
    rows = list(_crossing_rows(r))
    head = ["Id", "Kind", "Signal", "Layer", "Reference", "Net before", "Net after", "X", "Y",
            "Gap mm", "h mm", "Severity", "Fix", "By", "Decoupling caps"]
    out.append(_table(head, rows, left=(0, 1, 2, 3, 4, 5, 6, 12, 13, 14)) if not pdf or
               len(rows) < 400 else
               f"<p class='note'>{len(rows)} crossings: see the HTML report for the full list."
               "</p>")
    out.append("<h2 id='vias'>Vias changing reference plane</h2>")
    out.append(_table(["Id", "Signal", "From", "To", "Reference from", "Reference to", "X", "Y",
                       "Needs", "Fix", "By", "Decoupling caps"], _transition_rows(r),
                      left=(0, 1, 2, 3, 4, 5, 8, 9, 10, 11)))
    out.append("<h2 id='existing'>Existing capacitors between planes</h2>")
    out.append("<p class='note'>Every capacitor between two plane nets. One with a pad next to an "
               "IC pin on the same net is decoupling that IC and is never counted as a return "
               "path; the others are stitching capacitors, credited to the crossings within "
               "reach.</p>")
    out.append(_table(["Ref", "Value", "Net A", "Net B", "X", "Y", "Side", "Role", "Credited for",
                       "Flagged for"], _existing_rows(r), left=(0, 1, 2, 3, 6, 7, 8, 9)))

    out.append("<h2 id='maps'>Maps</h2>")
    out.append("<div class='maps'>")
    for layer in data.reference_layers():
        png = map_png(data, layer, LIGHT)
        out.append(f"<figure><img src='data:image/png;base64,{png}' "
                   f"alt='{_e(r.layer_name(layer))}' width='{900 if pdf else 1000}'>"
                   f"<figcaption class='note'>{_e(r.layer_name(layer))}: heat is the density "
                   "of return-path breaks: splits, voids, and vias that need a cap or a "
                   "return via.</figcaption></figure>")
    out.append("</div>")
    out.append("<h2 id='3d'>3D heat map</h2>")
    if pdf:
        out.append("<p>The interactive 3D heat map is attached to this PDF: "
                   f"{views_link}</p>" if views_link else
                   "<p class='note'>The interactive 3D heat map is in the HTML report.</p>")
    else:
        out.append(plotly_3d(snap, r, data))

    out.append("<h2 id='method'>Method and settings</h2>")
    out.append("<p class='note'>Each signal's routing is walked over the nearest plane layer "
               "above and below it. A gap between two plane nets under a trace is a split: a "
               "stitching capacitor between the two nets within reach gives its return "
               "current a way across. A via moving a signal onto a plane of another net needs "
               "one next to the via; onto another plane of the same net, a return via. Over no "
               "plane at all, nothing but rerouting or filling helps. Capacitors are shared by "
               "at most the set number of signals, and put on the board side nearer both "
               "planes, clear of other parts.</p>")
    out.append(_table(["Setting", "Value"], _settings_rows(r), cls=""))
    if pdf:
        from capscan.report.pdf import NOTES_MARKER
        out.append("<h2 id='user-notes'>User notes</h2>"
                   f"<table class='notes'><tr><td>{NOTES_MARKER}</td></tr></table>")
    body = "\n".join(out)
    if pdf:
        return (f"<html><head><meta charset='utf-8'><title>{_e(title)}</title></head>"
                f"<body>{body}</body></html>")
    import plotly.offline
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(title)}</title><style>{CSS}</style>"
            f"<script>{plotly.offline.get_plotlyjs()}</script></head><body>{body}"
            f"<script>{SORT_JS}</script></body></html>")


def views_html(snap: BoardSnapshot, r: ScanResult) -> str:
    import plotly.offline
    title = f"CapScan 3D heat map: {r.board_name}"
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{_e(title)}</title>"
            f"<style>{CSS}</style><script>{plotly.offline.get_plotlyjs()}</script></head>"
            f"<body><h1>{_e(title)}</h1>{plotly_3d(snap, r)}</body></html>")


def write_html(snap: BoardSnapshot, r: ScanResult, path) -> Path:
    path = Path(path)
    path.write_text(report_html(snap, r), encoding="utf-8")
    return path


def write_pdf(snap: BoardSnapshot, r: ScanResult, path) -> Path:
    """Needs the Qt application (fonts); see :func:`capscan.report.pdf.write_pdf`."""
    from capscan.report.pdf import file_link, write_pdf as print_pdf
    path = Path(path)
    name = path.stem + VIEWS_SUFFIX
    link = f"<a href='{_e(file_link(name))}'>{_e(name)}</a>"
    html = report_html(snap, r, pdf=True, views_link=link)
    return print_pdf(html, path, title=f"CapScan report: {r.board_name}",
                     attachments=[(name, views_html(snap, r).encode("utf-8"),
                                   "Interactive 3D heat map")])

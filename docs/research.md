# Why CapScan does what it does

## Return current and reference planes

At high frequency, the return current of a signal flows in the plane next to the trace,
right under it. That is the path of least inductance: the current spreads only about ±3h
to each side, where h is the dielectric height. When that plane is interrupted, the return
current has to detour round the interruption. The detour does several kinds of damage:

* it adds loop inductance and an impedance discontinuity, which shows up as reflections and
  slower edges;
* it couples signals that share the detour, causing crosstalk;
* it drives the slot between the planes as an antenna, causing EMI.

The usual cure, short of rerouting, is a path for the return current at the break:

* **Across a split between two plane nets**, a *stitching capacitor* between the two nets,
  as close to the crossing as possible. Its two pads go one each side of the split, and each
  has a via straight to its plane.
* **At a via that moves the signal onto a plane of another net**, a stitching capacitor
  between the two planes next to the via.
* **At a via that moves the signal onto another plane of the same net**, a return
  (stitching) via of that net next to the signal via.
* **Over no plane at all**, nothing local helps. Reroute, or fill the plane.

## The defaults, and where they come from

| Setting | Default | Source / reasoning |
|---|---|---|
| Nets scanned | Those picked in the window, or all signal nets (off by default) | Scanning everything on a large board gives a long list; pick the interfaces that matter |
| Edge rate | 100 ps (knee about 3.5 GHz) | CapScan assumes every signal is fast digital, so as to need no setup about signals |
| Cap distance from a split crossing | 2.5 mm (100 mil) | Riazi's split-plane simulations (PCD&M): a 0.1 µF capacitor within 100 mil of the crossing works; at 500 mil it is ineffective |
| Cap distance from a via | 1.27 mm (50 mil) | Guidance is "as close to the transition via as possible", tighter for faster edges; 50 mil is a tight figure that can still be reached |
| Return-via distance | 1.27 mm | TI's high-speed interface guidance gives 200 mil for some interfaces; tightened here to overdesign |
| Signals per cap | 2 | A mounted 0402 has about 1 nH of loop inductance; sharing one among many switching signals defeats it |
| Value | 100 nF X7R 0402 | TI recommends 1 µF or below; common practice is 1 nF to 100 nF. At these edge rates the package's inductance matters more than the value |
| Voltage rating | ≥ 2 × the voltage between the nets, at least 16 V; 50 V when a name gives no voltage | Derating of class II ceramics under DC bias |
| Decoupling test | Within 3 mm of an IC pin on either net | Return current through such a capacitor flows through the IC's supply pin and bond wires |
| Split vs void | A gap up to 2 mm between two nets is a split; any longer stretch over no plane is a void | The capacitor's vias must reach both planes |
| Ignored holes | Holes in one net's plane shorter than 1 mm under the trace | Via antipads and thermal-relief spokes: a small detour |
| Pad clearances | A hole round a through-hole pad or via of the trace's own net, where the trace ends or runs through it, reaching up to 1 mm past the pad | The signal leaves the layer through that pad; the hole is its clearance, not a break in the return path |
| Plane net | A zone fill of 2 % of the board (or 25 mm²), or a supply / ground name with any fill | So that pours count but small copper islands do not |
| Supply by its copper | Zone fills on 2 or more layers, or an array of 4 or more of its vias each within 1.5 mm of the next; either can be turned off (0) | A rail the schematic left unnamed ("Net-(U2-OUT)" on a load switch or regulator output) is still poured, and carries its current through via arrays. A signal is routed, not poured, and changes layer through one via at a time. Taken as a signal, such a rail's own via array cuts a slot in the ground plane under its own trace, which reads as a void |
| Reference layer | Plane nets cover 20 % of it | So that an outer layer with a little ground pour stays a signal layer |

## Turning checks off

Each kind of break can be switched off in the Scan panel:
* `check_splits`: traces over plane splits;
* `check_via_caps`: vias onto another net's plane;
* `check_via_stitch`: vias onto another plane of the same net;
* `check_voids`: traces over voids.

A check that is off is not looked for. It adds no capacitor, return via, table row or heat,
and the summary and reports name it under "Not checked", so a partial scan never reads as
a clean board. All four are on by default.

## Severity, and the heat map

* A **microstrip** has one reference plane, so a split in it is a full break: severity 1.
* A **stripline** with its other plane solid at the crossing keeps half its return path:
  severity 0.5. A capacitor is recommended anyway. A void in such a stripline's broken
  plane is listed as "other plane", not "reroute".

The heat map puts a Gaussian of severity weight on each break, on the reference layer it is
on, with a spread equal to the capacitor reach. Hot areas are where many signals lose their
return path close together, which is where crosstalk and EMI are worst. Every kind of break
adds heat:

* a split crossing, and a void (but not one a stripline's other, solid plane covers);
* a via onto another net's plane, on both of its reference layers;
* a via onto another plane of the same net that has no return via within reach, on both
  planes. One that already has its return via does not count: its return path is whole.

Each kind is kept on its own map. The window's Heat tick boxes pick which ones are added up,
and the sum is scaled by its hottest spot, so a board whose only trouble is missing return
vias still shows where they cluster.

## What CapScan does not do

* It does not simulate. The rules are deliberately conservative stand-ins for knowing each
  interface's edge rate and topology.
* It does not check traces running parallel to a split edge, or the plane-to-plane
  capacitance a stackup provides.
* It does not place footprints. Markers on User.Comments show the spots; add the parts in
  the schematic.

## Sources

* A. Riazi, *Effects of Plane Splits on High-Speed Signals*, PCD&M.
  https://pcdandf.com/pcdesign/index.php/2007-archive-articles/2377-effects-of-plane-splits-on-high-speed-signals-parts-1-and-2
* Texas Instruments, SPRAAR7J, *High-Speed Interface Layout Guidelines*: stitching
  capacitors of 1 µF or less, as close to the crossing as possible.
  https://www.ti.com/document-viewer/lit/html/SPRAAR7J/GUID-AD6D2D54-84AC-47F5-A2ED-87883A148A25
* JLCPCB, *Mastering Split Planes*. https://jlcpcb.com/blog/mastering-split-planes-pcb
* JLCPCB, *PCB Signal Transitions*. https://jlcpcb.com/blog/pcb-signal-transitions

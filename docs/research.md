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
| Edge rate | 100 ps (knee about 3.5 GHz), or set per net | CapScan takes every signal as fast digital, so as to need no setup about signals; an interface known to be slower or faster can be given its own |
| Trace impedance | 50 Ω, or set per net | 50 Ω single-ended, which is also each half of a 90–100 Ω differential pair (USB, PCIe, Ethernet, LVDS) |
| Cap distance from a split crossing | 2.5 mm (100 mil) | Riazi's split-plane simulations (PCD&M): a 0.1 µF capacitor within 100 mil of the crossing works; at 500 mil it is ineffective |
| Cap distance from a via | 1.27 mm (50 mil) | Guidance is "as close to the transition via as possible", tighter for faster edges; 50 mil is a tight figure that can still be reached |
| Return-via distance | 1.27 mm | TI's high-speed interface guidance gives 200 mil for some interfaces; tightened here to overdesign |
| Signals per cap | 2 | A mounted 0402 has about 1 nH of loop inductance; sharing one among many switching signals defeats it |
| Package | Picked from the edge rate: the largest of 0402 / 0603 / 0805 whose mounted inductance slows an edge by at most ¼ of its rise time (0402 at 100 ps), or several 0402s in parallel | See "Value and package" below |
| Value | Largest X7R the package stocks at the rating, up to 100 nF (1 nF for an 0402 at 100 V), never under 0.4 nF | See "Value and package" below. TI recommends 1 µF or below |
| Voltage rating | ≥ 2 × the voltage between the nets, at least 16 V; 50 V when a name gives no voltage | Derating of class II ceramics under DC bias |
| Decoupling test | A pad within 3 mm of an IC pin on the same net (pad to pin, not from the cap's centre, so a large cap beside its pin still counts) | Return current through such a capacitor flows through the IC's supply pin and bond wires |
| Split vs void | A gap up to 2 mm between two nets is a split; any longer stretch over no plane is a void | The capacitor's vias must reach both planes |
| Ignored holes | Holes in one net's plane shorter than 1 mm under the trace | Via antipads and thermal-relief spokes: a small detour |
| Pad clearances | A hole round a through-hole pad or via of the trace's own net, where the trace ends or runs through it, reaching up to 1 mm past the pad | The signal leaves the layer through that pad; the hole is its clearance, not a break in the return path |
| Plane net | A zone fill of 2 % of the board (or 25 mm²), or a supply / ground name with any fill | So that pours count but small copper islands do not |
| Supply by its copper | Zone fills on 2 or more layers, or an array of 4 or more of its vias each within 1.5 mm of the next; either can be turned off (0) | A rail the schematic left unnamed ("Net-(U2-OUT)" on a load switch or regulator output) is still poured, and carries its current through via arrays. A signal is routed, not poured, and changes layer through one via at a time. Taken as a signal, such a rail's own via array cuts a slot in the ground plane under its own trace, which reads as a void |
| Reference layer | Plane nets cover 20 % of it | So that an outer layer with a little ground pour stays a signal layer |

## Value and package

A stitching capacitor sits in series with the return current where the plane breaks. To the
signal it is a series discontinuity: the capacitance in series with the inductance of the
part, its pads and its vias (its *mounted inductance*). These govern opposite ends of the
spectrum, so CapScan picks them separately. Nobody has to set either.

**The package comes from the edge rate.** Above its self-resonance (a few MHz to about
100 MHz for 1 nF to 100 nF), a capacitor is just its mounted inductance, and at the
frequencies in a fast edge the value no longer matters. A series inductance *L* in a line of
impedance Z0 is an L/R filter with τ = L / 2Z0, which adds a 10–90 % rise time of
2.2τ = 1.1·L/Z0. Bogatin's rule for the capacitive case (Rule of Thumb #23) is that a
discontinuity starts to matter once its filter rise time reaches half the signal's. CapScan
allows a quarter (`cap_rise_time_budget`), which slows an edge by about 3 %:

    L_max = 0.25 · t_r · Z0 / 1.1        (1.14 nH for 100 ps on 50 Ω)

A well-mounted 0603 has at least 1–2 nH of connection inductance, about 1 nH of it from its
vias (LearnEMC). CapScan's estimates, for one via per pad to the planes next to it, are
0402 1.0 nH, 0603 1.2 nH and 0805 1.5 nH (`PACKAGES` in `rules.py`). It takes the largest
package within L_max, which is easiest to assemble and has the widest range of values. When even an 0402 is
over budget it asks for N = ⌈L / L_max⌉ of them in parallel, each with its own vias:

| Edge rate | L_max | Part |
|---|---|---|
| 50 ps | 0.57 nH | 2 × 0402 |
| 100 ps (default) | 1.14 nH | 0402 |
| 120 ps | 1.36 nH | 0603 |
| 150 ps and slower | 1.7 nH and up | 0805 |

The range stops at 0402 because smaller parts are hard to place by hand or at most assembly
houses, and parallel 0402s do the same job. It stops at 0805 because an 0805 already stocks
100 nF at 100 V, so a larger body would only add inductance.

**The value comes from the low end, and barely depends on speed.** Below self-resonance the
cap's 1/ωC is in parallel with the detour round the split (inductance L_d), and the two form
a parallel resonance whose impedance scales as √(L_d / C). Keeping that under a return-path
budget Z_t gives C ≥ L_d / Z_t². Take Z_t as 10 % of Z0 (5 Ω, about a 5 % reflection) and a
generous 10 nH detour (about an inch at Bogatin's 10 nH/inch, Rule of Thumb #7). That
gives C ≥ 0.4 nF. In the same package a larger value costs nothing at high frequency and
adds margin at low frequency, so CapScan takes the largest of 100 nF / 10 nF / 1 nF that the
package stocks at the voltage rating. X7R availability was spot-checked on DigiKey:

| | up to 50 V | 100 V |
|---|---|---|
| 0402 | 100 nF (GRM155R71H104KE14) | 1 nF |
| 0603, 0805 | 100 nF | 100 nF (GRM188R72A104KA35) |

If the package picked for inductance cannot reach C_min at the rating, the next larger one
is used and the capacitor's note says why. `cap_value` sets a value by hand; the package is
picked either way.

Each net can have its own edge rate and impedance (the net list's *Edge ps* and *Z0 Ω*;
the defaults are 100 ps and 50 Ω). A cap shared by several signals takes the tightest
budget among them, so its fastest signal picks the package, and the lowest impedance among
them sets C_min.

So signal speed picks the package and the count; the value follows from the rating and
comes out at 100 nF on nearly every board.

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
* It does not add the parts to the schematic. *Add to board* puts them on the board as
  ordinary footprints, and they are added to the schematic by hand.

## Adding the parts

* **Rules.** KiCad's API does not give the design rules, so they come from the project
  file: the netclass clearances, the minimum via size, drill and annular ring, hole-to-hole
  and copper-to-edge, and the predefined via sizes. Without a project file KiCad's defaults
  are assumed. Custom rules with a condition are left to DRC.
* **Via types.** KiCad 6 had switches allowing blind/buried vias and microvias; KiCad 7 on
  allow every type in the rules and leave it to the fab. So a type is offered only when the
  board already uses it, which shows the process makes it, and no custom rule disallows it.
  The shortest allowed via is used: a microvia joins two neighbouring layers, one of them
  outer; a blind via starts on an outer layer; a buried via has neither end outside.
* **Vias beside the pads.** One via per pad, straight out from the far end of the pad
  first, then turned to its sides, 0.1 mm steps up to 1.5 mm away. The via keeps its
  netclass clearance from every other net's track, pad and via on every layer it passes
  through; zones refill round it. The track to it is 1.5 times the default track width, at
  most the pad's width.
* **DRC.** `kicad-cli pcb drc --refill-zones` on the board as it is in the editor, before
  and after. A violation is the same one when its type and the items it names are (the
  measured value may differ). Anything a new violation names is taken off; if a new
  violation names none of the added parts, they all come off.

## Sources

* A. Riazi, *Effects of Plane Splits on High-Speed Signals*, PCD&M.
  https://pcdandf.com/pcdesign/index.php/2007-archive-articles/2377-effects-of-plane-splits-on-high-speed-signals-parts-1-and-2
* Texas Instruments, SPRAAR7J, *High-Speed Interface Layout Guidelines*: stitching
  capacitors of 1 µF or less, as close to the crossing as possible.
  https://www.ti.com/document-viewer/lit/html/SPRAAR7J/GUID-AD6D2D54-84AC-47F5-A2ED-87883A148A25
* E. Bogatin, *Rule of Thumb #23: When to worry about a capacitive discontinuity*, EDN.
  https://www.edn.com/electronics-blogs/all-aboard-/4438435/when-to-worry-about-a-capacitive-discontinuity--rule-of-thumb--23
* E. Bogatin, *Rule of Thumb #7: Total inductance in the return path*, EDN.
  https://www.edn.com/electronics-blogs/all-aboard-/4428560/total-inductance-in-the-return-path--rule-of-thumb--7
* LearnEMC, *EMC Question of the Week, May 17 2021* (connection inductance of a mounted
  decoupling capacitor). https://learnemc.com/qotw-210517
* JLCPCB, *Mastering Split Planes*. https://jlcpcb.com/blog/mastering-split-planes-pcb
* JLCPCB, *PCB Signal Transitions*. https://jlcpcb.com/blog/pcb-signal-transitions

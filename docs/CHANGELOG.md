# Changelog

What changed in each CapScan release. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Add a line under **Unreleased** with each change;
a release renames that heading to its version and date.

## [Unreleased]

### Added
- Every scan setting, and the net list's per-net edge rate and impedance, has a tooltip,
  and those about something easier seen than read show a small drawing under their text,
  in the window's colors, with the part the setting is about picked out: a split crossing
  and its gap, a via changing reference plane, a void, the edge rate, what a trace's
  characteristic impedance is, a decoupling cap, how a supply is told from a signal, and
  the voltage rating (#16).
- Screenshots in the README of each part of CapScan at work on a real board: the 3D heat
  map, the 2D placement, the tables, the net list, a tooltip, *Add to board* and the HTML
  report. `tools/screenshots.py` takes them again from the board open in KiCad (#15).

### Fixed
- HTML report: the 3D view's legend no longer covers the title of its heat scale.
- The 3D view's heat scale shows its whole title ("Transitions" was cut off at the right).

## [0.2.0] - 2026-10-27

### Added
- *Add to board…* puts the stitching capacitors on the board in KiCad, each pad with a via
  to its plane, and adds the return vias. The footprint, via size and type, and the first
  reference designator are picked from the board and can be changed; vias keep the
  project's design rules, and DRC must find the board as it was before. Add them all in one
  undo step, or step through them in KiCad one at a time (#6).
- A changelog (#11).
- The HTML report counts the decoupling capacitors it found (#8).
- A fuller About dialog: what CapScan does, links to the user guide, research notes,
  changelog and issue tracker, the KiCad, Python and package versions it runs on, the
  session log, and buttons to copy the version info, open the log folder and check GitHub
  for a newer release (#5).
- The About dialog shows how many times CapScan has been cloned and its releases
  downloaded, from a daily GitHub Actions workflow (#12).
- A welcome screen while CapScan starts, with an animated logo, the version and what it
  runs on, and what it is doing (connecting to KiCad, loading its packages); it closes
  itself once the window is open. The About dialog's icon is animated too (#4).

### Changed
- Before a scan the board's copper is drawn in KiCad's layer colors (F.Cu red, B.Cu blue,
  and so on), in 2D and 3D, with the nets picked to scan solid and the rest faint. After a
  scan the planes are still colored by net (#14).
- Each stitching cap's package and value are picked from the edge rate; there is no
  package to set. Faster edges get smaller packages, or several 0402s in parallel (a new
  Qty column); the value is the largest X7R that package stocks at the voltage rating. The
  reports say why the part was chosen (#13).
- Each net in the net list has its own edge rate and trace impedance, 100 ps and 50 Ω
  unless set. Double-click to set one, or set every net the filter shows at once; they are
  remembered per board. A shared cap takes the package its fastest signal needs. The
  defaults are at the top of the settings, and the Signals tables show each net's speed
  (#13).
- The HTML report lists every capacitor between two planes, including decoupling caps no
  crossing reached; before, a cap appeared only once a crossing credited or flagged it (#8).
- A capacitor is decoupling when one of its pads is within reach of an IC pin on the same
  net, measured pad to pin rather than from the cap's centre, so an 0805 or 1206 beside its
  pin is recognised (#8).
- The 3D view's Transition Density scale shows its values to the left of the bar.

### Fixed
- The window opens in front of KiCad on Windows instead of behind it (#3).
- PDF report: the maps fit the page instead of being cut off at the right (their legends
  were lost), and a heading moves to the next page with the start of what it heads, a
  short note under it included, instead of ending a page on its own (#7).
- Ticking nets before a scan no longer stalls the window: only the tracks are redrawn, not
  the planes (about 3 s down to 50 ms on a 3400-track board) (#17).
- Unticking *Signals*, *Caps* or the other groups on the 3D tab now keeps them hidden when
  the view is redrawn.
- *Add to board* works with kicad-python 0.7: it failed on every part. That release cannot
  flip a part, so caps meant for the bottom are left to place by hand, as on KiCad before
  10.0.6.

## [0.1.0] - 2026-10-25

First release: a KiCad 9/10 IPC-API plugin that finds every signal whose return current
meets a break in its reference plane and says where to put stitching capacitors and return
vias.

### Added
- Checks for traces over plane splits, vias moving a signal onto another net's plane
  (needs a stitching cap) or onto another plane of the same net (needs a return via), and
  traces over voids (reroute or fill). Each check can be switched off; one that is off is
  reported as "Not checked".
- Stitching capacitor placement within reach of each crossing, at most two signals per cap,
  on the board side nearer both planes and clear of other parts. Existing stand-alone caps
  are credited; decoupling caps next to IC pins are flagged instead of counted.
- A net list to pick which signal nets to scan, with a filter and a "Scan all signal nets"
  tick box. Picks are remembered per board.
- The board is shown before it is scanned: stackup, planes and routing in 3D and 2D, with
  the picked nets highlighted.
- Heat maps from every kind of break (splits, voids, vias needing a cap or a return via),
  each kind switched on or off from the 2D and 3D tabs.
- Supplies known by their copper as well as their name: a net poured on two or more layers
  or with an array of vias is a supply even when named like "Net-(U2-OUT)".
- Nothing is scanned until Scan is pressed, and KiCad's zones are refilled first only when
  "Refill zones before scanning" is ticked.
- Result tables, markers on User.Comments, and HTML and PDF reports.
- Zero-setup defaults, each with its source in `docs/research.md`.

### Fixed
- A trace ending on, or routed through, a through-hole pad or via of its own net is no
  longer reported as crossing a void at that pad's clearance hole.

[Unreleased]: https://github.com/drewsum/Kicad-Cap-Scan/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/drewsum/Kicad-Cap-Scan/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/drewsum/Kicad-Cap-Scan/releases/tag/v0.1.0

# Changelog

What changed in each CapScan release. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Add a line under **Unreleased** with each change;
a release renames that heading to its version and date.

## [Unreleased]

### Added
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

[Unreleased]: https://github.com/drewsum/Kicad-Cap-Scan/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/drewsum/Kicad-Cap-Scan/releases/tag/v0.1.0

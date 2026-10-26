# CapScan

**Where to put stitching capacitors across plane splits, for KiCad 9 and 10.**

CapScan is a KiCad PCB editor plugin. Click its button and it reads the open board through
KiCad's IPC API, finds every signal whose return current meets a break in its reference
plane, and tells you how many stitching capacitors are needed, where (X / Y and board side),
what value and voltage rating, and which two plane nets each one bridges.

It needs no setup. Every net that is not a plane is taken to be a fast digital signal
(100 ps edges), and every default leans towards more stitching rather than less: a basic
utility that keeps every signal's return path next to it.

## What it finds

| Problem | What CapScan says |
|---|---|
| A trace crossing a **split** between two plane nets on its reference layer | A stitching capacitor between the two nets within 2.5 mm (100 mil) of the crossing |
| A **via** moving a signal onto a reference plane of **another net** (GND → 3V3) | A stitching capacitor between them within 1.27 mm of the via |
| A via moving a signal onto **another plane of the same net** (GND1 → GND8) | A return via of that net within 1.27 mm, unless one is already there |
| A trace over **no plane at all** (a void, a cutout, past a plane's edge) | Flagged: no capacitor helps; reroute or fill the plane |
| A return that would flow through a **decoupling capacitor** (one next to an IC pin) | Flagged, and given a dedicated stitching capacitor of its own |

Existing capacitors between two plane nets are credited when they stand on their own. A
capacitor next to an IC's pin on either of its nets is decoupling that IC, and is never
counted as a stitching capacitor.

## What you get

* **3D Heat Map**: each reference plane with the density of split crossings rendered on it.
  Hotter means more routing crossing splits in a small area. Signals are colored by heat
  and the new capacitors are drawn on the board.
* **2D Placement**: one reference layer at a time. Plane nets are filled, crossings are
  marked, and the capacitors and return vias are labeled. Click one to find it in the tables.
* **Tables**:
  * Stitching caps: value, dielectric, package, voltage rating, nets, X / Y, side, angle,
    and the signals each one serves.
  * Signals: every signal crossing a split, with its routing layers and reference layers.
  * Crossings, via transitions, and the existing capacitor audit.
* **Markers on the board**: *Place markers* draws each capacitor, return via and void on
  User.Comments in one undo step. *Clear markers* removes them again.
* **Reports**: one self-contained HTML file with an interactive 3D view, or a PDF with the 3D
  view attached and a fillable notes box.

Double-click any row to find it on the map and select that net in KiCad.

## Install

### Requirements

* **KiCad 9.0 or later**, with **Preferences → Plugins → Enable KiCad API** checked.
  Before KiCad 9.0.3, KiCad cannot say which layers a plated pad has copper on, so pads are
  kept on every layer (CapScan says so in its log).
* **Python 3.10 to 3.13**, the one KiCad runs plugins with. On Windows that is KiCad's own
  Python; on macOS, select a Python 3.12 in *Preferences → Plugins* (KiCad's bundled 3.9 is
  too old).

### Steps

1. Link (or copy) this repo into KiCad's plugins folder as `CapScan`. For KiCad 9, put
   `9.0` in place of `10.0`:

   ```powershell
   $plug = "$([Environment]::GetFolderPath('MyDocuments'))\KiCad\10.0\plugins"
   New-Item -ItemType Junction -Path "$plug\CapScan" -Target "<path to this repo>"
   ```

   ```sh
   ln -s "<path to this repo>" ~/Documents/KiCad/10.0/plugins/CapScan        # macOS
   ln -s "<path to this repo>" ~/.local/share/kicad/10.0/plugins/CapScan     # Linux
   ```

   Or unzip `CapScan-<version>.zip` from the
   [latest release](https://github.com/drewsum/Kicad-Cap-Scan/releases/latest) there.

2. Restart the PCB editor. KiCad builds the plugin's Python environment from
   `requirements.txt` in the background, which takes a few minutes the first time. The
   **CapScan** toolbar button appears once it is ready.

## Using it

1. Open the board in the PCB editor and click **CapScan**. The scan starts at once. By
   default it refills the zones first, because the zone fills decide where the planes are.
2. Read the summary on the left. The **Stitching Caps** tab is the list of parts to add.
3. Click **Place markers** to see the spots in the PCB editor, then **Export PDF** or
   **Export HTML** for the report.

The *Scan* panel on the right has the few settings worth changing. *Advanced* holds every
rule, each explained in [docs/research.md](docs/research.md). Results are also saved beside
the board as `<board>.capscan.json.gz`.

## How it works

1. **Planes and signals.** A net is a plane net if its zone fills cover 2 % of the board on
   some layer, or if it is named like a supply or ground and has any fill. Every other routed
   net is a signal. Differential pairs (`_P`/`_N`, `+`/`-`) count as one return path.
2. **Reference layers.** A copper layer is a reference layer if plane nets cover 20 % of it.
   Each signal layer returns its current on the nearest reference layer above it and the
   nearest below. Heights come from the board's stackup.
3. **Crossings.** Each signal is walked along its centerline in 50 µm steps over each of
   its reference layers:
   * plane net A, then a gap, then plane net B is a split;
   * a long stretch over nothing is a void;
   * a void on one side of a stripline whose other plane is solid is noted, not flagged.
4. **Vias.** For each signal via and through-hole pin, CapScan reads the layers the signal
   actually enters and leaves on. It then compares the plane nets round the via on those
   layers' reference planes.
5. **Placement.** Existing stand-alone capacitors are credited first. Each new capacitor is
   placed greedily where it reaches the most uncovered signals, two at most. It goes on the
   board side nearer both planes, clear of other copper and parts, with its pads across the
   split.
6. **Value.** 100 nF X7R 0402. The voltage rating is at least twice the voltage between the
   two nets as their names give it (3V3, 1V8, +5V, VBUS), and never below 16 V. Nets whose
   voltage the name does not give get a 50 V rating.

## Development

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt   # bin/ on Linux and macOS
.venv/Scripts/python -m pytest
.venv/Scripts/python -m ruff check .
```

`python -m capscan --dump board.json.gz` saves the board open in KiCad as a snapshot.
`python -m capscan --scan board.json.gz --html out.html` scans a snapshot with no KiCad.
`python -m capscan board.json.gz` opens the window on one.

| Folder | What is in it |
|---|---|
| `capscan/kicad/` | Everything that talks to KiCad: extraction (with fallbacks for each KiCad 9 / 10 release) and the markers |
| `capscan/model/`, `capscan/geometry/` | The board snapshot and its geometry, shared with [KiPI](https://github.com/drewsum/KiPI) |
| `capscan/analysis/` | The scan: rules, references, crossings, transitions, existing caps, placement, values, heat map |
| `capscan/report/` | The 2D map, the 3D scene, and the HTML / PDF reports |
| `capscan/gui/` | The window (Qt, pyvista, matplotlib) |
| `tests/` | pytest. `tests/helpers/boards.py` builds boards with known answers; `kicad_fake.py` answers like each KiCad release |

## License

GPL-3.0-or-later. The extraction, board model, geometry, PDF printing and window
scaffolding come from [KiPI](https://github.com/drewsum/KiPI).

"""Take the README's screenshots, docs/images/*.png (#15), by driving CapScan's window.

    python tools/screenshots.py                     # the board open in KiCad
    python tools/screenshots.py board.json.gz       # a saved snapshot (no Add to board shot)
    python tools/screenshots.py --only caps,tooltip

Run it on the desktop, not with QT_QPA_PLATFORM=offscreen: the 3D view and the tooltips
are grabbed from the screen; the window stays on top until it closes. Nothing is
remembered for the board (its net picks and speeds go to a scratch settings file) and no
results are saved beside it. The HTML report is shot with Chrome or Edge, headless.

Not part of the plugin.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "images"
SIZE = (1680, 1013)             # the window, as KiPI's screenshots
BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "google-chrome", "chromium", "msedge"]


def settle(seconds: float = 0.6):
    """Let Qt draw (and the 3D view render) for a moment."""
    from qtpy.QtWidgets import QApplication
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)


def wait_task(win, timeout: float = 600.0):
    """Until the window's background job (reading or scanning the board) is done."""
    settle(0.3)
    end = time.monotonic() + timeout
    while win._task is not None and win._task.isRunning():
        if time.monotonic() > end:
            raise TimeoutError("CapScan's job did not finish")
        settle(0.1)
    settle(1.5)


def grab(widget, name: str, rect=None, tip=None):
    """Save what is on screen over ``widget`` (or ``rect``, in global coordinates), at the
    widget's size in logical pixels whatever the display's scaling. ``tip`` (a tooltip's
    window) is painted on where it is: the window kept on top hides it on the screen."""
    from qtpy.QtCore import QRect, Qt
    from qtpy.QtGui import QPainter
    widget.raise_()
    widget.activateWindow()
    settle()
    whole = QRect(widget.mapToGlobal(widget.rect().topLeft()), widget.size())
    rect = whole if rect is None else rect.intersected(whole)
    screen = widget.screen()
    geo = screen.geometry()
    pix = screen.grabWindow(0, rect.x() - geo.x(), rect.y() - geo.y(), rect.width(),
                            rect.height())
    if pix.width() != rect.width():
        pix = pix.scaled(rect.width(), rect.height(), Qt.IgnoreAspectRatio,
                         Qt.SmoothTransformation)
    if tip is not None:
        painter = QPainter(pix)
        painter.drawPixmap(tip.mapToGlobal(tip.rect().topLeft()) - rect.topLeft(), tip.grab())
        painter.end()
    path = OUT / f"{name}.png"
    pix.save(str(path))
    print(f"Saved {path.relative_to(ROOT)} ({pix.width()} x {pix.height()})")


def show_tip(widget, html: str, left_of=None):
    """Show ``html`` as ``widget``'s tooltip, as hovering does, level with it and left of
    ``left_of`` (the panel it is in: over the window rather than off its edge, and leaving
    the setting in view), and return its window."""
    from qtpy.QtCore import QPoint
    from qtpy.QtWidgets import QApplication, QToolTip

    def tip():
        return next((w for w in QApplication.topLevelWidgets()
                     if w.inherits("QTipLabel") and w.isVisible()), None)
    shown = None
    for _ in range(5):      # a tip shown while the last one fades out can be lost
        QToolTip.showText(widget.mapToGlobal(QPoint(0, widget.height())), html, widget)
        settle(0.5)
        shown = tip()
        if shown is not None:
            break
        QToolTip.hideText()
        settle(0.5)
    if shown is not None:
        left = (left_of or widget).mapToGlobal(QPoint(0, 0)).x()
        shown.move(left - shown.width() - 8, widget.mapToGlobal(QPoint(0, 0)).y())
        settle(1.0)
    return shown


def hide_tip():
    from qtpy.QtWidgets import QToolTip
    QToolTip.hideText()
    settle(0.5)


def union(*widgets):
    """The screen rectangle around ``widgets``."""
    from qtpy.QtCore import QRect
    out = QRect()
    for w in widgets:
        out = out.united(QRect(w.mapToGlobal(w.rect().topLeft()), w.size()))
    return out.adjusted(-6, -6, 6, 6)


def pick_cap(result, reach: float = 12.0):
    """The capacitor to show close up: the one with most others round it."""
    def near(c):
        return sum(abs(o.position[0] - c.position[0]) < reach
                   and abs(o.position[1] - c.position[1]) < reach for o in result.caps)
    return max(result.caps, key=lambda c: (near(c), c.id), default=None)


def zoom_map(win, xy, half: float = 18.0):
    """Zoom the 2D map to ``half`` mm round ``xy``, wider than tall as the tab is."""
    ax = win.map._ax
    ax.set_xlim(xy[0] - half * 1.6, xy[0] + half * 1.6)
    lo, hi = ax.get_ylim()
    ys = (xy[1] - half, xy[1] + half)
    ax.set_ylim(*(ys if lo < hi else ys[::-1]))
    win.map.canvas.draw()


def browser() -> str:
    for b in BROWSERS:
        if Path(b).is_file() or shutil.which(b):
            return b
    return ""


def shoot_html(path: Path, name: str, start: str = "", size=(1220, 760)):
    """A headless browser's shot of the top of the HTML report, or of it from the heading
    with id ``start`` (what is above is taken out: a headless shot of a scrolled page comes
    out blank)."""
    exe = browser()
    if not exe:
        print(f"No Chrome or Edge: {name} skipped")
        return
    page = path
    if start:
        page = path.with_name(f"{name}.html")
        page.write_text(path.read_text(encoding="utf-8").replace(
            "</body>", f"<script>const t = document.getElementById({start!r});"
            "while (t.previousSibling) t.previousSibling.remove();</script></body>"),
            encoding="utf-8")
    out = OUT / f"{name}.png"
    out.unlink(missing_ok=True)
    subprocess.run([exe, "--headless=new", "--hide-scrollbars",
                    f"--window-size={size[0]},{size[1]}", f"--screenshot={out}",
                    page.as_uri()], check=False, capture_output=True, timeout=120)
    print(f"Saved {out.relative_to(ROOT)}" if out.is_file() else f"{name} failed")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot", nargs="?", help="a saved board instead of KiCad's")
    ap.add_argument("--only", default="", help="comma-separated shot names")
    ap.add_argument("--filter", default="USB",
                    help="the nets picked for the before-scan shot (default: USB)")
    args = ap.parse_args(argv)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    want = lambda name: not only or name in only
    OUT.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="capscan-shots-"))

    from qtpy.QtCore import QSettings
    from capscan.gui import main_window
    from capscan.gui.app import bring_to_front, make_app, setup_logging
    setup_logging(console=False)
    # Leave the user's settings and the board's folder alone.
    main_window.QSettings = lambda *a: QSettings(str(scratch / "capscan.ini"),
                                                 QSettings.IniFormat)
    main_window.MainWindow._autosave = lambda self: None

    _app = make_app()      # held, or Qt deletes it
    kicad = board = snap = None
    if args.snapshot:
        from capscan.model.snapshot_io import load
        snap = load(args.snapshot)
    else:
        from capscan.kicad.extract import connect
        kicad, board = connect()
    win = main_window.MainWindow(kicad=kicad, board=board, snapshot=snap)
    from qtpy.QtCore import Qt
    win.setWindowFlag(Qt.WindowStaysOnTopHint)    # the screen is grabbed: nothing over it
    win.resize(*SIZE)
    bring_to_front(win)
    from qtpy.QtWidgets import QDockWidget
    log_dock = next(d for d in win.findChildren(QDockWidget) if d.windowTitle() == "Log")
    win.resizeDocks([log_dock], [120], Qt.Vertical)     # room for the whole Scan panel
    wait_task(win)
    panel, nets = win.settings_panel, win.settings_panel.nets

    # Before a scan: the board in its layer colors, a bus picked in the net list.
    nets.filter.setText(args.filter)
    nets.select_shown.click()
    settle(1.0)
    win.tabs.setCurrentIndex(0)
    if want("before_scan"):
        grab(win, "before_scan")
    if want("net_speeds"):      # a faster edge for the bus, set from the spin boxes
        nets.edge_spin.setValue(50)
        nets.z0_spin.setValue(45)
        nets.set_speed.click()
        settle(0.5)
        tip = show_tip(nets.edge_spin, nets.edge_spin.toolTip(), nets)
        grab(win, "net_speeds", union(nets, *([tip] if tip else [])), tip)
        hide_tip()
        nets.reset_speed.click()
        nets.edge_spin.setValue(100)
        nets.z0_spin.setValue(50)
    nets.filter.clear()
    nets.set_scan_all(True)

    win.start_scan()
    wait_task(win)
    r = win.result
    print(r.counts())
    cap = pick_cap(r)

    win.tabs.setCurrentIndex(0)
    if win.viewer is not None:
        win.viewer.plotter.reset_camera()
        win.viewer.plotter.camera.zoom(1.5)
    settle(2.0)
    if want("overview"):
        grab(win, "overview")
    if cap is not None and win.viewer is not None and want("heat_map_close"):
        win.viewer.plotter.camera.view_angle = 30.0     # as before the zoom above
        win.viewer.focus(cap.position, span_mm=22.0)
        grab(win, "heat_map_close")

    win.tabs.setCurrentWidget(win.map)
    win.map.labels.setChecked(True)
    if cap is not None:
        win._focus(cap.id)
        settle(2.0)
        zoom_map(win, cap.position)
    settle(2.0)
    if want("placement_2d"):
        grab(win, "placement_2d")

    for index, name in ((2, "caps"), (3, "signals"), (5, "via_transitions"),
                        (6, "existing_caps")):
        if want(name):
            win.tabs.setCurrentIndex(index)
            grab(win, name)

    if want("tooltip"):
        win.tabs.setCurrentIndex(0)
        w = panel._fields["max_cap_distance_mm"]      # the split crossing drawing
        tip = show_tip(w, w.toolTip(), panel)
        grab(win, "tooltip", union(panel, *([tip] if tip else [])), tip)
        hide_tip()

    if board is not None and r.caps and want("add_to_board"):
        from capscan.gui.add_dialog import AddDialog
        from capscan.kicad.install import Installer
        inst = Installer(kicad, board, win.snapshot, r)     # reads the board, adds nothing
        dlg = AddDialog(inst, win, win._next_first_ref(inst))
        dlg.show()
        grab(dlg, "add_to_board")
        dlg.reject()

    if want("report_tables") or want("report_3d"):
        from capscan.report.html import write_html
        html = scratch / "report.html"
        write_html(win.snapshot, r, str(html))
        if want("report_tables"):
            shoot_html(html, "report_tables")
        if want("report_3d"):
            shoot_html(html, "report_3d", "3d")

    win.close()
    settle(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())

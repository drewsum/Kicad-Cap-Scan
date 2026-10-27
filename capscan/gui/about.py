"""The About dialog: what CapScan is, who wrote it, and the versions it is running on.

Modelled on KiPI's (``kipi/gui/about.py``), without its repo statistics and dependency
manager.
"""

from __future__ import annotations

import html
import json
import platform
import sys
import threading
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import List, Optional, Tuple

from qtpy.QtCore import QObject, QSize, Qt, QUrl, Signal
from qtpy.QtGui import QDesktopServices, QGuiApplication, QPixmap
from qtpy.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from capscan import __version__
from capscan.gui import app as capscan_app

NAME = "CapScan"
DESCRIPTION = ("Stitching capacitor advisor for KiCad. CapScan finds every signal whose "
               "return current meets a plane split, a void, or a via onto another plane, and "
               "says how many stitching capacitors and return vias it needs, where they go, "
               "and what value and voltage rating each capacitor should have.")
AUTHOR = "Drew Maatman"
EMAIL = "drewmaatman@gmail.com"
REPO = "https://github.com/drewsum/Kicad-Cap-Scan"
ISSUES = REPO + "/issues"
CHANGELOG = REPO + "/blob/main/CHANGELOG.md"
RESEARCH = REPO + "/blob/main/docs/research.md"
LICENSE = "GPL-3.0-or-later"
SIBLING = "https://github.com/drewsum/KiPI"

# Distributions whose versions are worth quoting in a bug report.
_DEPS = ["kicad-python", "PySide6-Essentials", "qtpy", "pyvista", "pyvistaqt", "numpy",
         "shapely", "plotly", "matplotlib"]

_ROOT = Path(__file__).resolve().parents[2]
ICON_SIZE = 96


def icon_path() -> Optional[Path]:
    """The plugin icon, which lives beside the package in the plugin folder."""
    p = _ROOT / "resources" / "capscan_256.png"
    return p if p.is_file() else None


def help_path() -> Path:
    return _ROOT / "docs" / "help.html"


def versions(kicad_version: str = "") -> List[Tuple[str, str]]:
    """KiCad (when connected), Python and the dependency versions."""
    rows = [("KiCad", kicad_version or "not connected (opened from a snapshot)"),
            ("Python", f"{platform.python_version()} ({sys.platform})")]
    for dist in _DEPS:
        try:
            rows.append((dist, metadata.version(dist)))
        except metadata.PackageNotFoundError:
            pass
    return rows


def info_text(kicad_version: str = "") -> str:
    """Plain-text summary for pasting into an issue."""
    lines = [f"{NAME} {__version__}", REPO]
    lines += [f"{k}: {v}" for k, v in versions(kicad_version)]
    lines.append(f"Log: {capscan_app.LOG_PATH}")
    return "\n".join(lines)


#: The newest tagged release, from GitHub's API (no token needed for a public repo).
RELEASES_URL = "https://api.github.com/repos/drewsum/Kicad-Cap-Scan/releases/latest"
TIMEOUT_S = 5.0


def version_tuple(text: str) -> Optional[Tuple[int, ...]]:
    """``"v0.7.2"`` -> (0, 7, 2); None if it is not a plain version."""
    parts = text.strip().lstrip("vV").split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def fetch_latest_release(url: str = RELEASES_URL,
                         timeout: float = TIMEOUT_S) -> Optional[Tuple[str, str]]:
    """``(tag, release page)`` of the newest release, or None if GitHub cannot be asked:
    offline, a firewall, its rate limit, or a Python without CA certificates."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"CapScan/{__version__}",
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(1 << 20))
        return str(data["tag_name"]), str(data.get("html_url") or REPO + "/releases")
    except Exception:          # any failure is said in the dialog instead
        return None


def update_text(latest: Optional[Tuple[str, str]], current: str = __version__) -> str:
    """What the About dialog says once it has checked for updates (rich text)."""
    if latest is None:
        return ("Could not reach GitHub to check for updates; see "
                f"<a href='{REPO}/releases'>the releases</a>.")
    tag, url = latest
    new, have = version_tuple(tag), version_tuple(current)
    if new is not None and have is not None and new > have:
        return (f"CapScan {html.escape(tag)} is available: "
                f"<a href='{html.escape(url)}'>download it</a> "
                f"(<a href='{CHANGELOG}'>what changed</a>).")
    return (f"CapScan {html.escape(current)} is up to date "
            f"(the latest release is {html.escape(tag)}).")


class _UpdateCheck(QObject):
    """Asks GitHub for the newest release on a daemon thread; ``ready`` arrives on the UI
    thread."""

    ready = Signal(object)    # (tag, url), or None

    def start(self):
        threading.Thread(target=lambda: self.ready.emit(fetch_latest_release()),
                         name="capscan-update-check", daemon=True).start()


class AboutDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None, kicad_version: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"About {NAME}")
        self.kicad_version = kicad_version

        icon = QLabel(self)
        path = icon_path()
        if path is not None:
            icon.setPixmap(QPixmap(str(path)).scaled(
                QSize(ICON_SIZE, ICON_SIZE), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        icon.setAlignment(Qt.AlignTop)

        rows = "".join(f"<tr><td>{html.escape(k)}</td><td>&nbsp;&nbsp;{html.escape(v)}</td></tr>"
                       for k, v in versions(kicad_version))
        guide = QUrl.fromLocalFile(str(help_path())).toString()
        log = capscan_app.LOG_PATH
        self.text = QLabel(self)
        self.text.setTextFormat(Qt.RichText)
        self.text.setOpenExternalLinks(True)
        self.text.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.text.setWordWrap(True)
        self.text.setText(
            f"<h2 style='margin-bottom:0'>{NAME}</h2>"
            f"<p style='margin-top:2px'>Version {__version__}</p>"
            f"<p>{html.escape(DESCRIPTION)}</p>"
            f"<p><a href='{html.escape(guide)}'>User guide</a>: the window, the tables and "
            "the settings<br>"
            f"<a href='{RESEARCH}'>Research notes</a>: every rule, its default and its "
            "source<br>"
            f"<a href='{CHANGELOG}'>Changelog</a>: what changed in each release</p>"
            f"<p>Author: {AUTHOR} (<a href='mailto:{EMAIL}'>{EMAIL}</a>)<br>"
            f"Source: <a href='{REPO}'>{REPO}</a><br>"
            f"Report a bug: <a href='{ISSUES}'>{ISSUES}</a><br>"
            f"License: {LICENSE}<br>"
            f"The board extraction, geometry and report printing come from its sibling "
            f"<a href='{SIBLING}'>KiPI</a>, the power integrity analyzer.</p>"
            f"<p><b>Running on</b></p><table>{rows}</table>"
            f"<p>Session log: {html.escape(str(log))}</p>")

        col = QVBoxLayout()
        col.addWidget(self.text)
        # What Check for updates found; hidden until it is asked.
        self.update_label = QLabel(self)
        self.update_label.setTextFormat(Qt.RichText)
        self.update_label.setOpenExternalLinks(True)
        self.update_label.setWordWrap(True)
        self.update_label.setVisible(False)
        col.addWidget(self.update_label)
        self._update_check: Optional[_UpdateCheck] = None
        col.addStretch(1)

        top = QHBoxLayout()
        top.addWidget(icon)
        top.addSpacing(12)
        top.addLayout(col, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        copy = QPushButton("Copy version info", self)
        copy.setToolTip("Copy the versions above, for pasting into a bug report")
        copy.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(info_text(self.kicad_version)))
        buttons.addButton(copy, QDialogButtonBox.ActionRole)
        logs = QPushButton("Open log folder", self)
        logs.setToolTip("The session logs, to attach to a bug report")
        logs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log.parent))))
        buttons.addButton(logs, QDialogButtonBox.ActionRole)
        self.btn_update = QPushButton("Check for updates", self)
        self.btn_update.setToolTip("Ask GitHub whether a newer CapScan has been released")
        self.btn_update.clicked.connect(self.check_for_updates)
        buttons.addButton(self.btn_update, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(buttons)
        self.setMinimumWidth(560)

    def check_for_updates(self):
        """Ask GitHub for the newest release, on a thread; the answer shows below."""
        self.btn_update.setEnabled(False)
        self.update_label.setText("Checking for updates…")
        self.update_label.setVisible(True)
        self._update_check = _UpdateCheck()
        self._update_check.ready.connect(self._update_ready)
        self._update_check.start()

    def _update_ready(self, latest):
        try:
            self.update_label.setText(update_text(latest))
            self.btn_update.setEnabled(True)
        except RuntimeError:   # the dialog was deleted while the check ran
            pass

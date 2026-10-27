"""The About dialog: what CapScan is, who wrote it, and the versions it is running on.

Modelled on KiPI's (``kipi/gui/about.py``), with its repo statistics (#12) and without its
dependency manager.
"""

from __future__ import annotations

import html
import json
import platform
import sys
import threading
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, List, Optional, Tuple

from qtpy.QtCore import QObject, QSettings, QSize, Qt, QUrl, Signal
from qtpy.QtGui import QDesktopServices, QGuiApplication, QMovie, QPixmap
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


def animated_icon_path() -> Optional[Path]:
    """The icon as a loop: a signal crosses the split and the stitching capacitor drops in
    (#4; drawn by ``tools/make_logo_gif.py``)."""
    p = _ROOT / "resources" / "capscan.gif"
    return p if p.is_file() else None


def animate(label: QLabel, size: int, parent: QObject) -> Optional[QMovie]:
    """Play the animated icon in ``label``, or None to fall back to the still one: the GIF
    can be missing, and some Qt builds ship without the GIF image plugin."""
    path = animated_icon_path()
    if path is None:
        return None
    movie = QMovie(str(path), parent=parent)
    if not movie.isValid():
        return None
    movie.setScaledSize(QSize(size, size))
    label.setMovie(movie)
    movie.start()
    return movie


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


#: Clone and download counts, published daily to the repo's ``stats`` branch by
#: ``.github/workflows/stats.yml``. A public file: the Traffic API behind the clone counts
#: needs a token, and no token can ship in the plugin. Copied from KiPI.
STATS_URL = "https://raw.githubusercontent.com/drewsum/Kicad-Cap-Scan/stats/stats.json"
_STATS_KEY = "about/stats"


@dataclass(frozen=True)
class RepoStats:
    clones: int          # every clone since `since`
    since: str           # ISO date the clone history starts
    uniques_14d: int     # unique cloners over GitHub's 14-day window
    downloads: int       # release asset downloads, all releases

    def text(self) -> str:
        return (f"{self.clones:,} clones since {self.since} ({self.uniques_14d:,} unique in the "
                f"last 14 days) · {self.downloads:,} release downloads")


def parse_stats(data: Any) -> Optional[RepoStats]:
    """stats.json as the stats workflow writes it, or None if it is anything else."""
    try:
        c = data["clones"]
        return RepoStats(int(c["total"]), str(c["since"]), int(c["uniques_14d"]),
                         int(data["release_downloads"]["total"]))
    except (KeyError, TypeError, ValueError):
        return None


def fetch_stats(url: str = STATS_URL, timeout: float = 5.0) -> Optional[dict]:
    """The published stats as raw JSON, or None if they cannot be had: offline, a firewall,
    a Python without CA certificates, or the workflow not having run yet."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"CapScan/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(1 << 20))
    except Exception:          # any failure just leaves the stats out
        return None
    return data if parse_stats(data) is not None else None


def _settings() -> QSettings:
    return QSettings("CapScan", "CapScan")


def cached_stats() -> Optional[RepoStats]:
    """The stats last fetched, from any session, to show while (or instead of) fetching."""
    try:
        return parse_stats(json.loads(str(_settings().value(_STATS_KEY, ""))))
    except ValueError:
        return None


def _cache_stats(data: dict):
    _settings().setValue(_STATS_KEY, json.dumps(data))


class _StatsFetch(QObject):
    """Fetches the stats on a daemon thread; ``ready`` arrives on the UI thread."""

    ready = Signal(object)    # raw stats dict, or None

    def start(self):
        threading.Thread(target=lambda: self.ready.emit(fetch_stats()),
                         name="capscan-stats", daemon=True).start()


_fetched_this_session = False


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
        icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.movie = animate(icon, ICON_SIZE, self)
        path = icon_path()
        if self.movie is None and path is not None:
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

        # Repo statistics: the cached ones now, fresh ones when the fetch returns. Without
        # either the line stays hidden; nothing here can stop the dialog opening.
        self.stats_label = QLabel(self)
        self.stats_label.setWordWrap(True)
        self.stats_label.setToolTip("From the project's GitHub repository, updated daily")
        self._show_stats(cached_stats())
        self._fetch: Optional[_StatsFetch] = None
        global _fetched_this_session
        if not _fetched_this_session:
            _fetched_this_session = True
            self._fetch = _StatsFetch()
            self._fetch.ready.connect(self._stats_ready)
            self._fetch.start()

        col = QVBoxLayout()
        col.addWidget(self.text)
        col.addWidget(self.stats_label)
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
        top.addWidget(icon, 0, Qt.AlignTop)
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

    def _show_stats(self, stats: Optional[RepoStats]):
        if stats is not None:
            self.stats_label.setText(stats.text())
        self.stats_label.setVisible(bool(self.stats_label.text()))

    def _stats_ready(self, data: Optional[dict]):
        if data is None:
            return
        _cache_stats(data)
        try:
            self._show_stats(parse_stats(data))
        except RuntimeError:   # the dialog was deleted while the fetch ran
            pass

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

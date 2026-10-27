"""The welcome screen shows the animated logo and version, and closes on its own (#4)."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QLabel  # noqa: E402

from capscan import __version__  # noqa: E402
from capscan.gui import about, splash  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from capscan.gui.app import make_app
    return make_app()


def _spin(app, seconds):
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        app.processEvents()
        time.sleep(0.01)


def _wait_closed(app, s, seconds=10):
    deadline = time.monotonic() + seconds
    while s.isVisible() and time.monotonic() < deadline:
        _spin(app, 0.05)


def _text(s):
    return " ".join(lb.text() for lb in s.findChildren(QLabel))


def test_shows_version_and_packages(app):
    s = splash.Splash()
    text = _text(s)
    assert __version__ in text and splash.PLUGIN_ID in text and "Python" in text
    assert "Welcome to CapScan" in text
    s.close()


def test_plugin_id_is_the_manifests():
    import json
    from pathlib import Path
    manifest = Path(__file__).resolve().parents[2] / "plugin.json"
    assert json.loads(manifest.read_text())["identifier"] == splash.PLUGIN_ID


def test_logo_is_animated(app):
    assert about.animated_icon_path() is not None
    s = splash.Splash()
    assert s.movie is not None and s.movie.frameCount() > 1
    assert any(lb.movie() is s.movie for lb in s.findChildren(QLabel))
    s.close()


def test_about_icon_is_animated_too(app):
    d = about.AboutDialog()
    assert d.movie is not None and d.movie.frameCount() > 1
    d.close()


def test_closes_itself_after_one_loop_of_the_logo(app):
    s = splash.show_splash(app, show_ms=200)
    assert s is not None and s.isVisible()
    _spin(app, 0.5)
    assert s.isVisible()            # the short timer is not what closes it
    s.movie.jumpToFrame(s.movie.frameCount() - 1)   # skip to the end of the loop
    _wait_closed(app, s)
    assert not s.isVisible()


def test_still_logo_closes_after_a_timer(app, monkeypatch):
    monkeypatch.setattr(about.QMovie, "isValid", lambda self: False)
    s = splash.show_splash(app, show_ms=200)
    assert s is not None and s.movie is None and s.isVisible()
    assert any(lb.pixmap() is not None and not lb.pixmap().isNull()
               for lb in s.findChildren(QLabel))
    _wait_closed(app, s)
    assert not s.isVisible()


def test_a_broken_splash_does_not_stop_startup(app, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no screen")
    monkeypatch.setattr(splash, "Splash", boom)
    assert splash.show_splash(app) is None


def test_held_until_the_window_is_open_however_long_starting_takes(app):
    s = splash.show_splash(app, show_ms=100)
    s.hold()
    s.set_status("Connecting to KiCad…")
    assert "Connecting to KiCad…" in _text(s)
    s.movie.jumpToFrame(s.movie.frameCount() - 1)   # one loop played while connecting
    _spin(app, 0.5)
    assert s.isVisible()
    s.release()
    _wait_closed(app, s)
    assert not s.isVisible()


def test_packages_load_on_a_thread_while_the_splash_says_what(app, monkeypatch):
    import threading
    from capscan.gui import app as gui_app

    said, threads = [], []
    real = __import__("importlib").import_module

    def fake_import(name, *a):
        threads.append(threading.current_thread().name)
        if name == "no_such_package_capscan":
            raise ImportError(name)
        return real(name, *a)

    monkeypatch.setattr("importlib.import_module", fake_import)
    s = splash.Splash()
    monkeypatch.setattr(s, "set_status", said.append)
    gui_app.preload(s, (("One…", ("json", "no_such_package_capscan")), ("Two…", ("csv",))))
    assert said[0] == "One…" and set(said) <= {"One…", "Two…"}
    assert threads and set(threads) == {"capscan-preload"}
    s.close()

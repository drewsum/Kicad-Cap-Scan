"""The About dialog."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from capscan import __version__  # noqa: E402
from capscan.gui import about  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from capscan.gui.app import make_app
    return make_app()


def test_dialog_names_the_versions_and_links(app):
    d = about.AboutDialog(kicad_version="10.0.1")
    text = d.text.text()
    assert f"Version {__version__}" in text and "10.0.1" in text
    for link in (about.REPO, about.ISSUES, about.CHANGELOG, about.RESEARCH, "help.html"):
        assert link in text
    assert about.help_path().is_file() and about.icon_path() is not None
    d.close()


def test_info_text_is_for_a_bug_report():
    text = about.info_text("9.0.3")
    assert text.startswith(f"CapScan {__version__}\n")
    assert "KiCad: 9.0.3" in text and "Python:" in text and "Log:" in text
    assert "not connected" in about.info_text()


@pytest.mark.parametrize("latest, says", [
    (None, "Could not reach GitHub"),
    (("v99.0.0", "https://x/r"), "v99.0.0 is available"),
    ((f"v{__version__}", "https://x/r"), "is up to date"),
    (("nightly", "https://x/r"), "is up to date"),
])
def test_update_text(latest, says):
    assert says in about.update_text(latest)


def test_check_for_updates_reports_back(app, monkeypatch):
    monkeypatch.setattr(about, "fetch_latest_release", lambda: ("v99.0.0", "https://x/r"))
    d = about.AboutDialog()
    d.check_for_updates()
    import time
    t0 = time.time()
    while "available" not in d.update_label.text():
        assert time.time() - t0 < 10
        app.processEvents()
    assert d.btn_update.isEnabled()
    d.close()

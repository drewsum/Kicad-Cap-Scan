"""The About dialog."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from capscan import __version__  # noqa: E402
from capscan.gui import about  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from capscan.gui.app import make_app
    return make_app()


# Repo statistics (#12). The network and the settings store are stubbed for every test.

STATS = {"schema": 1, "updated": "2026-10-15T03:17:00Z",
         "clones": {"total": 1234, "since": "2026-10-01", "uniques_14d": 231, "daily": {}},
         "release_downloads": {"total": 56, "by_release": {"v0.1.0": 56}}}

_real_fetch_stats = about.fetch_stats


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    from qtpy.QtCore import QSettings
    ini = str(tmp_path / "capscan.ini")
    monkeypatch.setattr(about, "_settings", lambda: QSettings(ini, QSettings.IniFormat))
    monkeypatch.setattr(about, "fetch_stats", lambda *a, **k: None)
    monkeypatch.setattr(about, "_fetched_this_session", False)


def _wait(app, cond, seconds=5):
    import time
    t0 = time.time()
    while not cond() and time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.01)
    return cond()


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


def test_parse_stats_reads_what_the_workflow_writes():
    s = about.parse_stats(STATS)
    assert s == about.RepoStats(1234, "2026-10-01", 231, 56)
    assert s.text() == ("1,234 clones since 2026-10-01 (231 unique in the last 14 days) "
                        "· 56 release downloads")


@pytest.mark.parametrize("bad", [None, [], {}, {"clones": {}}, {"clones": {"total": "x"}}])
def test_parse_stats_rejects_anything_else(bad):
    assert about.parse_stats(bad) is None


def test_fetch_stats_reads_a_url_and_fails_quietly(tmp_path):
    good = tmp_path / "stats.json"
    good.write_text(json.dumps(STATS))
    assert _real_fetch_stats(good.as_uri()) == STATS
    assert _real_fetch_stats((tmp_path / "missing.json").as_uri()) is None
    good.write_text("not json")
    assert _real_fetch_stats(good.as_uri()) is None
    good.write_text('{"something": "else"}')
    assert _real_fetch_stats(good.as_uri()) is None


def test_stats_line_is_hidden_when_offline_with_nothing_cached(app):
    d = about.AboutDialog()
    assert not d.stats_label.isVisibleTo(d)


def test_stats_line_shows_fetched_stats_and_caches_them(app, monkeypatch):
    monkeypatch.setattr(about, "fetch_stats", lambda *a, **k: STATS)
    d = about.AboutDialog()
    assert _wait(app, lambda: d.stats_label.isVisibleTo(d))
    assert "1,234 clones" in d.stats_label.text()
    assert about.cached_stats() == about.parse_stats(STATS)


def test_stats_line_falls_back_to_the_cache_when_offline(app):
    about._cache_stats(STATS)
    d = about.AboutDialog()
    assert d.stats_label.isVisibleTo(d) and "56 release downloads" in d.stats_label.text()


def test_stats_are_fetched_once_per_session(app, monkeypatch):
    calls = []
    monkeypatch.setattr(about, "fetch_stats", lambda *a, **k: calls.append(1) or STATS)
    d1 = about.AboutDialog()
    assert _wait(app, lambda: d1.stats_label.isVisibleTo(d1))
    d2 = about.AboutDialog()
    assert d2._fetch is None and len(calls) == 1
    assert d2.stats_label.isVisibleTo(d2)    # from the cache the first fetch filled

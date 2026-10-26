# CapScan

KiCad 9/10 IPC-API plugin (kipy) that finds return-path breaks and recommends stitching
capacitors. Sibling of KiPI (`../KiPI`): its extraction, model, geometry, PDF printing and
window scaffolding were copied from there. Keep them in step by hand when KiPI fixes them.

- Run tests: `.venv/Scripts/python -m pytest` (set `QT_QPA_PLATFORM=offscreen` for GUI tests); lint: `.venv/Scripts/python -m ruff check .`
- Only `capscan/kicad/` imports kipy. `capscan/analysis/` has no Qt and no kipy: snapshot in, `ScanResult` out.
- Every rule and its default lives in `capscan/analysis/rules.py` (`ScanSettings`) with its source; explain new ones in `docs/research.md`.
- KiCad version differences are handled by what KiCad answers (`_unsupported` in `kicad/extract.py`), not by version numbers; `tests/helpers/kicad_fake.py` answers like each release. Add a release to `VERSIONS` and to the CI `kicad` job together.
- Test boards with known answers are built in `tests/helpers/boards.py`.
- `requirements.txt` (what KiCad installs into the plugin venv) must match `pyproject.toml` dependencies (`tests/test_requirements.py`).

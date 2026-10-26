"""KiCad IPC plugin action entry point (see plugin.json).

KiCad starts this in the plugin's own venv with KICAD_API_SOCKET / KICAD_API_TOKEN
set, so kipy connects to the instance that launched us.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    from capscan.gui import app as capscan_app
    from capscan.gui.app import run, setup_logging
    from capscan.kicad.extract import ExtractionError, connect
    from capscan.progress import ProgressReporter

    setup_logging(console=False)
    # KiCad captures plugin stdout/stderr through a pipe it does not always drain; a full
    # pipe blocks the writer forever. Send everything to this session's log folder.
    capscan_app.detach_std_streams()
    os.environ.setdefault("QT_API", "pyside6")
    app = capscan_app.make_app()
    try:
        kicad, board = connect(progress=ProgressReporter())
    except ExtractionError as e:
        from qtpy.QtWidgets import QMessageBox
        QMessageBox.critical(None, "CapScan", f"{e}\n\nLog: {capscan_app.LOG_PATH}")
        return 1
    del app
    return run(kicad=kicad, board=board)


if __name__ == "__main__":
    sys.exit(main())

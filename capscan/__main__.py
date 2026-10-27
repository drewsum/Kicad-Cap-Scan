"""Development entry point; CapScan itself runs only as a KiCad plugin.

    python -m capscan --dump out.json.gz      # read the board open in KiCad, save it, exit
    python -m capscan --scan board.json.gz    # scan a saved board, print the summary
    python -m capscan board.json.gz           # open the window on a saved board
    python -m capscan                         # open the window on the board open in KiCad
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="capscan", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot", nargs="?", help="saved board snapshot to open")
    ap.add_argument("--dump", metavar="PATH", help="extract from KiCad, save snapshot, exit")
    ap.add_argument("--scan", metavar="PATH", help="scan a saved snapshot and print a summary")
    ap.add_argument("--html", metavar="PATH", help="with --scan: also write the HTML report")
    ap.add_argument("--nets", metavar="A,B", help="with --scan: only these nets (default: all)")
    ap.add_argument("--refill", action="store_true", help="refill zones before extracting")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)

    from capscan.gui.app import setup_logging
    setup_logging(logging.DEBUG if args.debug else logging.INFO)

    if args.dump:
        from capscan.kicad.extract import connect, extract_board
        from capscan.model.snapshot_io import save
        from capscan.progress import ProgressReporter
        prog = ProgressReporter()
        kicad, board = connect(progress=prog)
        snap = extract_board(board, refill_zones=args.refill, progress=prog)
        snap.kicad_version = str(kicad.get_version())
        save(snap, args.dump)
        print(f"Saved {args.dump}")
        return 0

    if args.scan:
        from capscan.analysis.scan import run_scan
        from capscan.model.snapshot_io import load
        from capscan.progress import ProgressReporter
        from capscan.analysis.rules import ScanSettings
        snap = load(args.scan)
        picked = [n.strip() for n in (args.nets or "").split(",") if n.strip()]
        settings = ScanSettings(scan_all_nets=not picked, selected_nets=picked)
        r = run_scan(snap, settings, progress=ProgressReporter())
        print(r.counts())
        for c in r.caps:
            print(f"{c.id}: {c.amount} {c.voltage_v:g}V {c.package} {c.net_a}/{c.net_b} at "
                  f"({c.position[0]:.2f}, {c.position[1]:.2f}) {c.side}, "
                  f"for {', '.join(c.signals)}")
        if args.html:
            from capscan.report.html import write_html
            write_html(snap, r, args.html)
            print(f"Wrote {args.html}")
        return 0

    from capscan.gui.app import run
    if args.snapshot:
        from capscan.model.snapshot_io import load
        return run(snapshot=load(args.snapshot))
    from capscan.kicad.extract import ExtractionError, connect
    from capscan.progress import ProgressReporter
    try:
        kicad, board = connect(progress=ProgressReporter())
    except ExtractionError as e:
        print(e, file=sys.stderr)
        return 1
    return run(kicad=kicad, board=board)


if __name__ == "__main__":
    sys.exit(main())

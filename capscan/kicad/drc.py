"""KiCad's design rules check on the live board, through ``kicad-cli``.

KiCad's API cannot run DRC, so the board as it stands in the editor (unsaved edits too,
from ``SaveDocumentToString``) is written to a temporary folder with the project's rules
and checked there with ``kicad-cli pcb drc``, zones refilled first. Comparing a check
before adding parts with one after shows what the parts broke: a violation counts as the
same when its type and the items it names are.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

TIMEOUT_S = 900


class DrcError(RuntimeError):
    pass


@dataclass(frozen=True)
class Violation:
    type: str
    severity: str
    description: str
    items: Tuple[str, ...]              # the uuids of the items it names, sorted
    position: Tuple[float, float] = (0.0, 0.0)

    @property
    def key(self):
        return self.type, self.items

    def __str__(self) -> str:
        return (f"{self.severity} {self.type}: {self.description} "
                f"at x {self.position[0]:.2f}, y {self.position[1]:.2f} mm")


@dataclass
class DrcReport:
    violations: List[Violation] = field(default_factory=list)

    def new_since(self, before: "DrcReport") -> List[Violation]:
        """The violations here that ``before`` did not have (as many times over)."""
        left = Counter(v.key for v in before.violations)
        out = []
        for v in self.violations:
            if left[v.key] > 0:
                left[v.key] -= 1
            else:
                out.append(v)
        return out


def parse_report(data: dict) -> DrcReport:
    out = []
    for section in ("violations", "unconnected_items", "schematic_parity"):
        for v in data.get(section, []) or []:
            items = v.get("items", []) or []
            pos = items[0].get("pos", {}) if items else {}
            out.append(Violation(
                type=str(v.get("type", "")), severity=str(v.get("severity", "")),
                description=str(v.get("description", "")),
                items=tuple(sorted(str(i.get("uuid", "")) for i in items)),
                position=(float(pos.get("x", 0.0)), float(pos.get("y", 0.0)))))
    return DrcReport(out)


def find_cli(kicad=None) -> Optional[str]:
    """The kicad-cli of the running KiCad, else one on the PATH."""
    if kicad is not None:
        try:
            path = kicad.get_kicad_binary_path("kicad-cli")
            if path and Path(path).is_file():
                return path
        except Exception:  # noqa: BLE001 - older KiCad: look for it
            pass
    found = shutil.which("kicad-cli")
    if found:
        return found
    for base in (Path("C:/Program Files/KiCad"),):
        for exe in sorted(base.glob("*/bin/kicad-cli.exe"), reverse=True):
            return str(exe)
    mac = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
    return str(mac) if mac.is_file() else None


def run_drc(cli: str, board_text: str, project_dir: str, board_name: str,
            timeout_s: float = TIMEOUT_S) -> DrcReport:
    """DRC of ``board_text`` with the project's rules (and library tables)."""
    stem = Path(board_name).stem or "board"
    with tempfile.TemporaryDirectory(prefix="capscan-drc-") as tmp:
        tmp = Path(tmp)
        pcb = tmp / f"{stem}.kicad_pcb"
        pcb.write_text(board_text, encoding="utf-8")
        src = Path(project_dir) if project_dir else None
        if src is not None:
            for name in (f"{stem}.kicad_pro", f"{stem}.kicad_dru", "fp-lib-table"):
                if (src / name).is_file():
                    shutil.copy(src / name, tmp / name)
        out = tmp / "drc.json"
        cmd = [cli, "pcb", "drc", "--format", "json", "--severity-all", "--refill-zones",
               "--units", "mm", "-o", str(out), str(pcb)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=timeout_s, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DrcError(f"kicad-cli DRC did not run: {e}") from e
        if not out.is_file():
            raise DrcError(f"kicad-cli DRC failed ({proc.returncode}): "
                           f"{(proc.stderr or proc.stdout).strip()[-400:]}")
        return parse_report(json.loads(out.read_text(encoding="utf-8")))


def touching(violations: Iterable[Violation], ids: Iterable[str]) -> List[Violation]:
    ids = set(ids)
    return [v for v in violations if ids & set(v.items)]

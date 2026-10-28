"""Reading KiCad's S-expression files, as far as CapScan needs them: a library footprint
(``.kicad_mod``) to build a new capacitor from, and the custom rules (``.kicad_dru``)."""

from __future__ import annotations

import re
from typing import Iterator, List, Optional, Union

Atom = str
Node = Union[Atom, list]


class Quoted(str):
    """A string that was written in quotes."""


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text: str) -> list:
    """The first S-expression in ``text``, as nested lists of strings."""
    stack: List[list] = []
    pos = 0
    while True:
        m = _TOKEN.match(text, pos)
        if m is None or m.end() == pos:
            raise ValueError("Unbalanced S-expression")
        pos = m.end()
        opened, closed, quoted, atom = m.groups()
        if opened:
            stack.append([])
        elif closed:
            done = stack.pop()
            if not stack:
                return done
            stack[-1].append(done)
        elif quoted is not None:
            stack[-1].append(Quoted(re.sub(r"\\(.)", r"\1", quoted)))
        else:
            stack[-1].append(atom)


def children(node: list, name: str) -> List[list]:
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == name]


def child(node: list, name: str) -> Optional[list]:
    return next(iter(children(node, name)), None)


def walk(node: list) -> Iterator[list]:
    yield node
    for c in node[1:]:
        if isinstance(c, list):
            yield from walk(c)


def lib_id(fp: list) -> str:
    return str(fp[1]) if len(fp) > 1 and not isinstance(fp[1], list) else ""


def is_number(x) -> bool:
    if isinstance(x, list):
        return False
    try:
        float(x)
    except (TypeError, ValueError):
        return False
    return True

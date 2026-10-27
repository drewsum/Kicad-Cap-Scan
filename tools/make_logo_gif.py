"""Draw resources/capscan.gif, the animated logo on the welcome screen and in the About
dialog (#4), from the same picture as capscan_256.png.

    python tools/make_logo_gif.py

One loop tells what CapScan does: the stitching capacitor goes, a signal runs along the
trace, its return current meets the split and the gap glows hot, a scan line sweeps the
board, the capacitor drops in across the split, and the glow settles to the still logo.
The last frame is the still logo, so the loop starts and ends on it.

Needs Pillow and NumPy (both come with matplotlib); not part of the plugin.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SIZE = 256
SS = 4                              # supersampling, for smooth edges
FRAME_MS = 40
OUT = Path(__file__).resolve().parents[1] / "resources" / "capscan.gif"

BG = (30, 31, 36)
ORANGE = (255, 160, 64)
BLUE = (78, 162, 253)
WHITE = (255, 255, 255)
PAD = (215, 213, 211)
BODY = (34, 225, 255)
RED = (225, 38, 21)

# The still logo's geometry, in 256 px.
TILE = (4, 4, 252, 252, 40)         # x0, y0, x1, y1, corner radius
LEFT = (31, 31, 115, 224)
RIGHT = (141, 31, 224, 224)
TRACE = (18, 79, 237, 89)
GAP_X, TRACE_Y = 128, 84
CAP_PADS = ((84, 152, 106, 195), (149, 152, 171, 195))
CAP_BODY = (108, 152, 147, 195)
GLOW_R = 38


def _mask(boxes, dy=0.0, radius=0):
    """Antialiased coverage (0..1) of ``boxes``, moved down ``dy`` px."""
    im = Image.new("L", (SIZE * SS, SIZE * SS), 0)
    d = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in boxes:
        box = [x0 * SS, (y0 + dy) * SS, (x1 + 1) * SS - 1, (y1 + 1 + dy) * SS - 1]
        if radius:
            d.rounded_rectangle(box, radius * SS, fill=255)
        else:
            d.rectangle(box, fill=255)
    return np.asarray(im.resize((SIZE, SIZE), Image.BOX), dtype=np.float32)[..., None] / 255


def _over(img, color, alpha):
    return img * (1 - alpha) + np.asarray(color, np.float32) * alpha


YY, XX = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
R_GAP = np.hypot(XX - GAP_X, YY - TRACE_Y)
TILE_MASK = _mask([TILE[:4]], radius=TILE[4])
PLANES = (_mask([LEFT]), _mask([RIGHT]))
TRACE_MASK = _mask([TRACE])


def frame(glow: float, cap_alpha: float, cap_dy: float, pulse_x: float | None,
          scan_x: float | None) -> np.ndarray:
    img = np.zeros((SIZE, SIZE, 3), np.float32) + np.asarray(BG, np.float32)
    img = _over(img, ORANGE, PLANES[0])
    img = _over(img, BLUE, PLANES[1])
    if glow > 0:
        a = np.exp(-(R_GAP / (GLOW_R * math.sqrt(glow))) ** 2.6) * min(1.0, glow) * 0.97
        img = _over(img, RED, a[..., None])
    if scan_x is not None:
        band = np.exp(-((XX - scan_x) / 7) ** 2) * (YY > 24) * (YY < 232) * 0.7
        img = _over(img, BODY, band[..., None])
    img = _over(img, WHITE, TRACE_MASK)
    if pulse_x is not None:
        r = np.hypot(XX - pulse_x, (YY - TRACE_Y) * 1.6)
        img = _over(img, BODY, (np.exp(-(r / 13) ** 2) * 0.95)[..., None])
    if cap_alpha > 0:
        img = _over(img, PAD, _mask(CAP_PADS, cap_dy) * cap_alpha)
        img = _over(img, BODY, _mask([CAP_BODY], cap_dy) * cap_alpha)
    return np.concatenate([img, TILE_MASK * 255], axis=2)


def _ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def frames():
    """(glow, cap alpha, cap drop, pulse x, scan x) of each frame."""
    out = [(1.0, 1.0, 0.0, None, None)] * 10     # the still logo
    for i in range(10):                         # the cap and the glow go
        t = (i + 1) / 10
        out.append((1 - t, 1 - t, 0.0, None, None))
    for i in range(26):                         # a signal runs along the trace
        x = TRACE[0] + (TRACE[2] - TRACE[0]) * i / 25
        hit = max(0.0, (x - GAP_X + 12) / 40)   # the return current meets the split
        out.append((min(1.4, hit * 1.4), 0.0, 0.0, x, None))
    for i in range(16):                         # the scan finds it
        x = LEFT[0] + (RIGHT[2] - LEFT[0]) * i / 15
        out.append((1.4 + 0.12 * math.sin(i * 1.3), 0.0, 0.0, None, x))
    for i in range(12):                         # the stitching cap drops in
        t = _ease((i + 1) / 12)
        out.append((1.4, t, -40 * (1 - t), None, None))
    for i in range(16):                         # and the glow settles
        t = _ease((i + 1) / 16)
        out.append((1.4 - 0.4 * t, 1.0, 0.0, None, None))
    return out


def main() -> int:
    images = []
    for args in frames():
        rgba = np.clip(frame(*args), 0, 255).astype(np.uint8)
        im = Image.fromarray(rgba, "RGBA")
        # GIF transparency is on or off: the tile's corners are cut at half coverage.
        rgb = im.convert("RGB").quantize(127, method=Image.Quantize.MEDIANCUT)
        rgb.putpalette(rgb.getpalette()[:381] + [0, 0, 0] * 129)
        px = np.asarray(rgb).copy()
        px[rgba[..., 3] < 128] = 255
        p = Image.fromarray(px, "P")
        p.putpalette(rgb.getpalette())
        images.append(p)
    images[0].save(OUT, save_all=True, append_images=images[1:], duration=FRAME_MS, loop=0,
                   transparency=255, disposal=2, optimize=True)
    print(f"Wrote {OUT} ({len(images)} frames, {OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Truchet tilings.

Each cell carries a pair of quarter arcs joining the midpoints of its
edges. Because every tile connects the same four points, neighbouring tiles
always line up, and the random orientations produce long meandering ribbons
across the page. Drawing each arc as a pair of offset curves turns those
ribbons into bands wide enough to colour.
"""

from __future__ import annotations

import math
import random

from .base import Drawing, arc

#: (columns, rows) per difficulty. Fewer, fatter ribbons are easier to colour.
SIZES = {1: (5, 7), 2: (7, 9), 3: (10, 13)}


def _tile(d: Drawing, x0: float, y0: float, s: float, flipped: bool, band: float) -> None:
    """One Truchet cell: two quarter arcs, each drawn as a band."""
    corners = (
        [(x0, y0, 0.0), (x0 + s, y0 + s, math.pi)]
        if not flipped
        else [(x0 + s, y0, math.pi / 2), (x0, y0 + s, 3 * math.pi / 2)]
    )
    for cx, cy, a0 in corners:
        for r in (s / 2 - band, s / 2 + band):
            if r > 0.002:
                d.add(arc(cx, cy, r, a0, a0 + math.pi / 2, 20))


def generate(rng: random.Random, difficulty: int = 2) -> Drawing:
    cols, rows = SIZES.get(difficulty, SIZES[2])
    d = Drawing(kind="truchet")

    # Keep cells square: the page box is taller than it is wide.
    s = 1.0 / cols
    height = rows * s
    y_off = (1.0 - height) / 2 if height < 1.0 else 0.0

    band_ratio = rng.uniform(0.15, 0.21)

    for i in range(cols):
        for j in range(rows):
            _tile(d, i * s, y_off + j * s, s, rng.random() < 0.5, s * band_ratio)

    # A frame stops the ribbons from looking cut off.
    d.add(
        [(0, y_off), (1.0, y_off), (1.0, y_off + height), (0, y_off + height)],
        closed=True,
        width=1.3,
    )
    return d

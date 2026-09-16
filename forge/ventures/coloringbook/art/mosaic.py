"""Stained-glass rose windows.

A disc is divided into rings of panes, each ring staggered against the last
and each pane inset slightly so the gaps read as leading between the glass.
Panes are quads by default, with the occasional ring of petals to break up
the brickwork.
"""

from __future__ import annotations

import math
import random

from .base import Drawing, arc, circle, inset_polygon, petal

CX = CY = 0.5
R_MAX = 0.47
LEADING = 0.93


# ----------------------------------------------------------------------
# rose window
# ----------------------------------------------------------------------
def generate(rng: random.Random, difficulty: int = 2) -> Drawing:
    d = Drawing(kind="mosaic")
    sectors = rng.choice([8, 10, 12, 16])
    rings = {1: 3, 2: 4, 3: 5}.get(difficulty, 4)

    edges = [0.12]
    for i in range(rings):
        edges.append(edges[-1] + (R_MAX - 0.12) / rings)

    for ri in range(rings):
        r0, r1 = edges[ri], edges[ri + 1]
        # Offset alternate rings so the panes stagger like brickwork.
        phase = (math.pi / sectors) if ri % 2 else 0.0
        count = sectors * (2 if ri >= rings - 1 and rng.random() < 0.5 else 1)
        if rng.random() < 0.3:
            # A ring of petals, to stop every band reading as brickwork.
            for i in range(count):
                a = 2 * math.pi * (i + 0.5) / count + phase
                d.add(petal(CX, CY, a, r0, r1, math.pi / count * 0.95), closed=True, width=0.95)
            continue
        for i in range(count):
            a0 = 2 * math.pi * i / count + phase
            a1 = 2 * math.pi * (i + 1) / count + phase
            cell = arc(CX, CY, r0, a0, a1, 8) + arc(CX, CY, r1, a1, a0, 8)
            d.add(inset_polygon(cell, LEADING), closed=True, width=0.95)

    # Centre boss.
    d.add(circle(CX, CY, 0.115, 48), closed=True, width=1.1)
    for i in range(sectors):
        d.add(petal(CX, CY, 2 * math.pi * i / sectors, 0.03, 0.105, math.pi / sectors * 0.8), closed=True, width=0.9)

    d.add(circle(CX, CY, R_MAX, 128), closed=True, width=1.5)
    d.add(circle(CX, CY, R_MAX + 0.018, 128), closed=True, width=1.0)
    return d

"""Radially symmetric mandalas.

Built as a stack of concentric bands, each band filled with one motif
repeated around the circle. The symmetry order is fixed for the whole page,
which is what makes a mandala read as one design rather than a pile of
rings.
"""

from __future__ import annotations

import math
import random

from .base import Drawing, arc, circle, leaf, petal, polar, regular_polygon, scalloped_ring

CX = CY = 0.5
R_MAX = 0.48


def _band_petals(d: Drawing, r0: float, r1: float, n: int, rng: random.Random) -> None:
    spread = rng.uniform(0.72, 1.0) * math.pi / n
    bulge = rng.uniform(0.95, 1.3)
    for i in range(n):
        a = 2 * math.pi * i / n
        d.add(petal(CX, CY, a, r0, r1, spread, bulge), closed=True)


def _band_split_petals(d: Drawing, r0: float, r1: float, n: int, rng: random.Random) -> None:
    """Petals with an inner petal echoed inside them."""
    spread = rng.uniform(0.78, 1.0) * math.pi / n
    for i in range(n):
        a = 2 * math.pi * i / n
        d.add(petal(CX, CY, a, r0, r1, spread), closed=True)
        d.add(petal(CX, CY, a, r0 + (r1 - r0) * 0.22, r1 - (r1 - r0) * 0.18, spread * 0.55), closed=True, width=0.8)


def _band_dots(d: Drawing, r0: float, r1: float, n: int, rng: random.Random) -> None:
    rr = (r0 + r1) / 2
    radius = min((r1 - r0) * 0.48, math.pi * rr / n * 0.8)
    count = n * rng.choice([1, 1, 2])
    for i in range(count):
        a = 2 * math.pi * i / count
        cx, cy = polar(CX, CY, rr, a)
        d.add(circle(cx, cy, radius, 32), closed=True)
        if rng.random() < 0.4:
            d.add(circle(cx, cy, radius * 0.45, 24), closed=True, width=0.8)


def _band_teeth(d: Drawing, r0: float, r1: float, n: int, rng: random.Random) -> None:
    for i in range(n):
        a0 = 2 * math.pi * i / n
        a1 = 2 * math.pi * (i + 1) / n
        mid = (a0 + a1) / 2
        d.add([polar(CX, CY, r0, a0), polar(CX, CY, r1, mid), polar(CX, CY, r0, a1)], closed=True)


def _band_scallop(d: Drawing, r0: float, r1: float, n: int, rng: random.Random) -> None:
    d.add(scalloped_ring(CX, CY, r0, n, r1 - r0), closed=True)


def _band_leaves(d: Drawing, r0: float, r1: float, n: int, rng: random.Random) -> None:
    # A lens is as wide as bulge * its length, so pick bulge from the gap
    # between neighbours: the ring should close up, not dot the band.
    spacing = 2 * math.pi * ((r0 + r1) / 2) / n
    bulge = max(0.2, min(0.95, 0.82 * spacing / max(r1 - r0, 1e-6)))
    for i in range(n):
        a = 2 * math.pi * i / n
        d.add(leaf(polar(CX, CY, r0, a), polar(CX, CY, r1, a), bulge), closed=True)


def _band_lattice(d: Drawing, r0: float, r1: float, n: int, rng: random.Random) -> None:
    """Crossing diagonals that make a woven ring."""
    for i in range(n):
        a0 = 2 * math.pi * i / n
        a1 = 2 * math.pi * (i + 1) / n
        d.add([polar(CX, CY, r0, a0), polar(CX, CY, r1, a1)], width=0.85)
        d.add([polar(CX, CY, r0, a1), polar(CX, CY, r1, a0)], width=0.85)


def _band_arcs(d: Drawing, r0: float, r1: float, n: int, rng: random.Random) -> None:
    rr = (r0 + r1) / 2
    gap = rng.uniform(0.12, 0.3)
    for i in range(n):
        a0 = 2 * math.pi * (i + gap) / n
        a1 = 2 * math.pi * (i + 1 - gap) / n
        d.add(arc(CX, CY, rr, a0, a1, 16))
        d.add(arc(CX, CY, r1, a0, a1, 16), width=0.8)


def _band_polygons(d: Drawing, r0: float, r1: float, n: int, rng: random.Random) -> None:
    rr = (r0 + r1) / 2
    size = min((r1 - r0) * 0.55, math.pi * rr / n * 0.85)
    sides = rng.choice([3, 4, 5, 6])
    for i in range(n):
        a = 2 * math.pi * i / n
        cx, cy = polar(CX, CY, rr, a)
        d.add(regular_polygon(cx, cy, size, sides, a), closed=True)


#: Motifs that fill a wide band edge to edge.
_MAJOR = [_band_petals, _band_split_petals, _band_teeth, _band_leaves]
#: Motifs that read as a thin decorative stripe.
_MINOR = [_band_dots, _band_scallop, _band_arcs, _band_polygons, _band_lattice]


def _rosette(d: Drawing, r: float, n: int, rng: random.Random) -> None:
    d.add(circle(CX, CY, r, 64), closed=True)
    inner = r * rng.uniform(0.45, 0.65)
    for i in range(n):
        a = 2 * math.pi * i / n
        d.add(petal(CX, CY, a, inner * 0.35, r * 0.92, math.pi / n * 0.8), closed=True, width=0.85)
    d.add(circle(CX, CY, inner * 0.4, 32), closed=True)


def generate(rng: random.Random, difficulty: int = 2) -> Drawing:
    """A mandala. ``difficulty`` 1-3 controls how many bands and how fine."""
    d = Drawing(kind="mandala")
    n = rng.choice([8, 10, 12, 12, 16])
    majors = {1: 3, 2: 4, 3: 5}.get(difficulty, 4)

    # Alternate a wide filling band with a narrow accent stripe. Real
    # mandalas breathe like this; a stack of equal rings reads as a target.
    plan: list[tuple[object, float]] = []
    major_pool, minor_pool = _MAJOR[:], _MINOR[:]
    rng.shuffle(major_pool)
    rng.shuffle(minor_pool)
    for i in range(majors):
        plan.append((major_pool[i % len(major_pool)], rng.uniform(1.6, 2.4)))
        if i < majors - 1 or rng.random() < 0.6:
            plan.append((minor_pool[i % len(minor_pool)], rng.uniform(0.7, 1.0)))

    r_start = 0.085
    total = sum(w for _, w in plan)
    edges = [r_start]
    for _, w in plan:
        edges.append(edges[-1] + (R_MAX - r_start) * w / total)

    _rosette(d, r_start * 1.05, n, rng)

    for i, (motif, _) in enumerate(plan):
        r0, r1 = edges[i], edges[i + 1]
        motif(d, r0, r1 - (r1 - r0) * 0.05, n, rng)
        if rng.random() < 0.5:
            d.add(circle(CX, CY, r1, 96), closed=True, width=0.75)

    d.add(circle(CX, CY, R_MAX, 128), closed=True, width=1.25)
    if rng.random() < 0.7:
        d.add(circle(CX, CY, R_MAX + 0.015, 128), closed=True, width=0.9)
    return d

"""Botanical pages: stems, leaves and blooms, optionally in a patterned pot.

The stems are cubic curves fanned out from a common base, which is what
makes a bunch look gathered rather than scattered. Leaves and blooms hang
off sampled points along those curves, so they always sit on the stem.
"""

from __future__ import annotations

import math
import random

from .base import Drawing, Pt, circle, cubic, leaf, petal, polar, quad


def _bloom(d: Drawing, cx: float, cy: float, r: float, rng: random.Random) -> None:
    """A flower head: one or two rings of petals around a seeded centre."""
    n = rng.choice([5, 6, 7, 8])
    phase = rng.uniform(0, math.pi)
    spread = math.pi / n * rng.uniform(0.75, 1.0)
    for i in range(n):
        a = phase + 2 * math.pi * i / n
        d.add(petal(cx, cy, a, r * 0.28, r, spread, rng.uniform(0.95, 1.2)), closed=True)
    if rng.random() < 0.55:
        for i in range(n):
            a = phase + 2 * math.pi * (i + 0.5) / n
            d.add(petal(cx, cy, a, r * 0.22, r * 0.66, spread * 0.8), closed=True, width=0.85)
    d.add(circle(cx, cy, r * 0.26, 32), closed=True)
    for i in range(rng.randint(4, 7)):
        a = 2 * math.pi * i / 6
        d.add(circle(*polar(cx, cy, r * 0.13, a), r * 0.045, 16), closed=True, width=0.75)


def _bud(d: Drawing, cx: float, cy: float, r: float, angle: float) -> None:
    d.add(petal(cx, cy, angle, 0.0, r, 0.55), closed=True)
    d.add(petal(cx, cy, angle, 0.0, r * 0.55, 0.35), closed=True, width=0.8)


def _pot(d: Drawing, rng: random.Random) -> float:
    """Draw a pot across the bottom. Returns the y the stems should start at."""
    top_y, bot_y = 0.23, 0.02
    half_top, half_bot = 0.19, 0.13
    d.add(
        [
            (0.5 - half_top, top_y),
            (0.5 + half_top, top_y),
            (0.5 + half_bot, bot_y),
            (0.5 - half_bot, bot_y),
        ],
        closed=True,
        width=1.3,
    )
    # Rim and a decorated band, both useful places to put colour.
    rim = top_y - 0.035
    d.add([(0.5 - half_top * 0.97, rim), (0.5 + half_top * 0.97, rim)], width=1.0)
    band_y = (rim + bot_y) / 2
    d.add([(0.5 - half_bot * 1.16, band_y + 0.035), (0.5 + half_bot * 1.16, band_y + 0.035)], width=0.9)
    d.add([(0.5 - half_bot * 1.06, band_y - 0.035), (0.5 + half_bot * 1.06, band_y - 0.035)], width=0.9)
    motif = rng.choice(["dots", "zigzag", "waves"])
    if motif == "dots":
        for i in range(7):
            d.add(circle(0.5 + (i - 3) * 0.045, band_y, 0.016, 20), closed=True, width=0.85)
    elif motif == "zigzag":
        pts: list[Pt] = []
        for i in range(15):
            pts.append((0.5 - 0.15 + i * 0.0215, band_y + (0.022 if i % 2 else -0.022)))
        d.add(pts, width=0.85)
    else:
        pts = []
        for i in range(41):
            t = i / 40
            pts.append((0.5 - 0.15 + t * 0.3, band_y + 0.022 * math.sin(t * math.pi * 6)))
        d.add(pts, width=0.85)
    return top_y - 0.01


def generate(rng: random.Random, difficulty: int = 2) -> Drawing:
    d = Drawing(kind="floral")
    stems = {1: 3, 2: 5, 3: 7}.get(difficulty, 5) + rng.randint(-1, 1)
    has_pot = rng.random() < 0.6
    base_y = _pot(d, rng) if has_pot else 0.04

    spread = 0.30 if has_pot else 0.40
    for i in range(stems):
        # Fan the stems out evenly, then jitter so they are not a comb.
        frac = (i + 0.5) / stems - 0.5
        lean = frac * 2 * spread + rng.uniform(-0.03, 0.03)
        base = (0.5 + frac * 0.12, base_y)
        top = (0.5 + lean, rng.uniform(0.58, 0.9))
        c0 = (base[0] + lean * 0.15, base_y + (top[1] - base_y) * 0.45)
        c1 = (top[0] - lean * 0.25, base_y + (top[1] - base_y) * 0.8)
        stem = cubic(base, c0, c1, top, 40)
        d.add(stem, width=1.15)

        for k, t in enumerate([0.3, 0.5, 0.7]):
            if rng.random() > 0.75:
                continue
            idx = int(t * (len(stem) - 1))
            px, py = stem[idx]
            side = 1 if (k + i) % 2 == 0 else -1
            length = rng.uniform(0.07, 0.12)
            tip = (px + side * length, py + rng.uniform(0.02, 0.06))
            d.add(leaf((px, py), tip, rng.uniform(0.25, 0.4)), closed=True)
            d.add(quad((px, py), ((px + tip[0]) / 2, (py + tip[1]) / 2), tip, 10), width=0.7)

        head_r = rng.uniform(0.07, 0.115) * (1.0 if stems <= 5 else 0.85)
        if rng.random() < 0.78:
            _bloom(d, top[0], top[1] + head_r * 0.55, head_r, rng)
        else:
            _bud(d, top[0], top[1], head_r * 0.7, math.pi / 2 + lean * 0.5)

    return d

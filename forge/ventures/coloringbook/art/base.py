"""Geometry primitives shared by the page generators.

A page is a list of polylines in an arbitrary coordinate space; the layout
code fits whatever it is given onto the paper. Curves are flattened here
rather than carried as beziers, which keeps the renderers trivial and makes
the output easy to assert on in tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Pt = tuple[float, float]


@dataclass
class Path:
    """A single stroked polyline."""

    points: list[Pt]
    closed: bool = False
    #: Stroke weight multiplier. 1.0 is the book's normal line.
    width: float = 1.0

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError("a path needs at least two points")


@dataclass
class Drawing:
    """One colouring page, before it meets the paper."""

    paths: list[Path] = field(default_factory=list)
    title: str = ""
    #: Generator name, kept for the book's manifest.
    kind: str = ""

    def add(self, points: list[Pt], closed: bool = False, width: float = 1.0) -> None:
        if len(points) >= 2:
            self.paths.append(Path(points, closed, width))

    def extend(self, other: "Drawing") -> None:
        self.paths.extend(other.paths)

    def bounds(self) -> tuple[float, float, float, float]:
        """(minx, miny, maxx, maxy) over every point."""
        if not self.paths:
            return (0.0, 0.0, 1.0, 1.0)
        xs = [p[0] for path in self.paths for p in path.points]
        ys = [p[1] for path in self.paths for p in path.points]
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def point_count(self) -> int:
        return sum(len(p.points) for p in self.paths)

    def is_finite(self) -> bool:
        return all(
            math.isfinite(x) and math.isfinite(y)
            for path in self.paths
            for x, y in path.points
        )


# ----------------------------------------------------------------------
# curve helpers
# ----------------------------------------------------------------------
def polar(cx: float, cy: float, r: float, a: float) -> Pt:
    return (cx + r * math.cos(a), cy + r * math.sin(a))


def arc(cx: float, cy: float, r: float, a0: float, a1: float, steps: int = 48) -> list[Pt]:
    steps = max(2, steps)
    return [polar(cx, cy, r, a0 + (a1 - a0) * i / steps) for i in range(steps + 1)]


def circle(cx: float, cy: float, r: float, steps: int = 96) -> list[Pt]:
    return arc(cx, cy, r, 0.0, 2 * math.pi, steps)[:-1]


def quad(p0: Pt, c: Pt, p1: Pt, steps: int = 24) -> list[Pt]:
    """Flatten a quadratic bezier."""
    out = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        out.append(
            (
                u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0],
                u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1],
            )
        )
    return out


def cubic(p0: Pt, c0: Pt, c1: Pt, p1: Pt, steps: int = 32) -> list[Pt]:
    """Flatten a cubic bezier."""
    out = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        out.append(
            (
                u**3 * p0[0] + 3 * u * u * t * c0[0] + 3 * u * t * t * c1[0] + t**3 * p1[0],
                u**3 * p0[1] + 3 * u * u * t * c0[1] + 3 * u * t * t * c1[1] + t**3 * p1[1],
            )
        )
    return out


def rotate(p: Pt, cx: float, cy: float, a: float) -> Pt:
    dx, dy = p[0] - cx, p[1] - cy
    ca, sa = math.cos(a), math.sin(a)
    return (cx + dx * ca - dy * sa, cy + dx * sa + dy * ca)


def rotate_all(pts: list[Pt], cx: float, cy: float, a: float) -> list[Pt]:
    return [rotate(p, cx, cy, a) for p in pts]


def mirror_x(pts: list[Pt], cx: float) -> list[Pt]:
    return [(2 * cx - x, y) for x, y in pts]


def regular_polygon(cx: float, cy: float, r: float, sides: int, phase: float = 0.0) -> list[Pt]:
    return [polar(cx, cy, r, phase + 2 * math.pi * i / sides) for i in range(sides)]


def centroid(pts: list[Pt]) -> Pt:
    n = len(pts)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def inset_polygon(pts: list[Pt], factor: float) -> list[Pt]:
    """Shrink a polygon toward its centroid. Cheap, and convex-safe."""
    cx, cy = centroid(pts)
    return [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in pts]


def petal(
    cx: float,
    cy: float,
    angle: float,
    r_inner: float,
    r_outer: float,
    spread: float,
    bulge: float = 1.0,
) -> list[Pt]:
    """A closed teardrop pointing outward from (cx, cy) along ``angle``."""
    base = polar(cx, cy, r_inner, angle)
    tip = polar(cx, cy, r_outer, angle)
    mid_r = (r_inner + r_outer) / 2 * bulge
    left = quad(base, polar(cx, cy, mid_r, angle + spread), tip, steps=18)
    right = quad(tip, polar(cx, cy, mid_r, angle - spread), base, steps=18)
    return left + right[1:-1]


def leaf(p0: Pt, p1: Pt, bulge: float = 0.28) -> list[Pt]:
    """A closed lens shape between two points."""
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    nx, ny = -dy * bulge, dx * bulge
    a = quad(p0, (mx + nx, my + ny), p1, steps=16)
    b = quad(p1, (mx - nx, my - ny), p0, steps=16)
    return a + b[1:-1]


def scalloped_ring(cx: float, cy: float, r: float, count: int, depth: float) -> list[Pt]:
    """A closed ring made of ``count`` outward bumps."""
    pts: list[Pt] = []
    for i in range(count):
        a0 = 2 * math.pi * i / count
        a1 = 2 * math.pi * (i + 1) / count
        mid = (a0 + a1) / 2
        pts.extend(quad(polar(cx, cy, r, a0), polar(cx, cy, r + depth, mid), polar(cx, cy, r, a1), steps=10))
    return pts

"""Perfect mazes, drawn as merged wall runs.

Carved with an iterative randomised depth-first search, so every maze has
exactly one path between any two cells and no loops. Wall segments are
merged into long runs before drawing, which keeps the PDF small and the
corners clean.
"""

from __future__ import annotations

import random

from .base import Drawing

#: grid size per difficulty, as (cols, rows)
SIZES = {1: (12, 16), 2: (18, 24), 3: (26, 34)}


def _carve(cols: int, rows: int, rng: random.Random) -> tuple[set, set]:
    """Return the sets of vertical and horizontal walls that survive.

    ``vwalls`` holds (x, y) meaning a wall on the left edge of cell (x, y);
    ``hwalls`` holds (x, y) meaning a wall on the bottom edge of cell (x, y).
    """
    vwalls = {(x, y) for x in range(cols + 1) for y in range(rows)}
    hwalls = {(x, y) for x in range(cols) for y in range(rows + 1)}

    visited = [[False] * rows for _ in range(cols)]
    stack = [(0, 0)]
    visited[0][0] = True

    while stack:
        x, y = stack[-1]
        neighbours = []
        if x > 0 and not visited[x - 1][y]:
            neighbours.append((x - 1, y))
        if x < cols - 1 and not visited[x + 1][y]:
            neighbours.append((x + 1, y))
        if y > 0 and not visited[x][y - 1]:
            neighbours.append((x, y - 1))
        if y < rows - 1 and not visited[x][y + 1]:
            neighbours.append((x, y + 1))

        if not neighbours:
            stack.pop()
            continue

        nx, ny = rng.choice(neighbours)
        if nx == x:  # vertical move: drop the horizontal wall between them
            hwalls.discard((x, max(y, ny)))
        else:  # horizontal move: drop the vertical wall between them
            vwalls.discard((max(x, nx), y))
        visited[nx][ny] = True
        stack.append((nx, ny))

    return vwalls, hwalls


def _merge_runs(walls: set, vertical: bool) -> list[tuple[int, int, int]]:
    """Collapse adjacent unit walls into (fixed, start, end) runs."""
    by_line: dict[int, list[int]] = {}
    for x, y in walls:
        key, val = (x, y) if vertical else (y, x)
        by_line.setdefault(key, []).append(val)

    runs = []
    for key, vals in by_line.items():
        vals.sort()
        start = prev = vals[0]
        for v in vals[1:]:
            if v == prev + 1:
                prev = v
                continue
            runs.append((key, start, prev + 1))
            start = prev = v
        runs.append((key, start, prev + 1))
    return runs


def generate(rng: random.Random, difficulty: int = 2) -> Drawing:
    """A maze with an opening at the bottom left and the top right."""
    cols, rows = SIZES.get(difficulty, SIZES[2])
    vwalls, hwalls = _carve(cols, rows, rng)

    # Doors. Bottom edge of the first cell, top edge of the last.
    hwalls.discard((0, 0))
    hwalls.discard((cols - 1, rows))

    d = Drawing(kind="maze")
    cw, ch = 1.0 / cols, 1.0 / rows

    for x, y0, y1 in _merge_runs(vwalls, vertical=True):
        d.add([(x * cw, y0 * ch), (x * cw, y1 * ch)], width=1.1)
    for y, x0, x1 in _merge_runs(hwalls, vertical=False):
        d.add([(x0 * cw, y * ch), (x1 * cw, y * ch)], width=1.1)

    return d


def solve(rng_seed: int, difficulty: int = 2) -> Drawing:
    """The solution path for the maze produced by the same seed.

    Regenerating rather than caching keeps the page generator pure; a maze
    is cheap enough that carving it twice costs nothing.
    """
    cols, rows = SIZES.get(difficulty, SIZES[2])
    vwalls, hwalls = _carve(cols, rows, random.Random(rng_seed))

    def open_neighbours(x: int, y: int) -> list[tuple[int, int]]:
        out = []
        if (x, y) not in vwalls and x > 0:
            out.append((x - 1, y))
        if (x + 1, y) not in vwalls and x < cols - 1:
            out.append((x + 1, y))
        if (x, y) not in hwalls and y > 0:
            out.append((x, y - 1))
        if (x, y + 1) not in hwalls and y < rows - 1:
            out.append((x, y + 1))
        return out

    # Breadth-first from the entrance; a perfect maze gives one route.
    prev: dict[tuple[int, int], tuple[int, int] | None] = {(0, 0): None}
    frontier = [(0, 0)]
    target = (cols - 1, rows - 1)
    while frontier and target not in prev:
        nxt = []
        for cell in frontier:
            for nb in open_neighbours(*cell):
                if nb not in prev:
                    prev[nb] = cell
                    nxt.append(nb)
        frontier = nxt

    path = []
    cur: tuple[int, int] | None = target
    while cur is not None and cur in prev:
        path.append(cur)
        cur = prev[cur]
    path.reverse()

    cw, ch = 1.0 / cols, 1.0 / rows
    d = Drawing(kind="maze-solution")
    pts = [((x + 0.5) * cw, (y + 0.5) * ch) for x, y in path]
    if len(pts) >= 2:
        d.add([(pts[0][0], 0.0)] + pts + [(pts[-1][0], 1.0)], width=1.4)
    return d


def with_solution(seed: int, difficulty: int = 2) -> Drawing:
    """The maze drawn faintly with its solution traced over the top.

    A solution page is useless without the walls behind it, and both come
    from the same seed, so they are merged into one drawing and fitted to
    the page as a unit.
    """
    d = Drawing(kind="maze-solved")
    walls = generate(random.Random(seed), difficulty)
    for p in walls.paths:
        d.add(p.points, p.closed, width=0.35)
    d.extend(solve(seed, difficulty))
    return d

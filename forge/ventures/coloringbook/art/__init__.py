"""Procedural line art for colouring pages.

Every generator is a pure function of a seed: same seed, same page. The art
is plain geometry, so it costs nothing to make, prints crisply at any size,
and raises no question about whose work it is.
"""

from .base import Drawing, Path
from . import floral, mandala, maze, mosaic, truchet

#: name -> generator. A generator takes (rng, difficulty) and returns a Drawing.
GENERATORS = {
    "mandala": mandala.generate,
    "maze": maze.generate,
    "truchet": truchet.generate,
    "mosaic": mosaic.generate,
    "floral": floral.generate,
}

__all__ = ["Drawing", "Path", "GENERATORS"]

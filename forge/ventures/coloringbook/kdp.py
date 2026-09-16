"""Amazon KDP print specifications.

These numbers come from KDP's published paperback guidelines. Getting them
wrong means a rejected file, so they live in one place with the reasoning
attached rather than scattered through the layout code.

A caution that belongs in the code and not just the README: KDP has no
publishing API and its terms forbid scripted uploads. Forge produces files
and metadata; a person uploads them. That step is a queued human task, by
design.
"""

from __future__ import annotations

from dataclasses import dataclass

INCH = 72.0  # PDF points

#: Paperbacks must have an even page count, and at least this many pages.
MIN_PAGES = 24
MAX_PAGES = 828

#: Spine width per page, in inches, by paper type (KDP's own figures).
SPINE_PER_PAGE = {
    "white": 0.002252,
    "cream": 0.0025,
    "color": 0.002347,
}

#: Bleed on a full-wrap cover, all four outer edges.
COVER_BLEED = 0.125
#: Keep text and art this far inside the cover trim and away from the spine.
COVER_SAFE = 0.25
#: KDP prints a barcode here on the back cover, bottom right. Keep it clear.
BARCODE_W, BARCODE_H = 2.0, 1.2
BARCODE_MARGIN = 0.25


@dataclass(frozen=True)
class TrimSize:
    name: str
    width: float
    height: float


TRIM_SIZES = {
    "8.5x11": TrimSize("8.5x11", 8.5, 11.0),
    "8x10": TrimSize("8x10", 8.0, 10.0),
    "6x9": TrimSize("6x9", 6.0, 9.0),
}


def gutter_margin(pages: int) -> float:
    """Inside margin required for a given page count, in inches."""
    if pages <= 150:
        return 0.375
    if pages <= 300:
        return 0.5
    if pages <= 500:
        return 0.625
    if pages <= 700:
        return 0.75
    return 0.875


def spine_width(pages: int, paper: str = "white") -> float:
    """Spine width in inches. Under 100 pages KDP allows no spine text."""
    if paper not in SPINE_PER_PAGE:
        raise ValueError(f"unknown paper type {paper!r}")
    return pages * SPINE_PER_PAGE[paper]


def spine_text_allowed(pages: int) -> bool:
    return pages >= 100


def cover_size(pages: int, trim: TrimSize, paper: str = "white") -> tuple[float, float]:
    """Full-wrap cover size in inches: back + spine + front, plus bleed."""
    w = 2 * trim.width + spine_width(pages, paper) + 2 * COVER_BLEED
    h = trim.height + 2 * COVER_BLEED
    return (w, h)


def validate_page_count(pages: int) -> None:
    if pages % 2 != 0:
        raise ValueError(f"page count must be even for a paperback, got {pages}")
    if pages < MIN_PAGES:
        raise ValueError(f"KDP paperbacks need at least {MIN_PAGES} pages, got {pages}")
    if pages > MAX_PAGES:
        raise ValueError(f"KDP paperbacks cap out at {MAX_PAGES} pages, got {pages}")


def interior_box(pages: int, trim: TrimSize, recto: bool, extra: float = 0.125) -> tuple[float, float, float, float]:
    """Printable box (x, y, w, h) in points for one interior page.

    ``recto`` is a right-hand (odd) page, whose gutter is on the left.
    ``extra`` is padding beyond KDP's minimum, because art that crowds the
    minimum margin looks like a mistake even when it passes review.
    """
    gutter = gutter_margin(pages) + extra
    outer = 0.25 + extra
    left = gutter if recto else outer
    right = outer if recto else gutter
    x = left * INCH
    y = outer * INCH
    w = (trim.width - left - right) * INCH
    h = (trim.height - 2 * outer) * INCH
    return (x, y, w, h)

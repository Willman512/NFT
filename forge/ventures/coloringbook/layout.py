"""Turning drawings into print-ready PDFs.

Two files come out of here: the interior, laid out single sided with the
gutter alternating so nothing falls into the spine, and the full-wrap
cover sized from the final page count.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas as pdfcanvas

from . import kdp
from .art import GENERATORS
from .art.base import Drawing
from .art import maze as maze_art
from .metadata import BookMetadata, Theme

#: Base stroke weight for interior art. Coloring lines want to be bold.
BASE_LINE_PT = 2.0


@dataclass
class Page:
    drawing: Drawing
    caption: str = ""


def render_drawing(
    c: pdfcanvas.Canvas,
    drawing: Drawing,
    box: tuple[float, float, float, float],
    base_width: float = BASE_LINE_PT,
) -> None:
    """Fit a drawing into ``box`` (x, y, w, h in points) and stroke it."""
    if not drawing.paths:
        return
    x, y, w, h = box
    minx, miny, maxx, maxy = drawing.bounds()
    span_x, span_y = max(maxx - minx, 1e-9), max(maxy - miny, 1e-9)
    scale = min(w / span_x, h / span_y)
    ox = x + (w - span_x * scale) / 2
    oy = y + (h - span_y * scale) / 2

    c.setStrokeColor(colors.black)
    c.setLineJoin(1)
    c.setLineCap(1)

    # Grouping by stroke weight keeps the graphics state changes down.
    by_width: dict[float, list] = {}
    for p in drawing.paths:
        by_width.setdefault(round(p.width, 3), []).append(p)

    for width, paths in sorted(by_width.items()):
        c.setLineWidth(base_width * width)
        path = c.beginPath()
        for p in paths:
            px, py = p.points[0]
            path.moveTo(ox + (px - minx) * scale, oy + (py - miny) * scale)
            for px, py in p.points[1:]:
                path.lineTo(ox + (px - minx) * scale, oy + (py - miny) * scale)
            if p.closed:
                path.close()
        c.drawPath(path, stroke=1, fill=0)


def _centered(c: pdfcanvas.Canvas, text: str, x: float, y: float, font: str, size: float) -> None:
    c.setFont(font, size)
    c.drawCentredString(x, y, text)


def _wrapped(
    c: pdfcanvas.Canvas, text: str, x: float, y: float, width: float, font: str, size: float, leading: float
) -> float:
    c.setFont(font, size)
    for para in text.split("\n"):
        if not para.strip():
            y -= leading * 0.6
            continue
        for line in simpleSplit(para, font, size, width):
            c.drawString(x, y, line)
            y -= leading
    return y


# ----------------------------------------------------------------------
# page generation
# ----------------------------------------------------------------------
def build_pages(theme: Theme, seed: int, art_pages: int) -> tuple[list[Page], list[tuple[int, int]]]:
    """Generate the art. Returns the pages and any (seed, difficulty) mazes.

    Mazes are tracked separately because their solutions have to be printed
    in the back, keyed to the page they belong to.
    """
    rng = random.Random(seed)
    names = list(theme.generators)
    weights = [theme.generators[n] for n in names]

    pages: list[Page] = []
    mazes: list[tuple[int, int]] = []
    for i in range(art_pages):
        kind = rng.choices(names, weights=weights, k=1)[0]
        # Difficulty ramps across the book: gentle at the front, tight at the back.
        ramp = i / max(art_pages - 1, 1)
        difficulty = 1 if ramp < 0.3 else (2 if ramp < 0.75 else 3)
        difficulty = max(1, min(3, round((difficulty + theme.difficulty) / 2)))
        page_seed = rng.randrange(1 << 30)
        drawing = GENERATORS[kind](random.Random(page_seed), difficulty)
        pages.append(Page(drawing, caption=""))
        if kind == "maze":
            mazes.append((page_seed, difficulty))
    return pages, mazes


# ----------------------------------------------------------------------
# interior
# ----------------------------------------------------------------------
def write_interior(
    path: str | Path,
    meta: BookMetadata,
    pages: list[Page],
    mazes: list[tuple[int, int]],
    trim: kdp.TrimSize,
) -> int:
    """Write the interior PDF. Returns the page count actually written."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pw, ph = trim.width * kdp.INCH, trim.height * kdp.INCH

    solution_sheets = math.ceil(len(mazes) / 6) if mazes else 0
    total = 2 + 2 * len(pages) + 2 * solution_sheets + 2
    kdp.validate_page_count(total)

    c = pdfcanvas.Canvas(str(out), pagesize=(pw, ph))
    c.setTitle(meta.full_title)
    c.setAuthor(meta.author)
    c.setSubject("Colouring book")

    page_no = 0

    def new_page() -> bool:
        """Start a page and report whether it is a right-hand page."""
        nonlocal page_no
        if page_no:
            c.showPage()
        page_no += 1
        return page_no % 2 == 1

    # -- title page (recto) --------------------------------------------
    recto = new_page()
    box = kdp.interior_box(total, trim, recto)
    _centered(c, meta.title, pw / 2, ph * 0.62, "Helvetica-Bold", 30)
    y = ph * 0.56
    c.setFont("Helvetica", 13)
    for line in simpleSplit(meta.subtitle, "Helvetica", 13, box[2] * 0.85):
        c.drawCentredString(pw / 2, y, line)
        y -= 18
    _centered(c, meta.author, pw / 2, ph * 0.40, "Helvetica", 11)

    # -- verso: ownership line -----------------------------------------
    recto = new_page()
    box = kdp.interior_box(total, trim, recto)
    _centered(c, "This book belongs to", pw / 2, ph * 0.60, "Helvetica", 12)
    c.setLineWidth(0.8)
    c.line(pw * 0.28, ph * 0.54, pw * 0.72, ph * 0.54)

    # -- art pages -----------------------------------------------------
    for i, page in enumerate(pages, start=1):
        recto = new_page()
        box = kdp.interior_box(total, trim, recto)
        render_drawing(c, page.drawing, box)
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.grey)
        c.drawCentredString(pw / 2, 0.28 * kdp.INCH, str(i))
        c.setFillColor(colors.black)

        # Blank verso: the whole point of single-sided printing.
        new_page()

    # -- maze solutions ------------------------------------------------
    for sheet in range(solution_sheets):
        recto = new_page()
        box = kdp.interior_box(total, trim, recto)
        _centered(c, "Solutions", pw / 2, box[1] + box[3] - 6, "Helvetica-Bold", 14)
        chunk = mazes[sheet * 6 : sheet * 6 + 6]
        cell_w, cell_h = box[2] / 2, (box[3] - 30) / 3
        for k, (mseed, diff) in enumerate(chunk):
            col, row = k % 2, k // 2
            cx = box[0] + col * cell_w
            cy = box[1] + box[3] - 30 - (row + 1) * cell_h
            solved = maze_art.with_solution(mseed, diff)
            render_drawing(c, solved, (cx + 8, cy + 18, cell_w - 16, cell_h - 26), base_width=1.0)
            c.setFont("Helvetica", 8)
            c.setFillColor(colors.grey)
            c.drawCentredString(cx + cell_w / 2, cy + 6, f"maze {sheet * 6 + k + 1}")
            c.setFillColor(colors.black)
        new_page()

    # -- closing page --------------------------------------------------
    recto = new_page()
    box = kdp.interior_box(total, trim, recto)
    _centered(c, "Thank you", pw / 2, ph * 0.60, "Helvetica-Bold", 18)
    msg = (
        "If you enjoyed these pages, a short review helps more than you would "
        "think. It is the only way small books like this one get found."
    )
    c.setFont("Helvetica", 11)
    y = ph * 0.54
    for line in simpleSplit(msg, "Helvetica", 11, box[2] * 0.8):
        c.drawCentredString(pw / 2, y, line)
        y -= 16
    new_page()

    c.showPage()
    c.save()
    assert page_no == total, f"laid out {page_no} pages but planned {total}"
    return total


# ----------------------------------------------------------------------
# cover
# ----------------------------------------------------------------------
#: A quiet colour per theme. Covers print in colour even when the interior does not.
THEME_COLOURS = {
    "mandala": (0.29, 0.38, 0.51),
    "stainedglass": (0.35, 0.28, 0.48),
    "mazes": (0.20, 0.42, 0.40),
    "botanical": (0.45, 0.35, 0.27),
    "geometric": (0.24, 0.33, 0.45),
}


def write_cover(
    path: str | Path,
    meta: BookMetadata,
    motif: Drawing,
    pages: int,
    trim: kdp.TrimSize,
    paper: str = "white",
) -> tuple[float, float]:
    """Write the full-wrap cover PDF. Returns its size in inches."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    cw_in, ch_in = kdp.cover_size(pages, trim, paper)
    cw, ch = cw_in * kdp.INCH, ch_in * kdp.INCH
    bleed = kdp.COVER_BLEED * kdp.INCH
    safe = kdp.COVER_SAFE * kdp.INCH
    spine = kdp.spine_width(pages, paper) * kdp.INCH
    tw, th = trim.width * kdp.INCH, trim.height * kdp.INCH

    back_x = bleed
    spine_x = bleed + tw
    front_x = spine_x + spine

    r, g, b = THEME_COLOURS.get(meta.theme, (0.3, 0.35, 0.45))
    tint = colors.Color(r, g, b)
    pale = colors.Color(1 - (1 - r) * 0.12, 1 - (1 - g) * 0.12, 1 - (1 - b) * 0.12)

    c = pdfcanvas.Canvas(str(out), pagesize=(cw, ch))
    c.setTitle(f"{meta.full_title} (cover)")

    # Full bleed background.
    c.setFillColor(tint)
    c.rect(0, 0, cw, ch, stroke=0, fill=1)

    # -- front panel ---------------------------------------------------
    c.setFillColor(pale)
    c.rect(front_x + safe * 0.5, bleed + safe * 0.5, tw - safe, th - safe, stroke=0, fill=1)

    art_box = (front_x + safe, bleed + th * 0.30, tw - 2 * safe, th * 0.44)
    c.saveState()
    render_drawing(c, motif, art_box, base_width=1.6)
    c.restoreState()

    c.setFillColor(tint)
    title_y = bleed + th * 0.84
    c.setFont("Helvetica-Bold", 34)
    for line in simpleSplit(meta.title, "Helvetica-Bold", 34, tw - 2.4 * safe):
        c.drawCentredString(front_x + tw / 2, title_y, line)
        title_y -= 38

    c.setFont("Helvetica", 12.5)
    sub_y = title_y - 6
    for line in simpleSplit(meta.subtitle, "Helvetica", 12.5, tw - 2.6 * safe):
        c.drawCentredString(front_x + tw / 2, sub_y, line)
        sub_y -= 16

    c.setFont("Helvetica", 12)
    c.drawCentredString(front_x + tw / 2, bleed + th * 0.16, meta.author)

    # -- spine -----------------------------------------------------------
    if kdp.spine_text_allowed(pages) and spine > 12:
        c.saveState()
        c.translate(spine_x + spine / 2, bleed + th / 2)
        c.rotate(90)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", min(11, spine * 0.55))
        c.drawCentredString(0, -min(11, spine * 0.55) * 0.35, f"{meta.title}    {meta.author}")
        c.restoreState()

    # -- back panel ------------------------------------------------------
    c.setFillColor(pale)
    c.rect(back_x + safe * 0.5, bleed + safe * 0.5, tw - safe, th - safe, stroke=0, fill=1)

    c.setFillColor(tint)
    text_x = back_x + safe * 1.4
    text_w = tw - 2.8 * safe
    y = bleed + th - safe * 2.2
    c.setFont("Helvetica-Bold", 15)
    c.drawString(text_x, y, "Inside this book")
    y = _wrapped(c, meta.description, text_x, y - 26, text_w, "Helvetica", 10.5, 14.5)

    # KDP prints the barcode bottom-right of the back cover. Leave it empty.
    bx = back_x + tw - (kdp.BARCODE_W + kdp.BARCODE_MARGIN) * kdp.INCH
    by = bleed + kdp.BARCODE_MARGIN * kdp.INCH
    c.setFillColor(colors.white)
    c.rect(bx, by, kdp.BARCODE_W * kdp.INCH, kdp.BARCODE_H * kdp.INCH, stroke=0, fill=1)

    c.showPage()
    c.save()
    return (cw_in, ch_in)

"""Raster previews of a page, so you can look before you print.

Needs Pillow (`pip install pillow`); nothing else in Forge does, so the
import is deferred and the error message says what to install.
"""

from __future__ import annotations

from pathlib import Path

from .art.base import Drawing


def render_png(drawing: Drawing, path: str | Path, size: tuple[int, int] = (850, 1100), line: float = 2.4) -> Path:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - depends on the user's env
        raise RuntimeError("previews need Pillow: pip install pillow") from exc

    w, h = size
    margin = int(min(w, h) * 0.06)
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)

    minx, miny, maxx, maxy = drawing.bounds()
    span_x, span_y = max(maxx - minx, 1e-9), max(maxy - miny, 1e-9)
    scale = min((w - 2 * margin) / span_x, (h - 2 * margin) / span_y)
    ox = (w - span_x * scale) / 2
    oy = (h - span_y * scale) / 2

    def to_px(p: tuple[float, float]) -> tuple[float, float]:
        # PIL's y axis points down; the art's points up.
        return (ox + (p[0] - minx) * scale, h - (oy + (p[1] - miny) * scale))

    for p in drawing.paths:
        pts = [to_px(pt) for pt in p.points]
        if p.closed:
            pts.append(pts[0])
        draw.line(pts, fill="black", width=max(1, round(line * p.width)), joint="curve")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out

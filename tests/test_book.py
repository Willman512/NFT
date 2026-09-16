"""End to end: the book venture must produce files KDP would actually take."""

import json

import pytest

from forge.core.venture import TickContext
from forge.ventures.coloringbook import kdp
from forge.ventures.coloringbook.venture import ColoringBookVenture

pypdfium2 = pytest.importorskip("pypdfium2", reason="needs a PDF reader to check the output")


def _context(tmp_path, state=None):
    import logging

    state = state if state is not None else {}
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    return TickContext(
        now=0.0, budget_cents=0, float_cents=0,
        workdir=tmp_path / "work", out_dir=tmp_path / "out",
        dry_run=False, log=logging.getLogger("test"),
        get_state=lambda k, d=None: state.get(k, d),
        set_state=lambda k, v: state.__setitem__(k, v),
    )


def test_a_whole_book_comes_out_print_ready(tmp_path):
    state = {}
    venture = ColoringBookVenture({"art_pages": 12, "theme": "mazes"})
    venture.validate()
    result = venture.tick(_context(tmp_path, state))

    assert result.ok
    assert len(result.human_tasks) == 1, "a finished book must ask a human to publish it"

    book_dir = tmp_path / "out" / state["last_slug"]
    manifest = json.loads((book_dir / "metadata.json").read_text())
    assert (book_dir / "UPLOAD.md").exists()
    assert len(manifest["keywords"]) == 7
    assert len(manifest["categories"]) == 2

    interior = pypdfium2.PdfDocument(str(book_dir / "interior.pdf"))
    pages = len(interior)
    assert pages == manifest["pages"]
    kdp.validate_page_count(pages)
    width, height = interior[0].get_size()
    assert (width, height) == pytest.approx((8.5 * 72, 11 * 72))

    cover = pypdfium2.PdfDocument(str(book_dir / "cover.pdf"))
    assert len(cover) == 1
    cw, ch = cover[0].get_size()
    expected_w, expected_h = kdp.cover_size(pages, kdp.TRIM_SIZES["8.5x11"])
    assert cw == pytest.approx(expected_w * 72, abs=0.5)
    assert ch == pytest.approx(expected_h * 72, abs=0.5)


def test_art_pages_alternate_with_blank_backs(tmp_path):
    """Single sided printing is the selling point; the backs must stay empty."""
    state = {}
    venture = ColoringBookVenture({"art_pages": 12, "theme": "mandala"})
    venture.tick(_context(tmp_path, state))
    doc = pypdfium2.PdfDocument(str(tmp_path / "out" / state["last_slug"] / "interior.pdf"))

    # Pages 3, 5, 7... carry art (1-indexed); 4, 6, 8... are their blank backs.
    art = doc[2].get_objects()
    back = doc[3].get_objects()
    assert len(list(art)) > len(list(back))
    assert len(list(back)) == 0


def test_the_same_seed_rebuilds_the_same_book(tmp_path):
    first, second = {"next_seed": "4242"}, {"next_seed": "4242"}
    ColoringBookVenture({"art_pages": 12}).tick(_context(tmp_path / "a", first))
    ColoringBookVenture({"art_pages": 12}).tick(_context(tmp_path / "b", second))
    assert first["last_slug"] == second["last_slug"]
    assert first["last_title"] == second["last_title"]


def test_the_max_books_limit_is_respected(tmp_path):
    state = {"books_produced": "3"}
    result = ColoringBookVenture({"max_books": 3}).tick(_context(tmp_path, state))
    assert result.human_tasks == []
    assert "limit" in result.notes[0]


def test_bad_options_are_caught_at_startup():
    with pytest.raises(ValueError, match="trim"):
        ColoringBookVenture({"trim": "A4"}).validate()
    with pytest.raises(ValueError, match="art_pages"):
        ColoringBookVenture({"art_pages": 3}).validate()
    with pytest.raises(ValueError, match="theme"):
        ColoringBookVenture({"theme": "dinosaurs"}).validate()

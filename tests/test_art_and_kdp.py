import math
import random

import pytest

from forge.ventures.coloringbook import kdp
from forge.ventures.coloringbook.art import GENERATORS
from forge.ventures.coloringbook.art import maze as maze_art
from forge.ventures.coloringbook.art.base import Drawing, Path


@pytest.mark.parametrize("name", sorted(GENERATORS))
@pytest.mark.parametrize("difficulty", [1, 2, 3])
def test_every_generator_makes_a_usable_page(name, difficulty):
    drawing = GENERATORS[name](random.Random(11), difficulty)
    assert drawing.paths, f"{name} produced nothing"
    assert drawing.is_finite(), f"{name} produced NaN or infinity"
    minx, miny, maxx, maxy = drawing.bounds()
    assert maxx > minx and maxy > miny, f"{name} has no extent"
    # Nothing should be wildly outside the unit square the layout expects.
    assert -0.6 < minx and maxx < 1.6
    assert -0.6 < miny and maxy < 1.6


@pytest.mark.parametrize("name", sorted(GENERATORS))
def test_generators_are_deterministic(name):
    a = GENERATORS[name](random.Random(4), 2)
    b = GENERATORS[name](random.Random(4), 2)
    assert [p.points for p in a.paths] == [p.points for p in b.paths]


def test_a_path_needs_two_points():
    with pytest.raises(ValueError):
        Path([(0, 0)])


def test_empty_drawing_has_sane_bounds():
    assert Drawing().bounds() == (0.0, 0.0, 1.0, 1.0)


def test_maze_solution_is_continuous_and_spans_the_page():
    solution = maze_art.solve(99, 2)
    pts = solution.paths[0].points
    assert pts[0][1] == pytest.approx(0.0)
    assert pts[-1][1] == pytest.approx(1.0)
    # Consecutive points are adjacent cells, never a jump across the maze.
    step = max(
        math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)
    )
    assert step < 0.15


def test_solution_sheet_includes_the_walls():
    solved = maze_art.with_solution(99, 2)
    faint = [p for p in solved.paths if p.width < 0.5]
    bold = [p for p in solved.paths if p.width > 1.0]
    assert faint, "the maze itself is missing from the solution"
    assert bold, "the answer line is missing from the solution"


# ----------------------------------------------------------------------
def test_spine_matches_kdps_formula():
    assert kdp.spine_width(100, "white") == pytest.approx(0.2252)
    assert kdp.spine_width(100, "cream") == pytest.approx(0.25)


def test_cover_is_two_pages_plus_spine_plus_bleed():
    trim = kdp.TRIM_SIZES["8.5x11"]
    w, h = kdp.cover_size(60, trim)
    assert w == pytest.approx(2 * 8.5 + kdp.spine_width(60) + 0.25)
    assert h == pytest.approx(11 + 0.25)


def test_page_count_rules():
    kdp.validate_page_count(24)
    with pytest.raises(ValueError, match="even"):
        kdp.validate_page_count(25)
    with pytest.raises(ValueError, match="at least"):
        kdp.validate_page_count(22)
    with pytest.raises(ValueError, match="cap out"):
        kdp.validate_page_count(900)


def test_gutter_grows_with_the_book():
    assert kdp.gutter_margin(100) == 0.375
    assert kdp.gutter_margin(200) == 0.5
    assert kdp.gutter_margin(800) == 0.875


def test_the_gutter_swaps_sides_between_recto_and_verso():
    trim = kdp.TRIM_SIZES["8.5x11"]
    recto = kdp.interior_box(60, trim, recto=True)
    verso = kdp.interior_box(60, trim, recto=False)
    assert recto[0] > verso[0], "the inner margin should move to the spine side"
    assert recto[2] == verso[2], "both pages get the same printable width"
    # The box never strays past the trim.
    assert recto[0] + recto[2] <= trim.width * kdp.INCH + 1e-6


def test_unknown_paper_is_rejected():
    with pytest.raises(ValueError):
        kdp.spine_width(60, "papyrus")

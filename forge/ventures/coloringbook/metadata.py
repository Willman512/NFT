"""Themes, titles and listing metadata.

What a book is called and which seven keywords it carries matters more to
whether it sells than the art does, so this is kept separate from the
drawing code and is the part the salesman is allowed to argue about.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Theme:
    key: str
    display: str
    #: generator name -> relative weight
    generators: dict[str, float]
    difficulty: int
    titles: list[str]
    subtitles: list[str]
    keywords: list[str]
    audience: str
    blurb: str


THEMES: dict[str, Theme] = {
    "mandala": Theme(
        key="mandala",
        display="Mandalas",
        generators={"mandala": 0.75, "mosaic": 0.25},
        difficulty=2,
        titles=[
            "Mandala Calm",
            "Slow Circles",
            "The Quiet Mandala",
            "Mandalas for Stillness",
            "One Hundred Quiet Circles",
        ],
        subtitles=[
            "{n} Hand-Drawn Mandalas for Relaxation and Mindfulness",
            "{n} Symmetrical Designs to Colour and Unwind",
            "A Mindful Colouring Book with {n} Original Mandalas",
        ],
        keywords=[
            "mandala coloring book for adults",
            "adult coloring book relaxation",
            "mindfulness coloring book",
            "stress relief coloring book",
            "geometric coloring book adults",
            "coloring books for adults relaxation",
            "large print mandala coloring",
        ],
        audience="adults who colour to wind down",
        blurb=(
            "Forty-odd original mandalas, drawn in clean bold lines with plenty of "
            "open space. Every design is printed on its own page with the reverse "
            "left blank, so markers and gel pens will not bleed through onto the "
            "next picture."
        ),
    ),
    "stainedglass": Theme(
        key="stainedglass",
        display="Stained Glass",
        generators={"mosaic": 0.8, "mandala": 0.2},
        difficulty=2,
        titles=[
            "Stained Glass Windows",
            "Cathedral Light",
            "Rose Windows",
            "The Glass Garden",
        ],
        subtitles=[
            "{n} Rose Window Designs to Colour",
            "{n} Stained Glass Patterns for Calm Afternoons",
            "A Colouring Book of {n} Cathedral Windows",
        ],
        keywords=[
            "stained glass coloring book",
            "stained glass coloring book for adults",
            "rose window coloring",
            "church window coloring book",
            "geometric coloring book",
            "adult coloring book patterns",
            "mosaic coloring book",
        ],
        audience="adults who like bold, segmented shapes",
        blurb=(
            "Rose windows and mosaic panes with large, clearly bounded sections. "
            "The big cells suit coloured pencils and anyone who would rather fill "
            "shapes than shade fine detail."
        ),
    ),
    "mazes": Theme(
        key="mazes",
        display="Mazes",
        generators={"maze": 0.7, "truchet": 0.3},
        difficulty=2,
        titles=[
            "Lost and Found",
            "The Maze Hour",
            "Wander and Wonder",
            "Paths and Puzzles",
        ],
        subtitles=[
            "{n} Mazes with Solutions, from Gentle to Fiendish",
            "{n} Hand-Built Mazes for Puzzlers of Any Age",
            "A Puzzle Book of {n} Mazes and Patterns",
        ],
        keywords=[
            "maze book for adults",
            "maze puzzle book",
            "activity book for adults",
            "brain games puzzle book",
            "mazes for kids ages 8-12",
            "logic puzzle book",
            "travel activity book",
        ],
        audience="puzzlers, and parents buying quiet-time activities",
        blurb=(
            "Every maze has exactly one route from the opening at the bottom to "
            "the way out at the top, and every solution is printed in the back. "
            "The grids grow steadily tighter as the book goes on."
        ),
    ),
    "botanical": Theme(
        key="botanical",
        display="Botanical",
        generators={"floral": 0.8, "mandala": 0.2},
        difficulty=2,
        titles=[
            "Simple Blooms",
            "The Potted Garden",
            "Easy Flowers",
            "A Windowsill of Flowers",
        ],
        subtitles=[
            "{n} Bold and Simple Flower Designs for Easy Colouring",
            "{n} Large Print Botanical Pages for Relaxed Hands",
            "A Gentle Colouring Book of {n} Flowers and Pots",
        ],
        keywords=[
            "easy coloring book for adults",
            "large print coloring book",
            "flower coloring book for adults",
            "simple coloring book seniors",
            "bold and easy coloring book",
            "botanical coloring book",
            "coloring book for beginners",
        ],
        audience="beginners, older colourists, anyone wanting big simple shapes",
        blurb=(
            "Thick lines, large shapes and no fussy detail. Designed for hands "
            "that want something pleasant to fill in rather than a test of "
            "eyesight, and printed single sided throughout."
        ),
    ),
    "geometric": Theme(
        key="geometric",
        display="Geometric Ribbons",
        generators={"truchet": 0.7, "mosaic": 0.3},
        difficulty=2,
        titles=[
            "Ribbons and Loops",
            "Endless Patterns",
            "The Ribbon Book",
            "Knots and Coils",
        ],
        subtitles=[
            "{n} Flowing Geometric Patterns to Colour",
            "{n} Continuous Line Designs for Calm Focus",
            "An Adult Colouring Book of {n} Woven Patterns",
        ],
        keywords=[
            "geometric coloring book for adults",
            "pattern coloring book",
            "abstract coloring book adults",
            "modern coloring book",
            "adult coloring book designs",
            "relaxing pattern coloring",
            "op art coloring book",
        ],
        audience="adults who like repetitive, meditative filling-in",
        blurb=(
            "Interlocking ribbons that wander across each page and never quite "
            "repeat. Pick a colour, follow a band to wherever it goes, and the "
            "hour disappears."
        ),
    ),
}

#: KDP asks for two browse categories at listing time.
CATEGORIES = [
    "Books > Crafts, Hobbies & Home > Crafts & Hobbies",
    "Books > Humor & Entertainment > Puzzles & Games",
]

AUTHOR_DEFAULT = "The Quiet Press"


@dataclass
class BookMetadata:
    title: str
    subtitle: str
    author: str
    description: str
    keywords: list[str]
    categories: list[str]
    price_usd: float
    theme: str
    seed: int
    pages: int
    art_pages: int
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def full_title(self) -> str:
        return f"{self.title}: {self.subtitle}"

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "author": self.author,
            "description": self.description,
            "keywords": self.keywords,
            "categories": self.categories,
            "price_usd": self.price_usd,
            "theme": self.theme,
            "seed": self.seed,
            "pages": self.pages,
            "art_pages": self.art_pages,
            **self.extras,
        }


def build_metadata(
    theme: Theme,
    rng: random.Random,
    *,
    art_pages: int,
    total_pages: int,
    seed: int,
    author: str = AUTHOR_DEFAULT,
    price_usd: float = 7.99,
) -> BookMetadata:
    title = rng.choice(theme.titles)
    subtitle = rng.choice(theme.subtitles).format(n=art_pages)
    description = (
        f"{theme.blurb}\n\n"
        f"Inside you will find:\n"
        f"- {art_pages} original designs, one per page\n"
        f"- Single sided printing, so the backs stay clean for markers\n"
        f"- Wide inner margins, so nothing disappears into the spine\n"
        f"- {8.5} x {11} inch pages with room to work\n\n"
        f"Made for {theme.audience}."
    )
    return BookMetadata(
        title=title,
        subtitle=subtitle,
        author=author,
        description=description,
        keywords=list(theme.keywords[:7]),
        categories=list(CATEGORIES),
        price_usd=price_usd,
        theme=theme.key,
        seed=seed,
        pages=total_pages,
        art_pages=art_pages,
    )


def pick_theme(rng: random.Random, preferred: str | None = None) -> Theme:
    if preferred and preferred in THEMES:
        return THEMES[preferred]
    return THEMES[rng.choice(list(THEMES))]

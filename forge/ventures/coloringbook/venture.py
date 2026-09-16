"""The colouring book venture.

Costs nothing but electricity to run: the art is procedural, the PDFs are
vector, and nothing calls a paid API. That is deliberate. It is the one
earner in the swarm that can clear its own costs by construction, which
gives the banker something to compare the trader against.

What it cannot do is publish. KDP has no publishing API and its terms
forbid scripted uploads, so each finished book becomes a human task with
the files and the listing text already prepared.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

from ...core.money import usd
from ...core.venture import HumanTask, TickContext, TickResult, Venture
from . import kdp, layout, metadata


class ColoringBookVenture(Venture):
    name = "coloringbook"
    description = "Generates print-ready colouring books for Kindle Direct Publishing"
    min_capital_cents = 0

    def validate(self) -> None:
        trim = self.opt("trim", "8.5x11")
        if trim not in kdp.TRIM_SIZES:
            raise ValueError(f"unknown trim size {trim!r}; try one of {', '.join(kdp.TRIM_SIZES)}")
        art_pages = int(self.opt("art_pages", 30))
        if not 12 <= art_pages <= 120:
            raise ValueError(f"art_pages should be between 12 and 120, got {art_pages}")
        theme = self.opt("theme")
        if theme and theme not in metadata.THEMES:
            raise ValueError(f"unknown theme {theme!r}; try one of {', '.join(metadata.THEMES)}")

    # ------------------------------------------------------------------
    def tick(self, ctx: TickContext) -> TickResult:
        result = TickResult()

        produced = int(ctx.get_state("books_produced", "0") or 0)
        limit = int(self.opt("max_books", 0))
        if limit and produced >= limit:
            result.note(f"hit the configured limit of {limit} books; standing by")
            return result

        seed = int(ctx.get_state("next_seed", "") or random.randrange(1 << 30))
        rng = random.Random(seed)

        # The salesman gets a say: these hints are written by the office.
        theme = metadata.pick_theme(rng, ctx.get_state("theme_hint") or self.opt("theme"))
        price = float(ctx.get_state("price_hint", "") or self.opt("price_usd", 7.99))
        art_pages = int(self.opt("art_pages", 30))
        trim = kdp.TRIM_SIZES[self.opt("trim", "8.5x11")]

        ctx.log.info("building a %s book, %d pages, seed %d", theme.key, art_pages, seed)

        pages, mazes = layout.build_pages(theme, seed, art_pages)
        solution_sheets = (len(mazes) + 5) // 6 if mazes else 0
        total_pages = 2 + 2 * len(pages) + 2 * solution_sheets + 2

        meta = metadata.build_metadata(
            theme,
            rng,
            art_pages=art_pages,
            total_pages=total_pages,
            seed=seed,
            author=self.opt("author", metadata.AUTHOR_DEFAULT),
            price_usd=price,
        )

        slug = f"{theme.key}-{seed}"
        book_dir = ctx.out_dir / slug
        book_dir.mkdir(parents=True, exist_ok=True)

        written = layout.write_interior(book_dir / "interior.pdf", meta, pages, mazes, trim)
        cover_size = layout.write_cover(
            book_dir / "cover.pdf", meta, pages[0].drawing, written, trim, self.opt("paper", "white")
        )

        manifest = meta.to_dict()
        manifest.update(
            {
                "slug": slug,
                "trim": trim.name,
                "paper": self.opt("paper", "white"),
                "interior_pdf": str((book_dir / "interior.pdf").resolve()),
                "cover_pdf": str((book_dir / "cover.pdf").resolve()),
                "cover_size_in": [round(v, 3) for v in cover_size],
                "spine_in": round(kdp.spine_width(written, self.opt("paper", "white")), 4),
                "built_at": time.time(),
            }
        )
        (book_dir / "metadata.json").write_text(json.dumps(manifest, indent=2))
        (book_dir / "UPLOAD.md").write_text(_checklist(meta, manifest))

        cost_cents = usd(self.opt("cost_per_book_usd", 0))
        if cost_cents:
            result.spend(cost_cents, f"production cost for {meta.title}", ref=f"cost:{slug}")

        result.artifacts.extend([book_dir / "interior.pdf", book_dir / "cover.pdf"])
        result.human_tasks.append(
            HumanTask(
                title=f"Publish “{meta.title}” on KDP",
                body=(
                    f"{meta.full_title}\n"
                    f"{written} pages, {trim.name}, spine {manifest['spine_in']}in, "
                    f"list price ${price:.2f}\n\n"
                    f"Files and the full listing text are in:\n{book_dir.resolve()}\n"
                    f"Follow UPLOAD.md — it is about five minutes of copy and paste."
                ),
                artifact=str(book_dir.resolve()),
                url="https://kdp.amazon.com/en_US/bookshelf",
                payoff_cents=usd(self.opt("expected_royalty_usd", 0.0)),
                key=f"publish:{slug}",
            )
        )

        ctx.set_state("books_produced", str(produced + 1))
        ctx.set_state("next_seed", str(random.randrange(1 << 30)))
        ctx.set_state("last_title", meta.title)
        ctx.set_state("last_slug", slug)
        result.note(f"built “{meta.title}” ({written} pages) and queued it for upload")
        return result

    def status(self) -> dict[str, Any]:
        return {"themes": sorted(metadata.THEMES), "cost_model": "zero marginal cost"}


def _checklist(meta: metadata.BookMetadata, manifest: dict) -> str:
    """The copy-and-paste sheet that turns a folder into a listing."""
    kw = "\n".join(f"{i + 1}. {k}" for i, k in enumerate(meta.keywords))
    cats = "\n".join(f"- {c}" for c in meta.categories)
    return f"""# Publishing checklist

Everything below is ready to paste into KDP. Nothing here can be automated:
Amazon has no publishing API and its terms do not allow scripted uploads.

Start at <https://kdp.amazon.com/en_US/bookshelf> and choose **Create > Paperback**.

## 1. Paperback details

- **Title:** {meta.title}
- **Subtitle:** {meta.subtitle}
- **Author:** {meta.author}
- **Language:** English
- **Series:** leave blank
- **Edition number:** leave blank

**Description** (paste as is):

{meta.description}

**Keywords** (one per box, seven boxes):

{kw}

**Categories:**

{cats}

- **Adult content:** No
- **AI content disclosure:** these pages are produced by a procedural
  drawing program rather than a generative AI model. KDP asks separately
  about AI-generated text, images and translations. Answer honestly for how
  you understand your own book; if you are unsure, disclosing costs you
  nothing and an undisclosed answer can cost you the account.

## 2. Paperback content

- **Print option:** Black & white interior with {manifest['paper']} paper
- **Trim size:** {manifest['trim']} in
- **Bleed:** No bleed
- **Cover finish:** Matte
- **Interior file:** `{manifest['interior_pdf']}`
- **Cover file:** `{manifest['cover_pdf']}`
  (full wrap, {manifest['cover_size_in'][0]} x {manifest['cover_size_in'][1]} in,
  spine {manifest['spine_in']} in for {manifest['pages']} pages)

Run the KDP previewer before you approve. It is the only real check that
the margins survived.

## 3. Rights and pricing

- **Territories:** All
- **List price:** ${meta.price_usd:.2f} on Amazon.com
- Let KDP convert the other marketplaces automatically.

## 4. After it goes live

Royalties land in your Amazon account, not in this office. When Amazon
actually pays out, tell the books so the ledger is true:

    forge earned coloringbook <amount> --memo "KDP royalty, {meta.title}"

Until you do that, this book counts as a cost and nothing else, and the
banker will treat the venture accordingly.
"""

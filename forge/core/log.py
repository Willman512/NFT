"""Logging setup: readable on a terminal, greppable in a file."""

from __future__ import annotations

import logging
from pathlib import Path

_FMT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"


def setup_logging(workdir: Path, verbose: bool = False) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("forge")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    root.addHandler(console)

    fh = logging.FileHandler(workdir / "forge.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(_FMT, _DATE))
    root.addHandler(fh)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"forge.{name}")

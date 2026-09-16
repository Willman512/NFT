"""Which ventures exist, and how to build one by name."""

from __future__ import annotations

from typing import Any, Callable

from ..core.venture import Venture

_BUILDERS: dict[str, Callable[[dict[str, Any]], Venture]] = {}


def register(name: str, builder: Callable[[dict[str, Any]], Venture]) -> None:
    _BUILDERS[name] = builder


def available() -> list[str]:
    _load_builtins()
    return sorted(_BUILDERS)


def build(name: str, options: dict[str, Any]) -> Venture:
    _load_builtins()
    if name not in _BUILDERS:
        raise KeyError(f"unknown venture {name!r}; available: {', '.join(available())}")
    return _BUILDERS[name](options)


_loaded = False


def _load_builtins() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    from .coloringbook.venture import ColoringBookVenture
    from .memecoin.venture import MemecoinVenture

    register("coloringbook", lambda o: ColoringBookVenture(o))
    register("memecoin", lambda o: MemecoinVenture(o))
